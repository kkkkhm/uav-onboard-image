import copy
import json
import tempfile
import unittest
from pathlib import Path

from tools.uav_config.config import defaults
from tools.uav_telemetry.config import MESSAGES, build, validate


class ConfigTests(unittest.TestCase):
    def make_root(self, root, node=1):
        (root/"etc/uav").mkdir(parents=True)
        (root/"etc/default").mkdir(parents=True)
        data = defaults(f"uav{node}")
        (root/"etc/uav/config.json").write_text(json.dumps(data))
        (root/"etc/default/uav-mesh").write_text(f'MESH_ADDRESS=10.77.0.{node}/24\nMESH_IFACE=wlan1\n')
        return data

    def test_build_two_and_four_node_configs(self):
        for node in (1, 2, 3, 4):
            with self.subTest(node=node), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.make_root(root, node)
                config = build(root, (1, 2, 3, 4))
                self.assertEqual(config["node_id"], node)
                self.assertEqual(config["local_ip"], f"10.77.0.{node}")
                self.assertEqual(len(config["peers"]), 3)
                self.assertNotIn(str(node), config["peers"])
                self.assertEqual(config["serial_device"], "/dev/serial0")

    def test_rejects_commands_bad_peers_and_bool_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            self.make_root(Path(directory))
            original = build(Path(directory))
        for section, value in [("messages", ["HEARTBEAT", "COMMAND_LONG"]),
                               ("node_id", True), ("port", 22), ("serial_device", "/dev/../etc/passwd"),
                               ("peers", {"2": "10.77.0.1"}), ("heartbeat_timeout_s", float("nan"))]:
            with self.subTest(field=section):
                config = copy.deepcopy(original)
                config[section] = value
                with self.assertRaises(ValueError):
                    validate(config)

    def test_running_mesh_address_overrides_unsaved_network_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = self.make_root(root)
            data["mesh"]["address"] = "10.88.0.1/24"
            (root/"etc/uav/config.json").write_text(json.dumps(data))
            self.assertEqual(build(root)["local_ip"], "10.77.0.1")

    def test_console_and_missing_mesh_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = self.make_root(root)
            data["uart"]["console"] = True
            (root/"etc/uav/config.json").write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                build(root)
            data["uart"]["console"] = False
            (root/"etc/uav/config.json").write_text(json.dumps(data))
            (root/"etc/default/uav-mesh").unlink()
            with self.assertRaises(ValueError):
                build(root)


if __name__ == "__main__":
    unittest.main()
