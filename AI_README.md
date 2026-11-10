# Komari 架构与实现参考

代理协作规则见 [AGENTS.md](AGENTS.md)，开发和验收命令见
[DEVELOPMENT.md](DEVELOPMENT.md)。本文保留按主题查阅的实现细节；先定位相关章节，
无需每次全文阅读。判断当前行为仍需核对检出的源码和测试。

## 项目速览

| 项目 | 当前约束 |
| --- | --- |
| 集成 Domain | `komari` |
| 集成类型 | Home Assistant 自定义 `hub` 集成 |
| I/O 模型 | `cloud_polling`，全异步，只读 JSON-RPC 2.0 |
| 上一版发布 | `0.2.0-alpha.1` |
| 当前候选发布 | [`0.3.0-rc.1`](https://github.com/lucklyduck/Komari/releases/tag/v0.3.0-rc.1)（GitHub Prerelease） |
| Home Assistant 基线 | `2026.8.2+` |
| Komari 基线 | RPC2 `1.0.7+`，实际功能按 `rpc.methods` 探测 |
| Python | `3.14.x`，开发脚本固定安装 `3.14.6` |
| 平台 | `sensor`、`binary_sensor` |
| 依赖管理 | `uv` + `uv.lock` |
| 自动化门槛 | Ruff、Pyright、Pytest，分支覆盖率至少 `95%` |

每个 Home Assistant Config Entry 只代表一个 Komari 面板。每个 Entry 拥有独立的
Client、Coordinator、快照、面板服务设备和节点设备。多面板隔离是项目公共契约，
不能为了简化代码而共享运行时对象或使用裸节点 UUID 作为全局身份。

## 仓库结构

| 路径 | 职责 |
| --- | --- |
| `custom_components/komari/__init__.py` | Entry 装配、迁移、加载、卸载与注册表同步 |
| `api.py` | RPC2 Transport、方法选择、认证和错误映射 |
| `models.py` | 严格规范化远端数据，忽略未知字段，拒绝损坏核心结构 |
| `coordinator.py` | 多周期调度、不可变快照、局部降级、缺失计数和低噪声日志 |
| `data.py` | `KomariConfigEntry` 与每 Entry 的 `runtime_data` 类型 |
| `device.py` | 面板/节点 Device 标识、父子拓扑、查找和删除 |
| `entity.py` | 面板和节点实体公共基类、unique ID 与基础 availability |
| `sensor.py` | 面板、节点、到期、流量和 Ping sensors |
| `binary_sensor.py` | RPC、节点故障、在线、到期和自动续费 binary sensors |
| `options.py` | Options 默认值、边界收敛和序列化 |
| `config_flow.py` | 添加、Reauth、Reconfigure 和 Options Flow |
| `lifecycle.py` | 节点与 Ping 注册表身份解析和陈旧对象清理 |
| `diagnostics.py` | Config Entry/Device diagnostics 与深层脱敏 |
| `repairs.py` | Entry-scoped Repair 创建和自动清除 |
| `translations/` | 完整英文和简体中文翻译 |
| `tests/` | 协议、配置、调度、实体、生命周期和隐私回归测试 |
| `tests/fixtures/` | 仅包含脱敏、确定性的 RPC 数据结构 |
| `tests/manual/` | 三个 Fake Panel 与协议 smoke test |
| `script/` | WSL/uv 开发、质量门和本地联调入口 |
| `hacs.json` | HACS 显示名称和最低 Home Assistant 版本 |
| `.github/workflows/validate.yml` | HACS、Hassfest、Ruff、Pyright 和 Pytest CI |
| `CHANGELOG.md` | 与 GitHub Release 同步的版本变更记录 |

## 运行时架构

加载一个 Config Entry 时，执行链如下：

```text
async_setup_entry
  -> 获取 Home Assistant 管理的 ClientSession
  -> 创建 KomariApiClient
  -> 创建 KomariDataUpdateCoordinator
  -> 执行首轮 bootstrap
  -> 读取并清理与 Options 不符的注册表项
  -> 创建面板 Service Device 和节点 Devices
  -> 保存 entry.runtime_data
  -> 注册 Coordinator listener，同步新增/删除对象和 Repairs
  -> 加载 sensor 与 binary_sensor 平台
```

`entry.runtime_data` 只包含当前 Entry 的：

- `client`
- `coordinator`
- `panel_device_id`

不要把 Client、Coordinator、节点缓存或失败状态放到 Domain 级全局容器中。

## Config Entry 与配置流程

### Entry data

连接信息保存在 `entry.data`：

| 字段 | 语义 |
| --- | --- |
| `url` | 规范化后的面板根 URL，不含 `/api/rpc2` |
| `api_key` | 可选 API Key；匿名面板不保存空白 Key |
| `verify_ssl` | TLS 证书验证开关，默认 `true` |

Config Entry 当前为 `VERSION=1`、`MINOR_VERSION=1`。修改 data/options schema 时必须
设计幂等迁移；迁移不得改变 `entry_id` 或现有实体身份。

### URL 规范化

`normalize_panel_url()` 是唯一规范化入口。必须保持以下行为：

- 只接受 `http` 和 `https`。
- 规范化 scheme、host、默认端口和尾斜杠。
- 移除用户误填的终端 `/api/rpc2`。
- 拒绝 userinfo、query 和 fragment。
- 用规范化 URL 阻止重复面板，但不把 URL 设置为 Config Entry unique ID。
- 面板标题优先使用站点名称，缺失时回退到 hostname。

### Reauth、Reconfigure 与 Options

- Reauth 只更新当前 Entry 的 API Key，不回显旧 Key，不创建新 Entry。
- Reconfigure 只修改 URL/TLS，不显示或修改 Key；实际 URL 变化需要二次确认。
- Options 保存后只重载当前 Entry 一次。
- 默认 Options 通过读取时回退，不要求把每个默认值都写入存储。

当前 Options：

| 字段 | 默认值 | 限制 |
| --- | ---: | --- |
| `scan_interval` | 30 秒 | 15～300 秒 |
| `ping_interval` | 300 秒 | 60～1800 秒 |
| `enable_expiry_entities` | `true` | 每面板独立 |
| `enable_traffic_entities` | `true` | 每面板独立 |
| `ping_task_ids` | 空 | 最多 8 个稳定 task ID |
| `ping_stat_keys` | `latest,loss` | 可选 min/max/avg/p50/p99/stddev |
| `metric_interval` | 600 秒 | 300～3600 秒 |
| `metric_keys` | 空 | 最多 8 个 Metric key |
| `metric_tag_filters` | 空 | 精确匹配；边界见 `options.py` 与 `const.py` |

配置时估算 `可见节点数 × 任务数 × 统计项数`，超过 256 必须阻止保存。运行中节点
增长导致超限时停止创建超额实体，并创建 Repair。

## RPC2 Client 约束

所有请求 POST 到 `{panel_root}/api/rpc2`：

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "rpc.ping",
  "params": {}
}
```

- 有 Key 时使用 `Authorization: Bearer <key>`；匿名模式完全不发送该 Header。
- 复用 Home Assistant `ClientSession`，集成不负责关闭它。
- 总超时 20 秒、连接超时 10 秒。
- 不自动跟随 HTTP redirect，避免认证信息发送到非预期主机。
- 不实现 WebSocket、JSON-RPC batch 或写操作。
- 日志只记录安全的方法类别和异常类型，不记录请求/响应正文。

核心探测与调用包括：

- `rpc.ping`、`rpc.version`、`rpc.methods`
- 站点/版本信息的 public/common 兼容方法
- 节点清单的 public/common 兼容方法
- 必需的 `common:getNodesLatestStatus`
- Ping 任务 `public:getPublicPingTasks`
- Ping 统计首选 `public:getPingMetricStats`，再按能力回退到
  `public:getPingRecords` 或 `common:getRecords`

异常必须落入已有分类：连接、认证、权限、RPC、Method Not Found、协议结构和能力
缺失。认证/权限错误由 Coordinator 转成 `ConfigEntryAuthFailed`；核心数据错误转成
脱敏的 `UpdateFailed`；可选方法缺失只移除对应 capability。

## 模型与解析规则

远端数据只能在 `models.py` 的边界内规范化。新增字段时：

1. 为规范化模型增加有类型字段。
2. 同时支持已确认的映射/数组响应形态。
3. 保留合法的 `0`，不要使用 `value or fallback`。
4. 过滤 NaN/Infinity；时间使用带时区 UTC `datetime`。
5. 未知字段忽略；缺失或损坏的核心身份/结构明确抛协议错误。
6. 先增加脱敏 fixture 与 parser 测试，再让 Coordinator/Entity 使用该字段。

特殊能力规则：

- `None`、空字符串、`n/a`、`unknown` 等 GPU 名称不证明存在 GPU。
- 仅有 `gpu_usage=0` 不创建 GPU 实体；有效名称或曾观察到大于 0 的使用率才创建。
- 温度只有 `0 < value <= 200°C` 才视为有效能力。
- Ping task ID 无论原始是数字还是字符串，都规范化为字符串。
- task 名称只用于显示，不参与身份。
- Ping records 中负值表示失败；百分位使用 nearest-rank，标准差使用总体标准差。

## Coordinator、快照与故障边界

每次核心刷新都请求最新状态；低频分支按单调时钟判断是否到期：

| 分支 | 周期 |
| --- | --- |
| 最新节点状态 | 默认 30 秒，可配置 15～300 秒 |
| 节点清单 | 10 分钟 |
| Ping 任务清单 | 10 分钟 |
| Ping 统计 | 默认 5 分钟，可配置 60～1800 秒 |
| Metric Definitions | 10 分钟 |
| 动态 Metric | 默认 10 分钟，可配置 300～3600 秒 |
| capabilities/版本/站点信息 | 6 小时 |

一次刷新结束时发布一个新的 `KomariSnapshot`。不要原地修改上一个快照中的映射；
实体在同一周期必须读取同一批数据。

错误边界：

- 核心状态失败：当前 Entry 整体 unavailable，不影响其他 Entry。
- 节点清单失败：保留旧清单，不增加删除缺失次数。
- Ping 失败：保留旧结果到 TTL；连续两个 Ping 周期未成功后仅 Ping 实体不可用。
- `Method not found`：移除对应 capability，核心监控继续运行。
- 首次分支失败记一次 warning，持续失败降为 debug，恢复记一次 info。

不要对公网面板在同一刷新周期立即重试；等待下一个 Coordinator 周期恢复。

## Device 与 Entity 身份

### Device

```text
Panel identifier: (komari, "{entry_id}:panel")
Node identifier:  (komari, "{entry_id}:node:{node_uuid}")
```

- 每 Entry 创建一个 `DeviceEntryType.SERVICE` 面板设备。
- 每节点创建一个设备，并用面板实际注册表 ID 作为 `via_device_id`。
- Device 查找和删除始终带当前 `config_entry_id`。
- 面板设备不能走节点手动删除或陈旧节点清理路径。

本项目以 Home Assistant 2026.8.2+ 为基线：使用单 Config Entry Device Registry
接口、`async_get_device_by_identifier(identifier, entry_id)` 和
`async_remove_device()`。不要无测试地换回旧版 `via_device`/多 Entry 关联 API。

### Entity

```text
Panel: {entry_id}:panel:{entity_key}
Node:  {entry_id}:node:{node_uuid}:{entity_key}
Ping:  {entry_id}:node:{node_uuid}:ping:{task_id}:{stat_key}
```

名称、URL、IP、版本、任务名称、单位和当前值都不能参与 unique ID。所有实体使用
`translation_key` 和 `_attr_has_entity_name = True`。

改变现有 unique ID 前必须：

- 说明为什么不能保持兼容。
- 编写 Entity Registry 迁移。
- 处理目标 identity 已存在的冲突。
- 测试重复迁移和两个 Entry 相同远端 ID。

## 实体创建与可用性

实体从 Coordinator 快照读取数据，并由平台 listener 动态增加。不得因节点或任务
名称变化重复创建实体。

核心语义：

- 面板不可达：RPC connectivity 为 `off`，该 Entry 数据实体 unavailable。
- 节点明确离线：节点 connectivity 为 `off`，动态数值 unavailable，最后采集保留。
- 状态缺失：节点 connectivity 和动态数值 unavailable，不误报为离线。
- 静态到期和限额元数据在节点离线时仍可用；面板整体不可达时不可用。
- 流量 used/usage 依赖在线状态和所需 Agent 计数器。
- Ping 依赖节点在线、字段存在且结果未过期；单字段缺失只影响该字段。

到期与流量：

- `remaining_time = max(0, expired_at - now)`，单位为秒的 duration。
- 五种流量口径严格为 `sum/max/min/up/down`，未知口径不猜测。
- 使用率允许超过 100%。
- 计算来源为 `agent_counters`，并标记 `reset_sensitive=true`。
- 不在 HA 本地拼接账期流量。

## 生命周期与清理

节点和 Ping 任务使用权威清单的连续缺失计数：

- 只有对应清单请求成功时才增加计数。
- 对象重新出现时立即归零。
- 连续三次缺失后才确认删除。
- status/stats 缺失不等于清单删除。

清理必须限定当前 Entry：

- 节点确认删除后，删除节点 Device 和其 Entity Registry 项。
- 任务确认删除或用户取消选择后，只删除该 Entry 的对应 Ping entities。
- Options 关闭到期/流量实体后，清理对应 registry 项。
- 新节点或新有效可选能力可以动态增加实体，但不能重复添加。

## Diagnostics、日志和 Repairs

Diagnostics 只能输出结构和聚合信息，例如版本、capability 布尔值、节点数量、选项
数量、分支成功时间、错误类别、失败次数和实体数量。

禁止输出：

- API Key、Cookie、Authorization。
- 完整 URL、IP、UUID、名称、备注、任务目标。
- 原始响应、异常正文、标签值和真实监控指标值。

节点 diagnostics 使用 Entry-scoped 哈希，只报告字段是否存在。新增 diagnostics 字段
时必须通过 `async_redact_data` 和项目二次深层脱敏，并添加嵌套泄漏测试。

当前 Repairs：

- `selected_ping_task_missing`
- `ping_entity_limit_exceeded`
- `tls_verification_disabled`
- `metric_capability_unavailable`
- `selected_metric_missing`
- `metric_configuration_invalid`
- `metric_entity_limit_exceeded`

Repair ID 必须包含 Entry 范围；条件恢复或 Entry 删除时自动清除。认证失败只走
Reauth，不重复创建 Repair。

## 翻译与用户可见文本

- 配置、Options、错误、实体和 Repairs 的键同时加入 `en.json` 与 `zh-Hans.json`。
- 两种语言的 JSON 结构和 placeholders 必须一致。
- Python 不拼接固定英文/中文实体名称。
- 任务名称可以作为翻译 placeholder，但不能进入 identity。
- 不创建 `strings.json`。
- 用户可见语义变化时同步更新 `README.md`；开发/验收流程变化时更新
  `DEVELOPMENT.md` 和本文。

## 开发与验收入口

环境初始化、Fake Panel、匿名面板 smoke、CI 和发布步骤统一见
[DEVELOPMENT.md](DEVELOPMENT.md)。功能改动的文件映射见
[komari-development](.agents/skills/komari-development/SKILL.md)，检查范围见
[AGENTS.md](AGENTS.md)。

## 当前范围与未实现功能

当前开发分支已经实现 F16：Metric Definition 发现、用户选择、精确 tag 过滤、受限
预览、节点传感器、Repair、诊断与生命周期清理。实现继续演进时必须保持以下不变量：

- `entity_id + tags` 是 series 身份，`labels` 不参与身份；不从 tags 或 labels 猜测节点。
- 只接受有限数值或 `null`，只为当前可见节点的非空 `entity_id` 创建实体。
- metric key 使用 UTF-8 percent encoding，tags 使用完整 SHA-256；不得改动既有实体 identity。
- 查询固定为四小时、一个点、`last`、不 fill，并受 2 MiB、Definition、series 和实体
  数量上限保护。
- capability、定义或 series 暂时缺失时不得静默清空用户选择；只有三次权威 Definition
  缺失才清理对应 registry 实体。

当前不包含以下功能：

- HACS 默认仓库收录；当前只提供 HACS 自定义存储库。
- WebSocket 与 JSON-RPC batch。
- 完整历史时序导入。
- HA 本地重建权威账期流量。
- Home Assistant Core 官方集成提交。
- 24 小时长稳验收；这项不属于 `v0.3.0` 门禁，不能在发布说明中声称已完成。
