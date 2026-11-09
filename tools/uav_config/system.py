import ipaddress
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from .config import defaults, validate


def run(args, timeout=20, check=True):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(f"{shlex.join(args)}\n{result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.strip()


def read_env(path):
    if not path.exists():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            fields = shlex.split(value)
            values[key] = fields[0] if len(fields) == 1 else value
    return values


def interfaces():
    return re.findall(r"^\s*Interface (\S+)", run(["iw", "dev"]), re.M)


def radio_info(interface):
    info = run(["iw", "dev", interface, "info"])
    match = re.search(r"^\s*wiphy (\d+)", info, re.M)
    if not match:
        raise ValueError("无法识别无线物理设备")
    capabilities = run(["iw", "phy", f"phy{match[1]}", "info"])
    freqs = []
    for line in capabilities.splitlines():
        found = re.search(r"\* (\d+)(?:\.0)? MHz \[(\d+)\]", line)
        if found and not any(flag in line.lower() for flag in ("disabled", "no ir", "no-ir", "radar")):
            freqs.append((int(found[1]), int(found[2])))
    return info, capabilities, freqs


def usb_ids(interface):
    device = Path(f"/sys/class/net/{interface}/device").resolve()
    for parent in (device, *device.parents):
        if (parent / "idVendor").exists() and (parent / "idProduct").exists():
            return tuple((parent / key).read_text().strip() for key in ("idVendor", "idProduct"))
    raise ValueError("mesh 热插拔模板需要选择 USB 无线网卡")


def discover(root=Path("/")):
    config = defaults()
    config["node"]["hostname"] = (root / "etc/hostname").read_text().strip() if (root / "etc/hostname").exists() else config["node"]["hostname"]
    env = read_env(root / "etc/default/uav-mesh")
    mapping = {"MESH_ADDRESS": "address", "MESH_IFACE": "interface", "MESH_ID": "id", "MESH_BANDWIDTH": "bandwidth"}
    for key, field in mapping.items():
        if key in env:
            config["mesh"][field] = env[key]
    if "MESH_FREQUENCY" in env:
        config["mesh"]["frequency"] = int(env["MESH_FREQUENCY"])
    if "MESH_FORWARDING" in env:
        config["mesh"]["forwarding"] = env["MESH_FORWARDING"] == "1"
    boot = root / "boot/firmware/config.txt"
    if boot.exists():
        text = boot.read_text()
        config["uart"]["enabled"] = bool(re.search(r"^enable_uart=1\s*$", text, re.M))
        config["uart"]["disable_bluetooth"] = bool(re.search(r"^dtoverlay=disable-bt\s*$", text, re.M))
    cmdline = root / "boot/firmware/cmdline.txt"
    if cmdline.exists():
        config["uart"]["console"] = any(t.startswith(("console=serial0,", "console=ttyAMA0,", "console=ttyS0,")) for t in cmdline.read_text().split())
    if root == Path("/"):
        profile = run(["nmcli", "-g", "GENERAL.CONNECTION", "device", "show", config["wifi"]["interface"]], check=False)
        if profile and profile != "--":
            config["wifi"]["ssid"] = run(["nmcli", "-g", "802-11-wireless.ssid", "connection", "show", profile], check=False)
        config["mesh"]["autostart"] = run(["systemctl", "is-enabled", "uav-mesh"], check=False) == "enabled"
        config["mesh"]["hotplug"] = (root / "etc/udev/rules.d/90-uav-mesh.rules").exists()
    return validate(config)


def uart_files(boot, cmdline, settings):
    lines = [line for line in boot.splitlines() if not re.match(r"^\s*enable_uart=", line)
             and not re.match(r"^\s*dtoverlay=(?:disable-bt|miniuart-bt)(?:,|\s|$)", line)]
    sections = [line.strip() for line in lines if re.fullmatch(r"\s*\[[^]]+\]\s*", line)]
    if not sections or sections[-1] != "[all]":
        lines += ["", "[all]"]
    lines.append(f"enable_uart={int(settings['enabled'])}")
    if settings["disable_bluetooth"]:
        lines.append("dtoverlay=disable-bt")
    words = [w for w in cmdline.split() if not w.startswith(("console=serial0,", "console=ttyAMA0,", "console=ttyS0,"))]
    if settings["console"]:
        words.append("console=serial0,115200")
    return "\n".join(lines) + "\n", " ".join(words) + "\n"


def mesh_files(config, vendor, product):
    mesh = config["mesh"]
    iface = mesh["interface"]
    unit_iface = iface.replace("-", r"\x2d")
    env = {"MESH_ADDRESS": mesh["address"], "MESH_IFACE": iface, "MESH_ID": mesh["id"],
           "MESH_FREQUENCY": str(mesh["frequency"]), "MESH_BANDWIDTH": mesh["bandwidth"],
           "MESH_FORWARDING": str(int(mesh["forwarding"]))}
    unit = f"""[Unit]
Description=UAV mesh network
Wants=NetworkManager.service
BindsTo=sys-subsystem-net-devices-{unit_iface}.device
After=NetworkManager.service sys-subsystem-net-devices-{unit_iface}.device

[Service]
Type=oneshot
RemainAfterExit=yes
EnvironmentFile=/etc/default/uav-mesh
ExecStart=/usr/local/sbin/uav-mesh-up
ExecStop=-/usr/bin/env iw dev $MESH_IFACE mesh leave
ExecStop=-/usr/bin/env ip address del $MESH_ADDRESS dev $MESH_IFACE
TimeoutStartSec=30

[Install]
WantedBy=multi-user.target
"""
    script = '''#!/bin/bash
set -eu
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
nmcli device set "$MESH_IFACE" managed no
iw dev "$MESH_IFACE" mesh leave 2>/dev/null || :
ip link set "$MESH_IFACE" down
iw dev "$MESH_IFACE" set type mp
ip link set "$MESH_IFACE" up
iw dev "$MESH_IFACE" mesh join "$MESH_ID" freq "$MESH_FREQUENCY" "$MESH_BANDWIDTH"
iw dev "$MESH_IFACE" set mesh_param "mesh_fwding=$MESH_FORWARDING"
ip address replace "$MESH_ADDRESS" dev "$MESH_IFACE"
'''
    rule = f'ACTION=="add", SUBSYSTEM=="net", KERNEL=="{iface}", ATTRS{{idVendor}}=="{vendor}", ATTRS{{idProduct}}=="{product}", TAG+="systemd", ENV{{SYSTEMD_WANTS}}+="uav-mesh.service"\n'
    if not mesh["hotplug"]:
        rule = "# Hotplug startup disabled by uav-config\n"
    return {
        "/etc/default/uav-mesh": ("\n".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in env.items()) + "\n", 0o644),
        "/etc/NetworkManager/conf.d/90-uav-mesh.conf": (f"[device-uav-mesh]\nmatch-device=interface-name:{iface}\nmanaged=0\n", 0o644),
        "/usr/local/sbin/uav-mesh-up": (script, 0o755),
        "/etc/systemd/system/uav-mesh.service": (unit, 0o644),
        "/etc/udev/rules.d/90-uav-mesh.rules": (rule, 0o644),
    }


class System:
    def __init__(self, root=Path("/")):
        self.root = Path(root)
        self.backup = None

    def path(self, name):
        return self.root / name.lstrip("/")

    def write(self, name, text, mode=0o600):
        path = self.path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if self.backup is None:
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                self.backup = self.path(f"/var/backups/uav-config/{stamp}")
            dest = self.backup / name.lstrip("/")
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                shutil.copy2(path, dest)
        fd, temp = tempfile.mkstemp(prefix=".uav-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temp, mode)
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def save(self, config, name="/etc/uav/config.json"):
        validate(config)
        self.write(name, json.dumps(config, ensure_ascii=False, indent=2) + "\n")

    def live(self):
        if self.root != Path("/"):
            raise RuntimeError("离线目录只能生成文件，不允许执行系统修改")

    def apply_uart(self, config):
        self.live()
        validate(config)
        boot, cmdline = self.path("/boot/firmware/config.txt"), self.path("/boot/firmware/cmdline.txt")
        a, b = uart_files(boot.read_text(), cmdline.read_text(), config["uart"])
        self.write("/boot/firmware/config.txt", a, 0o644)
        self.write("/boot/firmware/cmdline.txt", b, 0o644)
        if not config["uart"]["console"]:
            run(["systemctl", "disable", "--now", "serial-getty@serial0.service",
                 "serial-getty@ttyAMA0.service", "serial-getty@ttyS0.service"])
        if config["uart"]["disable_bluetooth"] and shutil.which("systemctl"):
            if run(["systemctl", "list-unit-files", "hciuart.service", "--no-legend"], check=False).startswith("hciuart.service"):
                run(["systemctl", "disable", "--now", "hciuart.service"])
        elif not config["uart"]["disable_bluetooth"]:
            if run(["systemctl", "list-unit-files", "hciuart.service", "--no-legend"], check=False).startswith("hciuart.service"):
                run(["systemctl", "enable", "hciuart.service"])
        return "UART 启动配置已写入；需要手动重启树莓派后检查 /dev/serial0。"

    def apply_mesh(self, config):
        self.live()
        validate(config)
        m = config["mesh"]
        _, caps, freqs = radio_info(m["interface"])
        if "* mesh point" not in caps:
            raise ValueError("所选网卡没有声明 mesh point 支持")
        permitted = {freq for freq, _ in freqs}
        if m["frequency"] not in permitted:
            raise ValueError("当前监管域下，此频率未被允许用于本模板（排除禁用／DFS／no-IR）")
        secondary = m["frequency"] + (20 if m["bandwidth"] == "HT40+" else -20)
        if m["bandwidth"] != "HT20" and secondary not in permitted:
            raise ValueError("40MHz 副信道不可用，请选择HT20")
        vendor, product = usb_ids(m["interface"])
        if not re.fullmatch(r"[0-9a-f]{4}", vendor) or not re.fullmatch(r"[0-9a-f]{4}", product):
            raise ValueError("USB 设备标识不合法")
        current = run(["ip", "-j", "address", "show", "dev", m["interface"]])
        ours = {a["local"] for item in json.loads(current) for a in item.get("addr_info", [])}
        target = str(ipaddress.ip_interface(m["address"]).ip)
        if target not in ours:
            reply = subprocess.run(["ping", "-I", m["interface"], "-c", "1", "-W", "1", target], capture_output=True, timeout=4)
            if reply.returncode == 0:
                raise ValueError("新 mesh IP 已有设备响应，停止应用")
        old = read_env(self.path("/etc/default/uav-mesh")).get("MESH_IFACE", "wlan1")
        if self.path("/etc/systemd/system/uav-mesh.service").exists():
            run(["systemctl", "stop", "uav-mesh"])
        for name, (text, mode) in mesh_files(config, vendor, product).items():
            self.write(name, text, mode)
        hosts = self.path("/etc/hosts")
        content = hosts.read_text() if hosts.exists() else "127.0.0.1 localhost\n"
        content = re.sub(r"^127\.0\.1\.1\s+.*$", "127.0.1.1\t" + config["node"]["hostname"], content, flags=re.M)
        if not re.search(r"^127\.0\.1\.1\s", content, re.M):
            content += "\n127.0.1.1\t" + config["node"]["hostname"] + "\n"
        self.write("/etc/hosts", content, 0o644)
        self.write("/etc/hostname", config["node"]["hostname"] + "\n", 0o644)
        run(["hostnamectl", "set-hostname", config["node"]["hostname"]])
        run(["systemctl", "daemon-reload"])
        run(["udevadm", "control", "--reload-rules"])
        run(["nmcli", "general", "reload"])
        if old != m["interface"] and Path(f"/sys/class/net/{old}").exists():
            run(["nmcli", "device", "set", old, "managed", "yes"])
        run(["systemctl", "enable" if m["autostart"] else "disable", "uav-mesh"])
        run(["systemctl", "start", "uav-mesh"], timeout=40)
        return "节点与 mesh 已应用。\n" + run(["ip", "-br", "address", "show", m["interface"]])

    def apply_wifi(self, config):
        self.live()
        validate(config)
        wifi = config["wifi"]
        if not wifi["ssid"]:
            raise ValueError("请先选择或输入 SSID")
        print("按 NetworkManager 提示输入密码；密码不进入配置导出或日志。", flush=True)
        result = subprocess.run(["nmcli", "--ask", "device", "wifi", "connect", wifi["ssid"], "ifname", wifi["interface"]])
        if result.returncode:
            raise RuntimeError("Wi-Fi 连接未完成；配置未标记为应用成功")
        uuid = run(["nmcli", "-g", "GENERAL.CON-UUID", "device", "show", wifi["interface"]])
        run(["nmcli", "connection", "modify", "uuid", uuid, "connection.autoconnect", "yes" if wifi["autoconnect"] else "no"])
        return "Wi-Fi 连接成功，自动连接设置已保存。"
