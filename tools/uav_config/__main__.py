import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from .config import defaults, load


def main():
    parser = argparse.ArgumentParser(description="UAV Raspberry Pi configuration")
    parser.add_argument("--validate", metavar="JSON", help="只检查配置JSON，不执行系统修改")
    parser.add_argument("--example", action="store_true", help="输出示例配置，不执行系统修改")
    args = parser.parse_args()
    if args.validate:
        if not Path(args.validate).is_file():
            raise ValueError("待校验配置文件不存在")
        load(args.validate)
        print("配置校验通过")
        return
    if args.example:
        print(json.dumps(defaults("uav1"), ensure_ascii=False, indent=2))
        return
    if sys.platform != "linux" or os.geteuid() != 0:
        parser.error("请在树莓派Linux上使用 sudo uav-config")
    if not sys.stdin.isatty():
        parser.error("请在交互终端运行；SSH请分配终端")
    if not shutil.which("whiptail"):
        parser.error("缺少whiptail，请先运行安装脚本")
    from .ui import App
    App().main()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已退出；如正在应用，请进入状态页检查实际结果。")
    except (ValueError, OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
