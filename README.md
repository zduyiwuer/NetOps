# 网络设备运维平台

面向企业网络的多厂商设备监控平台。内置 **8 个厂商画像**，覆盖交换机、无线控制器与防火墙：

| 类别 | 支持设备 |
| --- | --- |
| 交换机 | MikroTik RouterOS、Cisco IOS/NXOS、华为 VRP、新华三 H3C Comware |
| 防火墙 | **Cisco ASA**、**飞塔 Fortinet FortiGate** |
| 无线控制器 | **Cisco WLC** |

提供实时接口速率、CPU/内存资源监控、分级告警、日志集中采集、资产台账、门店化管理、流量 TopN、
NetFlow 会话分析、gNMI 遥测、配置备份、基线巡检与自动报告生成。

> 每类设备都有**专属基线巡检模板**与**领域特有指标**
> （ASA 连接数与 CSC 表容量、WLC 的 AP 数与客户端数、飞塔的会话数与 VPN 隧道数），
> 巡检时按厂商自动匹配模板。详见「支持的设备类型明细」。

> **登录页的「支持厂商」标签由后端动态下发**（`GET /api/auth/config` 返回 `vendors`），
> 与设备管理页的厂商列表同源。以后新增厂商只需在后端 `app/collectors/vendors.py`
> 注册画像，登录页与设备页会自动同步，无需再改前端。

---

## 一、功能总览

| 模块 | 能力说明 |
| --- | --- |
| 登录与权限 | JWT 登录页、**四种角色（管理员 / 操作员 / 审批员 / 只读）**、**一个用户可分配多个角色**、登录审计、操作审计 |
| 域控对接 | LDAP / Active Directory 认证、域用户同步、AD 组 → 平台角色映射（**系统管理页在线配置**） |
| 设备管理 | 增删改查、Excel/CSV 批量导入导出、SNMP v1/v2c/v3 与 SSH 连通性测试、**单台/全量在线探测**；支持 8 类厂商 |
| **防火墙会话** | **各防火墙实时会话数一览与会话趋势**（ASA 连接数 / 飞塔会话数 / VPN 隧道数 / 在线用户数） |
| 实时接口速率 | IF-MIB 64 位计数器差分、协商速率自适应、利用率计算、Down/错包标识、**按门店筛选** |
| 资源监控 | CPU 使用率、内存使用率、设备温度、SNMP 响应时延、运行时长、历史曲线 |
| 告警中心 | 阈值规则 + 持续时间 + 静默期 + 自动恢复；**详细告警内容（定位到具体接口）+ 门店筛选**；Webhook / 企业微信 / 钉钉 / 邮件通知 |
| 日志中心 | 内置 Syslog UDP+TCP 接收服务（RFC3164/RFC5424）、SSH 主动拉取、级别与关键字检索 |
| 资产管理 | 资产编号、序列号、固件版本、采购/保修/合同信息、保修临期提醒 |
| 门店管理 | 按门店/网点组织设备，统计各门店在线率、资产分布 |
| 流量分析 | 接口 / 设备 / 门店三维度 TopN 排行、流量趋势、错包 TopN |
| **NetFlow 分析** | NetFlow v5/v9 与 IPFIX 报文接收、五元组会话明细、Top 通信源/会话对、应用分布 |
| **Telemetry 遥测** | gNMI（collector + client 双模式）、OpenConfig/厂商私有模型路径映射、遥测样本归一化入库 |
| 配置备份 | SSH 抓取 running-config、**按门店打包下载**、**在线查看配置内容**、版本对比、定时备份 |
| **设备接入** | 设备本地账号管理、**TACACS+/RADIUS 统一认证服务**、认证会话日志 |
| **配置审批** | 配置变更申请 → 管理员审批 → 下发设备（下发前自动备份、可回滚） |
| 基线巡检 | 可定制检查项模板、自动判定、HTML + Excel 双格式报告自动生成 |
| 网络拓扑 | LLDP / CDP 邻居采集、力导向拓扑图、疑似环路识别 |
| 审计日志 | 写操作自动记录 + 关键操作手动记录，按操作类型/用户聚合统计 |
| **AI 助手** | 对接国内外主流大模型 API，注入平台实时数据自动分析网络问题（流式对话） |
| **外观主题** | 深色 / 浅色 / 跟随系统三种模式，图表与组件全量适配 |
| **大屏自定义** | 自定义总览大屏显示的模块、顺序与宽度，配置本地持久化 |
| **采集服务开关** | SNMP / Telemetry / NetFlow / Syslog 四通道运行时启停，无需重启 |
| **定时任务配置** | 系统管理页直接编辑调度策略（cron / 间隔）、启停、立即执行、恢复默认 |
| 总览大屏 | 设备健康度、流量趋势、CPU/内存 Top10、告警与日志摘要 |

### 左侧导航栏

16 个功能入口按 6 个功能域分组收纳：

| 分组 | 入口 |
| --- | --- |
| 概览 | 总览大屏 |
| 设备管理 | 设备管理、实时接口速率、网络拓扑 |
| 流量分析 | 接口流量 TopN、NetFlow 会话分析、Telemetry 遥测 |
| 运维监控 | 告警中心、配置备份、基线巡检、日志中心 |
| 资产组织 | 门店管理、资产管理、审计日志 |
| 系统与智能 | AI 网络助手、系统管理 |

侧边栏顶部提供**功能搜索**（输入即过滤，无结果时显示空状态）；
每个分组可**独立折叠/展开**，顶部还有「全部收起 / 全部展开」一键切换，
展开状态持久化到浏览器；
侧边栏整体收起时自动隐藏分组标题、仅保留图标，菜单项不丢失。

### 三种采集通道

平台同时支持三条采集通道，可在设备编辑页「采集通道」标签中按设备开关：

| 通道 | 方向 | 默认端口 | 适用场景 |
| --- | --- | --- | --- |
| **SNMP 轮询** | 平台 → 设备 | 161/162 | 绝大多数设备的常规监控：接口计数器 + CPU/内存 |
| **Telemetry（gNMI）** | 双向 | 57400 | 需要秒级实时性、批量流式指标的现代设备 |
| **NetFlow / IPFIX** | 设备 → 平台 | 2055 / 4739 / 9995 | 需要会话级流量分析、Top 通信源、应用识别 |

三条通道的数据最终**归一化到同一套指标表**（`device_metrics` / `interface_metrics`），
因此总览、接口速率、TopN 等看板无需区分数据来源。

---

## 二、技术架构

```
netops/
├── backend/                    FastAPI 后端
│   ├── app/
│   │   ├── api/                REST 接口层（auth / devices / monitor / alerts / logs / ops）
│   │   ├── collectors/         采集层
│   │   │   ├── snmp.py         pysnmp 7 异步客户端（GET / WALK / BULK，v1/v2c/v3）
│   │   │   ├── ssh.py          Netmiko SSH 封装（配置备份 / 日志 / CPU 内存兜底）
│   │   │   ├── netflow.py      NetFlow v5/v9 + IPFIX 报文解析与 UDP/TCP 接收
│   │   │   ├── telemetry.py    gNMI Collector / Client 与路径映射
│   │   │   ├── vendors.py      8 类厂商画像：OID 候补表 + SSH 命令模板 + 分类说明
│   │   │   └── engine.py       采集引擎（速率差分、指标归一化、mock 模式）
│   │   ├── proto/              gNMI protobuf 定义（gnmi.proto，与官方规范一致）
│   │   ├── services/           业务服务层
│   │   │   ├── scheduler.py    APScheduler 采集调度
│   │   │   ├── alerting.py     告警规则评估 / 触发 / 自动恢复
│   │   │   ├── notifier.py     通知分发（Webhook / 企微 / 钉钉 / SMTP）
│   │   │   ├── syslog_server.py Syslog 接收服务
│   │   │   ├── netflow_service.py 流记录入库与 TopN 聚合
│   │   │   ├── telemetry_service.py 遥测样本归一化与会话管理
│   │   │   ├── backup.py       配置备份与版本对比
│   │   │   ├── inspection.py   基线巡检引擎
│   │   │   ├── report.py        HTML / Excel 报告生成
│   │   │   ├── importer.py     Excel / CSV 导入导出
│   │   │   └── ldap_service.py 域控对接
│   │   ├── core/               配置、安全、凭据加密、角色定义
│   │   └── models/             SQLAlchemy 数据模型
│   └── scripts/                验证脚本（SNMP / NetFlow / gNMI / 端到端 / UI / 角色）
└── frontend/                   Vue 3 + Element Plus + ECharts
    └── src/views/              14 个功能页面
```

**关键技术选型**

- **采集**：SNMP 为主、SSH 为辅。CPU/内存等私有 OID 采用「候补表」策略逐个尝试，
  任一私有 MIB 不可用时自动回退到标准 MIB 或 SSH 命令。
- **接口速率**：基于 `ifHCInOctets/ifHCOutOctets` 差分计算，速率计算使用进程内状态缓存
  （按 `device_id + ifIndex` 维度），计数器回绕（设备重启）时自动归零重算。
- **凭据安全**：SNMP 密钥 / SSH 密码使用 Fernet 对称加密落库（由 `SECRET_KEY` 派生），
  接口层一律脱敏返回 `********`，更新时提交掩码或空串则保留原值。
- **调度**：APScheduler 异步调度，全量采集 30s、离线检测 60s、日志拉取 5min、
  自动备份 24h、计划巡检 1h、数据清理 6h。

---

## 三、快速启动

### 1. 环境要求

- Python 3.11+（已在 3.13 验证）
- Node.js 18+（已在 22 验证）

### 2. 启动后端

```bash
cd netops/backend
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8900
```

首次启动会自动：建表、创建默认管理员、初始化 4 个示例门店、7 条默认告警规则、4 套巡检模板。

### 3. 启动前端（开发模式）

```bash
cd netops/frontend
npm install
npm run dev          # http://localhost:5180，已配置 /api 代理到 8900
```

### 4. 生产部署（前后端一体）

```bash
cd netops/frontend && npm run build     # 产物输出到 frontend/dist
cd ../backend && python -m uvicorn app.main:app --host 0.0.0.0 --port 8900
```

后端会自动托管 `frontend/dist`，单端口访问 <http://localhost:8900> 即可。

### 5. 后台运行与启停脚本

不想一直开着终端？用随项目提供的启停脚本，服务会在后台常驻，
**关闭终端 / 退出 SSH 后依然继续运行**。

> **Windows 上是怎么做到的**：用 WMI（`Win32_Process.Create`）直接创建
> `service_launcher.py`——**不经过 cmd 包装**。
> 进程由系统服务托管，**不属于启动它的终端 / 脚本 / 工具的进程树**，
> 因此父进程一退出也不受影响。
>
> 为什么不走 `cmd /c "python ..."`：那样会在桌面留下一个**可见的 cmd 窗口**
> （输出虽重定向到日志，窗口本身仍在），而且真正的服务进程是 cmd 的直接子进程——
> 用户关掉那个窗口，服务就跟着停了。
> 现在由启动器以 `DETACHED_PROCESS | CREATE_NO_WINDOW` 拉起服务进程后立即退出，
> 于是**既没有多余窗口，服务也彻底脱离进程树**。
>
> Linux / macOS 上则用 `start_new_session` 创建独立会话。
> 两种方式都会把 PID 写进 `backend/data/run/netops.pid`。

| 操作 | Linux / macOS | Windows |
| --- | --- | --- |
| 后台启动 | `./start.sh` | `start.bat` |
| 前台启动（调试用） | `./start.sh --foreground` | `start.bat --foreground` |
| 停止 | `./stop.sh` | `stop.bat` |
| 重启 | `./restart.sh` | `restart.bat` |
| 查看状态 | `./status.sh` | `status.bat` |

```bash
cd netops
./start.sh                 # 后台启动
./status.sh                # 查看状态
./stop.sh                  # 停止服务
```

**脚本行为**

- **自动准备环境**：缺后端依赖自动装、缺前端依赖自动装、`frontend/dist` 不存在时自动构建
- **PID 跟踪**：进程 PID 写入 `backend/data/run/netops.pid`，停止时按 PID 精确终止
- **日志落盘**：运行日志写入 `backend/data/logs/netops.log`；超过 5 MB 自动切分为历史文件
- **健康检查**：启动后轮询「进程存活 **且** 端口可连」，最多等 60 秒；
  两者缺一即判定失败，不会出现「报告启动成功、实际进程已死」的假成功
- **重复启动保护**：已在运行时再次执行会提示「已在运行」，不会起第二个实例
- **端口冲突检测**：端口被其他程序占用时明确报错，而不是静默失败
- **优雅停止**：先发终止信号，最多等 10 秒；未退出则强制结束，并清理端口上的残留进程
- **PID 文件失效**：进程已不存在时会自动清理 PID 文件，不留脏状态
- **双击不闪退**（Windows）：脚本结束前会暂停并提示「按任意键关闭此窗口」，
  避免窗口一闪而过看不到输出；自动化调用设 `NETOPS_NO_PAUSE=1` 可跳过等待

**产物位置**

| 路径 | 说明 |
| --- | --- |
| `backend/data/run/netops.pid` | 当前进程 PID |
| `backend/data/logs/netops.log` | 运行日志 |
| `backend/data/logs/netops_*.log` | 切分出的历史日志 |

**自定义端口**

```bash
PORT=9000 ./start.sh                     # Linux
set NETOPS_PORT=9000 && start.bat        # Windows
```

> **提示**：Windows 批处理为 **GBK 编码 + CRLF 换行**，这是 cmd.exe 正确解析中文的必需组合。
> 若用编辑器修改请保持该编码，否则会出现命令解析异常。

**遇到问题怎么办**

| 现象 | 排查 |
| --- | --- |
| 双击后窗口一闪而过 | 新版脚本已加暂停；若仍旧，用命令行运行 `start.bat` 看输出 |
| 提示端口被占用 | 换个端口 `set NETOPS_PORT=9000 && start.bat`，或先停掉占用进程 |
| 提示已在运行 | 用 `status.bat` 看 PID，再 `stop.bat` 停止 |
| 启动失败 | 看 `backend\data\logs\netops.log` 末尾的错误信息 |
| 服务显示「异常」 | 进程在但端口不通，多为仍在启动或已卡死，看日志定位 |

**自行验证**

```bash
cd netops/backend
python -u -m scripts.verify_service    # 54 项：脚本齐全性、编码、helper 子命令、启停全生命周期
```

### 6. 默认账号

| 用户名 | 密码 | 角色 |
| --- | --- | --- |
| `admin` | `admin123` | 管理员 |

> **生产环境务必修改默认密码，并设置强随机 `SECRET_KEY`。**

---

## 四、接入真实设备

### 方式一：批量导入

1. 设备管理 → **批量导入** → 下载 Excel / CSV 模板（含字段下拉与填写说明）
2. 按模板填写后上传；门店编码不存在时会自动创建门店
3. 同 IP 设备默认跳过，可勾选「覆盖已存在」进行更新

### 方式二：单台添加

设备管理 → 新增设备 → 填写基本信息 / SNMP 配置 / SSH 配置 / 资产信息。

### 关键配置项

| 厂商 | SNMP CPU OID（候补顺序） | SSH 兜底命令 |
| --- | --- | --- |
| RouterOS | `1.3.6.1.4.1.14988.1.1.3.4.4.0` | `/system resource print` |
| Cisco | `1.3.6.1.4.1.9.9.109.1.1.1.1.8` → HOST-RESOURCES-MIB | `show processes cpu` |
| 华为 | `1.3.6.1.4.1.2011.2.239.1.1.1.1.4` → HOST-RESOURCES-MIB | `display cpu-usage` |
| H3C | `1.3.6.1.4.1.25506.2.6.1.1.1.1.1.6` → HOST-RESOURCES-MIB | `display cpu-usage` |
| Cisco ASA | `1.3.6.1.4.1.9.9.109.1.1.1.1.8` → HOST-RESOURCES-MIB | `show processes cpu` |
| Cisco WLC | `1.3.6.1.4.1.14179.1.6.3.6.0` → HOST-RESOURCES-MIB | `show cpu usage` |
| 飞塔 FortiGate | `1.3.6.1.4.1.12356.101.1.1.1.0` → HOST-RESOURCES-MIB | `get system performance status` |

> 厂商别名自动归一化：`asa`/`pix` → Cisco ASA，`wlc`/`aireos` → Cisco WLC，
> `fortigate`/`fortios`/`飞塔` → Fortinet。

> 各厂商私有 OID 在不同型号/固件上可能不同，平台按候补顺序自动回退；
> 若全部失败则改走 SSH 采集。

### 支持的设备类型明细

平台内置 **8 个厂商画像**（4 个交换机 + 3 个安全/无线设备 + 通用回退），
下表列出每类的配置要点与差异。

#### Cisco ASA 防火墙（`cisco_asa`）

| 项目 | 内容 |
| --- | --- |
| netmiko 类型 | `cisco_asa` |
| CPU OID | `1.3.6.1.4.1.9.9.109.1.1.1.1.8`（CISCO-PROCESS-MIB 5min）→ HOST-RESOURCES-MIB |
| 内存 OID | `1.3.6.1.4.1.9.9.48.1.1.1.5/6`（Cisco 内存池 Used/Total） |
| SSH 兜底 | `show processes cpu` / `show memory statistics` |
| 配置备份 | `show running-config`（含完整配置） |
| 日志采集 | `show logging \| tail 200` |
| 特有指标 | **当前连接数**、**连接峰值**、**CSC 连接状态表容量** |
| 基线巡检 | 「Cisco ASA 防火墙基线巡检」8 项：CPU/内存/可达性 + 连接数 + CSC 表容量 + Down 接口 + 利用率 + 运行时长 |

ASA 的连接数与 CSC 表容量是判断**会话表耗尽**（导致新连接被拒）的关键指标，
平台通过 `ciscoASAConnCount` / `ciscoASACscTableSize` 采集，SSH 兜底用 `show connection count`。

#### Cisco WLC 无线控制器（`cisco_wlc`）

| 项目 | 内容 |
| --- | --- |
| netmiko 类型 | `cisco_wlc` |
| CPU OID | `1.3.6.1.4.1.14179.1.6.3.6.0`（CISCO-WLC-MIB ciscoWlcCpuUtilization）→ HOST-RESOURCES-MIB |
| 内存 OID | `1.3.6.1.4.1.14179.1.6.3.4.0` / `.5.0`（ciscoWlcMemUsed / MemTotal） |
| SSH 兜底 | `show cpu usage` / `show memory usage` |
| 配置备份 | `show running-config` |
| 日志采集 | `show messages \| tail 200` |
| 特有指标 | **已关联 AP 数**、**已关联客户端数** |
| 基线巡检 | 「Cisco WLC 无线控制器基线巡检」7 项：CPU/内存/可达性 + AP 数 + 客户端数 + 错包 + 运行时长 |

AP 数用于确认**AP 是否掉线**（关联数骤降但客户端数不变，说明是 AP 侧问题而非终端侧）。

#### 飞塔 FortiGate 防火墙（`fortinet`）

| 项目 | 内容 |
| --- | --- |
| netmiko 类型 | `fortinet` |
| CPU OID | `1.3.6.1.4.1.12356.101.1.1.1.0`（FORTINET-MIB fgSysCpuUtilization）→ HOST-RESOURCES-MIB |
| 内存 OID | `1.3.6.1.4.1.12356.101.1.1.3.0` / `.2.0`（fgSysMemUsage / fgSysMemTotal） |
| SSH 兜底 | `get system performance status` |
| 配置备份 | `diagnose firewall config-file save - flash` + `show full-configuration`（先存诊断快照便于回滚，再导出可读配置） |
| 日志采集 | `execute log filter category event` |
| 特有指标 | **并发会话数**、**登录用户数**、**VPN 隧道数** |
| 基线巡检 | 「飞塔 FortiGate 防火墙基线巡检」8 项：CPU/内存/可达性 + 会话数 + VPN 隧道数 + Down 接口 + 利用率 + 运行时长 |

飞塔的会话数是**并发会话容量**的核心指标（超限会直接丢新连接），通过 `fgSysSesCount` 采集，SSH 兜底解析 `get system performance status` 中的 `Current sessions`。

> **为什么飞塔的内存/CPU 常要走 SSH**：
> 多数 FortiGate 的 SNMP 只暴露接口 MIB，FORTINET-MIB 里的
> `fgSysMemTotal` / `fgSysMemUsage` / `fgSysCpuUtilization` 并不可用
> （返回 `No Such Object`）。此时平台会自动改用 SSH 执行
> `get system performance status`，解析其中的
> `Memory: 8171404k total, 2913948k used (35.7%)` 与
> `CPU states: 5% user 2% system 0% nice 93% idle`。
>
> 触发条件是「SNMP 采到了但关键指标缺失」——SNMP 部分可用时不会整体失败，
> 所以不能只靠异常兜底，必须逐项判断。

> **别名自动归一化**：填 `asa` / `pix` 识别为 Cisco ASA；`wlc` / `aireos` 识别为 Cisco WLC；
> `fortigate` / `fortios` / `飞塔` 识别为 Fortinet。

> **基线模板自动匹配**：巡检时平台按设备厂商自动选用对应模板——
> 飞塔用飞塔基线、ASA 用 ASA 基线、WLC 用 WLC 基线，
> 没有专用模板的厂商回退到通用模板。因此一次「全网巡检」可同时覆盖多种设备。

### 演示模式

设备编辑页开启「模拟模式」后，平台使用内置模拟数据（CPU/内存/接口速率/流量曲线），
可在无真实设备的环境下完整体验全部功能。

---

## 五、域控对接

在 `backend/.env` 中配置：

```ini
NETOPS_LDAP_ENABLED=true
NETOPS_LDAP_SERVER=ldap://dc.corp.local
NETOPS_LDAP_PORT=389
NETOPS_LDAP_USE_SSL=false
NETOPS_LDAP_BASE_DN=DC=corp,DC=local
NETOPS_LDAP_BIND_DN=CN=svc-netops,OU=Service,DC=corp,DC=local
NETOPS_LDAP_BIND_PASSWORD=xxxxxx
NETOPS_LDAP_USER_FILTER=(sAMAccountName={username})
NETOPS_LDAP_USER_DN_TEMPLATE=
NETOPS_LDAP_DOMAIN=CORP
NETOPS_LDAP_DEFAULT_ROLE=viewer
NETOPS_LDAP_GROUP_ROLE_MAP={"CN=NetOps-Admins,OU=Groups,DC=corp,DC=local":"admin","CN=NetOps-Ops,OU=Groups,DC=corp,DC=local":"operator"}
```

**说明**

- `NETOPS_LDAP_USER_DN_TEMPLATE` 可直接指定 DN 模板（如 `CN={username},OU=Users,DC=corp,DC=local`），
  留空则按 `NETOPS_LDAP_USER_FILTER` 搜索。
- `NETOPS_LDAP_GROUP_ROLE_MAP` 为 JSON，键为 AD 组 DN，值为平台角色；
  值可以是字符串（单角色）或数组（多角色），命中多个组时**累加**所有角色。
- 登录页在启用后自动出现「本地账号 / 域账号」切换；`auto` 模式下优先尝试域控。
- 系统管理 → 域控对接：测试连接、同步域用户、查看域用户与映射角色。

---

## 六、告警与通知

**内置指标**：`cpu`、`mem`、`temperature`、`traffic`、`util`、`loss`、`offline`、`interface_down`、`response_time`

**通知渠道**（系统管理 → 告警中心 → 通知渠道）

| 类型 | 填写内容 |
| --- | --- |
| Webhook | 通用 HTTP POST 地址 |
| 企业微信机器人 | `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx` |
| 钉钉机器人 | `https://oapi.dingtalk.com/robot/send?access_token=xxx` |
| 邮件 SMTP | 收件人 + JSON 配置 `{"host":"","port":465,"from":"","to":"","password":""}` |

规则可指定「全部设备 / 指定门店 / 指定设备」，支持静默期（同一规则重复触发抑制）
与条件不再满足时的自动恢复。

---

## 七、Syslog 日志接入

平台内置 Syslog 接收服务，默认监听 **UDP 5514 / TCP 5514**（可在 `.env` 调整）。

**各厂商配置备份命令**

| 厂商 | 备份命令 | 说明 |
| --- | --- | --- |
| Cisco IOS/NXOS | `show running-config` | 标准命令 |
| Cisco ASA | `show running-config` | 含完整配置 |
| Cisco WLC | `show running-config` | 无线控制器配置 |
| 华为 VRP / H3C | `display current-configuration` | 标准命令 |
| MikroTik RouterOS | `/export hide-sensitive` | 导出时隐藏敏感字段 |
| 飞塔 FortiGate | `diagnose firewall config-file save - flash` + `show full-configuration` | 先存诊断快照便于回滚，再导出可读配置 |

> 飞塔的诊断保存命令只返回状态回显、不含配置内容，平台已自动识别并只保留真正的配置文本。
> 备份文件的**扩展名与内容格式按厂商区分**（飞塔输出标准 `.conf`，其余厂商输出 `.cfg`），
> 详见「[九之十、配置备份输出格式](#九之十配置备份输出格式飞塔-conf)」。

**设备侧配置示例**

```
# H3C / 华为
logging host 10.10.0.100 5514
# Cisco
logging host 10.10.0.100
logging trap informational
```

**解析能力**

- RFC3164（`<PRI>MMM dd hh:mm:ss host tag: msg`）与 RFC5424
- 华为/H3C 格式 `%%01MODULE/LEVEL/TYPE:` → 从 TAG 提取级别
- Cisco 格式 `%LINK-3-UPDOWN:` → 从 TAG 提取级别
- 按源 IP / 主机名自动匹配设备，日志与告警联动

---

## 七之二、NetFlow / IPFIX 流量采集

平台内置 NetFlow 采集器，默认监听 **UDP 2055 / 4739 / 9995**（可在 `.env` 调整）。

**设备侧配置示例**

```
# H3C / 华为
flow-export version 9
flow-export destination 10.10.0.100 2055
# Cisco
flow export destination 10.10.0.100 2055 version 9
flow-export template timeout 60
```

**支持的报文版本**

| 版本 | 说明 | 特点 |
| --- | --- | --- |
| NetFlow v5 | 固定头 24B + 48B 定长记录 | 无需模板，但字段少、计数上限 32 位 |
| NetFlow v9 | 模板化（Template + Data FlowSet） | 字段可自定义，需先收模板再收数据 |
| IPFIX (v10) | 模板化 + 可变长 IE | 支持 64 位计数与企业自定义 IE |

平台会缓存收到的模板；**若设备重启导致模板丢失，平台会自动等待新模板并跳过无模板的数据流集**，
不会产生脏数据。

**分析能力**

- 五元组会话明细（源/目的 IP、端口、协议、应用）
- Top 通信源 / Top 通信目的 / Top 会话对
- 应用分布（按端口识别 HTTP/HTTPS/DNS/SSH/SMB 等 30+ 常见协议）
- 流量趋势（5 分钟窗口聚合）
- 错包、TCP 标志位、DSCP、AS 号、MAC 等扩展字段

---

## 七之三、Telemetry（gNMI）遥测采集

平台内置 gNMI 服务端（默认 **57400**），proto 定义与官方 openconfig/gnmi 规范一致，可直接对接
Cisco IOS-XR、H3C Comware、Huawei VRP 等设备的 gNMI 服务。

**两种工作模式**

| 模式 | 说明 | 配置 |
| --- | --- | --- |
| **collector（设备订阅）** | 设备连接平台并订阅；平台作为数据源，通过 SNMP/SSH 采集该设备指标并以 gNMI `SubscribeResponse` 回送 | 只需在平台登记设备 IP |
| **client（平台订阅）** | 平台作为 gNMI 客户端主动连接设备，订阅指定路径并接收推送 | 需填写设备地址、端口与订阅路径 |

**路径映射**：内置 OpenConfig 标准模型 + 主流厂商私有模型的映射表，采用三级匹配策略
（完整路径 → 后缀匹配 → 叶子名匹配），兼容不同厂商差异化的路径形态：

| 平台指标 | 示例路径 |
| --- | --- |
| 接口入/出字节数 | `interfaces/interface[name=Ethernet1/1]/state/counters/in-octets` |
| 接口状态 | `interfaces/interface/state/oper-status` |
| CPU 使用率 | `system/cpu/state/utilization` |
| 内存使用量 | `system/memory/state/used` |
| **无线终端连接数** | `access-points/access-point/state/num-clients` |
| **无线客户端 MAC** | `access-points/access-point/ssids/ssid/clients/client/state/mac-address` |

**无线终端连接信息**内置 16 条路径，覆盖 OpenConfig wifi 标准模型与常见厂商私有写法：

| 指标 | 说明 |
| --- | --- |
| `wlan_client_count` | 关联终端总数（设备级） |
| `wlan_client_mac` | 已关联终端的 MAC（接口级，每个终端一条） |
| `wlan_client_state` | 终端连接状态 |
| `wlan_client_conn_time` | 连接时长 |
| `wlan_client_rx_bytes` / `tx_bytes` | 终端流量 |
| `wlan_radio_client_count` | 单射频在线终端数 |
| `wlan_channel_utilization` | 信道利用率 |

### 自定义路径

「Telemetry 遥测」页 → 可订阅路径 → **新增路径**，填写设备 gNMI 可订阅的完整路径
（不含 origin 前缀）与平台指标名即可，无需等平台适配厂商私有模型。

自定义路径会与内置映射一起生效（采集器按最长前缀优先匹配），
并自动出现在订阅配置的可选路径里。内置 46 条 + 自定义 N 条，
列表区分「内置 / 自定义」来源，自定义项可删除。

**数据归一化**：遥测样本写入与 SNMP 采集**相同的** `device_metrics` / `interface_metrics` 表，
因此所有既有看板直接支持遥测数据，无需区分来源。

---

## 八、基线巡检与报告**内置模板**（共 7 套）：通用交换机、H3C/华为、Cisco、Cisco ASA 防火墙、
Cisco WLC 无线控制器、飞塔 FortiGate 防火墙、MikroTik RouterOS。

**按厂商自动匹配**：巡检时平台会依据每台设备的厂商自动选用对应模板——
飞塔用飞塔基线、ASA 用 ASA 基线、WLC 用 WLC 基线，以此类推；
某厂商没有专用模板时回退到通用模板。因此一次「全网巡检」可同时覆盖多种设备。

**设备类型特有指标**

| 设备 | 额外采集指标 | 来源 |
| --- | --- | --- |
| Cisco ASA | 当前连接数、连接峰值、CSC 表项数 | `ciscoASAConnCount` 等 OID，SSH 兜底 `show connection count` |
| Cisco WLC | 已关联 AP 数、已关联客户端数 | `ciscoWlcApClientAssociated` 等 OID，SSH 兜底 `show ap summary` / `show client summary` |
| 飞塔 FortiGate | 并发会话数、登录用户数、VPN 隧道数 | `fgSysSesCount` 等 OID，SSH 兜底 `get system performance status` |

**检查项结构**

```json
{
  "key": "cpu",
  "title": "CPU 使用率",
  "metric": "cpu",
  "operator": "<=",
  "threshold": 80,
  "unit": "%",
  "level": "critical",
  "desc": "CPU 使用率应低于 80%"
}
```

**报告**：每次巡检自动生成 **HTML**（可打印为 PDF，含合规得分与逐项明细）
与 **Excel**（概览页 + 明细页双表），均可在巡检任务列表下载。

---

## 九、验证脚本

```bash
cd netops/backend

# 1. SNMP 采集层验证（内置 Mock Agent，不依赖外部设备）
python -m scripts.verify_snmp        # 42 项：v2c/v3/BULK 整表遍历/速率差分/OID 候补/Syslog 解析

# 2. NetFlow / Telemetry 验证（构造真实格式报文）
python -m scripts.verify_flows       # 63 项：v5/v9/IPFIX 解析/路径映射/入库聚合/遥测归一化

# 3. gNMI 端到端验证（真实 gRPC 客户端连接 Collector）
python -m scripts.verify_gnmi        # 16 项：collector/client 双模式/订阅回送/样本归一化

# 4. 端到端链路验证（需先启动服务）
python -m scripts.verify_e2e         # 48 项：认证→采集→告警→日志→巡检→报告→备份

# 5. 前端 UI 渲染验证（需先启动服务，自动截图到 docs/screenshots）
python -m scripts.verify_ui          # 17 个页面渲染与控制台错误检查

# 6. 深色/浅色主题验证（需先启动服务）
python -m scripts.verify_theme       # 14 项：模式切换、持久化、刷新保持、组件样式适配

# 7. 左侧导航栏验证（需先启动服务）
python -m scripts.verify_sidebar     # 14 项：分组结构、搜索过滤、折叠态、滚动可达、菜单跳转

# 8. 新增设备类型验证（飞塔 / ASA / WLC）
python -m scripts.verify_newdev      # 41 项：厂商画像、别名归一化、指标解析、基线模板、备份命令

# 9. 设备接入与审批验证（TACACS+ 真实 TCP 交互）
python -m scripts.verify_access      # 21 项：TACACS+ 报文编解码、TCP 认证、RADIUS 响应、审批状态机

# 10. 告警详情与门店筛选验证
python -m scripts.verify_alert_detail  # 36 项：接口级告警明细、指标映射、门店筛选互斥性

# 11. 告警中心前端验证
python -m scripts.verify_alert_ui      # 12 项：门店筛选控件、联动、详情展开行、接口明细表

# 12. 飞塔配置转换验证（.conf 输出）
python -m scripts.verify_fortinet_config  # 48 项：噪声剥离、结构解析、元信息头、截断检出、扩展名区分

# 13. 多角色与审批员权限验证
python -m scripts.verify_roles         # 80 项：角色定义、多角色存储、权限判定、API、审批流程

# 14. 多角色前端 UI 验证（需先启动前后端服务）
python -m scripts.verify_roles_ui      # 15 项：多选角色、多角色标签展示、审批入口、职责分离

# 15. 后台启停脚本验证（脚本会自行启停服务）
python -m scripts.verify_service       # 54 项：脚本齐全性、编码、helper 子命令、启停全生命周期

# 16. 登录页品牌名与厂商列表验证（需先启动后端服务）
python -m scripts.verify_login_page     # 20 项：公开配置接口、厂商列表同源、页面渲染、品牌名替换

# 17. 系统管理·平台说明页验证（需先启动后端服务）
python -m scripts.verify_about_page     # 23 项：厂商分类与说明、类别排序、功能清单完整性

# 18. 设备在线探测验证（需先启动后端服务）
python -m scripts.verify_probe          # 39 项：探测服务逻辑、单设备/批量接口、探测界面

# 19. 新需求验证（设备管理布局 / 遥测自定义路径 / 防火墙会话）
python -m scripts.verify_new_features   # 28 项：表格无横向滚动、无线终端路径、自定义路径 CRUD、会话页

# 20. 启动窗口与飞塔内存采集验证（需先启动后端服务）
python -m scripts.verify_startup_and_fg_mem  # 15 项：无 cmd 窗口残留、关窗后服务存活、飞塔内存/CPU 解析
```

> 容器内运行验证脚本时，需先启动服务并把 `BASE` 指向容器地址。

> 修改了 `app/proto/gnmi.proto` 后需重新生成 protobuf：
> `python -m scripts.gen_proto`

---

## 九之二、域控对接在线配置

域控参数不再只能改 `.env`，可直接在 **系统管理 → 域控对接** 页配置并**立即生效**（无需重启）：

1. 打开「启用域控登录」开关
2. 填写服务器地址、端口（SSL / StartTLS）、Base DN、绑定服务账号与密码
3. 可选：填写用户过滤器或用户 DN 模板（两者二选一）
4. 可选：配置 AD 组 → 平台角色映射（JSON），例如
   `{"CN=NetOps-Admins,OU=Groups,DC=corp,DC=local":"admin"}`；
   值也可写成数组以授予多个角色：
   `{"CN=NetOps-Approvers,OU=Groups,DC=corp,DC=local":["operator","approver"]}`
5. 点「保存配置」→「测试连接」→「同步域用户」

配置保存在数据库 `settings` 表中，优先级高于环境变量。

---

## 九之三、AI 网络助手

**系统管理 → AI 助手** 中配置服务商与密钥后启用，即可使用流式对话分析网络问题。

**支持的服务商**（均走 OpenAI 兼容协议，除 Anthropic 外）

| 类别 | 服务商 |
| --- | --- |
| 国外 | OpenAI、Anthropic Claude、Gemini 兼容端点、Ollama 本地模型 |
| 国内 | DeepSeek、Moonshot Kimi、通义千问、智谱 GLM、百度千帆、字节豆包、SiliconFlow |
| 自定义 | 任意兼容 `/chat/completions` 的地址 |

**上下文注入**：开启「注入平台数据」后，模型会自动收到以下实时数据：

- 设备清单（名称、IP、厂商、型号、门店、状态、启用的采集通道）
- 最近 CPU / 内存 / 温度 / 响应时延指标
- 高流量接口 Top 15（含利用率、状态、错包）
- 未恢复告警（按严重程度排序）
- 异常级别设备日志
- 拓扑邻居关系（可选）

模型据此给出「结论 → 依据 → 排查步骤 → 建议」的结构化分析。
对话历史保存在服务端，支持多轮追问与上下文预览（可查看实际发给模型的内容）。

---

## 九之四、外观主题

顶栏的主题按钮可在 **深色 / 浅色 / 跟随系统** 之间切换：

- 选择保存在 `localStorage`，刷新后保持，首屏无闪烁（`main.js` 在挂载前先应用）
- 「跟随系统」会监听 `prefers-color-scheme` 变化实时切换
- 深色模式基于 Element Plus 官方暗色变量，覆盖侧边栏、表格、弹窗、描述列表等
- 所有 ECharts 图表的坐标轴、图例、标签文字均通过 CSS 变量响应主题

---

## 九之五、网络拓扑与审计日志

**网络拓扑**（网络拓扑页）

- 采集来源：SNMP（LLDP-MIB `lldpRemTable` + CISCO-CDP-MIB `cdpCacheAddressTable`）优先，
  无数据时回退 SSH 厂商命令（华为/H3C `display lldp neighbor`、Cisco `show lldp neighbors` 等）
- 力导向拓扑图：滚轮缩放、拖拽节点、邻接高亮
- 统计节点数、连接数、涉及设备数，并识别**双向邻居对**（疑似环路）

**审计日志**（审计日志页）

- 写操作（POST/PUT/DELETE）由中间件**自动记录**，含操作人、目标、来源 IP、状态码
- 关键操作（采集开关、设置变更、配置同步等）由业务代码**手动补充记录**
- 支持按操作类型、用户、关键字、时间范围检索，并给出按类型/用户的聚合统计

---

## 九之六、总览大屏自定义与采集服务开关

**大屏自定义**（总览大屏 → 自定义）

可勾选显示的模块、调整顺序与宽度（1/4 宽、1/2 宽、整行），配置保存在浏览器本地，
随时可「恢复默认」。

**采集服务开关**（系统管理 → 采集服务）

四个采集通道均可运行时启停，**无需重启服务**：

| 通道 | 说明 |
| --- | --- |
| SNMP 轮询 | 平台主动轮询，关闭后仅保留其他调度任务 |
| Telemetry（gNMI） | 关闭时释放 57400 端口，重新开启立即恢复监听 |
| NetFlow / IPFIX | 关闭时释放 2055/4739/9995 端口 |
| Syslog 接收 | 关闭时释放 5514 端口 |

开关状态持久化在数据库，服务重启后自动应用。

---

## 九之七、设备接入与统一认证（TACACS+/RADIUS）

平台内置 AAA 认证服务，交换机/防火墙可把平台当作 TACACS+ 或 RADIUS 服务器，
运维人员用平台账号登录设备时由平台统一认证授权。

**启用方式**：系统管理 → 设备接入 → 统一认证 (TACACS/RADIUS) → 打开开关 → 设置共享密钥 → 保存。
服务启动后监听 **TCP 49**（TACACS+）与 **UDP 1812**（RADIUS）。

**设备侧配置示例**

```
# Cisco / H3C / 华为
tacacs server 10.10.0.100
tacacs key 0 <共享密钥>
aaa authentication login default group tacacs+ local
aaa authorization exec default group tacacs+ local
```

**认证来源**（按优先级）

| 来源 | 说明 | 权限级别 |
| --- | --- | --- |
| 平台用户 | 本地库与域控同步的用户，密码与登录平台一致 | 管理员/操作员 → 15，只读 → 1 |
| 设备本地账号 | 「设备接入」页维护的设备账号（密码加密存储） | 按账号配置的 privilege |

**认证会话日志**：记录每次认证的协议、用户名、设备（NAS）IP、结果、权限级别与耗时，
可在设备接入页查看，也可在审计日志页追溯。

**设备本地账号**：可为每台设备维护多个登录账号（含 enable 密码、权限级别、认证方式），
用于平台代登录设备执行采集、备份、配置下发等操作。密码使用 Fernet 加密存储，接口只返回掩码。

---

## 九之八、设备配置变更审批

设备配置不能直接下发，需经审批流程：

```
提交申请(pending) → 审批员/管理员审批(approved / rejected) → 下发到设备(applied / failed)
```

- **提交**：操作员选择设备、填写变更标题/说明与待下发的配置命令（或指定要回滚到的备份版本），
  自动生成工单号（`CFG + 日期 + 序号`）
- **审批**：**审批员或管理员**可批准或驳回；驳回必须填写意见；已审批的申请不可重复审批；
  **申请人不能审批自己提交的申请**（职责分离，服务端强制校验）
- **下发**：按厂商自动进入对应配置模式（Cisco `configure terminal`、华为/H3C `system-view`、
  飞塔 `config`、MikroTik 脚本模式），下发前**自动备份当前配置**以便回滚，
  并解析设备回显判断是否出错
- **审计**：提交、审批、下发三个环节全部写入操作日志

> 角色的完整说明与多角色分配方式见「[九之十、角色体系与多角色分配](#九之十角色体系与多角色分配)」。

---

## 九之九、告警详情与定时任务配置

### 告警内容细化

告警不再只是一句「指标 X 超阈值」，而是直接给出**可定位的详细信息**：

| 告警类型 | 描述内容示例 |
| --- | --- |
| 接口错包 | 哪几个接口有错包、合计多少、错包最多的接口及其入/出向错包数、速率与状态、其他异常接口列表 |
| 接口利用率 | 最高利用率接口、利用率数值、速率、入/出向流量、超阈值接口清单 |
| 接口流量 | 峰值接口、流量 Mbps、入/出向流量、带宽、超阈值接口清单 |
| Down 接口 | 列出全部处于 DOWN 状态的接口名 |
| CPU / 内存 / 温度 | 当前值、阈值、**超出幅度**、指标中文名 |
| 设备离线 | 最后在线时间，并给出排查建议（电源、链路、管理网、凭据、日志） |

告警列表每行可**展开查看结构化明细**：接口名、UP/DOWN 状态、指标值、速率、入/出向流量或错包拆分，
以表格形式呈现，便于直接定位问题接口。

### 告警中心门店筛选

告警事件列表支持按**门店**筛选，且与设备筛选联动——选择门店后，设备下拉只显示该门店设备，
切换门店会自动清空不属于该门店的已选设备。

### 定时任务 Web 配置

系统管理 → 平台概况 → 定时任务配置，可在界面直接：

- **编辑调度策略**：填写 cron 表达式（5 段，如 `30 4 * * *`）或调整间隔秒数；
  cron 表达式非法时会明确报错并拒绝保存
- **启用 / 停用**：停用后任务立即从调度器移除并释放资源，重新启用即刻恢复
- **立即执行**：手动触发一次任务
- **恢复默认**：还原为内置的默认调度策略

配置持久化在数据库，服务重启后自动应用；所有变更写入操作日志。
页面同时展示每个任务的下次执行时间、上次执行时间与执行结果（成功/失败）。

---

## 九之九之一、设备在线探测

设备管理页提供两种粒度的在线探测：

| 入口 | 范围 | 说明 |
| --- | --- | --- |
| 状态列「探测」按钮 | 单台设备 | 探测当前这一台，立即弹出结果 |
| 工具栏「探测全部设备」 | 全部已启用设备 | 批量探测，结束后弹出明细对话框 |

探测结果会**写回设备状态**（`status` 与 `last_seen`），
表格里的「在线 / 离线 / 未知」标签随即刷新，鼠标悬停可看到最后在线时间。

### 探测与「连通测试」的区别

| | 在线探测 | 连通测试（`/test`） |
| --- | --- | --- |
| 开销 | 轻量：一次 SNMP GET（sysUpTime） | 较重：采集接口流量、CPU、内存等全部指标 |
| 用途 | 快速判断设备在不在 | 看详细指标与接口列表 |
| 写入 | 只更新 `status` / `last_seen` | 不写指标表 |

日常巡检用**探测**即可；需要看具体指标时再用连通测试。

### 兜底与保护

- **SSH 兜底** —— SNMP 不通但配置了 SSH 凭据时，自动改用 SSH 判断可达性
- **并发受限** —— 批量探测默认并发 10（上限 32），
  避免一次性对上百台设备发起请求把网络打满
- **演示模式** —— `mock_mode` 的设备直接判为在线，避免误报

### 接口

```http
POST /api/devices/{device_id}/probe      # 单设备
POST /api/devices/probe/batch             # 批量
```

批量请求体：

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `device_ids` | `int[]` | 不传 | 指定设备；不传则探测全部已启用设备 |
| `concurrency` | `int` | 10 | 并发上限（1~32） |
| `update_status` | `bool` | true | 是否写回设备状态 |

```bash
# 探测全部设备
curl -X POST http://localhost:8900/api/devices/probe/batch \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"concurrency": 10}'
```

返回示例：

```json
{
  "total": 3, "online": 2, "offline": 1, "elapsed_sec": 1.42,
  "items": [
    {"device_id": 1, "name": "核心交换机", "ip": "10.0.0.1",
     "online": true, "status": "online", "response_ms": 12.5, "error": ""}
  ]
}
```

---

## 九之九之二、防火墙实时会话

「运维监控 → 防火墙会话」集中展示所有防火墙设备的**当前会话数**与历史趋势。

### 各厂商采集的会话指标

| 厂商 | 指标 | 采集方式 |
| --- | --- | --- |
| Cisco ASA | 当前连接数 / 并发连接峰值 / CSC 表项数 | SNMP `ciscoASAConnCount` 等，或 `show connection count` |
| 飞塔 FortiGate | 当前会话数 / 登录用户数 / VPN 隧道数 | SNMP `fgSysSesCount` 等，或 `get system performance status` |

这些指标在**设备采集时**一并获取并写入 `device_metrics.extra_metrics`，
会话页读取该字段，因此数据随正常采集周期自动更新，无需单独轮询。

### 页面内容

- **汇总卡片** —— 防火墙设备数、会话总数、最高单台、离线设备数
- **设备一览** —— 每台防火墙的当前会话/连接数、并发峰值、在线状态、采集时间
- **会话趋势** —— 会话数随时间变化曲线（可切 6 小时 / 24 小时）

设备详情页也会为防火墙设备显示会话卡片与会话趋势图。

### 接口

```http
GET /api/monitor/firewalls/sessions          # 所有防火墙的当前会话数一览
GET /api/monitor/devices/{device_id}/sessions # 单台设备的会话数与趋势
```

---

## 九之十、角色体系与多角色分配

### 四种角色

| 角色 | 键名 | 层级 | 能力 |
| --- | --- | --- | --- |
| 管理员 | `admin` | 3 | 全部权限，**含审批权** |
| 操作员 | `operator` | 2 | 查看 + 设备增删改、执行采集、备份、巡检、**提交**变更申请 |
| **审批员** | `approver` | 2 | 查看 + **审批**配置变更申请（**不含**设备操作权） |
| 只读 | `viewer` | 1 | 仅查看，不可修改任何配置 |

**关键设计：审批权是独立的能力维度，不是层级链上的一环。**

- 纯 `approver` 不会因为层级是 2 而获得设备增删改权限（那属于 `operator`），
  两者能力互不覆盖；
- `admin` 天然包含审批权，因此管理员依旧可以审批；
- 审批员只能**审批**，不能**提交**变更申请——提交仍需操作员权限。

### 一个用户可以有多个角色

系统管理 → 用户管理 → 角色字段为**多选**，可同时勾选多个角色。

| 场景 | 分配角色 | 效果 |
| --- | --- | --- |
| 运维工程师 | 操作员 | 能提交变更申请，能操作设备 |
| 网络主管 | 操作员 + 审批员 | 既能提交变更申请，也能审批他人申请 |
| 专职审批 | 审批员 | 只审批，不碰设备 |
| 安全审计 | 只读 | 只看，不改 |

多角色用户的权限是**各角色能力的并集**，层级判定取最高者。
例如「操作员 + 审批员」的层级为 2（操作员），同时具备审批权。

### 职责分离：申请人不能审批自己的申请

无论一个人拥有多少角色，**他都不能审批自己提交的变更申请**。
这条规则在服务端强制执行：

- 提交人为 `alice`、审批人也为 `alice` → 拒绝，提示
  「申请人不能审批自己的变更申请，请转交其他审批人」；
- 前端「配置审批」页会**隐藏**自己提交的待审批单据的批准/驳回按钮；
- 这让「操作员 + 审批员」的双角色分配真正有意义——
  否则同一个人提交又审批，审批环节形同虚设。

### 域账号的多角色映射

`NETOPS_LDAP_GROUP_ROLE_MAP`（或系统管理 → 域控对接页）支持两种写法：

```json
{
  "CN=NetOps-Admins,OU=Groups,DC=corp,DC=local": "admin",
  "CN=NetOps-Operators,OU=Groups,DC=corp,DC=local": "operator",
  "CN=NetOps-Approvers,OU=Groups,DC=corp,DC=local": ["operator", "approver"]
}
```

- 值为**字符串**：单角色；
- 值为**数组**：该 AD 组的成员同时拥有多个角色；
- 命中**多个组**时**累加**所有角色（去重），而非只取最高权限。

域用户每次登录/同步都会按当前 AD 组重新计算角色集合。

### 数据兼容

| 字段 | 说明 |
| --- | --- |
| `users.role` | 主角色，等于角色集合中权限最高者（保留此字段以兼容既有代码与外部集成） |
| `users.roles` | 角色集合，JSON 数组，如 `["operator","approver"]` |

历史数据的 `roles` 为空时自动按 `role` 单值回退，**升级无需任何数据处理**。
新增的 `roles` 列由平台启动时的轻量迁移自动补齐。

### 自行验证

```bash
# 后端目录下执行：80 项断言，覆盖角色定义、多角色存储、权限判定、API 与审批流程
python -u -m scripts.verify_roles

# 前端 UI 验证（需先启动前后端服务）：15 项，覆盖多角色展示与审批入口
python -u -m scripts.verify_roles_ui
```

---

## 九之十一、配置备份输出格式（飞塔 .conf）

备份时不再把 SSH 原始回显直接存盘，而是先经**按厂商的格式化器**整理，
再写入备份目录。文件扩展名也按厂商区分：

| 厂商 | 输出扩展名 | 处理方式 |
| --- | --- | --- |
| 飞塔 FortiGate | **`.conf`** | 完整转换：剥离噪声 → 规范化缩进 → 补元信息头 → 完整性校验 |
| Cisco IOS/NXOS / ASA / WLC | `.cfg` | 剥离噪声 + 补元信息头，正文原样保留 |
| 华为 VRP / H3C | `.cfg` | 同上 |
| MikroTik RouterOS | `.cfg` | 同上 |

### 飞塔 `.conf` 长什么样

```
# ============================================================
# FortiGate 配置备份（FortiOS configuration）
# 设备名称 : FGT-60F
# 管理地址 : 10.10.3.1
# 采集时间 : 2026-11-10 15:22:52
# 来源命令 : diagnose firewall config-file save - flash / show full-configuration
# 配置块数 : 4
# ============================================================

config system global
    set hostname "FGT-60F"
    set timezone 04
    set admin-sport 8443
end
config system interface
    edit "port1"
        set vdom "root"
        set ip 10.10.3.1 255.255.255.0
        set allowaccess ping https ssh
    next
end
...
```

### 转换做了四件事

1. **剥离 CLI 噪声**：命令回显（`FGT # show full-configuration`）、提示符（`FGT #` /
   `FGT(config) #`）、诊断状态行（`Diagnostics : running` / `config-file saved`）、
   采集器分段标记（`# ===== show full-configuration =====`）、分隔线与空行。
2. **规范化结构**：顶层 `config` 行不缩进，层级按 FortiOS 的 **4 空格**制对齐，
   `edit` / `set` / `next` 关系保留，可直接被 FortiOS 解析或做版本对比。
3. **补元信息头**：设备名称、管理地址、采集时间、来源命令、配置块数，
   便于审计追溯与多份备份对照。
4. **完整性校验**：统计 `config` 块与 `end` 的配对、`edit` 条目数、`set` 命令数。

### 截断配置不会被"补全"

如果采集过程中连接中断导致配置**不完整**（有 `config` 块但缺少配对的 `end`），
平台**不会自动补 `end`** —— 补齐后会得到一份「看起来完整、实际缺配置」的危险文件，
一旦被误用于回灌就会造成设备配置丢失。处理方式是：

- 保持原始内容不动；
- 在文件头写入 `# [警告] 检测到 N 个配置块未闭合，配置可能被截断，请核查采集完整性`；
- 备份记录（message 字段）标注校验未通过的原因。

校验不通过的备份仍会落盘，便于人工复核，而不是被静默丢弃。

### 自行转换

```bash
# 后端目录下执行，会打印转换后的 .conf 内容
python -u -m scripts.verify_fortinet_config
```

该脚本包含 **43 项断言**，覆盖噪声剥离、结构解析、元信息头、内容保真度、
截断检出、扩展名区分与落盘命名。

---

## 十、配置项速查（`.env`）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `NETOPS_DATABASE_URL` | `sqlite:///./netops.db` | 数据库连接串（可换 MySQL/PostgreSQL） |
| `NETOPS_SECRET_KEY` | 占位值 | **生产必改**，用于签发 JWT 与加密凭据 |
| `NETOPS_ACCESS_TOKEN_EXPIRE_MINUTES` | `720` | 登录有效期 |
| `NETOPS_DEFAULT_POLL_INTERVAL` | `60` | 默认采集间隔（秒） |
| `NETOPS_MAX_CONCURRENT_POLL` | `20` | 并发采集上限 |
| `NETOPS_SNMP_TIMEOUT` | `3.0` | SNMP 超时（秒） |
| `NETOPS_METRIC_RETENTION_DAYS` | `30` | 指标保留天数 |
| `NETOPS_LOG_RETENTION_DAYS` | `60` | 日志保留天数 |
| `NETOPS_SYSLOG_ENABLED` | `true` | 是否启用 Syslog 接收 |
| `NETOPS_SYSLOG_UDP_PORT` | `5514` | Syslog UDP 端口 |
| `NETOPS_NETFLOW_ENABLED` | `true` | 是否启用 NetFlow 采集 |
| `NETOPS_NETFLOW_UDP_PORTS` | `[2055,4739,9995]` | NetFlow 监听端口（v9 / IPFIX / v5） |
| `NETOPS_NETFLOW_TCP_PORTS` | `[]` | NetFlow TCP 监听端口（留空则仅 UDP） |
| `NETOPS_NETFLOW_RETENTION_DAYS` | `7` | 流记录保留天数 |
| `NETOPS_TELEMETRY_ENABLED` | `true` | 是否启用 gNMI 遥测服务 |
| `NETOPS_GNMI_LISTEN_PORT` | `57400` | gNMI Collector 监听端口 |
| `NETOPS_SCHEDULER_ENABLED` | `true` | 是否启用采集调度器 |
| `NETOPS_BACKUP_DIR` | `./data/backups` | 备份文件目录 |
| `NETOPS_REPORT_DIR` | `./data/reports` | 巡检报告目录 |

---

## 十之一、Docker 部署

### 快速启动

```bash
# 1. 构建并启动
docker compose up -d --build

# 2. 查看状态与日志
docker compose ps
docker compose logs -f netops

# 3. 访问
#    http://<服务器IP>:8900
```

默认账号 `admin / admin123`，**登录后请立即修改**。

### 端口说明

| 端口 | 协议 | 用途 | 设备侧配置示例 |
| --- | --- | --- | --- |
| 8900 | TCP | Web 管理界面 + API | 浏览器访问 |
| 5514 | UDP | Syslog 日志接收 | `logging host 10.10.0.100 5514` |
| 2055 | UDP | NetFlow v9 | `flow export destination 10.10.0.100 2055 version 9` |
| 4739 | UDP | IPFIX | `flow export destination 10.10.0.100 4739` |
| 9995 | UDP | NetFlow v5 | `flow-export destination 10.10.0.100 9995` |
| 57400 | TCP | gNMI Telemetry Collector | 设备配置指向本平台 |

### 数据持久化

所有运行数据（数据库、配置备份、巡检报告、导入导出文件）都在命名卷 `netops-data` 中：

```
/app/data/
├── netops.db          # SQLite 数据库
├── backups/           # 设备配置备份
├── reports/           # 巡检报告（HTML/Excel）
└── imports/           # 导入导出临时文件
```

```bash
# 备份数据卷
docker run --rm -v netops-data:/data -v $(pwd):/backup alpine   tar czf /backup/netops-data-$(date +%Y%m%d).tar.gz -C /data .

# 恢复
docker run --rm -v netops-data:/data -v $(pwd):/backup alpine   tar xzf /backup/netops-data-20261110.tar.gz -C /data
```

### 环境变量

`docker-compose.yml` 中可覆盖的关键配置：

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `NETOPS_SECRET_KEY` | JWT 签发密钥与凭据加密密钥，**生产必改** | 占位值 |
| `NETOPS_DATABASE_URL` | 数据库连接串 | `sqlite:////app/data/netops.db` |
| `NETOPS_SCHEDULER_ENABLED` | SNMP 轮询调度开关 | `true` |
| `NETOPS_TELEMETRY_ENABLED` | gNMI Collector 开关 | `true` |
| `NETOPS_NETFLOW_ENABLED` | NetFlow 采集开关 | `true` |
| `NETOPS_SYSLOG_ENABLED` | Syslog 接收开关 | `true` |
| `NETOPS_GNMI_LISTEN_PORT` | gNMI 监听端口 | `57400` |
| `NETOPS_METRIC_RETENTION_DAYS` | 指标保留天数 | `30` |
| `NETOPS_NETFLOW_RETENTION_DAYS` | 流记录保留天数 | `7` |
| `NETOPS_MAX_CONCURRENT_POLL` | 并发采集上限 | `20` |
| `TZ` | 时区 | `Asia/Shanghai` |

生产环境建议用 `.env` 文件管理（`docker compose` 会自动读取同目录的 `.env`）：

```ini
NETOPS_SECRET_KEY=<openssl rand -hex 32 的结果>
```

### 切换 PostgreSQL

设备规模较大或写入频繁时建议使用 PostgreSQL：

```bash
docker compose --profile postgres up -d
```

并在 `docker-compose.yml` 中把 `NETOPS_DATABASE_URL` 改为：

```
postgresql+psycopg://netops:pass@db:5432/netops
```

### 镜像说明

`Dockerfile` 采用三阶段构建：

1. **前端构建**（node:20-alpine）→ 产出静态资源
2. **后端构建**（python:3.12-slim）→ 安装 Python 依赖、生成 gNMI protobuf 代码
3. **运行时**（python:3.12-slim）→ 仅保留运行所需，以非 root 用户（uid 10001）运行

镜像内已包含健康检查（`/api/health`），编排时 `docker compose ps` 会显示 `healthy` 状态。

单独使用 docker 命令：

```bash
docker build -t netops:latest .
docker run -d --name netops \
  -p 8900:8900 -p 5514:5514/udp -p 2055:2055/udp -p 4739:4739/udp -p 57400:57400 \
  -v netops-data:/app/data \
  -e NETOPS_SECRET_KEY="$(openssl rand -hex 32)" \
  --restart unless-stopped netops:latest
```

---

## 十之二、代码获取与 GitHub

### 克隆与部署

```bash
git clone <仓库地址>
cd netops
docker compose up -d --build
```

### 一键推送到 GitHub

仓库内置推送脚本，会自动创建远程仓库、推送 `main` 分支并打 `v1.0.0` 标签：

```bash
# 方式一：使用 Personal Access Token（推荐）
#   在 https://github.com/settings/tokens 生成，勾选 repo 权限
export GITHUB_TOKEN=ghp_你的token
./push_github.sh                      # 交互式
./push_github.sh my-netops            # 指定仓库名
./push_github.sh my-netops private    # 指定仓库名与可见性

# 方式二：已配置 SSH key 时无需 token
./push_github.sh my-netops
```

### 项目结构

```
netops/
├── backend/              FastAPI 后端
│   ├── app/
│   │   ├── api/          REST 接口层
│   │   ├── collectors/   采集层（SNMP / SSH / NetFlow / gNMI / 拓扑）
│   │   ├── core/         配置、安全、凭据加密
│   │   ├── models/       数据模型
│   │   ├── proto/        gNMI protobuf 定义
│   │   └── services/     业务服务层
│   ├── scripts/          14 套自动化验证脚本 + 服务管理辅助
│   └── requirements.txt
├── frontend/             Vue 3 + Element Plus + ECharts
│   └── src/views/        19 个功能页面
├── docs/screenshots/     界面截图
├── Dockerfile            三阶段构建
├── docker-compose.yml    编排配置
├── start.sh / stop.sh    后台启停脚本（Linux / macOS）
├── start.bat / stop.bat  后台启停脚本（Windows）
├── restart.sh / .bat     重启脚本
└── status.sh / .bat      状态查看脚本
└── push_github.sh        一键推送 GitHub
```

---

## 十一、生产部署建议

1. **修改默认密码**并设置强随机 `NETOPS_SECRET_KEY`（一旦设置不可更改，否则已加密的凭据无法解密）。
2. 使用 Nginx / IIS 反向代理并启用 HTTPS；`cors_origins` 收敛为实际域名。
3. SQLite 适合中小规模；设备数超过约 200 台建议切换 PostgreSQL：
   `NETOPS_DATABASE_URL=postgresql+psycopg://user:pass@host:5432/netops`
4. 备份目录（`data/backups`、`data/reports`）与数据库需纳入定期备份。
5. 采集调度器为单实例设计，多副本部署时仅在一个实例上开启
   （`NETOPS_SCHEDULER_ENABLED=true`），其余置 `false`。
