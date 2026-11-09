import copy
import json
import tempfile
import unittest
from pathlib import Path

from tools.images.firstboot import provision, settings
from tools.uav_config.system import read_env
from tools.uav_telemetry.config import load


class FirstbootTests(unittest.TestCase):
    def data(self, node):
        return {"version": 1, "node_id": node, "serial_device": "/dev/serial0",
                "baud": 115200, "peers": [1, 2, 3, 4]}

    def test_four_clones_have_distinct_matching_identities(self):
        for node in range(1, 5):
            with self.subTest(node=node), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                marker = root / "var/lib/uav-image/needs-setup"
                marker.parent.mkdir(parents=True)
                marker.touch()
                provision(root, self.data(node))
                self.assertFalse(marker.exists())
                self.assertEqual((root / "etc/hostname").read_text(), f"uav{node}\n")
                env = read_env(root / "etc/default/uav-mesh")
                self.assertEqual(env["MESH_ADDRESS"], f"10.77.0.{node}/24")
                relay = load(root / "etc/uav/telemetry.json")
                self.assertEqual(relay["node_id"], node)
                self.assertEqual(relay["local_ip"], f"10.77.0.{node}")
                self.assertEqual(set(relay["peers"]), {str(n) for n in range(1, 5) if n != node})
                self.assertIsNone(relay["system_id"])
                self.assertNotIn("power_save", (root / "usr/local/sbin/uav-mesh-up").read_text())

    def test_invalid_identity_does_not_write_any_files(self):
        cases = [("node_id", None), ("node_id", 0), ("node_id", True),
                 ("node_id", 255), ("baud", 12345), ("serial_device", "ttyAMA0"),
                 ("peers", [1, 1, 2]), ("peers", [1])]
        for key, value in cases:
            data = self.data(1)
            data[key] = value
            with self.subTest(key=key, value=value), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                with self.assertRaises(ValueError):
                    provision(root, data)
                self.assertEqual(list(root.iterdir()), [])

    def test_custom_uart_and_existing_hosts_are_preserved(self):
        data = self.data(3)
        data.update(serial_device="/dev/ttyUSB0", baud=57600)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "etc").mkdir()
            (root / "etc/hosts").write_text("127.0.0.1 localhost\n127.0.1.1 uav1\n::1 localhost\n")
            provision(root, data)
            relay = load(root / "etc/uav/telemetry.json")
            self.assertEqual((relay["serial_device"], relay["baud"]), ("/dev/ttyUSB0", 57600))
            hosts = (root / "etc/hosts").read_text()
            self.assertIn("::1 localhost", hosts)
            self.assertEqual(hosts.count("127.0.1.1"), 1)
            self.assertNotIn("uav1", hosts)


if __name__ == "__main__":
    unittest.main()
