import ipaddress
import json
import re
from pathlib import Path


MESSAGES = ["HEARTBEAT", "ATTITUDE", "LOCAL_POSITION_NED", "GLOBAL_POSITION_INT",
            "GPS_RAW_INT", "SYS_STATUS", "BATTERY_STATUS", "EXTENDED_SYS_STATE"]


def validate(config):
    expected = {"version", "node_id", "interface", "local_ip", "port", "serial_device", "baud",
                "system_id", "peers", "messages", "heartbeat_timeout_s", "reconnect_s"}
    if not isinstance(config, dict) or set(config) != expected:
        raise ValueError("遥测配置字段不完整或包含未知项")
    for field in ("version", "node_id", "port", "baud"):
        if type(config[field]) is not int:
            raise ValueError(f"{field} 必须是整数")
    if config["version"] != 1 or not 1 <= config["node_id"] <= 254:
        raise ValueError("不支持的版本或节点编号")
    if not isinstance(config["interface"], str) or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,15}", config["interface"]):
        raise ValueError("接口名无效")
    if not isinstance(config["local_ip"], str):
        raise ValueError("本机地址必须是IPv4字符串")
    own = ipaddress.IPv4Address(config["local_ip"])
    if own.is_multicast or own.is_unspecified or int(own) == 0xffffffff:
        raise ValueError("本机mesh地址不合法")
    if not 1024 <= config["port"] <= 65535 or config["baud"] not in (57600, 115200, 230400, 460800, 921600):
        raise ValueError("UDP端口或波特率不合法")
    if (not isinstance(config["serial_device"], str) or not config["serial_device"].startswith("/dev/")
            or ".." in Path(config["serial_device"]).parts or any(ord(c) < 32 for c in config["serial_device"])):
        raise ValueError("串口必须是 /dev/ 下的设备路径")
    if config["system_id"] is not None and (type(config["system_id"]) is not int or not 1 <= config["system_id"] <= 255):
        raise ValueError("system_id 必须为空（自动发现）或1～255")
    if not isinstance(config["peers"], dict) or not 1 <= len(config["peers"]) <= 253:
        raise ValueError("需要至少一个peer")
    addresses = set()
    for node, address in config["peers"].items():
        if not isinstance(node, str) or not node.isdigit() or not 1 <= int(node) <= 254 or int(node) == config["node_id"]:
            raise ValueError("peer编号不合法／与本机重复")
        if not isinstance(address, str):
            raise ValueError("peer地址必须是IPv4字符串")
        parsed = ipaddress.IPv4Address(address)
        if parsed == own or parsed.is_multicast or parsed.is_unspecified or int(parsed) == 0xffffffff or address in addresses:
            raise ValueError("peer地址不合法或重复")
        addresses.add(address)
    if not isinstance(config["messages"], list) or not config["messages"] or len(config["messages"]) > 32:
        raise ValueError("消息列表不合法")
    if any(not isinstance(name, str) or name not in MESSAGES for name in config["messages"]):
        raise ValueError("本版只转发已列出的状态遥测，不接受执行命令或参数消息")
    if "HEARTBEAT" not in config["messages"] or len(set(config["messages"])) != len(config["messages"]):
        raise ValueError("必须包含HEARTBEAT，消息列表不能重复")
    for field in ("heartbeat_timeout_s", "reconnect_s"):
        if type(config[field]) not in (float, int) or not 0.1 <= config[field] <= 60:
            raise ValueError(f"{field} 必须在0.1～60秒之间")
    return config


def load(path):
    return validate(json.loads(Path(path).read_text(encoding="utf-8")))


def build(root=Path("/"), peer_nodes=(1, 2)):
    from tools.uav_config.config import load as load_node
    from tools.uav_config.system import discover, read_env
    root = Path(root)
    node_path = root / "etc/uav/config.json"
    node = load_node(node_path) if node_path.exists() else discover(root)
    if node["uart"]["console"]:
        raise ValueError("请先关闭串口登录终端，再运行遥测服务")
    env = read_env(root / "etc/default/uav-mesh")
    if "MESH_ADDRESS" not in env:
        raise ValueError("请先完成并应用mesh配置，再生成遥测配置")
    local = ipaddress.IPv4Interface(env["MESH_ADDRESS"])
    peers = {}
    for peer in peer_nodes:
        if type(peer) is not int or not 1 <= peer <= 254:
            raise ValueError("peer节点编号须为1～254")
        address = local.network.network_address + peer
        if address not in local.network or address == local.network.broadcast_address:
            raise ValueError("mesh子网不足以按编号生成peer地址，请编辑独立遥测配置")
        if address != local.ip:
            peers[str(peer)] = str(address)
    config = {"version": 1, "node_id": node["node"]["id"],
              "interface": env.get("MESH_IFACE", node["mesh"]["interface"]),
              "local_ip": str(local.ip), "port": 14570,
              "serial_device": node["uart"]["device"], "baud": node["uart"]["baud"],
              "system_id": None, "peers": peers, "messages": MESSAGES.copy(),
              "heartbeat_timeout_s": 3.0, "reconnect_s": 2.0}
    return validate(config)
