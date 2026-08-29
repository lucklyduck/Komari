# AI 开发说明：Komari for Home Assistant

本文档面向接手本仓库的 AI 编码代理。开始分析、修改、测试或配置集成前，请完整
阅读本文，并以当前检出的源码和测试为最终事实来源。

## 1. 项目速览

| 项目 | 当前约束 |
| --- | --- |
| 集成 Domain | `komari` |
| 集成类型 | Home Assistant 自定义 `hub` 集成 |
| I/O 模型 | `cloud_polling`，全异步，只读 JSON-RPC 2.0 |
| 当前版本 | `0.2.0-alpha.1` |
| Home Assistant 基线 | `2026.8.2+` |
| Komari 基线 | RPC2 `1.0.7+`，实际功能按 `rpc.methods` 探测 |
| Python | `3.14.x`，开发脚本固定安装 `3.14.6` |
| 平台 | `sensor`、`binary_sensor` |
| 依赖管理 | `uv` + `uv.lock` |
| 自动化门槛 | Ruff、Pyright、Pytest，分支覆盖率至少 `95%` |

每个 Home Assistant Config Entry 只代表一个 Komari 面板。每个 Entry 拥有独立的
Client、Coordinator、快照、面板服务设备和节点设备。多面板隔离是项目公共契约，
不能为了简化代码而共享运行时对象或使用裸节点 UUID 作为全局身份。

## 2. AI 必须遵守的规则

1. 先执行 `git status --short --branch` 并阅读相关源码；保留用户已有改动，不覆盖
   与任务无关的文件。
2. 当前源码和测试优先于规划文档。两份 `KOMARI_*_PLAN.md` 描述完整目标与历史
   设计，不能仅凭其中的计划文字判断功能已经实现。
3. 诊断请求默认只读。只有用户明确要求实现、修复或配置时才修改文件或外部状态。
4. 新功能必须保持“一 Entry 一面板”的运行时和身份隔离；至少测试两个 Entry 使用
   相同 node UUID 或 task ID 的情况。
5. 不记录或输出 API Key、Cookie、Authorization、节点 Token、完整 URL、IP、UUID、
   私有备注、任务目标、原始响应或真实指标值。
6. 测试不得访问公网。真实面板只用于用户授权的匿名只读人工验收，响应必须脱敏后
   才能制作 fixture。
7. 不在 Python 属性访问器或实体对象中发起网络 I/O。所有远端数据通过 Client 和
   Coordinator 批量获取。
8. 不按 Komari 版本号硬编码可选功能；使用 `rpc.methods` 和明确的能力回退。
9. 不实现按节点或按任务的请求 fan-out。Ping 必须遵守批量请求、1 小时窗口和每
   series 最多 120 点的约束。
10. 不随意改变 Config Entry、Device 或 Entity unique ID。身份变化属于迁移，必须
    有注册表迁移、冲突处理和升级测试。
11. 自定义集成本地化只维护完整的 `translations/en.json` 与
    `translations/zh-Hans.json`；本项目不创建 `strings.json`。
12. Options 使用 `OptionsFlowWithReload`。不要再增加 Config Entry update listener，
    否则一次保存可能触发两次重载。
13. Windows 工作区中 Git 由 Windows Git 操作；Python、测试和开发 HA 默认在
    `Ubuntu-22.04 / WSL2` 中运行。
14. 除非用户明确要求，不提交、不推送、不合并、不打标签，也不修改远程仓库。

## 3. 事实来源优先级

出现冲突时按以下顺序判断：

1. 当前 checkout 的源码、测试和 `manifest.json`。
2. [README.md](README.md) 与 [DEVELOPMENT.md](DEVELOPMENT.md)。
3. [逐功能代码实现方案](KOMARI_HOME_ASSISTANT_IMPLEMENTATION_PLAN.md)。
4. [总体设计与调研](KOMARI_HOME_ASSISTANT_INTEGRATION_PLAN.md)。
5. Komari 与 Home Assistant 官方文档。

外部 API、Home Assistant 注册表接口或依赖版本可能变化。涉及当前版本兼容性时，先
确认本仓库锁定版本；需要查询互联网时只使用官方文档或上游源码作为技术依据。

## 4. 仓库结构

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

## 5. 运行时架构

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

## 6. Config Entry 与配置流程

### 6.1 Entry data

连接信息保存在 `entry.data`：

| 字段 | 语义 |
| --- | --- |
| `url` | 规范化后的面板根 URL，不含 `/api/rpc2` |
| `api_key` | 可选 API Key；匿名面板不保存空白 Key |
| `verify_ssl` | TLS 证书验证开关，默认 `true` |

Config Entry 当前为 `VERSION=1`、`MINOR_VERSION=1`。修改 data/options schema 时必须
设计幂等迁移；迁移不得改变 `entry_id` 或现有实体身份。

### 6.2 URL 规范化

`normalize_panel_url()` 是唯一规范化入口。必须保持以下行为：

- 只接受 `http` 和 `https`。
- 规范化 scheme、host、默认端口和尾斜杠。
- 移除用户误填的终端 `/api/rpc2`。
- 拒绝 userinfo、query 和 fragment。
- 用规范化 URL 阻止重复面板，但不把 URL 设置为 Config Entry unique ID。
- 面板标题优先使用站点名称，缺失时回退到 hostname。

### 6.3 Reauth、Reconfigure 与 Options

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

配置时估算 `可见节点数 × 任务数 × 统计项数`，超过 256 必须阻止保存。运行中节点
增长导致超限时停止创建超额实体，并创建 Repair。

## 7. RPC2 Client 约束

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

## 8. 模型与解析规则

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

## 9. Coordinator、快照与故障边界

每次核心刷新都请求最新状态；低频分支按单调时钟判断是否到期：

| 分支 | 周期 |
| --- | --- |
| 最新节点状态 | 默认 30 秒，可配置 15～300 秒 |
| 节点清单 | 10 分钟 |
| Ping 任务清单 | 10 分钟 |
| Ping 统计 | 默认 5 分钟，可配置 60～1800 秒 |
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

## 10. Device 与 Entity 身份

### 10.1 Device

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

### 10.2 Entity

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

## 11. 实体创建与可用性

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

## 12. 生命周期与清理

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

## 13. Diagnostics、日志和 Repairs

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

Repair ID 必须包含 Entry 范围；条件恢复或 Entry 删除时自动清除。认证失败只走
Reauth，不重复创建 Repair。

## 14. 翻译与用户可见文本

- 配置、Options、错误、实体和 Repairs 的键同时加入 `en.json` 与 `zh-Hans.json`。
- 两种语言的 JSON 结构和 placeholders 必须一致。
- Python 不拼接固定英文/中文实体名称。
- 任务名称可以作为翻译 placeholder，但不能进入 identity。
- 不创建 `strings.json`。
- 用户可见语义变化时同步更新 `README.md`；开发/验收流程变化时更新
  `DEVELOPMENT.md` 和本文。

## 15. 本地开发环境

实际仓库示例：

```text
Windows: C:\Users\Elecat\Documents\Code\Python\HomeAssistance\komari
WSL:     /mnt/c/Users/Elecat/Documents/Code/Python/HomeAssistance/komari
```

在 PowerShell 中进入 WSL 并初始化：

```powershell
wsl.exe -d Ubuntu-22.04 -- bash -lc "cd /mnt/c/Users/Elecat/Documents/Code/Python/HomeAssistance/komari && bash script/bootstrap"
```

脚本会把虚拟环境放在 WSL ext4：

```text
$HOME/.local/share/komari-ha/.venv
```

uv 缓存也留在 WSL 用户目录，不要在 `/mnt/c` 下另建 `.venv`。`script/_common`
负责设置 `UV_PROJECT_ENVIRONMENT`、缓存和 Pyright Node 环境。

## 16. 配置并运行开发 Home Assistant

启动 Home Assistant：

```powershell
wsl.exe -d Ubuntu-22.04 -- bash -lc "cd /mnt/c/Users/Elecat/Documents/Code/Python/HomeAssistance/komari && bash script/develop --log-no-color"
```

`script/develop` 会：

1. 使用 `$HOME/.local/share/komari-ha/config` 作为 HA 配置目录。
2. 把当前源码 `custom_components/komari` 链接到开发配置。
3. 启动 Home Assistant，默认 Web UI 为 `http://127.0.0.1:8123`。

如果目标链接位置已经是普通文件/目录，脚本会拒绝覆盖；不要用破坏性命令强行删除，
先确认其中是否包含用户数据。

在 HA UI 中通过“设置 -> 设备与服务 -> 添加集成 -> Komari”配置，不支持 YAML
配置。公开面板可只填 URL；私有面板填写专用 API Key。生产 HTTPS 必须保持 TLS
验证开启。

## 17. Fake Panel 与人工验收

启动三个脱敏 Fake Panel：

```bash
bash script/fake-panels
```

另一个终端执行协议 smoke test：

```bash
bash script/smoke-fake-panels
```

Fake A/B/C 覆盖：相同 node UUID/task ID、映射/数组节点清单、私有 Key、故障恢复、
节点和任务增删改名、Ping 分支故障、Method Not Found、延迟和 Key 轮换。

对获准公开面板只执行匿名只读 smoke：

```bash
bash script/smoke-public https://monitor.example.com
```

不要把真实响应复制到对话、Issue、日志或 fixture。必须制作 fixture 时，只保留协议
结构和边界类型，并替换名称、UUID、地址、目标、Token 和真实数值。

## 18. 质量门

每次功能修改都应运行：

```bash
bash script/lint
bash script/type-check
bash script/test
git diff --check
```

当前要求：

- Ruff check 通过。
- Ruff format check 通过，行宽 88，Python 3.14 语法目标。
- Pyright `standard` 无错误。
- Pytest 全部通过，branch coverage 不低于 95%。
- 测试没有公网依赖，fixture 完全脱敏。
- 工作树只包含本任务范围内的修改。

GitHub `Validate` workflow 在 push、pull request、每周定时任务和手动触发时重复执行
HACS repository validation、Hassfest 和上述项目质量门。HACS/Hassfest 检查不得通过
ignore 绕过。CI 通过仍然只属于自动化证据，不代表 HACS 真实安装或面板兼容性通过。

测试应与功能原子出现。根据改动类型至少覆盖：

- 成功、0 值、缺字段、非法结构和未知字段。
- 超时、TLS/HTTP、认证、RPC、Method Not Found 和恢复。
- 两个 Entry 的身份、调度、Options 和故障隔离。
- entity unit、device class、state class、默认启用和 availability。
- reload、unload、restart 后不重复。
- 注册表清理不跨 Entry。
- 日志和 diagnostics 无敏感字符串。

自动化测试通过只证明代码/fixture 行为；真实 HA UI 验收和真实 Komari 兼容性必须
单独报告，不能把静态或 mock 结果描述为真实运行通过。

## 19. 常见改动的文件映射

### 新增一个 RPC 字段

1. 在 `models.py` 增加有类型字段和严格 parser。
2. 增加脱敏 fixture 与 `test_api.py` 边界测试。
3. 在 Coordinator 快照中传递，不让 Entity 读取原始 JSON。
4. 增加/调整实体描述、创建条件和 availability。
5. 补齐双语翻译、身份、两 Entry 隔离和 registry 清理测试。

### 新增一个 RPC 方法或可选能力

1. 在 `const.py` 定义方法名。
2. 在 `api.py` 添加 typed adapter 和能力选择，不泄漏参数。
3. 在 `coordinator.py` 添加独立刷新分支、周期、TTL 和错误边界。
4. 在 `diagnostics.py` 只暴露 capability 布尔值和聚合状态。
5. 若用户可配置，更新 Options Flow 和翻译。
6. 测试 capability 缺失、Method Not Found、认证、局部失败和恢复。

### 新增实体

1. 优先使用 `EntityDescription`，声明 unit/device class/state class/category/default。
2. 明确创建条件和 offline/missing/panel-failure availability。
3. 使用既有 entry-scoped unique ID 规则。
4. 处理动态新增、选项关闭和远端删除后的 registry 清理。
5. 补齐 `en.json` 与 `zh-Hans.json`。
6. 测试 0 值、无数据、两个面板相同远端 ID、reload 和卸载。

### 新增 Option 或改变 schema

1. 在 `const.py` 和 `options.py` 定义默认值、范围和类型回退。
2. 在 `config_flow.py` 增加步骤、验证和实体数量预览。
3. 确保保存只重载当前 Entry 一次。
4. 评估并实现 Config Entry minor/major migration。
5. 测试旧 Entry、重复迁移、非法存储值和多 Entry 隔离。

### 准备版本发布

同步检查：

- `pyproject.toml`
- `custom_components/komari/manifest.json`
- `uv.lock`
- README 徽章和版本文字
- 测试中的版本断言
- `hacs.json`、HACS/Hassfest workflow 和仓库公开状态
- `CHANGELOG.md`、Git 标签和完整 GitHub Release

版本同步后重新运行完整质量门和真实 HA restart 验收。HACS 发布必须从对应标签创建
完整 GitHub Release；仅有标签不能作为已完成发布。仓库 description、topics、Issues
开关和公开可见性属于 GitHub 侧设置，需要在网页或 GitHub API 中单独确认。

## 20. 当前未实现边界

以下内容不应被误认为当前功能：

- F16 动态/自定义 Metric Definition 发现、选择与映射。
- HACS 默认仓库收录；当前只提供 HACS 自定义存储库。
- WebSocket 与 JSON-RPC batch。
- 完整历史时序导入。
- HA 本地重建权威账期流量。
- Home Assistant Core 官方集成提交。

实现 F16 时必须完整处理 definition discovery、内置指标去重、标签 identity、单位
白名单、series/点数/实体上限、Options 过滤、TTL、三次缺失清理、Diagnostics 和
Repairs；不要只把 `queryMetrics` 的原始结果直接暴露成实体。

## 21. AI 交付检查表

完成任务前逐项确认：

- [ ] 已检查 Git 状态并保留用户现有改动。
- [ ] 已说明修改范围和未修改范围。
- [ ] 当前源码、测试、README 和规划之间没有错误地混淆“计划”与“实现”。
- [ ] 多面板、相同 UUID/task ID 和 Entry-scoped identity 没有破坏。
- [ ] 网络请求仍然批量、异步且有界。
- [ ] 核心/可选分支的故障影响范围正确。
- [ ] Config Entry、Device 与 Entity identity 保持兼容或已有迁移。
- [ ] 双语翻译、文档、Diagnostics、Repairs 和 registry 生命周期已同步评估。
- [ ] 日志、fixture、diagnostics 和回复中没有敏感信息。
- [ ] Ruff、格式、Pyright、Pytest、coverage 和 `git diff --check` 已按风险运行。
- [ ] 已把静态检查、自动化测试、真实 HA、真实面板验证边界分别说明。
