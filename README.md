# Raspberry Pi UAV Onboard Image

面向 Raspberry Pi 4B 的机载通信系统：MT7612U USB网卡、802.11s单跳mesh、UART MAVLink状态遥测、终端配置工具。

## 下载与快速开始

从 [Releases](https://github.com/kkkkhm/uav-onboard-image/releases) 下载公共版镜像、配套包和SHA-256清单。

1. 使用 Raspberry Pi Imager 的自定义镜像入口写入 `.img.gz`，跳过Imager自定义设置。
2. 重新插卡，编辑bootfs中的 `uav-access.json`，填写自己的uav账户密码和调试Wi-Fi。
3. 编辑 `uav-node.json`，选择唯一节点编号，接USB无线网卡后上电。

公共版从官方干净系统构建，没有预置可用账户密码、个人Wi-Fi配置、用户文档或SSH主机密钥。账户名固定为uav，首次密码至少8字符；账户凭据设置成功后删除bootfs中的访问配置文件。

- [完整刷写与自定义Wi-Fi说明](docs/uav_public_flash_guide.md)
- [Mesh组网说明：开机、热插拔、单跳检查、新增节点、排查](docs/uav_mesh_network_guide.md)
- [节点1～4配置](examples/nodes)
- [访问配置模板，复制到bootfs时命名为uav-access.json](examples/uav-access.example.json)

## 默认参数

| 项目 | 默认值 |
|---|---|
| 调试网络 | 内置wlan0，用户自定义Wi-Fi或有线网络 |
| Mesh网络 | 外置wlan1，uav-mesh，5745MHz/HT20 |
| 单跳 | mesh_fwding=0；需要互通的节点彼此直接可达 |
| 节点N | uavN、10.77.0.N/24，编号1～254 |
| 串口 | /dev/serial0，115200，GPIO UART启用、串口登录关闭 |
| 遥测 | UDP14570，显式peers列表，只交换选定状态消息 |

默认mesh为开放台架网络；使用地点或设备变化时需检查监管域、合法频率和实际链路。节点编号与飞控MAV_SYS_ID独立；飞控固件、标定和TELEM参数需另行设置。

## 常用命令

```bash
sudo uav-config
uav-telemetry status
uav-telemetry listen --node 2 --message ATTITUDE
ping -I wlan1 -c 20 10.77.0.2
```

新增node5时，设置唯一节点编号5，并在已有参与节点更新：

```bash
sudo uav-telemetry configure --apply --peers 1,2,3,4,5
sudo systemctl restart uav-telemetry
```

## 验证与范围

源码27项配置、遥测和初始化测试通过；公共镜像另检查账户/网络/SSH身份、文件系统、完整启动依赖、ARM64原生依赖及压缩完整性。Release附来源、构建和检查报告。

尚未用备用卡完成公共镜像首次真实开机、重启和热插拔验收。此前两台原系统的近距离组网和遥测测试不能代替新镜像及外场验收。当前版本不实现队形控制或模块注册框架；遥测常驻程序不向飞控写入远端数据或下发控制。

## 构建与软件许可

系统底座为官方 Raspberry Pi OS Lite 64-bit；Release的构建报告记录精确URL和SHA-256。构建工具位于tools/images，需Linux/WSL、e2fsprogs、mtools、dosfstools及qemu-user-static；软件环境由一份本地已安装的ARM64镜像按白名单提取，仅提取Python虚拟环境，不复制其账户、网络、日志或用户文档。运行构建脚本前应核对输入文件及路径。

系统软件及Python依赖保留各自许可证。系统内可查看 `/usr/share/doc/*/copyright`，Python依赖固定版本见requirements-uav-config.txt。硬件暂限定Raspberry Pi 4B和MT7612U。
