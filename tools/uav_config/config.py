import copy
import difflib
import ipaddress
import json
import re
import socket
from pathlib import Path


BAUDS = (57600, 115200, 230400, 460800, 921600)


def defaults(hostname=None):
    hostname = hostname or socket.gethostname()
    match = re.fullmatch(r"uav(\d+)", hostname)
    node = int(match[1]) if match and 1 <= int(match[1]) <= 254 else 1
    return {
        "version": 2, "node": {"id": node, "hostname": hostname},
        "uart": {"device": "/dev/serial0", "baud": 115200,
                 "enabled": True, "console": False, "disable_bluetooth": True},
        "mesh": {"interface": "wlan1", "id": "uav-mesh", "address": f"10.77.0.{node}/24",
                 "frequency": 5745, "bandwidth": "HT20", "forwarding": False,
                 "autostart": True, "hotplug": True},
        "wifi": {"interface": "wlan0", "ssid": "", "autoconnect": True},
    }


def validate(data):
    template = defaults("uav1")
    if not isinstance(data, dict) or set(data) != set(template):
        raise ValueError("配置结构或顶层字段不正确")
    for section, expected in template.items():
        if isinstance(expected, dict):
            actual = data[section]
            if not isinstance(actual, dict) or set(actual) != set(expected):
                raise ValueError(f"{section} 字段缺失或包含未知字段")
            for name, value in expected.items():
                if type(actual[name]) is not type(value):
                    raise ValueError(f"{section}.{name} 类型不正确")
                if isinstance(actual[name], str) and any(ord(c) < 32 for c in actual[name]):
                    raise ValueError(f"{section}.{name} 不允许控制字符")
    if type(data["version"]) is not int or data["version"] != 2:
        raise ValueError("不支持的配置版本")
    node, uart, mesh, wifi = (data[k] for k in ("node", "uart", "mesh", "wifi"))
    if not 1 <= node["id"] <= 254:
        raise ValueError("节点编号须为1～254")
    if not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", node["hostname"]):
        raise ValueError("主机名须为1～63位字母、数字或中划线")
    for name in (mesh["interface"], wifi["interface"]):
        if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,15}", name):
            raise ValueError("网卡接口名称不合法")
    if mesh["interface"] == wifi["interface"]:
        raise ValueError("mesh 和调试 Wi-Fi 必须选择不同接口")
    address = ipaddress.ip_interface(mesh["address"])
    if address.version != 4 or address.ip.is_loopback or address.ip.is_multicast or address.ip.is_unspecified:
        raise ValueError("mesh 必须使用有效 IPv4 地址")
    if address.ip in (address.network.network_address, address.network.broadcast_address):
        raise ValueError("mesh 地址不能是子网或广播地址")
    if not 1 <= len(mesh["id"].encode("utf-8")) <= 32:
        raise ValueError("Mesh ID 必须为1～32字节")
    if not 2400 <= mesh["frequency"] <= 5900 or mesh["bandwidth"] not in ("HT20", "HT40+", "HT40-"):
        raise ValueError("不支持的频率或带宽")
    if uart["baud"] not in BAUDS:
        raise ValueError("不支持的波特率")
    if not uart["device"].startswith("/dev/") or "/../" in uart["device"]:
        raise ValueError("串口必须是 /dev/ 下的设备")
    if len(wifi["ssid"].encode("utf-8")) > 32:
        raise ValueError("SSID 不能超过32字节")
    return data


def load(path, initial=None):
    if Path(path).exists():
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(data, dict) and type(data.get("version")) is int and data["version"] == 1:
            data.pop("flight", None)
            data["version"] = 2
        return validate(data)
    return validate(copy.deepcopy(initial if initial is not None else defaults()))


def difference(before, after):
    a = json.dumps(before, ensure_ascii=False, indent=2, sort_keys=True).splitlines()
    b = json.dumps(after, ensure_ascii=False, indent=2, sort_keys=True).splitlines()
    return "\n".join(difflib.unified_diff(a, b, fromfile="已保存", tofile="草稿", lineterm="")) or "配置没有变化"
