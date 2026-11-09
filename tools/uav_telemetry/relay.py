import errno
import json
import logging
import math
import os
import select
import socket
import struct
import tempfile
import time
from pathlib import Path

import serial
from pymavlink.dialects.v20 import common as mavlink

from .config import validate


LOG = logging.getLogger("uav-telemetry")


def interface_ready(interface, address):
    import fcntl
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        request = struct.pack("256s", interface.encode("ascii"))
        try:
            addr = fcntl.ioctl(probe.fileno(), 0x8915, request)
            flags = fcntl.ioctl(probe.fileno(), 0x8913, request)
        except OSError:
            return False
    return socket.inet_ntoa(addr[20:24]) == address and bool(struct.unpack_from("H", flags, 16)[0] & 1)


def parser():
    decoder = mavlink.MAVLink(None)
    decoder.robust_parsing = True
    return decoder


def json_value(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    return value


def write_state(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".telemetry-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(json_value(data), stream, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
        os.chmod(temp, 0o640)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class Relay:
    def __init__(self, config, state_path):
        self.config = validate(config)
        self.state_path = state_path
        self.serial = None
        self.socket = None
        self.decoder = parser()
        self.serial_due = self.network_due = self.state_due = self.log_due = 0.0
        self.next_network_check = 0.0
        self.flight_id = None
        self.last_heartbeat = None
        self.local_latest = {}
        self.peer_latest = {node: {} for node in config["peers"]}
        self.peer_heartbeat = {node: None for node in config["peers"]}
        self.by_address = {address: node for node, address in config["peers"].items()}
        self.counters = dict(serial_frames=0, serial_bad=0, filtered=0, tx_datagrams=0,
                             tx_bytes=0, tx_errors=0, dropped_offline=0,
                             rx_datagrams=0, rx_frames=0, rx_bad=0, rx_untrusted=0)
        self.tx_peers = {node: 0 for node in config["peers"]}
        self.rx_peers = {node: 0 for node in config["peers"]}
        self.events = {}
        self.running = False
        self.started = time.monotonic()

    def event(self, key, message):
        if self.events.get(key) != message:
            self.events[key] = message
            LOG.info("%s: %s", key, message)

    def heartbeat(self, msg):
        return (msg.get_type() == "HEARTBEAT" and msg.get_srcComponent() == 1
                and msg.autopilot == mavlink.MAV_AUTOPILOT_PX4)

    def flight_online(self, now):
        return self.last_heartbeat is not None and now-self.last_heartbeat <= self.config["heartbeat_timeout_s"]

    def snapshot(self):
        now = time.monotonic()
        peers = {}
        for node, address in self.config["peers"].items():
            last = self.peer_heartbeat[node]
            peers[node] = {"address": address, "online": last is not None and now-last <= self.config["heartbeat_timeout_s"],
                           "heartbeat_age_s": None if last is None else round(now-last, 3),
                           "tx_submitted": self.tx_peers[node], "rx_frames": self.rx_peers[node],
                           "latest": self.peer_latest[node]}
        return {"running": self.running, "pid": os.getpid(), "updated_monotonic": now,
                "uptime_s": round(now-self.started, 2), "config": self.config,
                "serial_open": self.serial is not None, "flight_online": self.flight_online(now),
                "flight_system_id": self.flight_id,
                "flight_heartbeat_age_s": None if self.last_heartbeat is None else round(now-self.last_heartbeat, 3),
                "network_ready": self.socket is not None, "events": self.events,
                "counters": self.counters, "local_latest": self.local_latest, "peers": peers}

    def close_serial(self):
        if self.serial is not None:
            self.serial.close()
            self.serial = None
        self.decoder = parser()
        self.flight_id = None
        self.last_heartbeat = None
        self.local_latest.clear()

    def close_network(self):
        if self.socket is not None:
            self.socket.close()
            self.socket = None

    def connect_serial(self, now):
        if self.serial is not None or now < self.serial_due:
            return
        self.serial_due = now + self.config["reconnect_s"]
        try:
            self.serial = serial.Serial(self.config["serial_device"], self.config["baud"], timeout=0, exclusive=True)
            self.event("serial", "connected; waiting for PX4 heartbeat")
        except (OSError, serial.SerialException) as exc:
            self.event("serial", f"waiting: {exc}")

    def connect_network(self, now):
        config = self.config
        if self.socket is not None and now >= self.next_network_check:
            self.next_network_check = now+0.5
            if not interface_ready(config["interface"], config["local_ip"]):
                self.close_network()
                self.event("network", "mesh interface/address disappeared")
        if self.socket is not None or now < self.network_due:
            return
        self.network_due = now + config["reconnect_s"]
        if not interface_ready(config["interface"], config["local_ip"]):
            self.event("network", "waiting for configured mesh interface/address")
            return
        candidate = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            candidate.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, config["interface"].encode("ascii")+b"\0")
            candidate.bind((config["local_ip"], config["port"]))
            candidate.setblocking(False)
            self.socket = candidate
            self.event("network", f"ready on {config['interface']} {config['local_ip']}:{config['port']}")
        except OSError as exc:
            candidate.close()
            self.event("network", f"waiting: {exc}")

    def local_frame(self, msg, now):
        name = msg.get_type()
        if name == "BAD_DATA":
            self.counters["serial_bad"] += 1
            return
        self.counters["serial_frames"] += 1
        expected = self.config["system_id"]
        if self.heartbeat(msg) and (expected is None or msg.get_srcSystem() == expected):
            if self.flight_id != msg.get_srcSystem():
                self.local_latest.clear()
            self.flight_id = msg.get_srcSystem()
            self.last_heartbeat = now
        if (name not in self.config["messages"] or msg.get_srcComponent() != 1
                or msg.get_srcSystem() != self.flight_id or not self.flight_online(now)):
            self.counters["filtered"] += 1
            return
        self.local_latest[name] = {"received_monotonic": now, "system_id": msg.get_srcSystem(), "data": msg.to_dict()}
        if self.socket is None:
            self.counters["dropped_offline"] += 1
            return
        # Forward original bytes; never write received network data to the serial port.
        data = bytes(msg.get_msgbuf())
        for node, address in self.config["peers"].items():
            try:
                self.socket.sendto(data, (address, self.config["port"]))
                self.counters["tx_datagrams"] += 1
                self.counters["tx_bytes"] += len(data)
                self.tx_peers[node] += 1
            except OSError as exc:
                self.counters["tx_errors"] += 1
                if exc.errno in (errno.ENODEV, errno.ENETDOWN, errno.EADDRNOTAVAIL):
                    self.close_network()
                    self.event("network", f"mesh unavailable: {exc}")
                    break

    def read_serial(self, now):
        if self.serial is None:
            return
        try:
            data = self.serial.read(min(4096, max(1, self.serial.in_waiting)))
            if data:
                for msg in self.decoder.parse_buffer(data) or []:
                    self.local_frame(msg, now)
        except (OSError, serial.SerialException) as exc:
            self.close_serial()
            self.serial_due = now+self.config["reconnect_s"]
            self.event("serial", f"disconnected: {exc}")

    def network_packet(self, data, address, now):
        node = self.by_address.get(address[0])
        if node is None:
            self.counters["rx_untrusted"] += 1
            return
        self.counters["rx_datagrams"] += 1
        decoded = parser()
        messages = decoded.parse_buffer(data) or []
        if not messages:
            self.counters["rx_bad"] += 1
        for msg in messages:
            if msg.get_type() == "BAD_DATA":
                self.counters["rx_bad"] += 1
                continue
            if msg.get_type() not in self.config["messages"] or msg.get_srcComponent() != 1:
                continue
            if self.heartbeat(msg):
                self.peer_heartbeat[node] = now
            self.peer_latest[node][msg.get_type()] = {"received_monotonic": now,
                "system_id": msg.get_srcSystem(), "data": msg.to_dict()}
            self.counters["rx_frames"] += 1
            self.rx_peers[node] += 1

    def read_network(self, now):
        if self.socket is None:
            time.sleep(0.02)
            return
        try:
            if select.select([self.socket], [], [], 0.02)[0]:
                for _ in range(64):
                    try:
                        data, address = self.socket.recvfrom(2048)
                    except BlockingIOError:
                        break
                    self.network_packet(data, address, now)
        except OSError as exc:
            self.close_network()
            self.event("network", f"receive error: {exc}")

    def run(self, stop):
        self.running = True
        try:
            while not stop.is_set():
                now = time.monotonic()
                self.connect_network(now)
                self.connect_serial(now)
                self.read_serial(now)
                self.read_network(now)
                if now >= self.state_due:
                    write_state(self.state_path, self.snapshot())
                    self.state_due = now+1
                if now >= self.log_due:
                    LOG.info("flight_online=%s mesh_ready=%s tx_submitted=%d rx_frames=%d",
                        self.flight_online(now), self.socket is not None,
                        self.counters["tx_datagrams"], self.counters["rx_frames"])
                    self.log_due = now+10
        finally:
            self.running = False
            self.close_serial()
            self.close_network()
            write_state(self.state_path, self.snapshot())
