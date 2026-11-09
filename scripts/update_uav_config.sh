#!/bin/bash
set -eu
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
if [ "$(id -u)" -ne 0 ]; then
    echo "Run with sudo bash scripts/update_uav_config.sh" >&2
    exit 1
fi
source_dir=$(cd -- "$(dirname -- "$0")/.." && pwd)
test -f "$source_dir/tools/uav_config/__main__.py"
test -x /opt/uav-config/venv/bin/python
target=/opt/uav-config/tools/uav_config
backup="${target}.backup.$(date +%Y%m%d_%H%M%S_%N)"
if [ -d "$target" ]; then
    mv "$target" "$backup"
fi
if ! cp -a "$source_dir/tools/uav_config" "$target"; then
    if [ -d "$backup" ] && [ ! -e "$target" ]; then
        mv "$backup" "$target"
    fi
    exit 1
fi
chown -R root:root "$target"
echo "Updated application files only. Network, UART and flight settings were not applied."
echo "Run: sudo uav-config"
