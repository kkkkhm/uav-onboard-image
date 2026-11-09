# 树莓派单跳 Mesh 组网说明

适用：Raspberry Pi 4B、Raspberry Pi OS Lite 64-bit、支持 802.11s 的 MT7612U USB 无线网卡。本文按机载母版的默认配置说明。

## 1. 网络结构

```text
飞控A ── UART ── 树莓派A ── USB无线网卡 ))) 单跳Mesh ((( USB无线网卡 ── 树莓派B ── UART ── 飞控B
                    │                                                        │
                  wlan0                                                    wlan0
                    └──────────────── 调试路由器/电脑 ─────────────────────────┘
```

- `wlan0`：树莓派内置 Wi-Fi，用于 SSH、安装软件和调试。
- `wlan1`：外置 USB 网卡，用于节点间直接通信。
- Mesh 不依赖调试路由器。只要节点上电、配置一致并在无线覆盖范围内，就可以直接通信。
- 网卡需安装配套天线，并获得稳定供电。更换不同芯片的网卡时，需要重新核查 mesh 支持及热插拔规则。

## 2. 默认参数

| 参数 | 默认值 |
|---|---|
| 协议 | 802.11s |
| Mesh ID | `uav-mesh` |
| 频率 | 5745 MHz，信道149 |
| 带宽 | HT20，20 MHz |
| Mesh 转发 | `mesh_fwding=0` |
| 子网 | `10.77.0.0/24` |
| 节点 N 的地址 | `10.77.0.N/24`，N为1～254 |
| 遥测常驻端口 | UDP 14570 |

各台的 Mesh ID、频率和带宽必须一致；节点编号、主机名和 IP 必须各自唯一。频率应符合使用地点的监管域和设备支持情况。

本配置关闭 mesh 转发，节点之间直接收发，不经过其他节点中继。四台组网时，每对需要互通的节点均需能直接建立无线链路。本默认 mesh 为开放的台架测试网络，没有启用链路加密。

## 3. 使用母版配置节点

1. 按对应版本的刷写说明将母版写入新卡。
2. 首次启动前，在 bootfs 根目录放入该节点的 `uav-node.json`。
3. 每台选择不同的 `node_id`。例如节点3使用 `uav3`、`10.77.0.3/24`。
4. 接上 USB 无线网卡后上电，等待系统完成首次初始化。

如果尚未选择节点，可以登录后执行：

```bash
sudo uav-setup 3
```

后续查看或修改树莓派配置：

```bash
sudo uav-config
```

修改配置时需通过界面“保存与应用”使设置生效。节点已初始化后不重复运行首次初始化命令。

## 4. 两台互通检查

在两台树莓派分别执行：

```bash
ip -br addr show wlan1
iw dev wlan1 info
systemctl is-active uav-mesh
sudo iw dev wlan1 get mesh_param mesh_fwding
```

预期：接口 UP，地址为本机 `10.77.0.N/24`，类型为 `mesh point`，频率5745 MHz、宽度20 MHz，服务 active，转发值为0。

在节点1测试节点2：

```bash
ping -I wlan1 -c 20 10.77.0.2
```

在节点2测试节点1：

```bash
ping -I wlan1 -c 20 10.77.0.1
```

`-I wlan1` 指定走外置 mesh 接口。Ping显示的是往返时间 RTT，不是单程延时。

## 5. 确认单跳邻居

```bash
sudo iw dev wlan1 station dump
sudo iw dev wlan1 mpath dump
```

检查：

- 邻居状态 `mesh plink: ESTAB`，表示已建立 mesh 对等链路。
- 路径的 `DEST ADDR` 与 `NEXT HOP` 相同，`HOP_COUNT=1`，表示直接到达。
- `signal` 为接收信号强度；`tx retries`、`tx failed` 为累计重试和失败计数，应结合测试前后增量判断。

## 6. 开机、热插拔与换口

母版通过 `uav-mesh.service` 配置接口，通过 udev 规则响应 MT7612U 网卡加入事件。USB 网卡拔出时链路会中断，插回后需要等待设备枚举、接口配置和邻居链路建立。

节点编号和 IP 跟随树莓派配置，交换同型号网卡不会交换节点身份。换 USB 口或互换同型号网卡后，用上述接口、邻居和 ping 命令核实恢复结果。

手动重新配置 mesh：

```bash
sudo systemctl restart uav-mesh
```

本配置没有使用 `iw dev wlan1 set power_save off`。该 managed Wi-Fi 省电命令在当前 mesh 模式下会返回不支持；`station dump` 中 mesh PS 的 `ACTIVE` 表示活跃状态。

## 7. 读取飞控遥测

检查后台服务：

```bash
systemctl is-active uav-telemetry
uav-telemetry status
```

在节点1观察节点2的姿态：

```bash
uav-telemetry listen --node 2 --message ATTITUDE
```

节点自身未接飞控时，`flight_online=False` 正常；仍可接收对端状态。对端未接飞控时，peer没有飞控心跳，也可能显示离线，应另用ping和邻居表检查网络。

观察命令读取1Hz本机诊断快照，按Ctrl+C结束观察不停止后台服务。消息的接收年龄不是网络单程延时。当前服务只交换选定的状态遥测，不把远端数据写回飞控。

## 8. 新增 node5 及后续节点

节点5的首次配置文件示例：

```json
{
  "version": 1,
  "node_id": 5,
  "serial_device": "/dev/serial0",
  "baud": 115200,
  "peers": [1, 2, 3, 4, 5]
}
```

节点5使用 `uav5` 和 `10.77.0.5/24`。还需在每个已有参与通信的节点上同步遥测对端列表：

```bash
sudo uav-telemetry configure --apply --peers 1,2,3,4,5
sudo systemctl restart uav-telemetry
```

节点6往后同理，编号范围1～254。遥测对端列表当前为显式配置，不会自动发现新增节点。

## 9. 常用排查

| 现象 | 检查内容 |
|---|---|
| `No such device` | `lsusb`、`iw dev`，检查网卡是否枚举、接口是否仍叫wlan1 |
| `Network is unreachable` | 本机wlan1是否UP、是否有正确的10.77.0.N/24地址 |
| `Destination Host Unreachable` | 对端是否上电、节点地址是否正确、邻居是否ESTAB |
| ping通但收不到遥测 | 对端飞控连接、uav-telemetry服务、peers列表、UDP端口 |
| mesh正常但无法SSH到调试IP | 单独检查wlan0、调试Wi-Fi连接和当前DHCP地址 |

查看服务日志：

```bash
sudo journalctl -u uav-mesh -b --no-pager -n 60
sudo journalctl -u uav-telemetry -b --no-pager -n 60
```

修改频率、带宽、接口或USB芯片型号后，需要重新做双向通信、重启和热插拔测试。近距离台架测试不能代替四机、外场及飞行状态下的验收。
