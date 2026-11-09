#!/bin/bash
set -eu
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
if [ "$(id -u)" -ne 0 ]; then
    echo "Run with sudo bash scripts/install_uav_telemetry.sh [--enable]" >&2
    exit 1
fi
case "${1:-}" in
    ""|--enable) ;;
    *) echo "Unknown option" >&2; exit 1 ;;
esac
id uav >/dev/null
source_dir=$(cd -- "$(dirname -- "$0")/.." && pwd)
test -f "$source_dir/tools/uav_telemetry/__main__.py"
apt-get update
apt-get install -y python3-venv
install -d /opt/uav-telemetry/tools /etc/uav
cp -a "$source_dir/tools/uav_telemetry" /opt/uav-telemetry/tools/
cp -a "$source_dir/tools/uav_config" /opt/uav-telemetry/tools/
touch /opt/uav-telemetry/tools/__init__.py
chown -R root:root /opt/uav-telemetry/tools
if [ ! -x /opt/uav-telemetry/venv/bin/python ]; then
    python3 -m venv /opt/uav-telemetry/venv
fi
/opt/uav-telemetry/venv/bin/python -m pip install -r "$source_dir/requirements-uav-config.txt"
cat > /usr/local/sbin/uav-telemetry <<'COMMAND'
#!/bin/sh
cd /opt/uav-telemetry
exec /opt/uav-telemetry/venv/bin/python -m tools.uav_telemetry "$@"
COMMAND
chmod 755 /usr/local/sbin/uav-telemetry
usermod -aG dialout uav
if [ ! -f /etc/uav/telemetry.json ]; then
    uav-telemetry configure --apply
fi
uav-telemetry validate
install -m 644 "$source_dir/scripts/uav-telemetry.service" /etc/systemd/system/uav-telemetry.service
systemctl daemon-reload
if [ "${1:-}" = --enable ]; then
    systemctl enable uav-telemetry
    systemctl restart uav-telemetry
    systemctl --no-pager --full status uav-telemetry
else
    echo "Installed; not started. Inspect /etc/uav/telemetry.json, then:"
    echo "sudo systemctl enable --now uav-telemetry"
fi
