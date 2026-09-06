# Komari Home Assistant 集成代码实现方案

> 文档状态：实施规划稿  
> 编写日期：2026-08-26  
> 配套文档：[KOMARI_HOME_ASSISTANT_INTEGRATION_PLAN.md](KOMARI_HOME_ASSISTANT_INTEGRATION_PLAN.md)  
> 目标基线：Home Assistant Core 2026.8+、Komari 1.0.7+（RPC2）

## 1. 文档目的

本文件把功能调研报告拆成可执行的代码任务，只描述实现方法，不再重复产品需求。

集成必须遵守以下固定结构：

- 一个 Home Assistant Integration Domain：`komari`。
- 用户每执行一次“添加集成”，只添加一个 Komari 面板，生成一个 Config Entry。
- 用户可重复添加 Komari，从而同时监控多个面板。
- 每个 Config Entry 拥有独立的 API Client、认证信息、DataUpdateCoordinator、Options 和错误状态。
- 每个 Config Entry 创建一个 `DeviceEntryType.SERVICE` 面板 Device。
- 面板下的每个 Komari 节点创建一个独立 Device，并通过 `via_device_id` 指向本 Config Entry 的面板 Device。
- 面板实体只属于面板 Device；节点实体只属于对应节点 Device。

本文将功能分为 F01～F23。每个单元列出目标、代码位置、实现流程、边界条件、测试和完成标准，可据此拆分 Issue、提交和开发阶段。

## 2. 实施原则与验证边界

### 2.1 官方模型约束

Home Assistant 官方术语在本项目中的含义如下：

| 官方对象 | 本集成中的实现 |
| --- | --- |
| Integration | `komari` 集成本身 |
| Config Entry | 一个 Komari 面板的配置和运行时入口 |
| Device | 一个面板服务，或面板下的一个服务器节点 |
| Entity | 面板或节点的单个状态数据点 |
| Device Registry | 保存面板、节点、Config Entry 归属及父子拓扑 |
| Entity Registry | 保存实体稳定身份、启用状态和设备关联 |
| DataUpdateCoordinator | 每个面板共享一次拉取结果并更新其全部实体 |

目标版本 Home Assistant Core 2026.8+ 要求一个 Device 只属于一个 Config Entry。因此节点与面板必须使用包含 `entry.entry_id` 的集成侧标识，不能把多个面板中 UUID 相同的节点合并成同一设备。

### 2.2 证据等级

实现过程中分别记录以下验证等级，不互相替代：

1. 静态实现：代码和类型检查通过。
2. 单元测试：fixture、mock 和注册表断言通过。
3. Home Assistant 测试实例：Config Flow、设备、实体、重载和诊断可用。
4. Komari 测试面板：真实 RPC2 联调通过。
5. 多面板长期运行：断网、认证过期、节点增删和 HA 重启行为通过。

对 `monitor.nekokax.com` 的匿名只读测试只能证明其当时暴露的公开 RPC2 能力，不能代替私有面板、API Key 和不同 Komari 版本的验收。

### 2.3 首版技术取舍

- 使用 RPC2 HTTP POST，不使用旧 `/api/clients` WebSocket。
- RPC2 WebSocket 只是另一种传输方式；在没有服务端订阅推送协议的前提下，不为“实时”引入长连接。
- 使用 Home Assistant 提供的共享 `aiohttp.ClientSession`，禁止同步 HTTP 和每次请求新建 Session。
- API 原始结构只能进入 `api.py`/`models.py`，实体层只读取规范化模型。
- 每个轮询周期批量获取全部节点状态，禁止每个实体或每个节点独立请求。
- 未文档化、未通过 fixture 或真实响应验证的字段不得直接成为实体。

## 3. 目标目录结构

```text
custom_components/komari/
├── __init__.py
├── api.py
├── binary_sensor.py
├── config_flow.py
├── const.py
├── coordinator.py
├── data.py
├── diagnostics.py
├── entity.py
├── exceptions.py
├── manifest.json
├── models.py
├── sensor.py
├── services.yaml                 # 只有实际增加服务后再创建
├── icons.json
└── translations/
    ├── en.json
    └── zh-Hans.json

tests/components/komari/
├── __init__.py
├── conftest.py
├── fixtures/
│   ├── rpc_methods.json
│   ├── public_settings.json
│   ├── nodes_public.json
│   ├── nodes_private_map.json
│   ├── nodes_private_array.json
│   ├── latest_status.json
│   ├── ping_tasks.json
│   ├── ping_stats.json
│   ├── metric_definitions.json
│   └── metric_query.json
├── test_api.py
├── test_binary_sensor.py
├── test_config_flow.py
├── test_coordinator.py
├── test_diagnostics.py
├── test_entity_lifecycle.py
├── test_init.py
├── test_migration.py
├── test_multi_entry.py
├── test_options_flow.py
└── test_sensor.py

hacs.json
README.md
LICENSE
pyproject.toml
```

如果后续提交 Home Assistant Core，通信层应提取为独立、全异步、严格类型化的 PyPI 库；自定义集成阶段可以先保留在 `api.py` 和 `models.py` 中。

## 4. 核心数据模型

实体和协调器都依赖统一快照。先固定下列模型，再编写平台代码：

```python
@dataclass(frozen=True, slots=True)
class KomariPanelInfo:
    name: str
    version: str | None
    build_hash: str | None
    rpc_version: str | None
    private: bool | None

@dataclass(frozen=True, slots=True)
class KomariNode:
    uuid: str
    name: str
    os: str | None
    arch: str | None
    region: str | None
    agent_version: str | None
    expired_at: datetime | None
    traffic_limit: int | None
    traffic_limit_type: TrafficLimitType | None
    billing_cycle: str | None
    auto_renewal: bool | None

@dataclass(frozen=True, slots=True)
class KomariNodeStatus:
    node_uuid: str
    online: bool
    observed_at: datetime | None
    cpu_usage: float | None
    gpu_usage: float | None
    memory_used: int | None
    memory_total: int | None
    swap_used: int | None
    swap_total: int | None
    disk_used: int | None
    disk_total: int | None
    load_1m: float | None
    load_5m: float | None
    load_15m: float | None
    temperature: float | None
    network_in_rate: float | None
    network_out_rate: float | None
    network_total_up: int | None
    network_total_down: int | None
    process_count: int | None
    tcp_connections: int | None
    udp_connections: int | None

@dataclass(frozen=True, slots=True)
class KomariSnapshot:
    panel: KomariPanelInfo
    nodes: Mapping[str, KomariNode]
    statuses: Mapping[str, KomariNodeStatus]
    ping_tasks: Mapping[str, KomariPingTask]
    ping_stats: Mapping[PingSeriesKey, KomariPingStats]
    metric_definitions: Mapping[str, KomariMetricDefinition]
    metric_values: Mapping[MetricSeriesKey, KomariMetricValue]
    capabilities: frozenset[str]
    refreshed_at: datetime
```

快照使用不可变 dataclass 和只读映射，保证一次 coordinator 刷新期间所有实体读取的是同一批数据。原始 JSON 不保存在实体属性中。

运行时类型：

```python
@dataclass(slots=True)
class KomariRuntimeData:
    api: KomariApiClient
    coordinator: KomariDataUpdateCoordinator
    panel_device_id: str
    capabilities: frozenset[str]

type KomariConfigEntry = ConfigEntry[KomariRuntimeData]
```

## 5. 构建依赖和阶段路径

```text
F01 工程骨架
 ├─ F02 常量、模型、异常
 │   └─ F03 RPC2 Client
 │       └─ F04 能力发现与兼容层
 │           ├─ F05 Config Flow
 │           └─ F06 Config Entry 运行时
 │               ├─ F07 面板设备
 │               ├─ F08 节点设备与动态发现
 │               └─ F09 实体公共基类
 │                   ├─ F10 面板实体
 │                   ├─ F11 节点连接与最后采集
 │                   ├─ F12 资源指标
 │                   ├─ F13 网络与计数指标
 │                   ├─ F14 到期与流量限额
 │                   ├─ F15 Ping 任务
 │                   └─ F16 动态/自定义指标
 ├─ F17 调度与快照一致性
 ├─ F18 Reauth、Reconfigure 与 Options
 ├─ F19 生命周期、迁移和陈旧对象清理
 ├─ F20 诊断、日志与 Repairs
 ├─ F21 翻译、图标和文档
 ├─ F22 自动化及真实环境测试
 └─ F23 HACS 打包、发布与 Core 迁移准备
```

阶段划分：

| 阶段 | 功能 | 阶段出口 |
| --- | --- | --- |
| P0 通信基础 | F01～F04 | 能探测面板并得到规范化节点快照 |
| P1 多面板 MVP | F05～F13、F17 基础部分 | 两个面板可同时添加；面板与节点设备、核心实体稳定工作 |
| P2 可维护性与目标功能 | F14、F15、F18～F21 | 到期、流量、Ping、重认证、诊断和动态清理完成 |
| P3 动态指标 | F16、F17 完整部分 | 可选择动态指标，单位和标签身份稳定，实体数量受控 |
| P4 发布验收 | F22～F23 | 自动化测试、真实面板矩阵、HACS 安装和升级通过 |

P1～P3 都属于目标功能，阶段划分只表示依赖顺序。

## 6. F01：集成工程骨架

### 目标

创建 Home Assistant 能识别、加载和卸载的最小自定义集成，并固定 Domain 与平台列表。

### 代码落点

- `manifest.json`
- `__init__.py`
- `const.py`
- `translations/en.json`
- `translations/zh-Hans.json`
- `hacs.json`

### 实现方案

`manifest.json` 至少包含：

```json
{
  "domain": "komari",
  "name": "Komari",
  "codeowners": [],
  "config_flow": true,
  "documentation": "https://github.com/<owner>/<repo>",
  "integration_type": "hub",
  "iot_class": "cloud_polling",
  "requirements": [],
  "version": "0.1.0"
}
```

`const.py` 固定 `DOMAIN`、`PLATFORMS`、默认轮询周期、URL/API Key/TLS 配置键、Options 键、最低 Komari 版本和实体上限。所有字符串键集中定义，禁止在各平台散落字面量。自定义集成不使用 Core 构建流程专用的 `strings.json`；英文与中文文本直接完整写入各自翻译文件。

`__init__.py` 先实现 `async_setup`、`async_setup_entry`、`async_unload_entry` 的空框架；真正运行时接线在 F06 完成。

### 边界条件

- 自定义集成必须有 `version`；迁入 Core 后移除自定义版本字段并按 Core 规则维护 manifest。
- 不预先声明未使用的依赖和服务。
- `iot_class` 表达 Home Assistant 主动轮询远端服务，不把 WebSocket 传输误写成 `cloud_push`。

### 测试

- manifest/hassfest 校验。
- 测试 Domain 可加载，卸载不残留任务。
- 中英文翻译 JSON 可解析。

### 完成标准

Home Assistant 开发实例能发现 Komari 集成，且基础测试无 manifest、翻译和异步阻塞错误。

## 7. F02：常量、规范化模型与异常体系

### 目标

把 Komari JSON 变化与 Home Assistant 实体代码隔离，形成明确的类型、枚举和错误分类。

### 代码落点

- `const.py`
- `models.py`
- `exceptions.py`
- `tests/components/komari/test_api.py`

### 实现方案

定义异常层级：

```text
KomariError
├── KomariConnectionError       # DNS、连接、超时、TLS、HTTP 5xx
├── KomariProtocolError         # 非 JSON、结构错误、ID 不匹配
├── KomariRpcError              # JSON-RPC error
│   ├── KomariMethodNotFound
│   └── KomariPermissionError
├── KomariAuthenticationError   # API Key 无效或登录状态失效
└── KomariUnsupportedError      # 无 RPC2 或版本/能力不满足
```

定义 `TrafficLimitType(StrEnum)`，只接受 `sum`、`max`、`min`、`up`、`down`；未知值解析为 `None` 并记录一次 debug 日志，不阻断核心监控。

所有时间字段统一转换为带时区 UTC `datetime`。解析器需支持接口中实际出现的 Unix 秒、Unix 毫秒或 RFC 3339 格式，但每种格式必须有 fixture。禁止根据数值“猜测”后悄悄接受明显错误日期；超出合理范围时返回 `None`。

所有容量在模型层统一为字节，速率统一为字节/秒，百分比统一为 0～100。模型层只做确定性转换，不替实体层决定展示精度。

节点清单解析同时兼容：

- 以 UUID 为键的对象映射。
- 节点对象数组。
- 公开接口与认证接口字段子集不同。

### 边界条件

- 未知字段忽略，保证向前兼容。
- 核心字段缺失时只丢弃对应节点/状态记录；结构整体不可识别时抛 `KomariProtocolError`。
- 节点名称、备注和 IP 不进入日志异常消息。
- `0` 可能是有效监控值；只有文档规定的空值、非法范围和缺失才转为 `None`。

### 测试

- 数组/映射节点清单得到相同规范化结果。
- 时间、负数、零容量、未知限额类型和新增字段测试。
- API 原始字典修改不会改变已生成的不可变模型。

### 完成标准

后续代码不再直接使用原始字段名，所有错误都能被 Config Flow 或 Coordinator 准确分类。

## 8. F03：异步 JSON-RPC2 Client

### 目标

实现可注入 Home Assistant Session、可测试、无阻塞的 RPC2 HTTP POST 客户端。

### 代码落点

- `api.py`
- `exceptions.py`
- `tests/components/komari/test_api.py`

### 类与方法

```python
class KomariApiClient:
    async def async_call(self, method: str, params: object | None = None) -> Any: ...
    async def async_ping(self) -> None: ...
    async def async_get_rpc_version(self) -> str | None: ...
    async def async_get_methods(self) -> frozenset[str]: ...
    async def async_get_panel_info(self) -> KomariPanelInfo: ...
    async def async_get_nodes(self) -> Mapping[str, KomariNode]: ...
    async def async_get_latest_status(self) -> Mapping[str, KomariNodeStatus]: ...
    async def async_get_ping_tasks(self) -> Mapping[str, KomariPingTask]: ...
    async def async_get_ping_stats(...) -> Mapping[PingSeriesKey, KomariPingStats]: ...
    async def async_get_metric_definitions(...) -> Mapping[str, KomariMetricDefinition]: ...
    async def async_query_metrics(...) -> Mapping[MetricSeriesKey, KomariMetricValue]: ...
```

### 请求流程

1. Config Entry URL 规范化为面板根 URL，再用固定路径 `/api/rpc2` 构造 endpoint。
2. 使用 `async_get_clientsession(hass, verify_ssl=...)` 获取 HA 管理的 Session，不在 Client 关闭它。
3. 每次调用生成递增或 UUID 请求 ID，发送 JSON-RPC 2.0 对象。
4. API Key 只放入 Komari 文档要求的认证 Header，不写 URL、日志或 diagnostics。
5. 校验 HTTP 状态、JSON Content、`jsonrpc`、响应 ID、`result/error` 互斥关系。
6. 将远端错误码和 HTTP 状态映射到 F02 的异常类型。

总超时设为 20 秒，连接超时设为 10 秒，并通过构造参数注入以便测试。Client 内增加 `asyncio.Lock` 或小型 Semaphore，防止 Options 刷新、手动重载和 Coordinator 同时向面板发起大量请求。

### 边界条件

- 301/302 跳转后若主机变化，不把 API Key 自动发送到新主机；配置阶段应拒绝跨主机认证重定向。
- HTTP 401/403 或 RPC 权限错误映射为认证错误；匿名访问遇到私有站点时，Config Flow 提示需要 API Key。
- `Method not found` 映射为能力缺失，不判定面板整体离线。
- 不使用 JSON-RPC batch，除非 Komari 文档和真实响应都验证支持。
- 不重试非幂等调用；本集成当前只计划只读方法。

### 测试

- 成功、超时、TLS、5xx、401、非 JSON、ID 不匹配、RPC error。
- 断言 API Key 未出现在请求 URL、日志和异常文本。
- 多次并发调用受并发上限约束。
- Client 不关闭 HA 共享 Session。

### 完成标准

所有后续网络调用只通过该 Client，API 测试覆盖成功路径和每类异常映射。

## 9. F04：能力发现、版本兼容与调用策略

### 目标

通过实际可用 RPC 方法决定功能，而不是只依赖 Komari 版本字符串。

### 代码落点

- `api.py`
- `coordinator.py`
- `models.py`
- `tests/components/komari/test_api.py`

### 实现方案

配置阶段依次调用：

1. `rpc.ping`
2. `rpc.version`
3. `rpc.methods`
4. `public:getVersion`，必要时回退到可用的 `common:getVersion`
5. `public:getPublicSettings`
6. 节点清单与最新状态接口

把 `rpc.methods` 结果保存为 `frozenset[str]`，通过命名常量判断能力：

```python
supports_ping_tasks = {
    "public:getPublicPingTasks",
    "public:getPingRecords",
}.issubset(methods)

supports_ping_stats = "public:getPingMetricStats" in methods
supports_dynamic_metrics = {
    "public:listMetricDefinitions",
    "public:queryMetrics",
}.issubset(methods)
```

节点清单选择策略：

- 有 API Key 且 `common:getNodes` 可用：优先认证接口，以读取完整节点元数据。
- 否则使用 `public:getNodesInformation`。
- 最新状态优先使用 `common:getNodesLatestStatus`；如果匿名权限不允许，必须根据真实方法列表和响应决定是否有公开替代接口，不能臆造方法名。

Ping 聚合优先 `public:getPingMetricStats`，缺失时回退到 `public:getPingRecords` 并在本地只计算有限窗口。动态指标方法缺失时隐藏 Options 中相关字段，核心状态仍正常运行。

### 边界条件

- 最低目标 Komari 1.0.7；即使版本满足，仍以方法能力为准。
- `rpc.methods` 暂时失败时，首次配置失败；已配置运行时保留上次能力并在下次低频刷新重试。
- 方法在升级后新增：下次能力刷新后出现相应 Options。
- 方法在降级后消失：可选功能转为不可用并产生诊断信息，核心实体不离线。

### 测试

- 完整能力、仅核心能力、缺 Ping 聚合、缺动态指标、无 RPC2。
- API Key/匿名两种清单选择。
- 版本较新但缺方法、版本未知但方法完整。

### 完成标准

每项可选功能都有明确 capability gate，未知版本不会导致未捕获异常。

## 10. F05：Config Flow 与多面板添加

### 目标

实现“一次添加一个面板，重复添加支持多个面板”的 UI 配置流程。

### 代码落点

- `config_flow.py`
- `const.py`
- `translations/*`
- `tests/components/komari/test_config_flow.py`
- `tests/components/komari/test_multi_entry.py`

### 表单字段

- `url`：必填，面板根 URL。
- `api_key`：选填，私有站点或需要完整元数据时使用。
- `verify_ssl`：默认开启。

### URL 规范化

建立纯函数 `normalize_panel_url()`：

- 只接受 `http`/`https`。
- 主机名小写，移除 fragment、query 和尾部 `/`。
- 保留明确端口和确有需要的子路径。
- 用户若输入 `/api/rpc2`，转换回面板根 URL。
- 禁止 URL 中的用户名和密码。

规范化后使用 `_async_abort_entries_match({CONF_URL: normalized_url})` 阻止同一面板重复配置。按照 Home Assistant Config Flow 规则，URL 不作为 Config Entry `unique_id`；URL 只用于匹配当前已有条目。

### 校验流程

1. 本地格式校验。
2. 创建临时 Client。
3. 执行 F04 的能力与最小数据校验。
4. 私有站点无 Key 时返回 `api_key_required`。
5. Key 无效返回 `invalid_auth`。
6. 连接失败返回 `cannot_connect`。
7. 不支持 RPC2 返回 `unsupported_version`。
8. 成功后创建 Entry，标题优先站点名称，否则使用主机名。

Entry `data` 只保存连接必需字段；用户可调参数进入 `options`。API Key 由 Config Entry 存储机制保存，但任何显示、日志和 diagnostics 必须脱敏。

### 多面板关键测试

- 面板 A 与 B 可分别创建两个 Config Entries。
- 同一 URL 的大小写、尾斜杠和 `/api/rpc2` 变体被识别为重复。
- 不同端口或不同有效子路径不误判为重复。
- 两个面板存在相同节点 UUID 时，后续仍形成两个独立 Devices。

### 完成标准

UI 可分别添加公开面板和 API Key 面板；重复 URL 被阻止，不同面板互不覆盖。

## 11. F06：Config Entry 运行时装配与卸载

### 目标

为每个 Config Entry 创建完全独立的运行时对象，并保证失败、重试、重载和卸载行为符合 Home Assistant 规范。

### 代码落点

- `__init__.py`
- `data.py`
- `coordinator.py`
- `tests/components/komari/test_init.py`
- `tests/components/komari/test_multi_entry.py`

### `async_setup_entry` 顺序

```text
读取 entry.data/options
  → 获取 HA ClientSession
  → 创建本 Entry 的 KomariApiClient
  → 探测能力并创建 Coordinator
  → coordinator.async_config_entry_first_refresh()
  → 在 Device Registry 创建面板 service Device
  → 保存 panel_device_id 到 runtime_data
  → entry.runtime_data = runtime_data
  → forward sensor / binary_sensor 平台
  → 注册一次 options update listener
```

必须先得到首次完整快照，再创建面板 Device 和转发平台。这样所有节点实体在首次添加时都有稳定元数据，且 `via_device_id` 已存在。

异常映射：

- 临时连接失败：`ConfigEntryNotReady`。
- 认证失败：`ConfigEntryAuthFailed`，触发 Reauth。
- 永久不兼容：记录明确错误，并通过 Config Entry 状态/Repairs 引导用户处理。

`async_unload_entry`：

1. 卸载全部平台。
2. 取消 coordinator listener、低频调度和 options listener。
3. 清空本 Entry 的运行时引用。
4. 不关闭 HA 共享 Session。

### 独立性要求

- 不把任一面板 Client 或 Coordinator 存成模块全局变量。
- 不用一个 `hass.data[DOMAIN]` 对象共享多个面板状态。
- A 面板认证失败不得让 B 面板实体 unavailable。
- 每个 Entry 单独应用轮询周期、Ping 任务和动态指标选择。

### 测试

- 首次刷新成功/失败/认证失败。
- 正常卸载、重载和 HA 停止后无残留定时器。
- 两个 Entry 创建两个不同 Client、Coordinator 和 panel device ID。
- 修改 A 的 Options 只重载 A。

### 完成标准

多面板并行运行且状态、异常、重载和卸载完全隔离。

## 12. F07：面板 Service Device

### 目标

每个 Config Entry 创建且只创建一个面板设备，作为节点设备的父级和面板实体容器。

### 代码落点

- `__init__.py`
- `entity.py`
- `tests/components/komari/test_init.py`

### 实现方案

首次刷新后调用 Device Registry：

```python
panel_device = device_registry.async_get_or_create(
    config_entry_id=entry.entry_id,
    identifiers={(DOMAIN, f"{entry.entry_id}:panel")},
    entry_type=DeviceEntryType.SERVICE,
    name=coordinator.data.panel.name,
    manufacturer="Komari",
    model="Monitoring Panel",
    sw_version=coordinator.data.panel.version,
    configuration_url=normalized_panel_url,
)
```

保存真实的 `panel_device.id`，供节点实体的 `DeviceInfo.via_device_id` 使用。后续站点名称和版本变化时更新 registry 元数据，但标识保持不变。

### 边界条件

- 不使用 URL、站点名称或版本作为 Device identifier。
- 同一 Entry 重载不能创建第二个面板 Device。
- 面板名称变化只更新显示名。
- `configuration_url` 只指向已经配置并通过校验的面板根地址。

### 测试

- 一个 Entry 恰好一个 `SERVICE` Device。
- 两个 Entry 即使 URL 主机别名或站点名称相同，也有两个 Device。
- 重载后 ID 保持不变，版本元数据可更新。

### 完成标准

设备页面能明确看到一个 Komari 面板设备，其下游节点可以使用真实 `via_device_id` 建立拓扑。

## 13. F08：节点 Device、父子拓扑和动态发现

### 目标

为每个节点建立独立 Device，并在运行时自动增加新节点。

### 代码落点

- `entity.py`
- `sensor.py`
- `binary_sensor.py`
- `coordinator.py`
- `tests/components/komari/test_entity_lifecycle.py`

### 标识与 DeviceInfo

```python
node_identifier = (DOMAIN, f"{entry.entry_id}:node:{node.uuid}")

DeviceInfo(
    identifiers={node_identifier},
    name=node.name,
    manufacturer="Komari",
    model=node.os or "Monitored Node",
    sw_version=node.agent_version,
    via_device_id=entry.runtime_data.panel_device_id,
)
```

节点 UUID 在当前面板内稳定，`entry.entry_id` 负责隔离不同面板。IP、节点名和备注都不能作为 identifier。

### 动态实体发现

每个平台在 `async_setup_entry` 中：

1. 根据首次快照创建实体。
2. 保存 `known_entity_keys: set[EntityKey]`。
3. 注册 coordinator listener。
4. 每次权威节点清单刷新后计算新增实体键。
5. 只对新增键调用 `async_add_entities()`。
6. Listener 的取消函数通过 `entry.async_on_unload()` 注册。

所有节点至少创建 `online` 和 `last_seen`，因此 Device 不会成为无实体的孤立对象。

### 边界条件

- 临时状态中缺失但节点仍在权威清单：保留 Device，数值实体 unavailable。
- 节点改名：更新 Device name，不改变实体 unique ID。
- 新节点只添加一次，多个平台各自维护平台内实体键。
- 节点删除在 F19 中执行延迟确认，不在此功能中立即清理。

### 测试

- 新增节点无需重载即出现 Device 和核心实体。
- 同一节点重复刷新不产生重复实体。
- 两个面板相同 UUID 的节点属于各自 Entry 和父面板。
- `via_device_id` 指向本 Entry 面板，而不是其他面板。

### 完成标准

节点拓扑、身份和动态新增稳定，跨面板不会错误合并。

## 14. F09：实体公共基类、身份与可用性

### 目标

统一 Coordinator 订阅、unique ID、设备关联、状态读取和可用性规则。

### 代码落点

- `entity.py`
- `sensor.py`
- `binary_sensor.py`
- `tests/components/komari/test_sensor.py`

### 类结构

```text
CoordinatorEntity[KomariDataUpdateCoordinator]
├── KomariPanelEntity
└── KomariNodeEntity
    ├── KomariNodeSensor
    ├── KomariNodeBinarySensor
    ├── KomariPingSensor
    └── KomariMetricSensor
```

稳定 unique ID 格式：

```text
{entry_id}:panel:{entity_key}
{entry_id}:node:{node_uuid}:{entity_key}
{entry_id}:node:{node_uuid}:ping:{task_id}:{stat_key}
{entry_id}:{scope}:metric:{metric_key}:{tag_hash}
```

不要把实体名称、节点名称、URL、IP、单位或当前标签显示文本写入 unique ID。

### 可用性规则

- 普通 CoordinatorEntity：面板刷新失败时 unavailable。
- 节点 `online`：只要面板快照有效就保持 available，节点离线时状态为 `off`。
- `last_seen`：节点离线时仍 available，保留最后有效采集时间。
- 节点数值实体：要求面板快照有效、节点在线且指标值有效。
- 静态/限额实体：不因节点离线而 unavailable，只由字段是否存在决定。
- Ping/动态指标：同时受面板刷新、节点状态、任务/序列数据有效期影响。

`native_value`、`is_on` 和 `available` 不执行 I/O，不抛未处理异常。

### EntityDescription

核心传感器使用冻结的描述对象集中声明：

- key、翻译键。
- device class、state class、单位。
- 默认启用状态和 entity category。
- 取值函数和必要的可用性函数。

### 测试

- unique ID 在改名、重载和版本更新后稳定。
- 节点离线时，连接与最后采集实体仍可用，数值实体不可用。
- 0 值不会被当成缺失。
- 不同 Entry 的相同节点/任务/指标不冲突。

### 完成标准

后续每类实体只需要声明数据映射，不重复实现身份和基础可用性逻辑。

## 15. F10：面板汇总和连接实体

### 目标

在面板 Device 下提供面板可用性、版本和节点汇总。

### 实体

| key | 平台 | 类型 | 默认 |
| --- | --- | --- | --- |
| `rpc_available` | binary_sensor | connectivity | 开启 |
| `node_problem` | binary_sensor | problem | 开启 |
| `node_count` | sensor | measurement | 开启 |
| `online_node_count` | sensor | measurement | 开启 |
| `offline_node_count` | sensor | measurement | 开启 |
| `komari_version` | sensor | diagnostic | 开启 |
| `rpc_version` | sensor | diagnostic | 关闭 |
| `build_hash` | sensor | diagnostic | 关闭 |

### 连接实体特殊实现

普通 CoordinatorEntity 在更新失败时会变成 unavailable，无法表达“连接已断开”。`rpc_available` 应覆盖可用性：首次 setup 成功后始终 available，`is_on = coordinator.last_update_success`。Coordinator 失败和恢复时都要触发 listener 写状态。

这样自动化可以区分：

- `off`：面板当前不可达。
- `on`：最近一次刷新成功。
- `unavailable`：集成尚未完成首次成功初始化或已卸载。

`node_problem` 只在面板快照有效时计算；任一可见节点 `online=false` 即为 `on`。面板不可达时它应 unavailable，避免把“面板断联”误判成“所有节点正常”。

### 测试

- 成功 → 失败 → 恢复时 connectivity 为 on → off → on。
- 节点总数、在线数、离线数相加一致。
- 空面板无节点时 problem 为 off。
- 所有实体归属面板 Device。

### 完成标准

用户无需查看日志即可区分面板断联和节点离线，并可对两者分别建立自动化。

## 16. F11：节点在线状态和最后采集时间

### 目标

提供最稳定、最适合自动化的节点生命状态。

### 实体

- `online`：`BinarySensorDeviceClass.CONNECTIVITY`。
- `last_seen`：`SensorDeviceClass.TIMESTAMP`。

### 实现方案

`online` 直接读取规范化 `KomariNodeStatus.online`。如果节点仍在清单但状态记录缺失，实体 available 且 `is_on=False` 还是 unavailable 需要避免误报；实现规定为 unavailable，并增加内部 `status_missing` 诊断原因。只有接口明确返回 `online=false` 才显示 off。

`last_seen` 使用状态中的 `time/observed_at`，转换为 UTC aware datetime。节点离线后保留最后一次有效时间；不要在每次 HA 轮询时用当前时间覆盖。

### 边界条件

- 服务端时间在未来超过容差时保留原值并在 diagnostics 标记 clock skew，或直接设为 unavailable；不得静默改成 HA 当前时间。
- 节点清单存在但尚无任何状态时，两实体均不应伪造数据。
- `online` 不用字符串 sensor，以便 HA 正确作为连接状态使用。

### 测试

- 在线、明确离线、状态缺失、时间无效、时钟偏差。
- 离线后 `last_seen` 不被清空。
- 面板请求失败时 `online` 不显示错误的 off。

### 完成标准

连接自动化不会把“数据未知”误判为“已离线”。

## 17. F12：CPU、GPU、内存、Swap、磁盘、负载和温度

### 目标

将 `common:getNodesLatestStatus` 的核心资源数据映射为稳定传感器。

### 实体描述

| key | 值 | HA 元数据 | 默认 |
| --- | --- | --- | --- |
| `cpu_usage` | `cpu` | `%`, measurement | 开启 |
| `gpu_usage` | `gpu` | `%`, measurement | 有数据时开启 |
| `memory_usage` | `ram / ram_total * 100` | `%`, measurement | 开启 |
| `memory_used` | `ram` | data size, measurement | 开启 |
| `memory_total` | `ram_total` | data size, diagnostic | 关闭 |
| `swap_usage` | `swap / swap_total * 100` | `%`, measurement | 有容量时开启 |
| `swap_used` | `swap` | data size, measurement | 关闭 |
| `disk_usage` | `disk / disk_total * 100` | `%`, measurement | 开启 |
| `disk_used` | `disk` | data size, measurement | 开启 |
| `disk_total` | `disk_total` | data size, diagnostic | 关闭 |
| `load_1m` | `load` | measurement | 开启 |
| `load_5m` | `load5` | measurement | 开启 |
| `load_15m` | `load15` | measurement | 开启 |
| `temperature` | `temp` | temperature, measurement | 有效时开启 |

### 实现细节

- 使用 Home Assistant 原生单位常量，容量模型统一为 bytes。
- 百分比计算放在纯函数中：分母 `None` 或 `<= 0` 返回 `None`；结果允许短时略超 100，但对明显异常值不静默截断，应标记无效或保留诊断。
- GPU/Swap/温度实体只在节点具备有效数据时创建；后续数据暂时缺失时保留实体并 unavailable。
- 温度范围阈值写成常量。当前 legacy `common:getNodesLatestStatus` 以 `0` 表示未提供
  温度，因此只有 `0 < value <= 200°C` 有效；未来只有新 RPC 明确提供 presence 语义后，
  才能单独支持真实 `0°C`。
- 核心最新状态只有节点汇总值时，不创建“每块磁盘”“每张网卡”或“每个 GPU”实体。

### 测试

- 零总量、None、0%/100%、异常负数和短时超限。
- GPU/Swap/温度条件创建和数据暂时缺失。
- 所有在线数值实体在节点离线后 unavailable。
- 单位、device class、state class 和默认启用状态断言。

### 完成标准

核心资源实体与 Komari 最新状态一一对应，没有虚构维度或重复请求。

## 18. F13：网络速率、累计流量、进程和连接数

### 目标

完成节点网络与系统计数指标映射。

### 实体描述

| key | 数据 | HA 元数据 | 默认 |
| --- | --- | --- | --- |
| `network_in` | `net_in` | data rate, B/s, measurement | 开启 |
| `network_out` | `net_out` | data rate, B/s, measurement | 开启 |
| `network_total_up` | `net_total_up` | data size, total increasing | 开启 |
| `network_total_down` | `net_total_down` | data size, total increasing | 开启 |
| `process_count` | `process` | measurement | 关闭 |
| `tcp_connections` | `connections` | measurement | 关闭 |
| `udp_connections` | `connections_udp` | measurement | 关闭 |

### 实现细节

- `net_total_up/down` 按 Komari RPC 文档表示 Agent 启动后的累计值，Agent 重启后允许计数器下降。`TOTAL_INCREASING` 可让 HA 统计处理复位，但文档必须明确它不是天然的账期累计流量。
- 速率小于 0、NaN 或无穷值返回 unavailable；0 B/s 是有效值。
- 连接数和进程数只接受非负整数。
- 不把每次状态中的大段网络详情放入 `extra_state_attributes`。

### 测试

- 网络空闲 0 值保持 available。
- Agent 计数器复位后传感器状态更新，不抛异常。
- 无效负数/NaN/Infinity 处理。
- 状态和统计类型断言。

### 完成标准

网络速率与累计值可供 HA 历史统计使用，并清楚表达复位语义。

## 19. F14：到期、自动续费与流量限额

### 目标

将节点合同/限额元数据转成续费和流量预警实体，同时避免把 Agent 计数器误称为账期账单数据。

### 实体

| key | 类型 | 创建条件 | 默认 |
| --- | --- | --- | --- |
| `expires_at` | timestamp sensor | `expired_at` 有效 | 开启 |
| `remaining_days` | duration sensor | `expired_at` 有效 | 开启 |
| `expired` | problem binary sensor | `expired_at` 有效 | 开启 |
| `auto_renewal` | diagnostic binary sensor | 字段存在 | 关闭 |
| `traffic_limit` | data size sensor | limit > 0 | 开启 |
| `traffic_limit_type` | diagnostic sensor | 类型有效 | 关闭 |
| `traffic_used_by_limit_rule` | data size sensor | limit 和流量源有效 | 开启 |
| `traffic_limit_usage` | percentage sensor | limit > 0 且已用值有效 | 开启 |

### 到期实现

- `expires_at` 直接返回带时区 datetime。
- `remaining_days = max(0, (expired_at - utcnow()).total_seconds())`，使用 duration 单位表达；实体名称显示“剩余时间”，不要在代码中把小时粗暴取整后冒充精确天数。
- `expired = utcnow() >= expired_at`。
- 这些实体依赖时间推进，不能只在 30 秒网络刷新时变化。使用 coordinator 周期更新已经足够，不额外创建每实体计时器。

### 流量口径计算

```python
match traffic_limit_type:
    case "sum":  used = up + down
    case "max":  used = max(up, down)
    case "min":  used = min(up, down)
    case "up":   used = up
    case "down": used = down
```

数据源优先级：

1. 如果未来 Komari RPC 明确提供“当前账期已使用流量”字段，并通过文档、fixture 与真实响应验证，则优先使用该字段。
2. 否则只能用 `net_total_up/down` 计算“当前 Agent 运行累计值按限额规则折算”。

第二种来源会在 Agent 重启后复位，不能承诺账期准确。此时实体应包含少量非敏感属性：`calculation_source=agent_counters`、`reset_sensitive=true`；README 和 diagnostics 同时说明限制。不要通过 HA 本地持久化自行拼接账期总量，除非后续能可靠识别账期边界、节点重装和计数器复位。

`usage = used / limit * 100`；limit 为 0/None 表示未设置限额，百分比不可用或不创建，不能除零。

### 边界条件

- 到期字段为空表示未配置，不创建对应实体。
- 到期时间变化不改变 unique ID。
- 流量口径未知时只暴露 limit 和类型诊断，不猜测 used。
- 流量使用率可超过 100%，不能截断，否则无法表达超额。
- 价格和币种首版不创建实体。

### 测试

- 五种限额口径公式。
- limit 为 0、未知口径、缺上行/下行、计数器复位、超过 100%。
- 未来/当前/过去到期时间和时区处理。
- 节点离线时，静态限额和到期实体仍 available。

### 完成标准

用户能建立到期和流量阈值自动化，且界面与文档不把复位敏感的 Agent 累计量误称为账期权威用量。

## 20. F15：Ping 任务发现、选择和统计实体

### 目标

发现 Komari 公开 Ping 任务，由用户选择要同步的任务，并按节点创建延迟与丢包实体。

### 代码落点

- `api.py`
- `models.py`
- `coordinator.py`
- `config_flow.py`
- `sensor.py`
- `binary_sensor.py`
- `tests/components/komari/test_options_flow.py`
- `tests/components/komari/test_sensor.py`

### 数据模型

```python
@dataclass(frozen=True, slots=True)
class KomariPingTask:
    task_id: str
    name: str
    target_type: str | None
    interval: int | None

@dataclass(frozen=True, slots=True)
class KomariPingStats:
    latest_ms: float | None
    loss_percent: float | None
    minimum_ms: float | None
    maximum_ms: float | None
    average_ms: float | None
    p95_ms: float | None
    window_start: datetime | None
    sample_count: int
```

### 实现流程

1. 能力探测确认 Ping 方法可用。
2. 低频调用 `public:getPublicPingTasks` 获取任务清单。
3. Options Flow 用稳定 task ID 多选，显示名称只作 UI 标签。
4. Coordinator 只查询已选择任务。
5. 优先调用 `public:getPingMetricStats`；无该方法时调用 `public:getPingRecords`，只获取有限时间窗和点数后本地计算。
6. 将结果按 `(node_uuid, task_id)` 规范化。
7. 在对应节点 Device 下动态创建实体。

### 实体

- `latest_latency`：ms，duration/measurement。
- `packet_loss`：%，measurement。
- `minimum_latency`、`maximum_latency`、`average_latency`、`p95_latency`：ms，默认禁用其中的详细统计，避免实体过多。
- `ping_problem`：problem binary sensor；只有服务端有明确失败/丢包阈值语义，或集成定义并公开可配置阈值时创建。

unique ID 格式：

```text
{entry_id}:node:{node_uuid}:ping:{task_id}:{stat_key}
```

### 实体数量控制

- 默认不选择任何任务。
- Options 预览“节点数 × 任务数 × 每任务实体数”。
- 设置 `MAX_PING_ENTITY_COUNT` 硬上限，超限时阻止保存并给出可翻译错误。
- 只为接口实际返回该节点/任务组合的数据创建实体。

### 生命周期

- 任务改名：只更新实体显示名，不改变 unique ID。
- 新任务：出现在 Options，但未选择时不创建实体。
- 已选任务删除：连续三次权威任务清单缺失后清理实体注册表记录，并在 diagnostics 记录原因。
- 用户取消选择：Options 保存后重载该 Entry，并删除不再选择的 Ping 实体注册表项。

### 边界条件

- 没有公开任务时 Options 显示空状态，不报错。
- API Key 不代表一定存在私有 Ping 方法；首版只调用文档和能力列表确认的方法。
- 样本不足时 P95 unavailable，不用单点冒充统计值。
- 数据时间超过窗口容差时实体 unavailable。

### 测试

- 任务发现、选择、取消、改名、新增和删除。
- 聚合方法与 records 回退路径结果一致。
- 丢包 0%、延迟 0 ms、样本不足、过期数据。
- 实体数量上限和两个面板相同 task ID 隔离。

### 完成标准

用户可以按面板独立选择 Ping 任务，实体稳定归属节点，任务变化不会产生重复或永久陈旧实体。

## 21. F16：动态/自定义指标发现与实体映射

### 目标

发现、选择并显示 Komari Metric Definitions 与查询结果，同时避免硬编码未来指标。

### 代码落点

- `api.py`
- `models.py`
- `coordinator.py`
- `config_flow.py`
- `sensor.py`
- `const.py`
- `tests/components/komari/test_options_flow.py`
- `tests/components/komari/test_sensor.py`

### 数据模型

```python
@dataclass(frozen=True, slots=True)
class KomariMetricDefinition:
    metric_key: str
    unit: str | None
    description: str | Mapping[str, str] | None
    metric_type: str
    retention_days: int
    metadata: Mapping[str, str]

@dataclass(frozen=True, slots=True)
class KomariMetricSeriesKey:
    metric_key: str
    entity_id: str
    tags: tuple[tuple[str, str], ...]

@dataclass(frozen=True, slots=True)
class KomariMetricValue:
    value: float | None
    observed_at: datetime | None
```

### 实现流程

1. `public:listMetricDefinitions` 获取定义。
2. 从定义中排除已由 F12/F13 内置的等价指标，建立 `BUILT_IN_METRIC_KEYS` 去重表。
3. Options Flow 只展示服务端真实返回、尚未内置、受支持且 `retention_days > 0` 的指标。
4. 用户选择 metric key，并可设置大小写敏感的精确 tag 过滤；选择项按面板保存在
   Entry options，畸形过滤不会退化成空过滤查询。
5. Coordinator 调用 `public:queryMetrics` 时固定使用四小时窗口、一个点、`last` 和
   `fill_empty=false`，并将相同过滤条件的 key 合并请求。
6. 查询结果规范化为 series；只取最新的非空有限数值，`null` 保留为 unavailable，
   不向 HA 导入完整历史时序。
7. 仅当 `entity_id` 是当前可见节点 UUID 时创建节点实体。`labels` 解析后丢弃，不从
   tags 或 labels 猜测节点；空或未知 `entity_id` 的 series 只计入 diagnostics。

### 单位与类型映射

使用白名单，不进行模糊猜测：

| Komari unit/type | HA 映射 |
| --- | --- |
| gauge | `measurement`；再按单位设置 device class |
| counter | `total_increasing`；再按单位设置 device class |
| histogram / summary | 普通数值 sensor，不设置 state class |
| `%`, percent | `PERCENTAGE` |
| bytes | data size（bytes） |
| bytes/s | data rate（bytes/s） |
| ms | duration（milliseconds） |
| °C/celsius | temperature（Celsius） |
| count 或未识别单位 | 不设置 device class 和 unit |
| 未识别 type | 不可选择 |

不能接受 string 或 boolean Metric value。只有 `counter` 使用 `TOTAL_INCREASING`；不能
仅因为单位是 bytes 就推断状态类型。

### 稳定身份

tags 先按键排序并进行规范化编码，再计算完整 SHA-256：

```text
{entry_id}:node:{node_uuid}:metric:{percent_encoded_metric_key}:{sha256(canonical_tags)}
```

标签显示顺序、Definition description、节点名称和 labels 变化不影响身份；tags 值改变
代表一个新 series。使用完整摘要而不是短摘要，不需要碰撞扩展分支，且不得覆盖已有
实体。

### 实体数量与查询保护

- 默认不选择动态指标。
- 限制为每 Entry 8 个 metric key、256 个 Definitions、每响应 512 个 series、每个
  series 16 个 tags、每 Entry 128 个实体；Metric RPC 响应上限为 2 MiB。
- Options 预览超过 128 个实体或响应超限时阻止保存；运行时优先保留已知 identity，
  其余按 canonical identity 排序，并创建 Repair。
- Series 暂时消失只会 unavailable；Definition 连续三次权威缺失才清理其 registry
  entities，节点确认删除沿用节点 lifecycle 清理。
- 轮询默认 10 分钟，可设 300～3600 秒，不跟随 30 秒核心状态全量查询。

### 生命周期

- 定义新增：出现在 Options，不自动启用。
- 定义改名/改描述：更新显示信息，身份不变。
- 已选定义删除：实体先 unavailable；连续三次权威定义清单缺失后清理 registry，并向用户显示选择已失效。
- series 标签暂时消失：在过期窗口内保留实体 unavailable，不立刻删除。
- 取消选择：重载当前 Entry 并清理对应实体。

### 测试

- 内置指标去重。
- 已知/未知单位、boolean、累计值状态类型。
- 标签排序不影响 unique ID；标签值变化按新序列处理。
- 面板级/节点级归属。
- 高基数、超点数、超实体数和定义删除。
- 两个面板相同 metric key/labels 不冲突。

### 完成标准

动态指标由用户显式选择，身份和单位稳定，未知数据不被猜测，查询规模始终受控。

## 22. F17：Coordinator 调度、局部降级与快照一致性

### 目标

用每 Entry 一个 Coordinator 管理多种刷新周期，同时保证核心状态失败与可选功能失败的影响范围正确。

### 代码落点

- `coordinator.py`
- `data.py`
- `tests/components/komari/test_coordinator.py`

### 默认周期

| 数据 | 默认周期 | 可调范围 |
| --- | --- | --- |
| 最新节点状态 | 30 秒 | 15～300 秒 |
| 节点清单 | 10 分钟 | 固定或按 20 次核心刷新 |
| Ping 统计 | 5 分钟 | 60～1800 秒 |
| 动态指标 | 10 分钟 | 300～3600 秒；只在有有效选择时请求 |
| 能力、版本、公开设置 | 6 小时 | 低频固定 |

### 调度实现

Coordinator 的主 `update_interval` 使用核心状态周期。内部保存各低频数据的 `next_due` 单调时钟：

```text
每次 _async_update_data
  → 必须刷新 latest status
  → inventory 到期则刷新 nodes
  → ping 到期且有选择则刷新 ping
  → metrics 到期且有选择则刷新 metrics
  → capabilities 到期则刷新 methods/version/settings
  → 将新旧低频数据合成为一个不可变 KomariSnapshot
```

单调时钟只用于调度，快照时间使用 UTC。低频请求尽量并行，但必须受 Client 并发限制；有依赖的调用仍按顺序执行。

### 错误影响范围

- 最新状态请求失败：抛 `UpdateFailed`，本 Entry 普通实体 unavailable。
- 认证失败：抛 `ConfigEntryAuthFailed`。
- 节点清单暂时失败：保留上次清单，不执行删除；如果最新状态仍成功，可继续更新已有节点。
- Ping/Metric 可选请求失败：保留上次结果直到 TTL，超过 TTL 后只让相关实体 unavailable；核心实体继续工作。
- `Method not found`：移除对应 capability，禁用该功能并触发 Options/diagnostics 更新。

### 快照合并

- 不原地修改上一快照的字典。
- 新快照生成后一次性赋给 `coordinator.data`。
- 低频数据包含 `fetched_at`，实体根据 TTL 判断是否过期。
- 保留最后一次成功和各分支最后错误的摘要，但不保留敏感响应正文。

### 并发与抖动

- Coordinator 本身避免重入，API Client 再限制请求并发。
- 多个 Config Entries 可并行，但首次加载可加入小幅随机 jitter，避免 HA 重启时同时打满同一上游。
- Options 更新触发重载，不直接和正在运行的刷新争夺共享可变配置。

### 测试

- 使用可控 monotonic clock 验证 30 秒/10 分钟/6 小时调度。
- 可选分支失败不影响核心实体。
- 清单失败不触发陈旧设备计数。
- 新快照不可变，实体读取同一批数据。
- 两 Entry 的调度和失败计数隔离。

### 完成标准

网络请求数量可预测，局部故障只影响对应功能，所有实体在同一刷新周期读取一致快照。

## 23. F18：Options、Reauth 和 Reconfigure

### 目标

让用户无需删除集成即可调整轮询、选择功能、更新 Key 或更换面板连接参数。

### 代码落点

- `config_flow.py`
- `__init__.py`
- `const.py`
- `tests/components/komari/test_options_flow.py`
- `tests/components/komari/test_config_flow.py`

### Options Flow

按能力分步骤，避免一个超长表单：

1. `monitoring`：核心状态、Ping、动态指标轮询周期。
2. `ping_tasks`：可用任务多选和实体数量预览。
3. `metrics`：动态指标多选、标签过滤和数量预览。
4. `entities`：到期/流量功能开关及高级诊断实体策略。

保存时校验区间和实体数量上限。Options 更新后仅重载当前 Config Entry。注册 reload listener 只能有一处，避免配置流主动重载和 update listener 同时触发两次重载。

### Reauth Flow

Coordinator 抛 `ConfigEntryAuthFailed` 后启动 Reauth：

- 显示当前面板标题，不回显旧 Key。
- 用户输入新 API Key。
- 用当前 URL/TLS 配置执行最小校验。
- 成功后只更新认证字段，终止 reauth 并重载 Entry。
- 必须确认 reauth 对应原 Entry，不新建第二个 Entry。

### Reconfigure Flow

允许修改 URL 和 TLS 验证：

1. 规范化新 URL。
2. 用现有或新 Key 校验。
3. 检查是否与其他 Entry 冲突。
4. 更新当前 Entry data。
5. 保留 `entry.entry_id`，因此设备与实体 unique ID 不变化。
6. 如果连接的是完全不同面板，必须明确提示这会复用原设备身份；更安全的首版策略是禁止跨面板替换，只允许等价 URL/证书设置变更，换面板要求新增 Entry。

### 测试

- 每个轮询范围边界。
- Ping capability 暂时缺失时保留既有任务和统计选择；Metric capability 暂时缺失时保留
  既有选择并提供显式清除开关。
- 实体数超限阻止保存。
- Reauth 成功、失败、取消和归属原 Entry。
- Reconfigure URL 冲突与 TLS 修改。
- Options 保存只重载一次且只影响当前 Entry。

### 完成标准

认证和常用配置可维护，多面板 Entry 不会因配置更新互相影响。

## 24. F19：节点/任务/指标生命周期、迁移与清理

### 目标

处理远端对象新增、改名、删除以及集成版本升级，避免重复或永久陈旧设备/实体。

### 代码落点

- `coordinator.py`
- `__init__.py`
- `sensor.py`
- `binary_sensor.py`
- `tests/components/komari/test_entity_lifecycle.py`
- `tests/components/komari/test_migration.py`

### 权威缺失确认

分别维护节点、Ping 任务、Metric Definition/series 的缺失计数：

- 只有对应权威清单请求成功时才增加缺失计数。
- 对象重新出现时计数归零。
- 连续三次权威清单缺失才确认删除。
- 认证失败、超时、解析失败、可选能力暂时不可用时不增加计数。

### 设备清理

目标基线 Core 2026.8+ 中，一个 Device 只归属一个 Config Entry。确认节点已从 Komari 删除后，使用 Device Registry 的 `async_remove_device(device.id)` 删除节点 Device；不要再使用移除某个 `config_entry_id` 关联的旧模式。

实现 `async_remove_config_entry_device(hass, config_entry, device_entry) -> bool`：

- 只允许删除属于当前 Entry 的节点设备。
- 节点仍在最新权威清单时返回 `False`。
- 面板 service Device 不能通过该路径单独删除。

### 实体清理

- 节点 Device 删除时，其节点实体随设备清理。
- Ping/Metric 取消选择或确认删除时，按 unique ID 前缀定位当前 Entry 的实体注册表项并移除。
- 查询和删除都必须限定 `config_entry_id`，不能仅按 Domain 或远端 UUID 批量操作。
- 活跃实体对象的 removal 和 registry removal 使用 HA 支持的实体平台/注册表 API，并配套测试，禁止直接操作内部字典。

### Config Entry 迁移

从首版开始设置 `VERSION` 和 `MINOR_VERSION`，实现 `async_migrate_entry`：

- data/options schema 变化逐版本迁移，保证幂等。
- 迁移前复制数据，不原地修改只读映射。
- 迁移失败返回 `False` 并记录非敏感原因。

### Entity unique ID 迁移

如果开发期曾使用 URL 或裸 node UUID，正式发布前用 Entity Registry 迁移到 `{entry_id}:...`。迁移必须检测冲突，不覆盖用户已有实体。

### 测试

- 一次/两次缺失不删除，第三次成功清单缺失才删除。
- 权威清单失败不计数。
- 节点恢复后计数清零。
- 手动设备删除的允许/拒绝路径。
- 旧 Config Entry 和 unique ID 迁移、重复迁移、冲突迁移。

### 完成标准

对象生命周期可预测，升级和动态变化不产生重复身份或跨 Entry 误删。

## 25. F20：诊断、日志、故障提示与 Repairs

### 目标

提供足够的排障信息，同时严格脱敏 API Key、地址和节点私密信息。

### 代码落点

- `diagnostics.py`
- `coordinator.py`
- `exceptions.py`
- `tests/components/komari/test_diagnostics.py`

### Diagnostics 接口

实现官方入口：

```python
async def async_get_config_entry_diagnostics(hass, entry) -> dict[str, Any]: ...
async def async_get_device_diagnostics(hass, entry, device) -> dict[str, Any]: ...
```

使用 `async_redact_data`，至少脱敏：

- API Key、Cookie、Authorization Header。
- 面板完整 URL 中的用户信息、query 和敏感路径。
- 节点 IP、公开/私有地址、Token、备注。
- 原始请求头、原始错误响应和未筛选原始 JSON。

允许输出：

- 集成版本、HA 目标兼容信息。
- Komari/RPC 版本和方法能力布尔值。
- Entry options 中非敏感的轮询周期与选择数量，不输出敏感标签值。
- 节点数量、在线/离线数量。
- 各数据分支最后成功时间、连续失败次数和脱敏错误类别。
- Ping/Metric definitions 与已创建实体数量。
- 流量计算来源是否为 `agent_counters`。

### 日志策略

- 正常轮询失败：Coordinator 标准日志；避免每 30 秒重复完整堆栈。
- 首次进入 unavailable 记录一次 warning，持续失败使用 debug；恢复时记录一次 info。
- 协议结构未知记录方法名和字段路径，不记录响应正文。
- API Key、IP、节点备注和标签值禁止出现在日志。

### Repairs/用户提示

适合创建可修复提示的问题：

- RPC2/Komari 版本不受支持。
- 选中的 Ping 任务或 Metric Definition 已被删除。
- 动态指标 series 超过安全上限。
- TLS 验证关闭。
- 流量百分比来自会复位的 Agent 累计计数，而非账期权威数据；此项更适合 diagnostics/文档提示，避免持续打扰用户。

### 测试

- Config Entry 和 Device diagnostics 结构。
- 对嵌套 Header、URL、节点对象和异常消息做泄漏断言。
- unavailable 只记录一次，恢复只记录一次。
- 两个 Entry 的诊断数据不混合。

### 完成标准

用户提交 diagnostics 即可判断能力和失败范围，且其中不含认证和节点私密数据。

## 26. F21：翻译、图标、命名和用户文档

### 目标

让集成配置、错误、设备和实体在中英文环境中一致且可理解。

### 代码落点

- `translations/en.json`
- `translations/zh-Hans.json`
- `icons.json`
- `README.md`

### 实现方案

- 所有实体使用 `translation_key`，不在 Python 中拼接英文/中文显示名。
- 实体名称只表达指标，例如“CPU 使用率”；Device 名负责表达节点，避免“节点 A 节点 A CPU 使用率”。
- Ping 实体名称可通过任务名占位符生成，但 unique ID 只使用 task ID。
- 未识别动态指标以服务端定义名称显示；服务端名称不进入翻译文件和 identity。
- Options 和错误字符串覆盖：连接失败、认证失败、私有站点、版本不支持、重复 URL、实体上限、能力缺失。

README 必须说明：

1. 安装和多面板添加方式。
2. API Key 权限与 TLS 设置。
3. 每个面板/节点的 Device 模型。
4. 核心实体、默认禁用实体和 Options。
5. 到期、流量口径及 Agent 重启复位限制。
6. Ping 与动态指标会产生的实体数量。
7. 支持的 Komari/HA 版本。
8. 已知限制、升级和 diagnostics 获取方法。

### 测试

- translation placeholders 完整且两种语言键一致。
- entity name、device name 和 unique ID 快照测试。
- hassfest translations/icons 校验。

### 完成标准

用户不看源码也能理解配置项、实体语义和已知数据限制。

## 27. F22：自动化测试、联调矩阵和验收

### 目标

建立从协议 fixture 到多面板真实运行的完整验证链。

### 单元测试层

| 模块 | 必测内容 |
| --- | --- |
| API | RPC 成功/错误、HTTP/TLS/超时、认证、脱敏、响应结构 |
| 模型 | 数组/映射兼容、时间/单位、未知字段、边界值 |
| Config Flow | 公开/API Key、重复 URL、私有站点、版本不支持 |
| Coordinator | 多周期、局部降级、首次失败、认证过期、恢复 |
| Devices | 面板 service、节点父子关系、单 Entry 所有权 |
| Entities | 数值、单位、状态类型、默认启用、可用性和身份 |
| 到期流量 | 时区、五种口径、复位、超限、零限额 |
| Ping | 选择、回退统计、任务生命周期、数量上限 |
| Metrics | 去重、单位白名单、标签 identity、series 上限 |
| Diagnostics | 完整脱敏、多 Entry 隔离 |
| Migration | schema、unique ID、冲突和幂等 |

测试不应依赖公共线上面板。把已获授权且脱敏后的响应制作成 fixture；删除 UUID、地址、名称、Token 和真实指标值，保留结构和边界类型。

### 多面板集成测试

至少构造两个 Config Entries：

- 两个面板有相同 node UUID。
- 两个面板有相同 Ping task ID 和 metric key。
- A 刷新失败、B 正常。
- A 修改 Options，B 不重载。
- A 删除节点，不影响 B 的同 UUID Device。

### 真实 Komari 验收矩阵

| 环境 | 核心状态 | API Key | Ping | Metrics | 节点增删 |
| --- | --- | --- | --- | --- | --- |
| 当前公开面板 | 必测 | 不适用 | 必测 | 必测 | 只读，不操作 |
| 自建公开测试面板 | 必测 | 可选 | 必测 | 必测 | 必测 |
| 自建私有测试面板 | 必测 | 必测 | 按能力 | 按能力 | 必测 |
| 最低支持版本 1.0.7 | 必测 | 必测 | 按能力降级 | 应隐藏 | 必测 |
| 当前稳定 Komari | 必测 | 必测 | 必测 | 必测 | 必测 |

### 非 24 小时真实验收

- HA 重启、Entry reload、Komari 重启。
- 断网 10 分钟后恢复。
- API Key 失效并完成 Reauth。
- Agent 离线/上线、Agent 重启导致流量计数复位。
- 节点新增、改名、暂时缺失和确认删除。
- 记录完整的非 24 小时真实矩阵、HACS 安装/升级/卸载结果和证据等级。
- 24 小时长稳验收明确排除在 `v0.3.0` 门禁外；保留为未执行，不能在任何文档或
  Release 中声称通过。

### 完成标准

自动化测试通过，并明确记录哪些行为只在 mock 中验证、哪些已在真实 Komari 与 Home Assistant 中验证。

## 28. F23：HACS 打包、版本发布和 Core 迁移准备

### 目标

先形成可安装的 HACS 自定义集成，再为可能的 Core 提交保留清晰路径。

### HACS 发布

- `hacs.json` 声明集成名称、最低 HA 版本等必要信息。
- GitHub Release 使用语义化版本和变更日志。
- Release 前运行测试、类型检查、ruff、hassfest 和 HACS Action。
- 从全新 HA 实例测试安装、升级、降级失败提示和删除。
- README 明确这是第三方自定义集成，不冒充 Home Assistant 官方集成。

### 版本策略

- `0.1.x`：P1 多面板 MVP。
- `0.2.x`：F14/F15、Reauth、诊断和生命周期。
- `0.3.x`：F16 动态指标。
- `1.0.0` 不属于本轮范围；本轮只准备 `v0.3.0`，不创建或承诺 `v1.0.0`。

不要为了版本号提前发布未完成的实体；版本升级必须保留 Config Entry、Device 和 Entity identity。

### Core 迁移准备

如果提交 Home Assistant Core：

1. 把 RPC2 Client/模型提取到独立 PyPI 包，允许注入 ClientSession。
2. 满足 Core 的配置流、测试覆盖、异步依赖和 Integration Quality Scale 要求。
3. 补齐 `quality_scale.yaml`、品牌资源和官方文档 PR。
4. 评估首个 Core PR 的合理范围；官方通常要求新集成首个 PR 聚焦单一平台，可先提交核心 sensor，再增补 binary_sensor 等平台。
5. 移除 HACS 专用内容，按 Core manifest 与依赖锁定规则调整。

### 完成标准

用户可通过 HACS 安装和升级；发布物与源码标签一致，升级不破坏已有配置和实体历史。

## 29. 横向代码约束

### 29.1 安全

- 默认启用 TLS 验证。
- API Key 不进入 URL、entity attributes、日志或 diagnostics。
- 不执行 Komari 写操作；首版所有 RPC wrapper 都是只读。
- 不把节点 IP、备注和标签全集暴露为状态属性。

### 29.2 性能

- 核心状态每 Entry 每周期最多一次批量请求。
- 实体属性访问不得发起 I/O。
- 动态指标和 Ping 必须有查询点数、series 和实体数量上限。
- 不把历史点数组写入 recorder；只保存当前值和少量窗口结果。

### 29.3 类型和时间

- 新代码完整类型标注，避免 `Any` 扩散出 API 边界。
- 时间全部使用 aware UTC datetime。
- 浮点值进入实体前过滤 NaN/Infinity。
- 不用 `dict.get(...) or fallback` 处理可能为 0 的指标。

### 29.4 Identity

- Device 和 Entity identity 一经发布视为公共接口。
- `entry.entry_id` 隔离面板，node UUID/task ID/metric key 隔离远端对象。
- 名称、URL、IP、单位、版本和当前值不参与 unique ID。

## 30. 开发任务拆分

以下提交边界便于审核和回退：

1. `build: scaffold komari custom integration`
2. `feat: add typed rpc2 client and response models`
3. `feat: add capability discovery and config flow`
4. `feat: add per-entry coordinator and panel service device`
5. `feat: add node discovery and core connectivity entities`
6. `feat: add resource and network sensors`
7. `feat: add options, reauth and reconfigure flows`
8. `feat: add expiry and traffic limit entities`
9. `feat: add selectable ping task entities`
10. `feat: add selectable dynamic metric entities`
11. `feat: add stale object cleanup and migrations`
12. `feat: add diagnostics, translations and documentation`
13. `test: complete multi-panel and live acceptance matrix`
14. `release: prepare hacs package`

每个提交都应同时包含对应测试，不把所有测试推迟到最后。

## 31. 开工检查表

开始编码前先完成：

- [ ] 确定仓库许可证、GitHub owner 和 HACS 发布仓库地址。
- [ ] 保存脱敏 RPC2 fixtures，并注明对应 Komari 版本和方法。
- [ ] 通过 `rpc.help`/官方文档确认 Ping 与 Metric 查询参数结构。
- [ ] 固定首版 Config Entry data/options schema 与 unique ID 格式。
- [ ] 固定支持的 Home Assistant 最低版本为 2026.8 或调整 `via_device_id` 兼容策略。
- [ ] 决定动态指标每 Entry 的默认上限和硬上限。
- [ ] 准备一个可操作节点增删的自建 Komari 测试面板。

P1 完成前检查：

- [ ] 两个面板、相同节点 UUID 的隔离测试通过。
- [ ] 面板 Service Device 和节点父子拓扑正确。
- [ ] Config Flow、首次刷新、卸载、重载和断网恢复通过。
- [ ] 核心实体的单位、状态类型、可用性和 unique ID 通过测试。

完整目标完成前检查：

- [ ] 到期和五种流量限额口径通过测试。
- [ ] 流量计数复位限制已在 UI 文档和 diagnostics 明示。
- [ ] Ping 发现、选择、统计、删除和数量上限通过。
- [ ] 动态指标去重、单位、标签、归属和高基数限制通过。
- [ ] Reauth、Reconfigure、陈旧对象清理和迁移通过。
- [ ] diagnostics 无敏感信息泄漏。
- [ ] HACS 全新安装与版本升级通过。

## 32. 参考资料

### Komari

- [Komari 官方文档](https://komari-monitor.github.io/komari-document/)
- [Komari RPC2 文档](https://komari-monitor.github.io/komari-document/dev/rpc)
- [Komari API 文档](https://komari-monitor.github.io/komari-document/dev/api.html)
- [Komari 项目源码](https://github.com/komari-monitor/komari)
- [Komari Web 源码](https://github.com/komari-monitor/komari-web)

### Home Assistant

- [Config Entries](https://developers.home-assistant.io/docs/config_entries_index/)
- [Config Flow](https://developers.home-assistant.io/docs/core/integration/config_flow/)
- [Integration Manifest](https://developers.home-assistant.io/docs/creating_integration_manifest/)
- [Fetching Data](https://developers.home-assistant.io/docs/integration_fetching_data/)
- [Device Registry](https://developers.home-assistant.io/docs/device_registry_index/)
- [Entity](https://developers.home-assistant.io/docs/core/entity/)
- [Integration Diagnostics](https://developers.home-assistant.io/docs/core/integration/diagnostics/)
- [Integration Quality Scale Rules](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/)
- [Stale Devices Rule](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/stale-devices/)
- [Device Registry single Config Entry change](https://developers.home-assistant.io/blog/2026/07/21/device-registry-single-config-entry/)
- [Contributing an integration to Core](https://developers.home-assistant.io/docs/core/integration/contributing_to_core/)
- [Home Assistant Device Registry source](https://github.com/home-assistant/core/blob/dev/homeassistant/helpers/device_registry.py)

## 33. 实施概要

先确定身份和运行时边界，再添加传感器。F01～F09 固定 RPC、Config Entry、面板/节点 Device 与实体 identity；F10～F16 加入面板状态、核心指标、到期流量、Ping 和动态指标；F17～F23 处理调度、维护、诊断、测试和发布。

每个功能单元需同时完成代码、异常边界、注册表行为和测试，并保持多面板隔离。
