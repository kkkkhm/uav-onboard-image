#!/bin/bash
set -eu
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

if [ "$(id -u)" -ne 0 ]; then
    echo "Run with: sudo bash install_uav_mesh.sh" >&2
    exit 1
fi

case "$(hostname)" in
    uav1) mesh_address=10.77.0.1/24 ;;
    uav2) mesh_address=10.77.0.2/24 ;;
    *) echo "This installer supports uav1 and uav2 only." >&2; exit 1 ;;
esac

for required_command in iw ip nmcli systemctl udevadm; do
    command -v "$required_command" >/dev/null
done
iw dev wlan1 info >/dev/null

# Preserve prior files when re-running the installer.
backup_stamp=$(date +%Y%m%d_%H%M%S)
for target in /etc/default/uav-mesh /etc/NetworkManager/conf.d/90-uav-mesh.conf /usr/local/sbin/uav-mesh-up /etc/systemd/system/uav-mesh.service /etc/udev/rules.d/90-uav-mesh.rules; do
    if [ -e "$target" ]; then
        cp -p "$target" "${target}.bak.${backup_stamp}"
    fi
done

install -d /etc/default /etc/NetworkManager/conf.d /usr/local/sbin /etc/udev/rules.d
printf 'MESH_ADDRESS=%s\n' "$mesh_address" > /etc/default/uav-mesh

cat > /etc/NetworkManager/conf.d/90-uav-mesh.conf <<'CONF'
[device-uav-mesh]
match-device=interface-name:wlan1
managed=0
CONF

cat > /usr/local/sbin/uav-mesh-up <<'SCRIPT'
#!/bin/bash
set -eu
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
nmcli device set wlan1 managed no
# A manually joined mesh may still be active on the first service start.
iw dev wlan1 mesh leave 2>/dev/null || :
ip link set wlan1 down
iw dev wlan1 set type mp
ip link set wlan1 up
# Generic station power_save is unsupported on this adapter in mesh mode.
iw dev wlan1 mesh join uav-mesh freq 5745 HT20
iw dev wlan1 set mesh_param mesh_fwding=0
ip address replace "$MESH_ADDRESS" dev wlan1
SCRIPT
chmod 755 /usr/local/sbin/uav-mesh-up

cat > /etc/systemd/system/uav-mesh.service <<'UNIT'
[Unit]
Description=UAV single-hop mesh on wlan1
Wants=NetworkManager.service
BindsTo=sys-subsystem-net-devices-wlan1.device
After=NetworkManager.service sys-subsystem-net-devices-wlan1.device

[Service]
Type=oneshot
RemainAfterExit=yes
EnvironmentFile=/etc/default/uav-mesh
ExecStart=/usr/local/sbin/uav-mesh-up
ExecStop=-/usr/bin/env iw dev wlan1 mesh leave
ExecStop=-/usr/bin/env ip address del ${MESH_ADDRESS} dev wlan1
TimeoutStartSec=30

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/udev/rules.d/90-uav-mesh.rules <<'RULE'
ACTION=="add", SUBSYSTEM=="net", KERNEL=="wlan1", ATTRS{idVendor}=="0e8d", ATTRS{idProduct}=="7612", TAG+="systemd", ENV{SYSTEMD_WANTS}+="uav-mesh.service"
RULE

systemctl daemon-reload
udevadm control --reload-rules
nmcli general reload
systemctl enable uav-mesh.service
systemctl restart uav-mesh.service

echo "Mesh configured: $(hostname) ${mesh_address}; channel 149, HT20, forwarding disabled."
echo "Open mesh for bench testing; MT7612U hotplug startup enabled when named wlan1."
systemctl --no-pager --full status uav-mesh.service
ip -br address show wlan1
iw dev wlan1 get mesh_param mesh_fwding
