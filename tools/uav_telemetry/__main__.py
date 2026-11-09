import argparse
import json
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

from .config import build, load


CONFIG = "/etc/uav/telemetry.json"
STATE = "/run/uav-telemetry/state.json"


def status(state, raw=False):
    if raw:
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return
    age = max(0, time.monotonic()-state["updated_monotonic"])
    current = state["running"] and age < 4
    config = state["config"]
    print(f"node={config['node_id']} process={'running' if current else 'stopped/stale'} snapshot_age={age:.1f}s")
    print(f"UART={config['serial_device']} baud={config['baud']} serial_open={state['serial_open'] and current} flight_online={state['flight_online'] and current} flight_sysid={state['flight_system_id']}")
    print(f"mesh={config['interface']} {config['local_ip']}:{config['port']} ready={state['network_ready'] and current}")
    print("counters:", json.dumps(state["counters"]))
    for node, peer in state["peers"].items():
        print(f"peer={node} ip={peer['address']} online={peer['online'] and current} hb_age={peer['heartbeat_age_s']}s tx_submitted={peer['tx_submitted']} rx_frames={peer['rx_frames']}")
    if not current:
        print("Snapshot is not live; check systemctl status uav-telemetry.")
    for source, event in state["events"].items():
        print(f"{source}: {event}")


def listen(args):
    from .config import MESSAGES
    if args.message and args.message not in MESSAGES:
        raise ValueError("请选择支持的状态消息名：" + ",".join(MESSAGES))
    last = {}
    started = time.monotonic()
    print("Reading local snapshots (1Hz diagnostics); Ctrl+C to stop.", flush=True)
    while not args.seconds or time.monotonic()-started < args.seconds:
        try:
            state = json.loads(Path(args.state).read_text(encoding="utf-8"))
        except FileNotFoundError:
            time.sleep(0.5)
            continue
        if state["running"] and time.monotonic()-state["updated_monotonic"] < 4:
            groups = {str(state["config"]["node_id"]): state["local_latest"]}
            groups.update({node: peer["latest"] for node, peer in state["peers"].items()})
            for node, messages in groups.items():
                if args.node is not None and int(node) != args.node:
                    continue
                for name, entry in messages.items():
                    if args.message and args.message != name:
                        continue
                    key = (node, name)
                    if last.get(key) != entry["received_monotonic"]:
                        age = time.monotonic()-entry["received_monotonic"]
                        print(f"node={node} sysid={entry['system_id']} {name} receive_age={age:.3f}s {json.dumps(entry['data'], ensure_ascii=False)}", flush=True)
                        last[key] = entry["received_monotonic"]
        time.sleep(0.2)


def main():
    parser = argparse.ArgumentParser(description="Read-only MAVLink telemetry over mesh")
    parser.add_argument("--config", default=CONFIG)
    parser.add_argument("--state", default=STATE)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="长期运行；一般由systemd启动")
    show = sub.add_parser("status", help="查看服务快照，不打开串口")
    show.add_argument("--json", action="store_true")
    follow = sub.add_parser("listen", help="观察收到的遥测；不占用UDP监听端口")
    follow.add_argument("--node", type=int)
    follow.add_argument("--message")
    follow.add_argument("--seconds", type=float, default=0)
    sync = sub.add_parser("configure", help="从现有mesh／UART配置生成独立运行配置")
    sync.add_argument("--peers", default="1,2", help="节点列表，如1,2,3,4")
    sync.add_argument("--apply", action="store_true", help="确认写入配置；还需重启服务生效")
    sub.add_parser("validate", help="只检查配置")
    args = parser.parse_args()
    if args.command == "configure":
        config = build(peer_nodes=tuple(int(x.strip()) for x in args.peers.split(",")))
        print(json.dumps(config, ensure_ascii=False, indent=2))
        if args.apply:
            if os.geteuid() != 0:
                raise ValueError("写入运行配置请使用sudo")
            from tools.uav_config.system import System
            System().write(args.config, json.dumps(config, ensure_ascii=False, indent=2)+"\n", 0o644)
            print("已保存；运行 sudo systemctl restart uav-telemetry 后生效。")
        return
    if args.command == "validate":
        load(args.config)
        print("Configuration OK")
        return
    if args.command == "status":
        status(json.loads(Path(args.state).read_text(encoding="utf-8")), args.json)
        return
    if args.command == "listen":
        if args.seconds < 0:
            raise ValueError("seconds不能为负数")
        listen(args)
        return
    if sys.platform != "linux":
        raise ValueError("常驻服务需要Linux的接口绑定功能")
    from .relay import Relay
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    Relay(load(args.config), args.state).run(stop)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except (ValueError, OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
