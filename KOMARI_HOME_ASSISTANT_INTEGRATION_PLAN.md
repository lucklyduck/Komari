# Komari Home Assistant 集成功能规划报告

> 调研日期：2026-08-26
>
> 目标：规划一个支持多 Komari 面板、多节点设备的 Home Assistant 自定义集成。每次添加集成只配置一个面板；用户可重复添加 Komari，建立多个相互独立的 Config Entry，同时监控多个面板。

## 1. 结论摘要

Komari 集成的正确定位不是在 Home Assistant 中复刻 Komari 仪表盘，而是把 Komari 已采集的“节点状态和关键指标”转成 Home Assistant 的设备、实体和自动化触发条件。

按照 Home Assistant 官方术语，目标模型如下：

- `komari` 是一个 Integration Domain，manifest 中声明为 `integration_type: hub`。
- 用户每完成一次“添加集成”，创建一个 Config Entry；一个 Config Entry 只保存并管理一个 Komari 面板的连接配置。
- 同一个 Komari Integration Domain 允许存在多个 Config Entry，因此可以同时监控多个面板。
- 每个 Config Entry 创建且只创建一个面板 Device。该设备使用 `DeviceEntryType.SERVICE`，承载面板版本、连接状态和节点汇总等 Entities。
- 面板下每个 Komari 节点分别创建一个独立 Device；节点状态和指标作为该节点的 Entities。
- 节点 Device 与其所属面板 Device 使用 `via_device_id` 建立拓扑关系；所有这些 Devices 只归属于创建它们的 Config Entry。
- 每个 Config Entry 拥有独立 API client、DataUpdateCoordinator、认证信息、Options 和错误状态；一个面板故障不得影响其他面板。
- 使用 HTTPS `POST /api/rpc2` 轮询。首版不使用 WebSocket，因为 Komari 前端仍需要主动调用状态方法，WebSocket 只是 RPC 传输方式，并不等于状态订阅。
- 默认每 30 秒更新实时状态；节点清单、版本和能力列表采用低频刷新。
- 完整目标同时覆盖到期与流量限额、Ping 任务，以及动态/自定义指标；其中 Ping 和动态指标通过用户选择控制实体数量，但不是规划外功能。
- 默认只读，绝不调用 `admin:exec`、远程终端、数据库执行、增删节点、清理记录等管理接口。
- 首版目标应达到 Home Assistant Integration Quality Scale 的 Bronze 基线，并按 Silver/Gold 所需的重认证、诊断、动态设备和陈旧设备清理进行设计。

## 2. 调研范围与依据

### 2.1 Komari 官方能力

[Komari RPC2 文档](https://komari-monitor.github.io/komari-document/dev/rpc)明确推荐使用 `/api/rpc2`，支持 POST 和 WebSocket，最低要求 Komari `>= 1.0.7`，支持匿名访问、Cookie 和 API Key。RPC2 还提供 `rpc.methods`、`rpc.help` 和 `rpc.version`，适合在配置阶段动态探测能力，避免完全依赖版本号判断。

与 Home Assistant 集成直接相关的方法包括：

| 方法 | 用途 | 规划中的使用方式 |
| --- | --- | --- |
| `rpc.ping` | RPC2 健康检查 | 配置校验、故障诊断 |
| `rpc.version` | RPC 协议版本 | 诊断信息 |
| `rpc.methods` | 获取当前实例可用方法 | 能力发现、版本兼容 |
| `public:getVersion` / `common:getVersion` | 服务端版本和构建哈希 | 面板设备信息、诊断实体 |
| `public:getPublicSettings` | 站点名称、私有站点状态等 | 配置项标题、能力判断 |
| `public:getNodesInformation` | 公开节点清单 | 匿名模式优先使用 |
| `common:getNodes` | 完整节点信息 | API Key 模式使用；兼容映射/数组返回 |
| `common:getNodesLatestStatus` | 所有或部分节点最新状态 | 主要实时数据来源 |
| `common:getNodeRecentStatus` | 单节点最近状态缓存 | 调试或后续短期聚合 |
| `common:getRecords` | 负载/Ping 历史记录 | 目标功能的窗口统计兼容接口，不用于全量历史导入 |
| `public:getPublicPingTasks` | 公开 Ping 任务 | Ping 任务发现与选择 |
| `public:getPingRecords` | Ping 历史与统计 | 延迟、丢包和窗口统计实体 |
| `public:getPingMetricStats` | Ping 聚合统计 | 新版本优先使用 |
| `public:listMetricDefinitions` | 指标定义、单位和保留策略 | 动态指标能力发现 |
| `public:queryMetrics` | 查询原始/降采样时间序列 | 动态/自定义指标和窗口统计 |

`common:getNodesLatestStatus` 已提供 CPU、GPU、内存、交换空间、负载、温度、磁盘、网络速率、累计流量、进程数、TCP/UDP 连接数和在线状态，足以完成首版核心集成。

### 2.2 指定实例的只读验证

对用户提供的 [monitor.nekokax.com](https://monitor.nekokax.com/) 在 2026-08-26 进行了匿名只读测试，未输出节点名称、地址、UUID 或监控值：

- `rpc.ping`：HTTP 200，返回 `pong`。
- `public:getVersion`：Komari `1.4.3`。
- `rpc.methods`：返回 91 个方法。
- `public:getNodesInformation`：返回 4 个节点。
- `common:getNodesLatestStatus`：返回 4 个节点状态。
- `public:listMetricDefinitions`：返回 21 个指标定义。
- `public:getPublicPingTasks`：返回 2 个公开 Ping 任务。
- `public:queryMetrics` 和 `common:getNodesLatestStatus` 均存在。

这证明 RPC2 路线能够覆盖目标实例。匿名可访问性属于该实例当前配置，集成仍必须支持 API Key 和私有站点场景。

### 2.3 同类服务器监控系统带来的功能启示

| 系统 | 相关能力 | 对 Komari 集成的启示 |
| --- | --- | --- |
| [Beszel](https://beszel.dev/) | 多服务器、容器、CPU/内存/磁盘/带宽/温度告警、SMART、REST API | 核心指标之外，用户最需要的是“离线、资源超限、磁盘健康”等可自动化状态；容器和 SMART 只应在 Komari API 真正提供后加入 |
| [哪吒监控](https://nezha.wiki/en_US/guide/api) | 节点、分组、服务监控、告警规则、细粒度 PAT scope | 应支持分组/标签信息；长期应推动或优先使用只读、可限定节点范围的 Komari API Key，而不是要求高权限管理令牌 |
| [Netdata](https://learn.netdata.cloud/api) | 多节点指标、指标上下文、告警、历史聚合和降采样 | 不应把全部时间序列和全部维度变成 HA 实体；采用能力发现、白名单和用户选择，控制实体数量与 Recorder 写入量 |
| [Uptime Kuma](https://www.home-assistant.io/integrations/uptime_kuma/) | 状态、响应时间、证书到期、1/30/365 日可用率、30 秒更新 | 为节点在线状态、Ping 延迟/丢包和全局故障汇总提供了直接参考 |

### 2.4 Home Assistant 现有集成带来的架构启示

#### Glances

[Glances 官方集成](https://www.home-assistant.io/integrations/glances/)覆盖 CPU、内存、交换空间、文件系统、磁盘 I/O、网络、温度、GPU、RAID、Docker 和 uptime，并使用 `DataUpdateCoordinator` 统一更新实体。其动态创建磁盘、网卡、温度和 GPU 实体的方式值得参考，但 Komari 当前最新状态是节点级汇总，不能虚构分磁盘或分网卡实体。

#### Uptime Kuma

[Uptime Kuma 官方集成](https://www.home-assistant.io/integrations/uptime_kuma/)每 30 秒获取一次数据，为每个 monitor 创建独立设备，提供稳定 unique ID、动态增加 monitor、重认证、诊断和版本迁移。它同时暴露状态、响应时间、可用率与诊断属性。Komari 应复用这些模式，但应把 `online` 表示为 `binary_sensor`，以便直接参与 HA 的条件、分组和告警自动化。

#### UniFi Network

[UniFi Network 集成源码](https://github.com/home-assistant/core/tree/dev/homeassistant/components/unifi)体现了“一套控制器连接管理多个设备”的典型模型。Komari 面板与节点也是相同关系，因此每个面板 Config Entry 应创建一个面板 service Device 和多个节点 Devices，而不是为每个节点建立单独的面板登录配置。

## 3. 产品目标与边界

### 3.1 目标

1. 允许用户重复执行“添加集成”；每次创建一个面板 Config Entry，从而同时监控一个或多个 Komari 面板。
2. 将节点在线状态、关键资源指标和重要元数据变成可用于仪表盘、历史图表和自动化的实体。
3. 为每个面板创建一个独立面板 Device，为面板下每个节点创建一个独立节点 Device，并把相关 Entities 归入正确设备。
4. 自动发现后加入各面板的节点，并只在对应 Config Entry 内处理新增、改名和删除。
5. 支持匿名公开面板、API Key 私有面板、HTTPS 和自签名证书场景。
6. 支持节点到期时间、剩余天数、流量限额、已用流量及使用率，满足续费和流量预警自动化。
7. 发现 Komari Ping 任务，并按用户选择提供延迟、丢包及窗口聚合实体。
8. 发现动态/自定义指标，并根据服务端定义的类型和单位安全映射为 HA 实体。
9. 在网络中断、面板升级、认证失效、扩展 API 缺失时保持可诊断、可恢复，并保证各面板故障隔离。
10. 控制请求频率、实体数量和 Recorder 数据量，支持多个面板及每个面板几十个节点长期运行。

### 3.2 明确不做

- 不复刻 Komari 的完整历史图表页面。
- 不把 Komari 历史时间序列回填进 Home Assistant Recorder。
- 不提供远程 Shell、终端、命令执行、文件管理或 Agent 更新命令。
- 不提供增删节点、修改 Token、清理记录、数据库 SQL 或面板配置修改。
- 不根据 Komari 分组自动修改 Home Assistant Area；分组和区域是不同概念。
- 不为 API 没有提供的分磁盘、分网卡、容器或 SMART 数据创建虚假实体。

## 4. Home Assistant 官方模型下的目标架构

### 4.1 官方术语与 Komari 映射

[Home Assistant Config Entries 文档](https://developers.home-assistant.io/docs/config_entries_index/)将 Config Entry 定义为由用户通过 UI 创建并由 Home Assistant 持久保存的配置数据；Home Assistant 会对每个 Config Entry 分别调用 `async_setup_entry(hass, entry)`。本项目按以下方式映射：

因此，用户要求中的“每个面板作为单一的集成实例”，在本文和代码中统一采用官方表述：“每个 Komari 面板由一个 Config Entry 表示”。`Integration` 保留用于指整个 `komari` Integration Domain，不用来指某一个已配置面板。

| Home Assistant 对象 | 官方模型中的作用 | Komari 目标设计 |
| --- | --- | --- |
| Integration Domain | 一类集成的稳定 domain | 唯一 domain 为 `komari` |
| Integration Type | manifest 中描述集成主要形态 | 使用 `hub`，因为一个面板为多个节点提供网关和数据聚合 |
| Config Entry | 用户通过 Config Flow 创建并持久保存的一组配置 | 一个 Config Entry 只代表一个 Komari 面板 |
| Device | 物理设备或服务；由一个或多个 Entity 表示 | 一个面板 service Device，加上该面板的多个节点 Devices |
| Entity | 设备或服务的数据点 | 面板状态、节点在线状态和各项监控指标 |
| Device Registry | 保存设备身份、归属和拓扑 | 每个 Device 只归属一个 Config Entry |
| Entity Registry | 保存 Entity 的稳定身份与设备关联 | 每个 Entity 关联到面板 Device 或某个节点 Device |

[Home Assistant manifest 文档](https://developers.home-assistant.io/docs/creating_integration_manifest/)说明 `hub` 类型用于向多个设备或服务提供网关的集成，因此 Komari 应在 `manifest.json` 中明确声明：

```json
{
  "domain": "komari",
  "name": "Komari",
  "config_flow": true,
  "integration_type": "hub",
  "iot_class": "cloud_polling"
}
```

自定义集成发布时还必须包含符合 Home Assistant 要求的 `version` 字段。

### 4.2 多面板与多 Config Entry 结构

用户通过“设置 → 设备与服务 → 添加集成 → Komari”配置一个面板。需要监控第二个面板时，再次添加 Komari。每次成功配置都创建新的 Config Entry，不在一个 Config Entry 中保存面板列表，也不使用 Config Subentry 表示节点。

```text
Komari Integration Domain（komari）
├── Config Entry A：panel-a.example.com
│   └── 面板 A Device（DeviceEntryType.SERVICE）
│       ├── 面板 A Entities
│       │   ├── RPC 连接状态
│       │   ├── Komari 版本
│       │   └── 节点总数 / 在线数 / 离线数 / 任一节点故障
│       ├── 节点 A-1 Device
│       │   └── 核心指标 / 到期流量 / Ping / 动态指标 Entities
│       └── 节点 A-2 Device
│           └── 核心指标 / 到期流量 / Ping / 动态指标 Entities
└── Config Entry B：panel-b.example.com
    └── 面板 B Device（DeviceEntryType.SERVICE）
        ├── 面板 B Entities
        ├── 节点 B-1 Device
        └── 节点 B-2 Device
```

该结构的强制约束：

- 一个 Config Entry 恰好对应一个 Komari 面板。
- 一个 Config Entry 恰好创建一个面板 Device。
- 一个 Komari 节点恰好对应其所属 Config Entry 下的一个节点 Device。
- 面板数据只能创建在面板 Device 下，节点数据只能创建在对应节点 Device 下。
- 不创建跨面板共享 Device。即使两个面板返回相同节点 UUID，也分别表示为两个 Devices。
- 不把节点实现为 Config Entry 或 Config Subentry；节点是面板 Config Entry 动态发现并管理的 Devices。

### 4.3 Device 归属与父子拓扑

Home Assistant Core 2026.8 起，[每个 Device 只归属于一个 Config Entry](https://developers.home-assistant.io/blog/2026/07/21/device-registry-single-config-entry/)。因此目标实现必须满足：

1. `async_setup_entry` 先通过 Device Registry 为当前 Config Entry 注册面板 Device，并保存返回的 `device.id`。
2. 面板 Device 设置 `entry_type=DeviceEntryType.SERVICE`、`configuration_url`、`manufacturer="Komari"` 和 `sw_version`。
3. 节点 Device 使用普通 Device 类型，不设置 `DeviceEntryType.SERVICE`。
4. 节点 Device 通过 `via_device_id=<panel_device.id>` 指向当前 Config Entry 的面板 Device。
5. 新代码使用 `via_device_id`，不使用从 2026.8 开始弃用的 `via_device`。
6. 设备查找必须限定当前 `config_entry.entry_id`，使用 `async_get_device_by_identifier(identifier, entry.entry_id)`，避免跨 Config Entry 歧义。

本文以 Home Assistant Core 2026.8+ 的当前官方 API 为目标基线。若以后需要兼容更早版本，应在单独的兼容层处理旧 Device Registry API，不能把已弃用的 `via_device` 写成目标架构。

DeviceInfo 规划：

| 字段 | 面板 Device | 节点 Device |
| --- | --- | --- |
| Config Entry owner | 当前面板 `entry.entry_id` | 与面板相同的 `entry.entry_id` |
| `entry_type` | `DeviceEntryType.SERVICE` | 不设置 |
| `identifiers` | `(DOMAIN, f"{entry.entry_id}:panel")` | `(DOMAIN, f"{entry.entry_id}:node:{node_uuid}")` |
| `name` | Komari `sitename`，重复时 UI 的 Config Entry title 附加 host | Komari 节点名称 |
| `manufacturer` | `Komari` | `Komari` |
| `model` | `Monitoring Panel` | `Monitored Server` |
| `sw_version` | Komari 服务端版本 | Komari Agent 版本；匿名 API 不提供时留空 |
| `configuration_url` | 面板规范化根 URL | 面板规范化根 URL |
| `via_device_id` | 不设置 | 当前 Config Entry 的面板 `device.id` |

### 4.4 每 Config Entry 独立运行

每个 Config Entry 在 `entry.runtime_data` 中保存独立的运行时对象：

```text
KomariRuntimeData
├── api_client
├── coordinator
├── panel_device_id
├── capabilities
├── normalized_panel_url
└── node_cache
```

不得把某个面板的 API client、coordinator、节点缓存或认证状态存为 domain 级单例。这样可以保证：

- 面板 A 超时只会使面板 A 的 Entities unavailable。
- 面板 A 需要 Reauth 时不会触发面板 B 的 Reauth。
- 每个面板可以使用不同 API Key、TLS 验证设置、轮询周期、Ping 任务和动态指标选择。
- 卸载或删除 Config Entry A 时，只清理面板 A 的 listener、任务、Devices 和 Entities。

### 4.5 Config Entry、Device 与 Entity 标识

[Home Assistant Config Flow 文档](https://developers.home-assistant.io/docs/core/integration/config_flow/)明确将 URL 列为不适合作为 Config Entry `unique_id` 的来源。Komari 当前公开 API 没有提供稳定的面板实例 UUID，因此目标设计为：

- 不把面板 URL 设置为 Config Entry `unique_id`。
- 将规范化 URL 保存到 Config Entry data，并通过 `_async_abort_entries_match({CONF_URL: normalized_url})` 阻止同一面板被重复添加。
- 若 Komari 将来提供不可变的面板实例 ID，再通过 Config Entry migration 引入真正的 `unique_id`。
- Config Entry title 使用 Komari `sitename`；必要时附加 host 便于区分，但 title 不参与身份判断。
- 面板 Device identifier：`(DOMAIN, f"{entry.entry_id}:panel")`。
- 节点 Device identifier：`(DOMAIN, f"{entry.entry_id}:node:{node_uuid}")`。
- 面板 Entity unique ID：`f"{entry.entry_id}:panel:{entity_key}"`。
- 节点 Entity unique ID：`f"{entry.entry_id}:node:{node_uuid}:{entity_key}"`。
- Ping Entity 在末尾加入 task ID；动态指标 Entity 加入 metric key 和规范化标签键。

`entry.entry_id` 用于把设备和实体限定在一个 Config Entry 内，Komari 节点 UUID 用于保持节点在该面板内改名后的稳定身份。不能使用面板名称、节点名称、IP 或 URL 作为 Entity unique ID。

## 5. 实体功能规划

### 5.1 面板级实体

| 实体 | 平台 | 默认状态 | 说明 |
| --- | --- | --- | --- |
| RPC 可用 | `binary_sensor` / connectivity | 启用 | 表示 HA 到 Komari 面板的通信状态 |
| 任一节点故障 | `binary_sensor` / problem | 启用 | 任意可见节点离线时为 `on` |
| 节点总数 | `sensor` | 启用 | 当前可见节点数量 |
| 在线节点数 | `sensor` | 启用 | 当前在线数量 |
| 离线节点数 | `sensor` | 启用 | 适合告警和仪表盘 |
| Komari 版本 | `sensor` / diagnostic | 启用 | 同时写入面板 device `sw_version` |
| RPC 版本、构建哈希 | `sensor` / diagnostic | 默认禁用 | 主要用于诊断 |

### 5.2 每节点核心实体（MVP）

| 实体键 | HA 类型/单位 | 默认 | 数据来源及处理 |
| --- | --- | --- | --- |
| `online` | `binary_sensor`, connectivity | 启用 | `online`；数值实体在离线时标记 unavailable |
| `last_seen` | `sensor`, timestamp | 启用 | `time`；即使节点离线也保留最后采集时间 |
| `cpu_usage` | `%`, measurement | 启用 | `cpu` |
| `gpu_usage` | `%`, measurement | 有数据时启用 | `gpu`；无 GPU 或长期无效时不创建 |
| `memory_usage` | `%`, measurement | 启用 | `ram / ram_total * 100`，总量为 0 时 unavailable |
| `memory_used` | bytes, data size | 启用 | `ram` |
| `memory_total` | bytes, diagnostic | 默认禁用 | `ram_total` |
| `swap_usage` | `%`, measurement | 有交换空间时启用 | `swap / swap_total * 100` |
| `swap_used` | bytes | 默认禁用 | `swap` |
| `disk_usage` | `%`, measurement | 启用 | `disk / disk_total * 100` |
| `disk_used` | bytes | 启用 | `disk` |
| `disk_total` | bytes, diagnostic | 默认禁用 | `disk_total` |
| `load_1m` | measurement | 启用 | `load` |
| `load_5m` | measurement | 启用 | `load5` |
| `load_15m` | measurement | 启用 | `load15` |
| `temperature` | °C, temperature | 有有效值时启用 | `temp`；需处理 0、空值和异常范围 |
| `network_in` | B/s, data rate | 启用 | `net_in` |
| `network_out` | B/s, data rate | 启用 | `net_out` |
| `network_total_up` | bytes, total increasing | 启用 | `net_total_up`；允许 Agent 重启后的计数器复位 |
| `network_total_down` | bytes, total increasing | 启用 | `net_total_down` |
| `process_count` | measurement | 默认禁用 | `process` |
| `tcp_connections` | measurement | 默认禁用 | `connections` |
| `udp_connections` | measurement | 默认禁用 | `connections_udp` |

静态信息应优先放入 DeviceInfo 或诊断实体：节点名称、OS、内核、架构、虚拟化类型、CPU 型号、核心数、Agent 版本、区域、分组和标签。IP、私有备注和 Token 不应作为普通状态属性暴露。

### 5.3 到期与流量限额（目标功能）

认证后 `Client` 可能包含 `expired_at`、`traffic_limit`、`traffic_limit_type`、`billing_cycle`、`auto_renewal` 等字段。建议提供：

- 到期时间：timestamp sensor。
- 剩余天数：duration sensor。
- 流量限额：data size diagnostic sensor。
- 当前计费口径流量：依据 `sum/max/min/up/down` 从累计上传、下载计算。
- 流量使用率：percentage sensor，可用于 80%/90% 告警。
- 自动续费：diagnostic binary sensor。

价格和币种不是服务器健康指标，首版不创建实体；后续若有用户需求，应默认禁用并归类为 diagnostic。

### 5.4 Ping 任务（目标功能，按任务选择实体）

集成必须实现 Ping 任务发现、选择、数据获取和实体生命周期。由于任务数量与节点数量相乘可能产生大量实体，不默认创建所有组合；用户应在 Options Flow 中选择需要同步的任务：

- 最近延迟：duration，毫秒。
- 丢包率：percentage。
- 窗口最小/最大/平均/P95 延迟：measurement。
- Ping 状态：connectivity 或 problem binary sensor。

实体应归属节点设备，并在 unique ID 中加入 Ping task ID。共享任务自身可作为 diagnostic 属性，不必再创建一层设备。

### 5.5 动态/自定义指标（目标功能，按指标选择实体）

新版本 Komari 可通过 `public:listMetricDefinitions` 和 `public:queryMetrics`发现指标。目标实例目前返回 21 个定义，包括 `cpu.usage`、`memory.used`、`disk.used`、`net.in.rate`、`ping.latency_ms`、GPU 设备指标等。

实现原则：

- 集成必须实现指标定义发现、选择、查询、单位映射和实体增删；服务端不具备相关方法时才隐藏该能力。
- 内置核心指标继续使用稳定的 `common:getNodesLatestStatus`，避免重复实体。
- 仅显示 API 实际返回的指标定义。
- 用户在 Options Flow 中显式选择动态指标后再创建实体。
- 根据指标定义中的 `type` 和 `unit` 映射 device class、state class 和单位。
- 未识别单位的指标保持普通 measurement，不猜测单位。
- 标签维度可能导致实体爆炸，应设置总数上限并提供过滤。

## 6. 数据更新设计

根据 Home Assistant [Fetching data](https://developers.home-assistant.io/docs/integration_fetching_data/) 指南，使用一个 `DataUpdateCoordinator` 完成一次请求、更新全部节点实体。

这里的“一个 coordinator”是指每个 Config Entry 各有一个 coordinator。多个面板不得共用一次状态请求、更新成功标志或节点数据快照。Home Assistant 加载多个 Komari Config Entries 时，应并行且相互独立地运行多个 coordinator。

建议调度：

| 数据 | 默认周期 | 说明 |
| --- | --- | --- |
| 最新节点状态 | 30 秒 | Options 可设 15～300 秒；小于 15 秒不推荐 |
| 节点清单 | 10 分钟 | 也可在每 20 次状态更新后刷新一次 |
| 面板版本、公开设置、RPC 方法 | 6 小时或重载时 | 版本变化后重新探测能力 |
| Ping 聚合 | 5 分钟 | 只请求用户选择的 Ping 任务 |
| 自定义指标窗口统计 | 5～15 分钟 | 只请求用户选择的指标和标签 |

关键行为：

- 首次刷新失败时让 HA 按 `ConfigEntryNotReady` 机制重试。
- 网络、超时、5xx、JSON 解析错误转换为 `UpdateFailed`。
- API Key 失效或权限拒绝转换为 `ConfigEntryAuthFailed`，启动 Reauth Flow。
- 可选方法返回 `Method not found` 时只禁用对应能力，不让整个集成离线。
- 面板整体请求失败时，所有实体 unavailable；单个节点 `online=false` 时，连接实体保持可用并显示 off，其动态数值实体 unavailable，最后采集时间保持可用。
- 使用统一超时，建议连接 10 秒、总请求 20 秒，并复用 Home Assistant 注入的 `aiohttp.ClientSession`。
- 支持 `always_update=False` 的前提是规范化数据模型可正确比较，减少没有变化时的状态写入。

## 7. 配置与用户体验

### 7.1 Config Flow

每次从 Home Assistant UI 添加 Komari 都只处理一个面板，并创建一个新的 Config Entry。完成后，用户可以再次添加 Komari 来配置另一个面板。

首次配置字段：

1. 面板 URL，支持带反向代理 base path。
2. API Key，可留空使用公开访问。
3. 验证 TLS 证书，默认开启。

提交时依次验证：

1. URL 规范化和 scheme 合法性。
2. 调用 `rpc.ping`，确认 `/api/rpc2` 可用。
3. 调用 `public:getVersion` 和 `rpc.methods`。
4. 调用节点清单与最新状态方法。
5. 区分 cannot_connect、invalid_auth、unsupported_version、invalid_response 和 no_visible_nodes。
6. 将 URL 规范化为 scheme、host、有效端口和 base path，移除无意义尾部斜杠。
7. 使用 `_async_abort_entries_match({CONF_URL: normalized_url})` 检查已有 Config Entries；同一规范化 URL 已配置时以 `already_configured` 中止。
8. 不调用 `async_set_unique_id(normalized_url)`，因为官方规则不允许使用 URL 作为 Config Entry unique ID。
9. 以 `sitename` 或 `sitename (host)` 创建 Config Entry title。

匿名模式下允许“当前没有可见节点”，但需要清楚提示；私有站点应引导用户提供 API Key。

### 7.2 多 Config Entry 行为

- 支持同一 Integration Domain 下同时加载多个 Komari Config Entries。
- 不设置“只允许一个 Config Entry”的限制，也不使用仅限单实例的 discovery flow helper。
- 不同规范化 URL 可以分别使用匿名或 API Key 认证。
- 同一 URL 即使填写不同 API Key，也视为同一面板，不允许重复 Config Entry；更换 API Key 使用 Reauth。
- Reconfigure 修改 URL 时，需要检查目标 URL 是否已被另一个 Komari Config Entry 使用。
- 每个 Config Entry 的 Options 只作用于其面板，不允许修改其他面板的轮询周期或实体选择。
- UI 中各 Config Entry title 必须可区分；站点名称重复时附加 host。

### 7.3 Options Flow

建议选项：

- 实时状态更新周期。
- 到期/流量限额实体的启用策略；API 提供数据时默认启用到期、剩余天数和流量使用率。
- 需要同步的 Ping 任务。
- 需要同步的动态指标及标签过滤。
- 是否创建默认禁用的高级诊断实体。

面板 URL、API Key 和 TLS 验证属于连接必要参数，应使用当前 Config Entry 的 Reconfigure/Reauth Flow，而不是普通 Options Flow。

### 7.4 多语言和命名

- 至少提供英文和简体中文翻译。
- 使用 `translation_key` 和 `_attr_has_entity_name = True`。
- 实体名只表达指标，如“CPU 使用率”，设备名负责表达节点名称，避免生成“节点 A 节点 A CPU 使用率”。
- 不把节点名称写进 unique ID。
- 面板 Device 名称使用站点名称，节点 Device 名称使用 Komari 节点名称；名称变化只更新注册表展示信息。

## 8. 认证与安全

[Komari API 文档](https://komari-monitor.github.io/komari-document/dev/api.html)说明 API Key 使用 Bearer Authentication。规划应遵守以下原则：

- 优先允许匿名只读接入公开面板。
- 私有面板或隐藏节点使用 `Authorization: Bearer <API_KEY>`。
- 不支持保存用户名和密码并自动登录；Cookie 会话和 2FA 不适合长期机器集成。
- API Key 只存入 Config Entry data，不写日志、不放状态属性、不包含在 diagnostics。
- Diagnostics 还应脱敏 Cookie、Token、IP、私有备注、请求头和可能包含敏感信息的 RPC error data。
- TLS 验证默认开启。允许用户为自签名部署关闭校验，但在 UI 中给出风险提示，不能在代码中静默 `verify=False`。
- 当前 Komari API Key 权限可能高于 HA 所需范围。集成本身必须保持只读，并在文档中建议为 HA 使用独立 Key；未来若 Komari提供 scoped token，应迁移到 node/read、metrics/read、ping/read 等最小权限。
- 禁止把 `admin:*` 方法做成 HA service/action，即使用户提供的 Key 有权限。

## 9. 动态节点与生命周期

[Home Assistant stale-devices 规则](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/stale-devices/)要求中心系统删除设备后，集成能处理陈旧设备。

建议策略：

- 每个 Config Entry 只对自己的完整节点清单计算新增、保留和缺失节点。
- 新节点自动创建设备和核心实体，无需重载 Home Assistant。
- 不因一次缺失立即删除设备；连续三次“完整清单成功且节点缺失”后，才能确认删除。
- 认证失败、私有站点权限变化或清单请求失败时，不执行设备删除。
- 提供 `async_remove_config_entry_device`，让用户可以手动移除已经不在当前权威清单中的设备。
- 节点改名只更新设备名称，不创建新设备。
- 删除节点 Device 时必须限定其所属 Config Entry，不能删除另一个面板中 UUID 相同的节点 Device。
- 集成卸载时正确取消该 Config Entry 的 coordinator listener、关闭自建任务并卸载平台。
- 删除一个面板 Config Entry 后，该面板 Device、其节点 Devices 和所有相关 Entities 被清理，其他面板 Config Entries 保持加载。

## 10. 历史数据策略

Home Assistant Recorder 会从集成启用后开始保存实体状态。Komari 自身已经保存更高频、保留期可控并可降采样的历史数据，因此不应把历史点批量写入 Recorder。

允许的历史使用方式：

- 计算并暴露少量窗口结果，例如 1 小时 CPU 平均值、24 小时最大磁盘使用率、Ping P95 和丢包率。
- 窗口实体必须低频更新、默认禁用或由用户显式启用。
- 优先调用服务端聚合/降采样能力，并限制 `max_points`。
- 如果 API 不支持某个窗口或指标，不在 HA 端下载海量原始点后自行长期聚合。

## 11. 推荐代码结构

```text
custom_components/komari/
├── __init__.py
├── api.py                 # 异步 JSON-RPC2 客户端、认证、异常映射
├── binary_sensor.py       # 面板/节点连接状态、全局问题状态
├── config_flow.py         # user、reauth、reconfigure、options
├── const.py
├── coordinator.py         # 状态、清单和低频能力刷新
├── data.py                # KomariRuntimeData 与 typed ConfigEntry
├── diagnostics.py
├── entity.py              # KomariEntity 公共基类
├── manifest.json
├── models.py              # 规范化 dataclass，隔离 API 版本差异
├── sensor.py
├── icons.json
└── translations/
    ├── en.json
    └── zh-Hans.json

tests/
└── components/komari/
    ├── conftest.py
    ├── fixtures/
    ├── test_api.py
    ├── test_config_flow.py
    ├── test_coordinator.py
    ├── test_diagnostics.py
    ├── test_init.py
    └── test_sensor.py

hacs.json
README.md
```

自 Home Assistant 2026.8 的官方自定义集成本地化规则起，自定义集成不使用 Core 构建流程专用的 `strings.json`；英文与中文文本应直接完整写入 `translations/en.json` 和 `translations/zh-Hans.json`。

首个 HACS 版本可以暂时把 API 客户端放在 `api.py`。若以后提交 Home Assistant Core，应将通信层提取为独立、全异步、严格类型化的 PyPI 库，并允许注入 Home Assistant 的 ClientSession；这符合 Core 对依赖透明度和 Platinum 异步实现的方向。

运行时类型应明确表达“一条 Config Entry 对应一个面板运行时”：

```python
@dataclass
class KomariRuntimeData:
    api: KomariApiClient
    coordinator: KomariDataUpdateCoordinator
    panel_device_id: str
    capabilities: frozenset[str]

type KomariConfigEntry = ConfigEntry[KomariRuntimeData]
```

`hass.data[DOMAIN]` 如有使用，只能保存真正的 domain 级公共资源，不能把某一个面板的 API client 或 coordinator 放在这里供其他 Config Entries 共用。

## 12. API 与 Home Assistant 兼容策略

1. 设定最低支持 Komari `1.0.7`，因为更早版本没有 RPC2。
2. 配置时调用 `rpc.methods` 建立 capability set；能力判断优先于硬编码版本范围。
3. 对 `common:getNodes` 同时接受 UUID 映射和旧数组，统一转换为 `dict[str, Node]`。
4. 优先使用 `public:*` 获取脱敏公开数据；提供 API Key 时再选择 `common:*` 补充私有节点和元数据。
5. 动态指标、Ping 聚合等方法不存在时隐藏相关 Options，不报全局错误。
6. 对未知字段忽略并保留向前兼容；对核心必需字段进行显式校验。
7. 不依赖 Komari 前端主题路由构造节点详情链接；`configuration_url` 首版只指向面板根地址。
8. 不依赖旧 `/api/clients` WebSocket。REST 兼容层只作为后续需求，不应与 RPC2 主路径同时维护两套完整逻辑。
9. Home Assistant 目标基线为 Core 2026.8+，遵守 Device 只归属一个 Config Entry 的规则，并使用 `via_device_id`。
10. 如果未来降低 HA 最低版本，旧 Device Registry API 适配必须集中封装，并配置迁移测试；业务模型仍保持一个面板 Config Entry、一个面板 Device 和多个节点 Devices。

## 13. 故障处理与诊断

Diagnostics 建议包含：

- 集成版本、HA 版本、Komari 版本、RPC 版本。
- 已发现方法名列表或已启用 capability 列表。
- 面板 URL 的脱敏形式、TLS 验证状态、认证模式（anonymous/API key），不含 Key。
- 节点总数、在线/离线数、各能力最近成功时间。
- 最近一次错误类别、HTTP 状态和 JSON-RPC error code；`error.data` 必须脱敏。
- coordinator 更新时间、轮询周期和启用的高级功能。

日志原则：面板不可用时记录一次，恢复时记录一次；不能每 30 秒刷同样的错误。节点离线属于监控状态，不应作为 Python error 日志持续输出。

## 14. 测试与验收标准

### 14.1 自动化测试

- 匿名和 API Key Config Flow 成功路径。
- 两个不同面板 URL 可以创建并同时加载两个 Config Entries。
- 相同规范化 URL 第二次添加时由 `_async_abort_entries_match` 中止，且 URL 不被设置为 Config Entry unique ID。
- URL、TLS、超时、DNS、401/403、RPC method not found、无效 JSON。
- API Key 失效触发 Reauth，更新后不创建重复 Config Entry。
- 面板 A 认证失败、超时或 unload 时，面板 B 的 coordinator、Devices 和 Entities 不受影响。
- 节点映射/数组两种返回格式。
- 每个 Config Entry 的单次 coordinator 请求只驱动该面板的全部实体。
- 每个 Config Entry 恰好创建一个 `DeviceEntryType.SERVICE` 面板 Device。
- 面板 Entities 全部关联面板 Device，节点 Entities 全部关联对应节点 Device。
- 节点 Device 的 `via_device_id` 指向同一 Config Entry 的面板 Device。
- 两个面板返回相同节点 UUID 时创建两个分别归属于各自 Config Entry 的节点 Devices。
- 新增节点自动创建设备与实体。
- 节点改名保持 unique ID。
- 节点临时缺失不删除，确认删除后清理陈旧设备。
- 节点离线时 connectivity 与数值实体的 availability 行为。
- 单位、百分比、除零、计数器复位、无 GPU/无 swap/无温度。
- Diagnostics 脱敏 API Key、Cookie、IP 和备注。
- 翻译、卸载、重载、Options、Reconfigure 和 Reauth。

### 14.2 手工验收

- 公开面板匿名添加成功。
- 私有面板 API Key 添加成功。
- 至少同时添加两个面板，并在“设备与服务”中显示两个 Komari Config Entries。
- 每个 Config Entry 下只显示一个面板 Device 及其自己的节点 Devices。
- 删除或重载其中一个 Config Entry 不影响另一个面板继续更新。
- HTTP、可信 HTTPS、自签名 HTTPS 三种场景。
- 至少 1、20、100 个节点的响应时间和实体数量测试。
- 断网、反向代理重启、Komari 重启后自动恢复。
- 节点离线/恢复能触发 HA 自动化。
- 新增、改名、删除节点后设备注册表行为正确。
- HA 重启后无重复设备或实体。

静态测试和 mock API 测试不能替代真实 Komari/HA 运行验证；发布前至少要完成一个公开实例和一个需要认证的私有实例测试。

## 15. 分阶段路线图

### P0：通信与模型验证

- 异步 RPC2 client。
- `rpc.ping`、版本、方法发现、节点清单、最新状态。
- 规范化 `Node` / `NodeStatus` 数据模型和异常分类。
- 验证每 Config Entry 独立 API client/coordinator 的并行加载模型。
- 使用当前目标实例制作脱敏 fixture。

### P1：可发布 MVP

- UI Config Flow、匿名/API Key、TLS 校验。
- 支持重复添加 Komari；一个 Config Entry 对应一个面板。
- 每个 Config Entry 创建一个 `DeviceEntryType.SERVICE` 面板 Device 和动态节点 Devices。
- 使用 `via_device_id` 建立节点 Device 到面板 Device 的拓扑。
- 多面板运行时、认证、更新与卸载相互隔离。
- 面板汇总实体、节点 connectivity 和全部核心资源传感器。
- 30 秒 DataUpdateCoordinator。
- 中英文翻译、README、HACS 元数据。
- 基础单元测试、卸载和异常恢复。

P1～P3 共同构成完整目标范围；分阶段仅表示实施顺序，不表示 P2/P3 功能属于规划外增强。

### P2：稳定性、到期流量与 Ping

- Reauth、Reconfigure、Options Flow。
- Diagnostics、Repairs、低噪声日志。
- 节点动态增加和安全的陈旧设备删除。
- 到期时间、流量限额和用户选择的 Ping 任务。
- 完整测试覆盖，按 Silver/Gold 规则校验。

### P3：动态/自定义指标与生态

- 动态 Metric Definition 和用户选择的自定义指标。
- 1 小时/24 小时窗口统计与服务端降采样。
- 大规模节点性能测试和请求限流。
- 独立 Python API 库、严格类型化，评估提交 Home Assistant Core。

## 16. 完成定义

### 16.1 基础可用里程碑

基础版本只有满足以下条件才可用于早期测试：

- 用户可以仅输入 URL，或输入 URL + API Key，通过 UI 添加一个面板 Config Entry，并能重复添加其他面板。
- 同时配置两个不同面板时，Home Assistant 创建两个 Komari Config Entries；相同规范化 URL 不能重复添加。
- 每个 Config Entry 正确且只创建一个面板 service Device，并创建该面板的所有可见节点 Devices。
- 面板 Entities 与节点 Entities 分别归属正确 Device；节点 Device 的 `via_device_id` 指向本 Config Entry 的面板 Device。
- 每个 Device 只归属于一个 Config Entry；相同节点 UUID 出现在不同面板时不合并 Device。
- 节点在线、CPU、内存、磁盘、负载、网络和最后采集时间稳定更新。
- 节点离线、面板不可达和认证失败在 HA 中表现不同且可恢复。
- 一个面板不可达、Reauth、Reload、Unload 或 Remove 时，不影响其他面板继续工作。
- 所有实体拥有稳定 unique ID、正确单位、device class/state class 和翻译。
- API Key、IP、Token、Cookie 和私有备注不会出现在日志或 diagnostics。
- 节点新增、改名、删除不会制造重复或永久陈旧实体。
- 自动化测试通过，并在真实 Komari 和真实 Home Assistant 实例完成运行验证。

### 16.2 完整目标完成定义

完整目标除满足基础里程碑外，还必须满足：

- 每个面板分别保存和应用自己的轮询周期、到期流量选项、Ping 任务和动态指标选择。
- 到期时间、剩余天数、流量限额、计费口径流量和流量使用率能够按 API 数据正确创建、更新和移除。
- `traffic_limit_type` 的 `sum/max/min/up/down` 五种计算口径均有自动化测试。
- 能发现 Ping 任务，用户可以选择任务，并获得节点级延迟、丢包及支持的窗口统计实体。
- Ping 任务新增、改名、删除或取消选择后，不产生重复和永久陈旧实体。
- 能发现动态指标定义，用户可以选择指标和标签，且单位、类型、可用性和 unique ID 映射稳定。
- 动态指标与内置核心指标正确去重，并限制实体总量、查询点数和请求频率。
- Komari 旧版本缺少 Ping 聚合或动态指标方法时，核心监控仍可运行，并在 Options/Diagnostics 中明确显示能力不可用。
- 到期、流量、Ping 和动态指标均完成真实 Komari/HA 联调，而不只通过 fixture 或 mock 验证。

## 17. 最终建议

实施应先完成“一个面板、多个节点、当前状态、可靠自动化”这条最短闭环，再完成其余目标功能。优先级顺序为：

1. 稳定身份与设备拓扑。
2. 在线状态和核心资源实体。
3. 认证、恢复、动态节点和诊断。
4. 到期/流量和 Ping。
5. 动态指标与历史窗口。

上述五项都属于完整目标范围。按此顺序实现，既能尽早形成可运行版本，也能避免一开始陷入 Komari 管理 API、海量历史数据和远程控制的安全与维护成本。

## 参考资料

- [Komari RPC2 接口](https://komari-monitor.github.io/komari-document/dev/rpc)
- [Komari API 接口](https://komari-monitor.github.io/komari-document/dev/api.html)
- [Komari GitHub 仓库](https://github.com/komari-monitor/komari)
- [Home Assistant：Integration Quality Scale](https://developers.home-assistant.io/docs/core/integration-quality-scale/)
- [Home Assistant：Config entries](https://developers.home-assistant.io/docs/config_entries_index/)
- [Home Assistant：Fetching data](https://developers.home-assistant.io/docs/integration_fetching_data/)
- [Home Assistant：Device registry](https://developers.home-assistant.io/docs/device_registry_index/)
- [Home Assistant：Config flow](https://developers.home-assistant.io/docs/core/integration/config_flow/)
- [Home Assistant：Integration manifest 与 integration_type](https://developers.home-assistant.io/docs/creating_integration_manifest/)
- [Home Assistant Core 2026.8：Device 归属于单一 Config Entry](https://developers.home-assistant.io/blog/2026/07/21/device-registry-single-config-entry/)
- [Home Assistant：Integration file structure](https://developers.home-assistant.io/docs/creating_integration_file_structure/)
- [Home Assistant：Glances](https://www.home-assistant.io/integrations/glances/)
- [Home Assistant：Uptime Kuma](https://www.home-assistant.io/integrations/uptime_kuma/)
- [Home Assistant Core：Uptime Kuma 源码](https://github.com/home-assistant/core/tree/dev/homeassistant/components/uptime_kuma)
- [Home Assistant Core：UniFi 源码](https://github.com/home-assistant/core/tree/dev/homeassistant/components/unifi)
- [Beszel 官方文档](https://beszel.dev/)
- [哪吒监控 V2 API](https://nezha.wiki/en_US/guide/api)
- [Netdata API](https://learn.netdata.cloud/api)
