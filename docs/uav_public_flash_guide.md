# 公共版树莓派机载镜像：刷写与首次配置

适用 Raspberry Pi 4B、64位系统与 MT7612U USB 无线网卡。公共版基于官方干净系统构建，首次启动由使用者设置自己的登录密码和调试 Wi-Fi。

## 1. 下载与刷写

从本仓库 Releases 下载：

- `uav-rpi4-public-20261109.img.gz`
- `SHA256SUMS.txt`
- 配套文件包，包含节点示例、说明和程序源码。

在 Raspberry Pi Imager 选择 Raspberry Pi 4 → 使用自定义镜像 → 上述公共版 `.img.gz`。目标卡至少16GB，推荐64GB。写入并校验后，重新插入读卡器，打开 Windows 可读的 bootfs 分区。

跳过 Imager 的账户、Wi-Fi 等自定义设置。本公共版使用下面两个文件进行初始化。

## 2. 设置自己的账户密码与 Wi-Fi

编辑 bootfs 根目录的 `uav-access.json`：

```json
{
  "version": 1,
  "password": "",
  "wifi": {
    "ssid": "",
    "password": ""
  }
}
```

- 外层 `password`：填写你为 Linux 用户 **uav** 选择的登录密码，至少8个字符。
- `wifi.ssid`：填写调试路由器的 Wi-Fi 名称。
- `wifi.password`：填写该 Wi-Fi 的密码；本模板支持 WPA-Personal，密码8～63个字符或64位十六进制PSK。
- 使用有线网络时，将两个 Wi-Fi 字段都留空，但仍需填写外层账户密码。
- 保持有效JSON格式，不要加入注释；密码中的双引号、反斜杠按JSON规则转义。不支持企业EAP Wi-Fi的首次配置。

公共版没有预置可用登录密码。必须先填好账户密码再首次上电；为空时初始化不会启用账户。文件包含你自己的明文凭据，应保存在自己的卡上，不要提交到GitHub。成功设置账户与网络后，系统会删除该文件；后续改密码使用 `passwd`，改Wi-Fi按第6节操作。

## 3. 选择唯一节点编号

编辑同一分区的 `uav-node.json`，例如节点3：

```json
{
  "version": 1,
  "node_id": 3,
  "serial_device": "/dev/serial0",
  "baud": 115200,
  "peers": [1, 2, 3, 4]
}
```

节点3将使用 `uav3`、`10.77.0.3/24`。每台编号必须不同，支持1～254。节点1～4示例在 `examples/nodes` 中。

未选择节点编号时，可以先通过调试网络登录，执行 `sudo uav-setup 3`。此时临时主机名为 `uav-setup-主板序列号末8位`，mesh和遥测保持暂停。

初始化后，节点文件改名为 `uav-node.applied.json`。后续节点、串口和mesh配置使用 `sudo uav-config`。

## 4. 上电与登录

安全弹出卡，插入树莓派，接上配套天线和 USB 网卡后上电。等待系统完成首次启动。电脑连接可访问树莓派的调试网络后，在 Windows PowerShell 执行：

```powershell
ssh uav@uav3.local
```

输入第2节设置的账户密码。如果 `.local` 无法解析，改用路由器实际分配的IP；也可接显示器、键盘登录查看：

```bash
ip -br addr show wlan0
ip -br addr show eth0
```

SSH主机密钥和machine-id由各板独立生成。调试网络的DHCP地址可能变化，mesh地址跟随节点编号固定。

## 5. 检查网络与遥测

```bash
hostname
ip -br addr show wlan1
systemctl is-active uav-mesh uav-telemetry
uav-telemetry status
```

确认另一节点已上电后测试，例如访问节点1：

```bash
ping -I wlan1 -c 20 10.77.0.1
sudo iw dev wlan1 station dump
sudo iw dev wlan1 mpath dump
sudo iw dev wlan1 get mesh_param mesh_fwding
```

预期邻居 `mesh plink: ESTAB`，路径 `HOP_COUNT=1`，转发参数为0。详细步骤见 [Mesh组网说明](uav_mesh_network_guide.md)。

观察节点2飞控姿态：

```bash
uav-telemetry listen --node 2 --message ATTITUDE
```

裸板没有接飞控时，本机 `flight_online=False` 正常；仍可接收对端遥测。状态显示为1Hz诊断快照，不是控制程序的数据接口，也不是网络单程延时测量。

## 6. 后续更换调试 Wi-Fi

内置 `wlan0` 用于调试，外置 `wlan1` 用于mesh。可通过网线或显示器、键盘登录后执行：

```bash
sudo nmcli --ask device wifi connect "新的WiFi名称" ifname wlan0
WIFI_UUID=$(nmcli -g GENERAL.CON-UUID device show wlan0)
sudo nmcli connection modify uuid "$WIFI_UUID" connection.autoconnect yes connection.permissions ""
ip -br addr show wlan0
```

按提示输入Wi-Fi密码。如果通过原Wi-Fi的SSH会话切换网络，连接可能中断；电脑需要能访问新网络，再用新IP登录。也可在节点初始化完成后运行 `sudo uav-config`，配置调试Wi-Fi并保存应用。

## 7. 串口、飞控与新增节点

默认 UART `/dev/serial0`、115200，串口登录关闭，蓝牙禁用以使用PL011。树莓派物理8=TX、10=RX；与飞控TX/RX交叉连接并共地，信号为3.3V TTL。

每块飞控需另外配置PX4固件、TELEM接口、波特率、流控及MAV_SYS_ID。树莓派节点编号与飞控系统ID独立。该镜像不会修改飞控参数或发送控制指令。

更改节点、mesh或串口并应用后，同步遥测运行配置：

```bash
sudo uav-telemetry configure --apply --peers 1,2,3,4
sudo systemctl restart uav-telemetry
```

增加node5时，使用唯一编号5和IP `10.77.0.5/24`，并在参与通信的已有节点上将peers更新为 `1,2,3,4,5`。详见Mesh说明的新增节点章节。

## 8. 空间与验收

根分区预设8GB，保留官方初始化扩容参数。启动后用 `df -h /` 检查；如尚未使用整卡容量，运行 `sudo raspi-config`，选择 Advanced Options → Expand Filesystem，然后重启。

默认mesh使用5745MHz/HT20，监管域CN，转发关闭；使用地点或网卡变化后应重新检查合法频率及驱动能力。默认mesh未加密，适用于当前台架配置。

公共版完成离线文件系统检查、账户/网络配置检查、软件测试、ARM64原生依赖导入及压缩完整性验证。备用卡上的真实首次启动、重启和网卡热插拔仍待验收。未实现队形控制或模块注册框架。

## 9. 系统来源与软件许可

底座为 Raspberry Pi 官方 Raspberry Pi OS Lite 64-bit，具体下载URL、SHA-256和验证记录见Release的构建报告。系统及其软件包保留各自许可和版权文件；镜像内可查 `/usr/share/doc/*/copyright`。Python依赖版本见 `requirements-uav-config.txt`。
