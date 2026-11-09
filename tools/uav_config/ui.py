import copy
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from .config import BAUDS, difference, load, validate
from .flight import diagnose
from .system import System, discover, interfaces, radio_info, run


class Dialog:
    def call(self, kind, text, *args):
        cols, rows = shutil.get_terminal_size((80, 24))
        if rows < 18 or cols < 60:
            raise RuntimeError("终端至少需要60列×18行，请放大窗口后重试")
        height, width = min(rows-3, 24), min(cols-3, 100)
        base = ["whiptail", "--title", "UAV 机载配置"]
        if kind == "--textbox":
            base.append("--scrolltext")
        base += [kind, text, str(height), str(width)]
        if kind == "--menu":
            base.append(str(min(12, height-9)))
        result = subprocess.run(base + list(args), text=True, stderr=subprocess.PIPE)
        if result.returncode in (1, 255):
            return None
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "终端菜单无法启动")
        return result.stderr.rstrip("\n")

    def menu(self, text, choices):
        return self.call("--menu", text, *(str(x) for pair in choices for x in pair))

    def input(self, text, value=""):
        return self.call("--inputbox", text, str(value))

    def confirm(self, text):
        return self.call("--yesno", text) is not None

    def show(self, text):
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as stream:
            stream.write(str(text) + "\n")
            stream.flush()
            self.call("--textbox", stream.name)


LABELS = {
    "node": {"id": "节点编号", "hostname": "主机名"},
    "uart": {"device": "树莓派串口设备", "baud": "程序串口波特率", "enabled": "GPIO UART",
             "console": "串口登录终端", "disable_bluetooth": "关闭蓝牙并使用PL011"},
    "mesh": {"interface": "mesh网卡", "id": "Mesh ID（开放网络）", "address": "本机IPv4/CIDR",
             "frequency": "频率MHz／信道", "bandwidth": "带宽", "forwarding": "允许中继转发",
             "autostart": "开机启动", "hotplug": "热插拔恢复"},
    "wifi": {"interface": "调试Wi-Fi网卡", "ssid": "SSID（手动输入）", "autoconnect": "自动连接"},
}


class App:
    def __init__(self):
        self.dialog = Dialog()
        self.system = System()
        path = "/etc/uav/config.json"
        self.initial = load(path) if Path(path).exists() else discover()
        self.draft = copy.deepcopy(self.initial)

    def choose_value(self, section, key):
        value = self.draft[section][key]
        label = LABELS[section][key]
        if isinstance(value, bool):
            choice = self.dialog.menu(label, [("yes", "是／启用"), ("no", "否／关闭")])
            return None if choice is None else choice == "yes"
        if key == "baud":
            chosen = self.dialog.menu(label, [(str(x), str(x)) for x in BAUDS])
        elif key == "interface":
            choices = []
            for interface in interfaces():
                info = run(["iw", "dev", interface, "info"])
                mac = next((line.strip() for line in info.splitlines() if line.strip().startswith("addr ")), "")
                choices.append((interface, mac))
            chosen = self.dialog.menu(label, choices) if choices else None
        elif section == "mesh" and key == "frequency":
            _, _, allowed = radio_info(self.draft["mesh"]["interface"])
            chosen = self.dialog.menu("仅列出当前设备允许的非DFS信道", [(str(f), f"信道{c}／{f} MHz") for f, c in allowed])
        elif key == "bandwidth":
            chosen = self.dialog.menu(label, [("HT20", "20MHz（当前已验证）"), ("HT40+", "40MHz，副信道在上"), ("HT40-", "40MHz，副信道在下")])
        else:
            chosen = self.dialog.input(label, value)
        if chosen is None:
            return None
        return int(chosen) if isinstance(value, int) else chosen

    def edit(self, section):
        while True:
            values = self.draft[section]
            text = "修改只进入草稿；从“保存与应用”执行。"
            if section == "uart":
                text += "\n默认GPIO：物理8=TX、10=RX。"
            choices = [(key, f"{label}: {values[key]}") for key, label in LABELS[section].items()]
            if section == "wifi":
                choices.append(("scan", "扫描并选择SSID"))
            key = self.dialog.menu(text, choices)
            if key is None:
                return
            try:
                if key == "scan":
                    listing = run(["nmcli", "-g", "SSID", "device", "wifi", "list", "ifname", self.draft["wifi"]["interface"]])
                    names = list(dict.fromkeys(line.replace(r"\:", ":").replace(r"\\", "\\") for line in listing.splitlines() if line))
                    selected = self.dialog.menu("选择SSID", [(str(i), name) for i, name in enumerate(names)]) if names else None
                    value = None if selected is None else names[int(selected)]
                    key = "ssid"
                else:
                    value = self.choose_value(section, key)
                if value is None:
                    continue
                updated = copy.deepcopy(self.draft)
                updated[section][key] = value
                if section == "node" and key == "id":
                    if self.dialog.confirm(f"同时建议主机名uav{value}与mesh地址10.77.0.{value}/24？"):
                        updated["node"]["hostname"] = f"uav{value}"
                        updated["mesh"]["address"] = f"10.77.0.{value}/24"
                validate(updated)
                self.draft = updated
            except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
                self.dialog.show(str(exc))

    def status(self):
        commands = [["hostname"], ["ip", "-br", "address"], ["nmcli", "device", "status"],
                    ["systemctl", "is-active", "uav-mesh"],
                    ["iw", "dev", self.draft["mesh"]["interface"], "station", "dump"],
                    ["iw", "dev", self.draft["mesh"]["interface"], "mpath", "dump"]]
        lines = ["当前实际状态（配置JSON仅表示期望值，不代表已应用）"]
        for cmd in commands:
            lines += ["\n$ " + " ".join(cmd), run(cmd, check=False)]
        path = Path("/dev/serial0")
        lines += [f"\n/dev/serial0 → {path.resolve()}" if path.exists() else "\n/dev/serial0 不存在"]
        history = Path("/var/lib/uav-config/last-apply.json")
        if history.exists():
            lines += ["\n最近一次应用结果", history.read_text()]
        self.dialog.show("\n".join(lines))

    def diagnostics(self):
        choice = self.dialog.menu("测试会临时打开串口，先结束其他读取程序", [
            ("uart", "UART心跳与版本查询"), ("position", "UART本地位置10Hz／30秒（只验证消息）"),
            ("ping", "经mesh ping指定IPv4")])
        if choice is None:
            return
        if choice == "ping":
            target = self.dialog.input("目标mesh IPv4", "10.77.0.2" if self.draft["node"]["id"] == 1 else "10.77.0.1")
            if target is not None:
                import ipaddress
                address = ipaddress.IPv4Address(target)
                self.dialog.show(run(["ping", "-I", self.draft["mesh"]["interface"], "-c", "10", "-W", "1", str(address)], timeout=25, check=False))
        else:
            print("正在读取飞控，请等待……", flush=True)
            self.dialog.show(diagnose(self.draft["uart"]["device"], self.draft["uart"]["baud"], position=choice == "position"))

    def record(self, section, ok, text):
        from datetime import datetime
        name = "/var/lib/uav-config/last-apply.json"
        path = self.system.path(name)
        previous = json.loads(path.read_text()) if path.exists() else {}
        previous[section] = {"time": datetime.now().astimezone().isoformat(), "success": ok, "message": text}
        self.system.write(name, json.dumps(previous, ensure_ascii=False, indent=2) + "\n")

    def apply(self, section):
        validate(self.draft)
        self.dialog.show(json.dumps(self.draft, ensure_ascii=False, indent=2))
        notes = {"mesh": "将应用节点身份与mesh，通信会短暂中断。若SSH走mesh可能断线。",
                 "uart": "将修改UART启动配置，需手动重启树莓派。",
                 "wifi": "将切换调试Wi-Fi；当前SSH可能断开。密码由NetworkManager在终端询问。"}
        if not self.dialog.confirm(notes[section] + "\n确认应用此子系统？"):
            return
        self.system.save(self.draft)
        self.initial = copy.deepcopy(self.draft)
        try:
            result = getattr(self.system, "apply_" + section)(self.draft)
            self.record(section, True, result)
        except Exception as exc:
            result = "应用未全部完成：" + str(exc)
            self.record(section, False, result)
        if self.system.backup:
            result += f"\n备份目录：{self.system.backup}"
        self.dialog.show(result)

    def storage(self):
        while True:
            choice = self.dialog.menu("保存不等于已应用；按子系统操作", [
                ("diff", "查看相对已保存配置的修改"), ("save", "仅保存期望配置"),
                ("mesh", "应用节点身份与mesh"), ("uart", "应用树莓派UART"),
                ("wifi", "应用调试Wi-Fi"),
                ("export", "导出草稿JSON（不含密码）"), ("import", "导入JSON到草稿")])
            if choice is None:
                return
            try:
                if choice == "diff":
                    self.dialog.show(difference(self.initial, self.draft))
                elif choice == "save":
                    self.system.save(self.draft)
                    self.initial = copy.deepcopy(self.draft)
                    self.dialog.show("已保存到 /etc/uav/config.json；系统尚未应用新的修改。")
                elif choice == "export":
                    path = self.dialog.input("导出绝对路径（不含Wi-Fi密码）", "/home/uav/uav-config-export.json")
                    if path is not None:
                        if not Path(path).is_absolute():
                            raise ValueError("请输入绝对路径")
                        if Path(path).exists() and not self.dialog.confirm("目标文件已存在，确认覆盖？"):
                            continue
                        self.system.save(self.draft, path)
                        self.dialog.show("导出完成")
                elif choice == "import":
                    path = self.dialog.input("导入JSON文件绝对路径", "/home/uav/uav-config-export.json")
                    if path is not None:
                        if not Path(path).is_file():
                            raise ValueError("导入文件不存在")
                        self.draft = load(path)
                        self.dialog.show("已读入草稿，尚未保存或应用。")
                else:
                    self.apply(choice)
            except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
                self.dialog.show(str(exc))

    def main(self):
        while True:
            choice = self.dialog.menu("草稿→保存→分项应用。", [
                ("status", "当前实际状态"), ("node", "节点编号／主机名"), ("uart", "树莓派串口"),
                ("mesh", "Mesh网络"), ("wifi", "调试Wi-Fi"),
                ("tests", "通信测试"), ("storage", "保存／应用／导入／导出"), ("exit", "退出")])
            try:
                if choice in (None, "exit"):
                    if self.draft != self.initial and not self.dialog.confirm("草稿尚未保存，确认丢弃并退出？"):
                        continue
                    return
                if choice == "status":
                    self.status()
                elif choice == "tests":
                    self.diagnostics()
                elif choice == "storage":
                    self.storage()
                else:
                    self.edit(choice)
            except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
                self.dialog.show(str(exc))
