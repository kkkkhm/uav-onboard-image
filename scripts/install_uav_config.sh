#!/bin/bash
set -eu
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

if [ "$(id -u)" -ne 0 ]; then
    echo "Run with sudo bash install_uav_config.sh" >&2
    exit 1
fi
source_dir=$(cd -- "$(dirname -- "$0")/.." && pwd)
test -f "$source_dir/tools/uav_config/__main__.py"
test -f /boot/firmware/config.txt
apt-get update
apt-get install -y whiptail python3-venv iw network-manager iputils-ping
install -d /opt/uav-config/tools /etc/uav
cp -a "$source_dir/tools/uav_config" /opt/uav-config/tools/
touch /opt/uav-config/tools/__init__.py
chown -R root:root /opt/uav-config/tools
if [ ! -x /opt/uav-config/venv/bin/python ]; then
    python3 -m venv /opt/uav-config/venv
fi
/opt/uav-config/venv/bin/python -m pip install -r "$source_dir/requirements-uav-config.txt"
cat > /usr/local/sbin/uav-config <<'COMMAND'
#!/bin/sh
cd /opt/uav-config
exec /opt/uav-config/venv/bin/python -m tools.uav_config "$@"
COMMAND
chmod 755 /usr/local/sbin/uav-config
echo "Installed. Run: sudo uav-config"
echo "Installation has not applied node, UART, mesh, Wi-Fi or flight parameter changes."
