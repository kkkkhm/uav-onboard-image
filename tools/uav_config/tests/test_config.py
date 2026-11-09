import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.uav_config.config import defaults, difference, load, validate
from tools.uav_config.system import System, discover, mesh_files, read_env, uart_files


class ConfigTests(unittest.TestCase):
    def test_each_node_has_distinct_defaults(self):
        for i in range(1, 5):
            data = validate(defaults(f"uav{i}"))
            self.assertEqual(data["node"]["id"], i)
            self.assertEqual(data["mesh"]["address"], f"10.77.0.{i}/24")

    def test_import_rejects_invalid_and_secret_fields(self):
        cases = [("node", "id", True), ("node", "id", 255), ("node", "hostname", "bad;command"),
                 ("mesh", "interface", "wlan1;echo"), ("mesh", "interface", "wlan0"),
                 ("mesh", "address", "10.77.0.255/24"), ("mesh", "address", "::1/64"),
                 ("uart", "baud", 12345),
                 ("mesh", "id", "x\nMESH_IFACE=wlan0")]
        for section, key, value in cases:
            with self.subTest(section=section, key=key, value=value):
                data = defaults("uav1")
                data[section][key] = value
                with self.assertRaises(ValueError):
                    validate(data)
        data = defaults("uav1")
        data["wifi"]["password"] = "must-not-be-exported"
        with self.assertRaises(ValueError):
            validate(data)

    def test_import_file_and_difference(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"data.json"
            data = defaults("uav1")
            path.write_text(json.dumps(data))
            self.assertEqual(load(path), data)
            modified = copy.deepcopy(data)
            modified["node"]["id"] = 2
            self.assertIn('+    "id": 2', difference(data, modified))

    def test_existing_mesh_and_uart_are_discovered(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/"etc/default").mkdir(parents=True)
            (root/"etc/hostname").write_text("uav2\n")
            (root/"etc/default/uav-mesh").write_text("MESH_ADDRESS=10.77.0.2/24\nMESH_ID='custom mesh'\n")
            (root/"boot/firmware").mkdir(parents=True)
            (root/"boot/firmware/config.txt").write_text("[all]\nenable_uart=1\ndtoverlay=disable-bt\n")
            (root/"boot/firmware/cmdline.txt").write_text("console=tty1 root=PARTUUID=abc rootwait\n")
            data = discover(root)
            self.assertEqual(data["mesh"]["id"], "custom mesh")
            self.assertEqual(data["mesh"]["address"], "10.77.0.2/24")
            self.assertFalse(data["uart"]["console"])

    def test_uart_edit_preserves_unrelated_boot_options_and_root(self):
        boot = "[pi5]\ndtparam=pciex1\n[all]\nenable_uart=1\nenable_uart=1\ndtoverlay=disable-bt\narm_64bit=1\n"
        cmdline = "console=serial0,115200 console=tty1 root=PARTUUID=abc rootwait quiet"
        first = uart_files(boot, cmdline, defaults("uav1")["uart"])
        second = uart_files(*first, defaults("uav1")["uart"])
        self.assertEqual(first[0].count("enable_uart="), 1)
        self.assertIn("dtparam=pciex1", first[0])
        self.assertIn("arm_64bit=1", first[0])
        self.assertEqual(first[1], "console=tty1 root=PARTUUID=abc rootwait quiet\n")
        self.assertEqual(first[1], second[1])
        self.assertEqual(first[0], second[0])

    def test_mesh_templates_and_env_round_trip(self):
        config = defaults("uav2")
        config["mesh"]["id"] = "test 'quoted' $mesh"
        config["mesh"]["interface"] = "mesh-usb"
        files = mesh_files(validate(config), "0e8d", "7612")
        self.assertNotIn("set power_save", files["/usr/local/sbin/uav-mesh-up"][0])
        self.assertIn(r"mesh\x2dusb.device", files["/etc/systemd/system/uav-mesh.service"][0])
        self.assertIn('ATTRS{idProduct}=="7612"', files["/etc/udev/rules.d/90-uav-mesh.rules"][0])
        with tempfile.TemporaryDirectory() as directory:
            system = System(Path(directory))
            for path, (text, mode) in files.items():
                system.write(path, text, mode)
            env = read_env(system.path("/etc/default/uav-mesh"))
            self.assertEqual(env["MESH_ID"], config["mesh"]["id"])
            self.assertEqual(env["MESH_ADDRESS"], "10.77.0.2/24")

    def test_atomic_save_backup_and_offline_execution_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            system = System(Path(directory))
            first = defaults("uav1")
            system.save(first)
            second = defaults("uav2")
            system.save(second)
            self.assertEqual(load(system.path("/etc/uav/config.json")), second)
            old = json.loads((system.backup/"etc/uav/config.json").read_text())
            self.assertEqual(old, first)
            if os.name != "nt":
                self.assertEqual(system.path("/etc/uav/config.json").stat().st_mode & 0o777, 0o600)
            with self.assertRaises(RuntimeError):
                system.apply_mesh(second)

    def test_existing_v1_configuration_migrates_without_changing_network(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"old.json"
            old = defaults("uav2")
            old["version"] = 1
            old["flight"] = {"system_id": 2, "port": "TELEM1"}
            old["mesh"]["address"] = "10.88.0.12/24"
            path.write_text(json.dumps(old))
            migrated = load(path)
            self.assertNotIn("flight", migrated)
            self.assertEqual(migrated["version"], 2)
            self.assertEqual(migrated["mesh"], old["mesh"])
            self.assertEqual(migrated["uart"], old["uart"])
            self.assertEqual(json.loads(path.read_text()), old)
            system = System(Path(directory)/"root")
            system.save(migrated)
            saved = system.path("/etc/uav/config.json").read_text()
            self.assertNotIn('"flight"', saved)

    def test_cli_runs_without_hardware_and_rejects_bad_file(self):
        result = subprocess.run([sys.executable, "-m", "tools.uav_config", "--example"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        validate(json.loads(result.stdout))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"bad.json"
            path.write_text('{"version":2}')
            result = subprocess.run([sys.executable, "-m", "tools.uav_config", "--validate", str(path)], capture_output=True)
            self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
