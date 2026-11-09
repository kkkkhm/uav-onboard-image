import copy
import json
import os
import select
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from pymavlink.dialects.v20 import common as mavlink

from tools.uav_telemetry.relay import Relay, json_value, write_state


def wait_for(predicate, timeout=4):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition did not become true")


@unittest.skipUnless(sys.platform == "linux", "需要Linux真实PTY和SO_BINDTODEVICE")
class RelayTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.device_dir = tempfile.TemporaryDirectory(prefix="uav-telemetry-test-", dir="/dev/shm")
        self.master, slave = os.openpty()
        self.alias = Path(self.device_dir.name)/"uart"
        self.alias.symlink_to(os.ttyname(slave))
        os.close(slave)
        self.peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.peer.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, b"lo\0")
        self.peer.bind(("127.0.0.2", 0))
        self.peer.settimeout(2)
        self.port = self.peer.getsockname()[1]
        self.config = {"version": 1, "node_id": 1, "interface": "lo", "local_ip": "127.0.0.1",
                       "port": self.port, "serial_device": str(self.alias), "baud": 115200,
                       "system_id": None, "peers": {"2": "127.0.0.2"},
                       "messages": ["HEARTBEAT", "ATTITUDE", "LOCAL_POSITION_NED"],
                       "heartbeat_timeout_s": 1.5, "reconnect_s": 0.1}
        self.state = Path(self.directory.name)/"state.json"
        self.local_codec = mavlink.MAVLink(None, srcSystem=1, srcComponent=1)
        self.remote_codec = mavlink.MAVLink(None, srcSystem=2, srcComponent=1)
        self.stop = threading.Event()
        self.thread = None
        self.error = None

    def start(self):
        self.relay = Relay(self.config, self.state)
        def worker():
            try:
                self.relay.run(self.stop)
            except Exception as exc:
                self.error = exc
        self.thread = threading.Thread(target=worker, daemon=True)
        self.thread.start()
        wait_for(lambda: self.relay.serial is not None or self.error is not None)
        if self.error:
            raise self.error

    def tearDown(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=4)
        self.peer.close()
        if self.master is not None:
            os.close(self.master)
        self.device_dir.cleanup()
        self.directory.cleanup()
        if self.error:
            raise self.error

    def heartbeat(self, codec=None):
        return mavlink.MAVLink_heartbeat_message(2, 12, 0, 0, 3, 3).pack(codec or self.local_codec)

    def test_serial_raw_frame_is_forwarded_unchanged_and_remote_is_not_echoed(self):
        self.start()
        wait_for(lambda: self.relay.socket is not None)
        packet = self.heartbeat()
        os.write(self.master, packet)
        received, source = self.peer.recvfrom(2048)
        self.assertEqual(received, packet)
        self.assertEqual(source, ("127.0.0.1", self.port))
        self.peer.sendto(self.heartbeat(self.remote_codec), source)
        wait_for(lambda: self.relay.counters["rx_frames"] == 1)
        self.assertTrue(self.relay.snapshot()["peers"]["2"]["online"])
        self.assertFalse(select.select([self.master], [], [], 0.15)[0], "网络数据不应写回飞控串口")
        self.peer.settimeout(0.15)
        with self.assertRaises(socket.timeout):
            self.peer.recvfrom(2048)

    def test_rejects_network_commands_unknown_peer_and_bad_data(self):
        self.start()
        wait_for(lambda: self.relay.socket is not None)
        target = ("127.0.0.1", self.port)
        command = mavlink.MAVLink_command_long_message(1, 1, 400, 0, 0, 0, 0, 0, 0, 0, 0).pack(self.remote_codec)
        self.peer.sendto(command, target)
        self.peer.sendto(b"not-mavlink", target)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as stranger:
            stranger.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, b"lo\0")
            stranger.bind(("127.0.0.3", 0))
            stranger.sendto(self.heartbeat(self.remote_codec), target)
        wait_for(lambda: self.relay.counters["rx_untrusted"] >= 1 and self.relay.counters["rx_bad"] >= 1)
        self.assertEqual(self.relay.counters["rx_frames"], 0)
        self.assertFalse(select.select([self.master], [], [], 0.15)[0])

    def test_network_not_ready_retries_without_buffering_old_frames(self):
        blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        blocker.bind(("127.0.0.1", self.port))
        try:
            self.start()
            os.write(self.master, self.heartbeat())
            wait_for(lambda: self.relay.counters["dropped_offline"] == 1)
            self.assertIsNone(self.relay.socket)
            blocker.close()
            wait_for(lambda: self.relay.socket is not None)
            self.peer.settimeout(0.15)
            with self.assertRaises(socket.timeout):
                self.peer.recvfrom(2048)
            packet = self.heartbeat()
            os.write(self.master, packet)
            self.peer.settimeout(2)
            self.assertEqual(self.peer.recvfrom(2048)[0], packet)
        finally:
            blocker.close()

    def test_uart_disconnect_reconnect_through_stable_device_alias(self):
        self.start()
        wait_for(lambda: self.relay.socket is not None)
        os.write(self.master, self.heartbeat())
        self.peer.recvfrom(2048)
        os.close(self.master)
        self.master = None
        wait_for(lambda: self.relay.serial is None)
        self.assertFalse(self.relay.snapshot()["flight_online"])
        self.master, slave = os.openpty()
        self.alias.unlink()
        self.alias.symlink_to(os.ttyname(slave))
        os.close(slave)
        wait_for(lambda: self.relay.serial is not None)
        packet = self.heartbeat()
        os.write(self.master, packet)
        self.assertEqual(self.peer.recvfrom(2048)[0], packet)

    def test_heartbeat_stale_and_refresh_and_state_cli(self):
        self.config["heartbeat_timeout_s"] = 0.15
        self.start()
        wait_for(lambda: self.relay.socket is not None)
        packet = self.heartbeat(self.remote_codec)
        self.peer.sendto(packet, ("127.0.0.1", self.port))
        wait_for(lambda: self.relay.snapshot()["peers"]["2"]["online"])
        wait_for(lambda: not self.relay.snapshot()["peers"]["2"]["online"])
        self.peer.sendto(packet, ("127.0.0.1", self.port))
        wait_for(lambda: self.relay.snapshot()["peers"]["2"]["online"])
        wait_for(lambda: self.state.exists() and '"HEARTBEAT"' in self.state.read_text())
        output = subprocess.run([sys.executable, "-m", "tools.uav_telemetry", "--state", str(self.state), "status", "--json"],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(output.returncode, 0, output.stderr)
        self.assertEqual(json.loads(output.stdout)["peers"]["2"]["latest"]["HEARTBEAT"]["system_id"], 2)
        output = subprocess.run([sys.executable, "-m", "tools.uav_telemetry", "--state", str(self.state),
                                 "listen", "--node", "2", "--message", "HEARTBEAT", "--seconds", "0.3"],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(output.returncode, 0, output.stderr)
        self.assertIn("sysid=2", output.stdout)

    def test_offline_interface_never_falls_back_to_default_route(self):
        self.config["interface"] = "uav-no-such-if"
        self.start()
        os.write(self.master, self.heartbeat())
        wait_for(lambda: self.relay.counters["dropped_offline"] == 1)
        self.assertIsNone(self.relay.socket)
        self.assertEqual(self.relay.counters["tx_datagrams"], 0)


class StateTests(unittest.TestCase):
    def test_nonfinite_sensor_values_stay_explicitly_invalid(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"state.json"
            write_state(path, {"roll": float("nan"), "samples": [float("inf"), 1.0]})
            self.assertEqual(json.loads(path.read_text()), {"roll": None, "samples": [None, 1.0]})


if __name__ == "__main__":
    unittest.main()
