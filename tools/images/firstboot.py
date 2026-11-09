"""Assign a cloned Raspberry Pi its own identity before starting mesh services."""
import argparse
import json
import os
import re
import socket
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/opt/uav-telemetry")
from tools.uav_config.config import defaults, validate
from tools.uav_config.system import mesh_files
from tools.uav_telemetry.config import build


def write(root, name, text, mode=0o644):
    path = root / name.lstrip("/")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".uav-new")
    temporary.write_text(text, encoding="utf-8")
    temporary.chmod(mode)
    temporary.replace(path)


def settings(data):
    if not isinstance(data, dict) or set(data) != {"version", "node_id", "serial_device", "baud", "peers"}:
        raise ValueError("uav-node.json fields: version, node_id, serial_device, baud, peers")
    if type(data["version"]) is not int or data["version"] != 1 or type(data["node_id"]) is not int or not 1 <= data["node_id"] <= 254:
        raise ValueError("Select a unique node_id from 1 to 254")
    peers = data["peers"]
    if (not isinstance(peers, list) or len(set(peers)) != len(peers)
            or any(type(n) is not int or not 1 <= n <= 254 for n in peers)
            or not any(n != data["node_id"] for n in peers)):
        raise ValueError("peers must contain distinct node numbers and at least one other node")
    config = defaults("uav" + str(data["node_id"]))
    config["uart"]["device"] = data["serial_device"]
    config["uart"]["baud"] = data["baud"]
    return validate(config)


def provision(root, data):
    config = settings(data)
    hostname = config["node"]["hostname"]
    write(root, "/etc/uav/config.json", json.dumps(config, ensure_ascii=False, indent=2) + "\n", 0o600)
    for name, (text, mode) in mesh_files(config, "0e8d", "7612").items():
        write(root, name, text, mode)
    hosts_path = root / "etc/hosts"
    hosts = hosts_path.read_text() if hosts_path.exists() else "127.0.0.1 localhost\n"
    hosts = re.sub(r"^127\.0\.1\.1\s+.*\n?", "", hosts, flags=re.M)
    write(root, "/etc/hosts", hosts.rstrip() + "\n127.0.1.1\t" + hostname + "\n")
    write(root, "/etc/hostname", hostname + "\n")
    telemetry = build(root, tuple(data["peers"]))
    write(root, "/etc/uav/telemetry.json", json.dumps(telemetry, indent=2) + "\n", 0o644)
    write(root, "/var/lib/uav-image/identity.json", json.dumps(data, indent=2) + "\n", 0o600)
    marker = root / "var/lib/uav-image/needs-setup"
    if marker.exists():
        marker.unlink()
    return config


def main():
    parser = argparse.ArgumentParser(description="Configure a unique cloned UAV node")
    parser.add_argument("node", type=int, nargs="?")
    parser.add_argument("--boot", action="store_true")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("Run with sudo")
    root = Path("/")
    pending = root / "var/lib/uav-image/needs-setup"
    if not pending.exists():
        print("Identity already initialized; use sudo uav-config for later configuration.")
        return
    if args.boot:
        subprocess.run(["ssh-keygen", "-A"], check=True)
    path = root / "boot/firmware/uav-node.json"
    data = json.loads(path.read_text())
    if args.node is not None:
        data["node_id"] = args.node
    if data.get("node_id") is None:
        if args.boot:
            serial = (root / "proc/device-tree/serial-number").read_bytes().decode().strip("\x00\n")
            hostname = "uav-setup-" + serial[-8:]
            write(root, "/etc/hostname", hostname + "\n")
            hosts = (root / "etc/hosts").read_text()
            hosts = re.sub(r"^127\.0\.1\.1\s+.*$", "127.0.1.1\t" + hostname, hosts, flags=re.M)
            write(root, "/etc/hosts", hosts)
            socket.sethostname(hostname.encode())
            print("Mesh and telemetry remain stopped. Run sudo uav-setup NODE_NUMBER.")
            return
        data["node_id"] = int(input("Unique node number (1-4 for this fleet): ").strip())
    # Validate all external input before stopping services or writing configuration.
    settings(data)
    if not args.boot:
        subprocess.run(["systemctl", "stop", "uav-telemetry", "uav-mesh"], check=True)
    config = provision(root, data)
    socket.sethostname(config["node"]["hostname"].encode())
    path.rename(path.with_name("uav-node.applied.json"))
    if not args.boot:
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        subprocess.run(["udevadm", "control", "--reload-rules"], check=True)
        subprocess.run(["nmcli", "general", "reload"], check=True)
        subprocess.run(["systemctl", "try-restart", "avahi-daemon.service"], check=False)
        subprocess.run(["systemctl", "restart", "uav-mesh", "uav-telemetry"], check=True)
    print("Initialized " + config["node"]["hostname"] + " " + config["mesh"]["address"])


if __name__ == "__main__":
    main()
