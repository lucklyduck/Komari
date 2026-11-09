# Komari 上游更新审查与逐项处理计划

整理及更新日期：2026-11-10。KUP-01、KUP-02 的代码修复及本地自动化已完成，
真实环境验收另由 KUP-09 跟踪；其他事项按下表继续处理。

本文汇总本轮上游审查发现的缺陷、可改进项和验收缺口，作为后续逐项实施的清单。
已有的代理指导与工作流整理见 [agent-guidance-audit.md](agent-guidance-audit.md)，
其历史验证记录不作为本清单的完成证据。

第一阶段的具体设计、回归矩阵和交付条件见
[phase-1-fix-plan.md](phase-1-fix-plan.md)。该方案已补齐最低版本的 TCP 字段契约及
Ping 空分配语义，并记录第一阶段的实际实现和本地验证。

## 1. 审查基线与证据边界

| 项目 | 本次采用的基线 |
| --- | --- |
| 本项目版本 | `manifest.json`、`pyproject.toml` 均为 `0.3.0-rc.1` |
| 本地 HEAD | `a45a77b76dd42c3d7ce1ba8beba935bbcb5dc0b8`，2026-09-07 |
| 最近主要功能提交 | `2ca96da`，2026-09-06；随后提交包含发布准备和文档更新 |
| 工作区 | 存在既有未提交的文档、脚本、CI 和代理指导改动；后续实现应保留这些改动 |
| 服务端主线审查范围 | 从本项目上次更新时的 `b11ffd3aa7cca03502a75eb64ecbe827d6831d3a` 到 `62af91037924057b961dff7b93beacd3f76a81e8`，47 次提交 |
| 服务端发布范围 | `1.5.0` 至 `1.6.1`，以及期间 RC、预发布和 `Snapshot-2611090908`，共 8 个发布记录；另外核对 `1.5.1` 发布分支 |
| 服务端稳定版快照 | `1.6.1`，对应 `96cf0f79d8cfdf6cc8be54fd1976bbc026d58f7f` |
| Agent 审查范围 | 同一时间窗口内的 17 次主线提交及相关发布，检查到 `ddbdbdcdaeb12abc0e68ba5bc2f0a6b914dad3a7` |
| 本地开发基线 | `uv.lock` 锁定 Home Assistant `2026.8.2`；开发 Python 为 WSL 中的 `3.14.6` |

此次审查没有发现必须迁移核心 RPC 方法的接口变更。节点清单、最新状态、Ping 和
动态 Metric 的主要响应形态仍可被现有代码解析。这个结论来自上游源码核对和合成
响应验证，不等同于全部目标版本已经通过真实 Home Assistant 验收。

### 1.1 已有证据

- 已在 Ubuntu-22.04 / WSL2 的 Python `3.14.6` 中，使用合成数据调用现有解析器和
  Options 估算函数，复现 KUP-01、KUP-02、KUP-04 的具体行为。
- 已用 `AsyncMock` 捕获 Ping 回退请求，确认 KUP-03 所列的参数和响应上限缺口。
- 已验证新 GPU 附加字段、空 Ping 统计、零标准差以及 Metric `series` 响应可解析。
- 这些检查是临时验证，尚未全部转成仓库内可重复运行的回归测试。
- 本轮初始审查未运行完整质量门；后续第一阶段实现已通过 Ruff、Pyright、325 项完整
  测试及含分支的 95.52% 覆盖率。新的 GitHub CI、真实面板、真实 HA UI 或 HACS
  生命周期验收仍未执行。

历史完整测试和 CI 结果保留在 [COMPATIBILITY.md](../COMPATIBILITY.md)。后续执行时
分别记录本地自动化、GitHub CI、真实运行时和 HACS 证据，不能互相替代。

### 1.2 上游更新与处理归属

| 发布 | 相关变化 | 本项目处理方式 |
| --- | --- | --- |
| [1.5.0](https://github.com/komari-monitor/komari/releases/tag/1.5.0) | 修复实时 GPU 使用率，附加 GPU 数量和详情；终端和文件管理增强，Agent v1 移除 | 聚合 GPU 使用率已可直接读取；完善 GPU 发现与回归，见 KUP-04、KUP-05、KUP-08 |
| [1.5.0-fix1](https://github.com/komari-monitor/komari/releases/tag/1.5.0-fix1) | PWA 修复，移除内置流量定时通知 | 无直接代码适配项；保持流量实体语义，见 KUP-07、KUP-08 |
| [1.5.1-rc1](https://github.com/komari-monitor/komari/releases/tag/1.5.1-rc1) | 发布分支构建、前端来源和路由调整 | 核对实际提交差异，无核心 RPC 迁移项 |
| [1.5.1](https://github.com/komari-monitor/komari/releases/tag/1.5.1) | 固定前端发布来源、构建裁剪和 `/admin` 跳转 | 无监控接口适配项 |
| [1.6.0-pre1](https://github.com/komari-monitor/komari/releases/tag/1.6.0-pre1) | 删除部分管理 RPC，增加 Agent 管理能力，调整通知系统与流量增量计算 | 被删除方法均未被本项目调用；流量相关回归归 KUP-08 |
| [1.6.0](https://github.com/komari-monitor/komari/releases/tag/1.6.0) | 通知插件化、WebSocket 压缩与心跳、流量重试和长断报保护 | 继续使用现有只读 HTTP RPC；保持 Agent 累计计数语义 |
| [1.6.1](https://github.com/komari-monitor/komari/releases/tag/1.6.1) | 重构指标和 Ping 查询，Ping 队列独立扩容，数据库兼容性修复，历史 Ping 支持数量限制 | 利用响应限制并补充契约回归，见 KUP-03、KUP-06、KUP-08 |
| [Snapshot-2611090908](https://github.com/komari-monitor/komari/releases/tag/Snapshot-2611090908) | 允许空 Ping 任务；主题目录权限、插件备份和测试调整 | 空任务归 KUP-02；主题、备份和服务端测试调整无直接集成适配项 |
| [Agent 发布](https://github.com/komari-monitor/komari-agent/releases) | GPU、Android/macOS 磁盘统计、安装脚本、LXC uptime 和统计文件写入提示改进 | 输入数据修复通过现有状态读取受益；GPU 归 KUP-04、KUP-05，uptime 归可选 KUP-10 |

## 2. 待办总表

P2 表示影响数值正确性、配置、资源边界或兼容性证据的近期事项；P3 表示能力完善或
可选增强。P3 项目不自动成为稳定版发布门禁。KUP-09 的发布验收要求来自已有开发指南。

| ID | 优先级 | 事项 | 性质及证据 | 当前状态 |
| --- | --- | --- | --- | --- |
| KUP-01 | P2 | 修正 TCP 连接数 | 已有缺陷，正式解析与实体回归已通过 | 代码及本地验证完成 |
| KUP-02 | P2 | 修正 Ping 实体数量估算 | 正式覆盖稀疏分配、历史占用、重载与上限 | 代码及本地验证完成 |
| KUP-03 | P2 | 限制 Ping 回退响应 | 已确认请求缺少上游新增限制和字节上限；尚无真实资源耗尽证据 | 待改进 |
| KUP-04 | P3 | 使用 GPU 数量发现空闲 GPU | 新字段尚未采用；特定条件下的发现缺口已复现 | 待增强 |
| KUP-05 | P3 | 完善逐 GPU 动态 Metric 使用与回归 | 已有框架可承载，完整联动验收待补 | 待完善 |
| KUP-06 | P3 | 明确 Ping 统计来源和近似标志 | 已确认质量字段未保留；统计含义需专项核对 | 待核对并完善 |
| KUP-07 | P2 | 修正文档版本、链接和范围冲突 | 本地文档差异已确认 | 待整理 |
| KUP-08 | P2 | 补齐新上游版本自动化契约回归 | 第一阶段正式回归已补齐，完整版本矩阵待补 | 部分完成 |
| KUP-09 | P2 | 完成真实 HA / 面板 / HACS 验收 | 现有兼容性矩阵与生命周期清单仍为 Pending | 待验收 |
| KUP-10 | P3 | 可选增加节点 uptime 传感器 | 上游已有字段，属于功能提案 | 候选，非发布门禁 |

## 3. 实施时共同保持的约束

执行规则以 [AGENTS.md](../AGENTS.md) 为准，命令、环境和发布流程统一查
[DEVELOPMENT.md](../DEVELOPMENT.md)。本文只维护事项、方案和验收条件。

- Client、Coordinator、快照、故障与注册表操作继续按 Config Entry 隔离。两个面板
  具有相同节点 UUID 或任务 ID 时，数据、选项、实体和清理均不互相影响。
- 保留已有 Entity/Device identity；数值修正或新运行时模型字段不应顺带改变 Config
  Entry schema。确需身份或存储迁移时，单独补充幂等迁移和冲突测试。
- 请求继续只读、异步、复用 HA ClientSession；实体及属性访问器不做网络 I/O。
  按 `rpc.methods` 选方法，字段和参数兼容性根据对应版本源码验证。
- 保留当前 Ping 一小时、120 点参数、8 任务与 256 实体上限；保留动态 Metric 四小时、
  `last`、一个点、`fill_empty=false`、2 MiB 响应和 128 实体等已有上限。
  新增限制需写清含义，不能用提高常量或删除检查掩盖问题。
- 核心失败、可选分支失败和权威清单删除分别处理。可选分支失败不触发跨 Entry 清理，
  不清空已保存的用户选择，不在同一周期反复重试。
- 新测试使用固定合成数据。报告、日志和 Diagnostics 不保存凭证、真实面板或节点信息、
  任务目标、标签值及真实监控值；上面的数值示例均为合成数据。
- Options 继续使用 `OptionsFlowWithReload`；涉及文案时维护完整中英文翻译及一致的
  placeholders，不增加 `strings.json`。

## 4. KUP-01：修正 TCP 连接数

**状态：代码与本地自动化已完成；P2；真实验收归 KUP-09。**

### 证据与影响

上游 `1.0.7`、`1.4.3` 和 `1.6.1` 的 `common:getNodesLatestStatus` 都将 `connections` 填为
`TCP + UDP`，并单独返回 `connections_udp`。修复前 `_parse_status()` 将前者直接赋给
`tcp_connections`，使 TCP 实体包含 UDP 数量。该诊断实体默认禁用，但启用后的数值
及其自动化判断会受影响。

合成复现：真实 TCP 为 10、UDP 为 3；输入 `connections=13`、`connections_udp=3`，
修复前解析得到 TCP=13、UDP=3，正确 TCP 应为 10。

定位：[models.py](../custom_components/komari/models.py) 的 `_parse_status()`、
[sensor.py](../custom_components/komari/sensor.py) 的 `tcp_connections` 描述。
上游依据：[1.0.7](https://github.com/komari-monitor/komari/blob/1.0.7/api/jsonRpc/common.go#L211)、
[1.4.3](https://github.com/komari-monitor/komari/blob/1.4.3/web/rpc/jsonrpc/common.go)、
[1.6.1](https://github.com/komari-monitor/komari/blob/96cf0f79d8cfdf6cc8be54fd1976bbc026d58f7f/web/rpc/jsonrpc/common.go#L389)。

### 处理步骤

1. 第一阶段细化时已核对最低 `1.0.7` 的 `api/jsonRpc/common.go`，同样为总数口径；
   实施时将该契约转为正式合成回归，解析修正无需运行时按版本分支。
2. 对已证实的总数口径，在两个字段均为合法非负整数且总数不小于 UDP 时计算差值。
   缺字段、非法值或相互矛盾的数据保持未知，不强行钳制成正常的 0。
3. 保留 TCP/UDP 实体的 unique ID、翻译键、单位和默认启用状态。
4. 更新合成 fixture 的口径说明与断言，避免测试继续把“总数”当作“TCP”。
5. 在变更说明中交代新采集值会被修正，既有 HA 历史数据不做批量重写。

### 回归与完成条件

- [tests/test_api.py](../tests/test_api.py)：覆盖 `13/3 -> 10`、`10/0 -> 10`、`0/0 -> 0`，
  以及缺失、负值、布尔值、非整数和 UDP 大于总数。
- [tests/test_entities.py](../tests/test_entities.py)：启用诊断实体后检查实际状态，重载前后
  unique ID 不变；两个 Entry 的同 UUID 节点分别计算。
- [x] 历史版本契约有可追溯来源，正确值及未知值行为均有测试。
- [x] 质量门通过，说明文件与实体显示口径一致。

## 5. KUP-02：修正 Ping 实体数量估算

**状态：代码与本地自动化已完成；P2；真实验收归 KUP-09。**

### 证据与影响

修复前 `_ping_entity_estimate()` 使用“所有可见节点数 × 已选且存在的任务数 × 统计项数”，
没有使用任务的 `clients`。新快照允许没有分配节点的空任务，因此估算更容易超过实际
需要的实体数，并触发 `too_many_ping_entities` 阻止保存。

合成复现：32 个节点，任务 A 仅分配给一个节点，任务 B 为新建空任务，选择全部 8 个
统计项；修复前估算为 `32 × 2 × 8 = 512`，当前适用组合仅需 8 个实体。该示例没有旧的
额外统计 series。运行时平台按真实返回的 series 创建实体，空任务本身不会产生实体。

定位：[config_flow.py](../custom_components/komari/config_flow.py) 的
`_ping_entity_estimate()`、`async_step_confirm()` 和 Metric 确认步骤；
[models.py](../custom_components/komari/models.py) 的 `parse_ping_tasks()`；
[repairs.py](../custom_components/komari/repairs.py) 的运行时超限检查。
上游依据：[允许空任务的提交](https://github.com/komari-monitor/komari/commit/10d9463c069a5c0738d8a1ee9cdfebff7771f009)、
[任务调度实现](https://github.com/komari-monitor/komari/blob/96cf0f79d8cfdf6cc8be54fd1976bbc026d58f7f/utils/pingSchedule.go)。

### 处理步骤

1. 第一阶段细化时已确认官方响应的空数组与显式 `null` 都表示当前无分配，缺字段和
   非法结构表示未知。原 parser 把多种情况收敛为空集合；本次实现已保留这一区别，
   具体可信度与降级规则见第一阶段方案。
2. 对权威分配列表，按 `clients` 与当前 Entry 可见节点的交集计算节点/任务对并去重。
   上游当前按 `task.Clients` 下发任务，不能把 `default_on=true` 直接当作覆盖全部
   现有节点的依据。
3. 把仍会保留或重新创建的已存在 series/注册表组合纳入预算。任务刚调整分配时，一小时
   查询窗口中可能仍有旧节点的统计，单看当前分配会低估运行时实体数。运行时同时核算
   保留注册项，补重载与 Repair 回归，防止局部集合重置造成容量遗漏。
4. 对字段缺失、分配未知、过期清单等情况保留保守估算；不因统计暂时为空就判断长期无需
   实体，也不清空用户选择。尚未确认删除的任务继续遵守现有缺失宽限。
5. 让普通确认页、Metric 确认页使用同一估算语义，并与运行时实际创建上限、Repair 配合。
6. 保存选项仍只重载当前 Entry 一次；任务分配变化不改变 task ID 或已有实体身份。

### 回归与完成条件

- [tests/test_options_flow.py](../tests/test_options_flow.py)：覆盖上述 `512 -> 8` 场景、
  仅空任务、部分节点分配、重复客户端、不可见节点、字段未知、分配变更后的历史 series。
- 通过合成场景分别检查 255、256、257 个候选实体；真正超限仍阻止保存。
- [tests/test_entities.py](../tests/test_entities.py)、[tests/test_coordinator.py](../tests/test_coordinator.py)：
  空任务后续分配、新任务开始上报、清单失败、同 ID 恢复及两个 Entry 同远端 ID。
- [x] 合法的小规模分配可以保存，未知分配保持保守，运行时 256 上限仍有效。
- [x] 现有超限测试按明确的任务分配构造数据，保留真正超限的断言。

## 6. KUP-03：限制 Ping 回退响应

**状态：待改进；P2；资源边界缺口，未证实发生过真实资源耗尽。**

### 证据与影响

回退到 `public:getPingRecords` 时，本地仅传 `hours` 和 `task_id` 或 `uuid`。
`1.6.1` 新增了 `max_count`，只有其大于 0 时才限制返回记录数量；本地尚未使用。
本地 `max_points=120` 的后续裁剪发生在响应接收和解析后，不能限制下载体积。
该 Ping 请求也未传入已有 Transport 支持的 `max_response_bytes`。

这个路径用于缺少首选 Ping 统计能力的面板；默认拥有完整能力的面板通常使用
`public:getPingMetricStats`。这里的缺口不能据此写成所有面板都会收到巨大响应。

定位：[api.py](../custom_components/komari/api.py) 的 `async_get_ping_stats()` 和
`_async_read_json_response()`；[const.py](../custom_components/komari/const.py)；
[coordinator.py](../custom_components/komari/coordinator.py) 的 Ping 分支。
上游依据：[新增参数与采样逻辑](https://github.com/komari-monitor/komari/blob/96cf0f79d8cfdf6cc8be54fd1976bbc026d58f7f/web/rpc/jsonrpc/public.go#L295)。

### 处理步骤

1. 记录三个接口的限制含义：首选方法的 `max_points`、公开历史方法的 `max_count`、
   `common:getRecords` 的 `maxCount`。区分每序列点数与整个响应的总点数预算。
2. 为公开历史回退传总记录预算，并检查多节点或多任务的采样公平性；不把单序列 120 点
   参数未经分析地当作所有节点共享的总额。保持已有查询窗口和批量调用约束。
3. 验证旧接口对额外参数的处理。已查 `1.6.0` 的 `BindParams()` 使用
   `json.Unmarshal`，可忽略未知字段；最低支持版本和其他回退形态仍应补充契约测试。
4. 为 Ping 响应设置明确、独立的字节上限，复用现有流式读取逻辑。用最大合法合成场景
   确定阈值，并在常量注释中记录依据；不改变动态 Metric 已有的 2 MiB 限制。
5. 同时检查首选统计和 common 回退是否采用适当响应保护。服务端忽略参数、缺少
   Content-Length 或返回压缩数据时，本地解码后的大小限制仍应生效。
6. 超限按已有安全异常和 Ping 分支失败处理；CPU 等核心数据继续刷新。认证失败仍走
   Reauth，方法缺失仍走能力降级，不记录原始响应或马上发起反复重试。
7. 说明历史采样下的 loss、percentile 等统计语义，交由 KUP-06 统一整理。

### 回归与完成条件

- [tests/test_api.py](../tests/test_api.py)：验证方法选择、参数名、总预算、字节上限；
  覆盖旧服务端忽略参数、多节点密集/稀疏数据、分块响应、超限和畸形响应。
- [tests/test_coordinator.py](../tests/test_coordinator.py)：超限只影响 Ping 分支，按原有
  失败次数和恢复策略工作，另一 Entry 正常；不存在立即重试风暴。
- [tests/manual/fake_komari.py](../tests/manual/fake_komari.py)：必要时增加忽略上限或返回
  大响应的合成模式，供 KUP-08 本地 smoke 使用。
- [ ] 记录数量与解码后响应大小均有可测试边界，合法多节点数据没有因预算分配被静默饿死。
- [ ] 数据缺失、采样和请求失败的行为有明确说明，已有安全与隐私测试通过。

## 7. KUP-04：使用 GPU 数量发现空闲 GPU

**状态：待增强；P3；新字段的发现能力缺口。**

### 证据与影响

上游 `1.5.0` 开始在最新状态中返回 `gpu_count`、`gpu_average_usage` 和
`gpu_detailed_info`，同时修复 `gpu` 的实际使用率。现有聚合 GPU 数值已能直接受益。
但 `_has_gpu()` 仅看有效基础名称或曾观测到大于 0 的使用率，parser 未保留 GPU 数量。

已复现的特定场景：基础 `gpu_name` 缺失，状态明确返回 `gpu_count=1`、`gpu=0`，
当前不会发现 GPU 实体。有有效基础名称的空闲 GPU 不受这个条件限制。

定位：[models.py](../custom_components/komari/models.py) 的 `KomariNodeStatus`、
`_parse_status()`；[sensor.py](../custom_components/komari/sensor.py) 的 `_has_gpu()`
及重载时可选实体检查。上游依据：
[实时 GPU 修复](https://github.com/komari-monitor/komari/commit/1333d63ce0fab3f46394a8317291e618cf9a68e2)。

### 处理步骤

1. 增加可选、严格类型的 GPU 数量模型字段，兼容旧响应没有该字段的情况。
2. 将合法的 `gpu_count > 0` 作为额外硬件证据，保留现有有效名称和正使用率路径。
   单独的 `gpu=0`、`gpu_count=0`、占位名称不证明有硬件。
3. 保留现有聚合实体 ID 和数值来源；不增加新的重复聚合 GPU 实体。
4. 检查新字段对测试构造器、不可变快照、实体动态发现与重载清理的影响。
5. Diagnostics 仅在需要时提供脱敏能力布尔信息，不直接输出 GPU 详情或名称列表。

### 回归与完成条件

- [tests/test_api.py](../tests/test_api.py)：缺失、0、正整数、负值、布尔值和非整数。
- [tests/test_entities.py](../tests/test_entities.py)：有数量无名称的 0% GPU、无硬件的
  0%、旧版有效名称、后来出现正使用率、恢复到 0%、重载和离线可用性。
- [ ] 合成空闲硬件可以发现；无硬件节点不会因占位值创建 GPU 实体。
- [ ] 原聚合 GPU 实体身份和两个 Entry 的隔离保持稳定。

## 8. KUP-05：完善逐 GPU 动态 Metric 使用与回归

**状态：待完善；P3；已有能力的可用性与证据改进。**

上游提供 `gpu.device.usage`、`gpu.memory.used`、`gpu.memory.total` 和
`gpu.temperature`。现有 F16 支持用户选择、精确 tag 过滤、节点归属与稳定 series 身份，
这些 key 未被聚合 GPU 的内置 key 排除。这里首先需要确认现有端到端行为和说明是否
充分，不预设必须新增一套独立平台或专用配置流程。

定位：[config_flow.py](../custom_components/komari/config_flow.py) 的 Metric 选项与预览、
[sensor.py](../custom_components/komari/sensor.py) 的动态名称和单位映射、
[tests/test_metric_support.py](../tests/test_metric_support.py)。
上游依据：[key 定义](https://github.com/komari-monitor/komari/blob/96cf0f79d8cfdf6cc8be54fd1976bbc026d58f7f/internal/metricstore/metrics.go)、
[逐设备写入与 tags](https://github.com/komari-monitor/komari/blob/96cf0f79d8cfdf6cc8be54fd1976bbc026d58f7f/internal/metricstore/report_mapping.go)。

### 处理与验收

1. 构造同节点两张 GPU 的合成 Definition 和 series，覆盖上述四个 key，以及
   `device_index` 等精确过滤，不复制真实设备名称和标签值。
2. 验证发现、预览、保存、重载、单位、0 值、空值、设备暂时缺失和恢复；确认缺失
   Definition/能力不会静默清空选择。
3. 保持完整 `entity_id + tags` 身份，标签顺序变化不产生重复实体，不从 labels 猜测节点。
   新设备、远端完整 tags 改变与显示名变化应分别处理，不自行合并不同 series。
4. 在 README 补充四个 key、按 GPU 过滤示例、保留策略和实体数限制。跨卡总览可继续
   使用已有聚合 GPU 实体；缺少这些数据时说明对应上游能力条件。
5. 只有验证暴露具体缺陷时才修改模型或实体实现；局部补齐需要的双语文案。

- [ ] [tests/test_api.py](../tests/test_api.py)、[tests/test_options_flow.py](../tests/test_options_flow.py)、
  [tests/test_entities.py](../tests/test_entities.py) 覆盖完整链路与两个 Entry 相同 series 身份。
- [ ] 使用当前四小时、一个 `last` 点和精确过滤，不增加额外逐卡网络轮询。
- [ ] 文档示例可由合成面板验证，128 个动态实体上限及 2 MiB 保护保持有效。

## 9. KUP-06：明确 Ping 统计来源和近似标志

**状态：待核对并完善；P3；不认定为此次升级新引入的故障。**

上游 `public:getPingMetricStats` 有 `loss_approximate` 等语义信息，本地
`KomariPingStats` 与 `parse_ping_metric_stats()` 没有保留该标志。该上游字段在本项目
上次更新时的源码中已存在，不能写成 `1.6.1` 新增破坏性字段。

同时，服务端聚合统计与本地历史记录回退分别计算，percentile 和 stddev 的含义需要按
实际算法说明。本地记录路径使用 nearest-rank 和总体标准差，不能据此推断服务端跨
bucket 聚合采用完全相同的算法。

定位：[models.py](../custom_components/komari/models.py) 的 Ping 模型与两个统计 parser、
[sensor.py](../custom_components/komari/sensor.py) 的 Ping 实体、
[diagnostics.py](../custom_components/komari/diagnostics.py)。上游依据：
[统计响应与聚合实现](https://github.com/komari-monitor/komari/blob/96cf0f79d8cfdf6cc8be54fd1976bbc026d58f7f/web/rpc/jsonrpc/public.metric.go)。

### 处理与验收

1. 分别记录首选聚合、公开 records 和 common records 的时间窗口、采样、loss 与
   percentile 口径，并区分已有行为与本次上游变化。
2. 设计可选的统计来源和质量字段，明确标志缺失时是未知还是有契约依据的默认值。
   保持现有数值与实体 ID，不用额外请求追求完全相同的统计输出。
3. 按用户判断价值决定放在实体安全属性、说明或脱敏 Diagnostics 的位置；Diagnostics
   只放安全类别/布尔信息，不加入真实延迟、标签、目标和原始统计响应。
4. 通过测试确定 0 标准差、100% 丢包、latest 缺失、近似标志 true/false/缺失，以及
   切换回退方法时的行为。不要把部分字段缺失扩大成整个面板不可用。

- [ ] 完成正式算法对照与显示方案后，再决定是否需要代码改动。
- [ ] 需要修改时，由 [tests/test_api.py](../tests/test_api.py)、
  [tests/test_entities.py](../tests/test_entities.py)、[tests/test_metric_support.py](../tests/test_metric_support.py)
  分别覆盖数值、属性和隐私边界；需要的中英文文案同步。

## 10. KUP-07：修正文档版本、链接和范围冲突

**状态：待整理；P2；已确认文档差异。**

| 位置 | 当前问题 | 处理要求 |
| --- | --- | --- |
| [README.md](../README.md) 顶部徽章 | 仍显示 `0.2.0-alpha.1`，正文与元数据为 `0.3.0-rc.1` | 与当前候选版统一，不借此宣布稳定版已发布 |
| README 的 RPC 文档链接；两份历史设计计划的引用 | 旧 `/dev/rpc` 路径在此次检查中为 404 | 活跃入口改为[官方 API 文档](https://komari-monitor.github.io/komari-document/dev/api.html)；历史记录只修正引用或注明迁移，不改写历史验收事实 |
| README“当前限制与路线图” | 将 HACS 默认仓库收录列入后续发布门禁，与开发指南的当前范围冲突 | 按 DEVELOPMENT 区分自定义仓库生命周期验收和默认仓库收录，后者保持后续目标 |
| DEVELOPMENT 的发布状态文字 | 开头明确 RC 已发布，末尾“尚未创建 Release”容易误读为连 RC 也未发布 | 明确 `v0.3.0-rc.1` 是 Prerelease，稳定 `v0.3.0` 尚未发布 |
| README 的选项、刷新频率和动态 Metric 使用说明 | 主要表格未完整交代 Metric 的周期、筛选和边界；GPU 使用入口不够具体 | 对照当前源码补齐，结合 KUP-05；不要把计划中功能写成已实现 |
| [COMPATIBILITY.md](../COMPATIBILITY.md) | 仍主要列旧上游版本，且真实矩阵为 Pending | 按 KUP-08、KUP-09 补记录，保留历史行和证据级别 |

### 处理与验收

1. 用 manifest、pyproject 和锁文件确认当前集成版本；核对受影响的版本断言与发布文字。
   本项不要求为文档整理改变版本或依赖。
2. 按上述表格逐处同步；[AI_README.md](../AI_README.md) 仅更新受影响的架构事实，
   [CHANGELOG.md](../CHANGELOG.md) 仅在已有代码变更确实完成后记录结果。
3. 保持 DEVELOPMENT 是操作和发布流程唯一入口，不复制第二套命令或门禁。
4. 检查相对链接、外部引用、字段默认值、双语键和文档之间的范围一致性。

- [ ] 活跃说明不再互相矛盾，已实现、候选提案和待验收状态清楚。
- [ ] 纯 Markdown 修改按 AGENTS 的文档检查交付，不为措辞变化新增测试。

## 11. KUP-08：补齐新版本自动化契约回归

**状态：部分完成；P2；第一阶段正式回归已纳入，完整版本矩阵与新 smoke 待补。**

本项整合 KUP-01 至 KUP-05 涉及的回归，不要求重复建立同一批测试。已有测试仍保留，
但 fake panel 的返回值和断言必须来自核对过的官方契约，不能只模拟本地实现喜欢的形状。

### 合成契约矩阵

| 形态 | 要覆盖的行为 | 证据要求 |
| --- | --- | --- |
| 最低支持版本 `1.0.7` | 核心方法/字段、缺少可选能力、参数兼容 | 已定位 `api/jsonRpc/` 并核对 TCP 字段；其余契约与正式回归继续补齐，不预填通过 |
| `1.4.3` | 已有节点、计数、Ping 与 Metric 形态 | 保留升级前回归基线 |
| `1.5.0` | 正确实时 GPU 使用率与新增 GPU 字段 | 0 值、附加字段、旧字段兼容 |
| `1.6.0` | 流量计数与可选 RPC 的兼容、缺少新数量限制 | 验证新增参数兼容和核心功能独立性 |
| `1.6.1` | 重构后的统计/series、`max_count`、标签与采样 | 检查当前稳定版契约和响应预算 |
| `Snapshot-2611090908` | 空 Ping 任务及后续分配 | 作为独立快照用例，不把快照行为推广为最低版本保证 |

### 实施步骤

1. 将临时复现整理为上述各项的正式用例；补充来源注释和合成 fixture，保留隐私要求。
2. 在 [fake_komari.py](../tests/manual/fake_komari.py) 与
   [smoke_rpc.py](../tests/manual/smoke_rpc.py) 中按必要范围加入新契约场景。
   版本名称只标注 fixture 来源，运行时继续通过 capabilities 选择方法。
3. 持续覆盖三面板同 UUID/task ID、公开/私有面板、Ping/Metric 分支故障、方法缺失、
   Key 轮换、离线恢复、重命名、三次权威缺失与同 ID 重现。
4. 流量回归继续验证计数复位与五种限额口径。服务端 `traffic.up/down` 增量修复不等于
   最新状态的 `net_total_up/down` 获得权威账期语义；不借此在 HA 本地拼账。
5. 每个可审查功能批次完成后按 DEVELOPMENT 运行必要质量门，完整含分支覆盖率至少
   95%。只在新改动、失败或未解决问题需要时扩大或重复测试。
6. 需要发布验证时，再记录对应提交实际运行的 GitHub Validate；不复用旧提交绿灯。

- [ ] 新契约、旧版本和故障恢复均有可重复的自动化结果。
- [ ] 本地 fake panel smoke 与完整质量门有日期、提交和脱敏结果，CI 单独记录。
- [ ] 不把本项结果写入真实 HA 或真实面板的“已通过”单元格。

## 12. KUP-09：完成真实 HA、面板与 HACS 生命周期验收

**状态：待验收；P2；现有稳定版发布门禁。**

[COMPATIBILITY.md](../COMPATIBILITY.md) 中 R1 至 R5 与 HACS 生命周期仍为 Pending。
源码兼容和 fake panel 通过不能关闭这些事项。本项沿用
[DEVELOPMENT.md 的发布流程](../DEVELOPMENT.md#版本发布)，不新增独立操作入口。

### 执行计划

1. 保留原有 R1 至 R5 的目标及未完成状态。补充拟验证的 `1.5.0`、`1.6.1` 行，分别
   记录匿名和 API Key 能力；快照另列为可选试验，不能替代稳定版验证。
2. 确认测试环境、确切 HA/Komari/Agent 版本、认证模式和已有授权。使用已获准范围，
   缺少环境授权时继续本地事项并记录依赖，不访问未经授权的公网面板。
3. 真实面板检查按 AGENTS 的匿名只读授权边界进行。需要验证私有/API Key 流程时，
   先确定受控环境和凭据使用范围，不从匿名授权推导私有访问权限。
4. 分别验证首次配置、重载、HA 与 Komari 重启、十分钟断网恢复、失效 Key 和仅当前
   Entry 重认证、节点离线/在线、增删改名、Ping 分配变化与动态 Metric 恢复。
5. 重点检查修正后的 TCP、合法稀疏 Ping 配置、空任务、聚合/逐卡 GPU 和上限行为；
   检查两个 Entry 同 UUID/task ID 不重复、不串数据、不跨 Entry 清理。
6. 在全新 HA 实例执行 HACS 自定义仓库安装、旧 alpha 升级到目标 RC、直接安装 RC、
   重启、删除 Entry、卸载和清理检查；保留身份与历史关联。
7. 将实际日期、版本、提交、场景和脱敏结果写入兼容性台账。失败项记录根因与重测条件，
   缺少环境的项保持 Pending，不通过删行消除门禁。
8. 若进入发布任务，按已有授权执行版本同步、CI、标签和 Release 流程；发布前再次核对
   当时的上游版本与源码，不把本文件中的“最新稳定版”视为永久结论。

### 完成条件

- [ ] 原有发布必需场景有真实、可追溯结果，新版本兼容声明对应实际证据。
- [ ] HACS 安装、升级、移除有独立生命周期证据，旧身份与用户选择正确保留。
- [ ] Diagnostics、日志和台账均已脱敏，无监听器、任务、Repair 或注册表残留。
- [ ] HACS 默认仓库收录和 24 小时长稳测试仍按已有范围处理，不自动加入本次门禁。

## 13. KUP-10：可选增加节点 uptime 传感器

**状态：候选；P3；需要单独实施的功能增强，不阻塞上述修复。**

服务端最新状态已有 `uptime`，当前模型未采用。Agent 的 LXC 修复让 Linux 容器可读取
自身 `/proc/uptime`，因此可以评估增加低成本的节点运行时长实体。
上游依据：[Agent uptime 修复](https://github.com/komari-monitor/komari-agent/commit/d990cce056352e6c34229f1bde46e3adf2ce4106)、
[状态接口](https://github.com/komari-monitor/komari/blob/96cf0f79d8cfdf6cc8be54fd1976bbc026d58f7f/web/rpc/jsonrpc/common.go)。

### 候选实施与验收

1. 在 `KomariNodeStatus` 中加入可选、非负整数秒字段；确认最低目标版本的字段支持，
   缺失保持未知，合法 0 保留。
2. 采用秒单位的 duration 诊断传感器，默认禁用；复用既有核心刷新，不新增网络请求。
   初始范围只提供服务端报告的时长，不据此猜测精确开机时间。
3. unique ID 继续按 Entry、节点 UUID 和固定实体键组成；基础名称不参与身份。
4. 补齐中英文翻译、README 和 parser/实体测试，覆盖离线、缺失、复位、重载及同 UUID
   的双 Entry；LXC 与物理机差异以实际 Agent 数据为准。

- [ ] 若纳入实施批次，上述行为与质量门通过；若暂缓，保持候选状态并记录理由即可。
- [ ] 不把可选功能未实现记录成既有核心监控故障。

## 14. 分批顺序与依赖

| 批次 | 事项 | 进入条件与预期产物 | 验证 |
| --- | --- | --- | --- |
| A：正确性与配置 | KUP-01、KUP-02 | 按[第一阶段方案](phase-1-fix-plan.md)形成 TCP 修正和 Ping 预算的可审查补丁及回归 | 定向验证后完成所需完整质量门 |
| B：请求资源边界 | KUP-03 | 明确点数/字节预算与旧版参数兼容；形成 API 与分支隔离补丁 | Transport、API、Coordinator 回归及完整质量门 |
| C：GPU 能力完善 | KUP-04、KUP-05 | 可与 B 独立开展；GPU 数量模型和逐卡 Metric 用例可分别审查 | parser、实体、Options、隐私及完整质量门 |
| D：合成验收与文档收口 | KUP-08、KUP-07 | 汇总 A 至 C 的实际完成范围；新版本 fixture、smoke、文档和台账一致 | 所需本地质量门、fake smoke、文档链接与差异检查 |
| E：真实验收与发布准备 | KUP-09 | 自动化达到可测试状态，真实环境与授权可用；产生 Runtime/HACS 证据 | 原有兼容性矩阵和 DEVELOPMENT 发布门禁 |
| F：后续候选 | KUP-06、KUP-10 | 在主修复之外安排；KUP-06 可先完成算法说明，再决定显示改动 | 按实际代码/文档变化执行对应检查 |

KUP-07 中已有的版本徽章、失效链接和范围冲突可以独立修正文档；涉及 A 至 C 新行为的
说明在行为确定后同步。KUP-05 的现有框架验证可先进行，不必等待 KUP-04 的模型变更。
KUP-06 中影响 KUP-03 采样正确性的核对应在 B 批次完成，额外质量属性的展示可后续安排。

每个补丁只完成其明确范围。实现阶段保留已有未提交改动，先审查差异再按用户已有授权
决定提交、推送或发布；本计划不替代相应授权，也不要求重复确认已获准的操作。

## 15. 逐项关闭记录

开始执行时更新状态为“处理中”；只有对应完成条件和必要检查已有证据，才改为“完成”。
代码完成但真实环境尚未验收时，分别记录，不把整项概括为全部通过。

| ID | 当前状态 | 实现/说明提交 | 本地证据 | CI 证据 | 真实验收或剩余依赖 |
| --- | --- | --- | --- | --- | --- |
| KUP-01 | 代码及本地验证完成 | 本记录所在的第一阶段修复提交 | Ruff、Pyright、325 项测试，95.52% 覆盖率；含 TCP 正式回归 | 未执行本项新 CI | 真实运行时归 KUP-09 |
| KUP-02 | 代码及本地验证完成 | 本记录所在的第一阶段修复提交 | 同上；含稀疏/历史/重载容量/Repair 回归 | 未执行本项新 CI | 真实运行时归 KUP-09 |
| KUP-03 | 待改进 | — | 仅有模拟请求捕获 | 未执行本项新 CI | 预算、公平性和旧版本契约待验证 |
| KUP-04 | 待增强 | — | 仅有特定条件的合成复现 | 未执行本项新 CI | 新模型与实体发现回归待补 |
| KUP-05 | 待完善 | — | 有响应形态临时检查，无完整逐卡链路证据 | 未执行本项新 CI | 多 GPU 合成联动待补 |
| KUP-06 | 待核对并完善 | — | 字段与源码初步核对 | 未执行本项新 CI | 算法、质量字段显示方案待定 |
| KUP-07 | 待整理 | — | 文档差异已定位 | 不适用纯文档本地检查 | 新行为说明依赖实际实现 |
| KUP-08 | 部分完成 | 第一阶段修复提交 | KUP-01/02 正式回归已纳入；完整版本矩阵及新 smoke 待补 | 待对应实现提交运行 | 真实兼容由 KUP-09 单独记录 |
| KUP-09 | 待验收 | — | 不能替代 Runtime/HACS 证据 | 待目标提交实际运行 | 真实环境、适用授权及原有矩阵 |
| KUP-10 | 候选 | — | 未实现 | 未执行本项新 CI | 可暂缓，不影响主修复关闭 |

## 16. 资料与记录入口

- [本项目开发与验收指南](../DEVELOPMENT.md)、[架构参考](../AI_README.md)、
  [兼容性台账](../COMPATIBILITY.md)、[版本变更](../CHANGELOG.md)。
- [上游本轮主线对比](https://github.com/komari-monitor/komari/compare/b11ffd3aa7cca03502a75eb64ecbe827d6831d3a...62af91037924057b961dff7b93beacd3f76a81e8)。
- [上游 1.5.1 发布分支对比](https://github.com/komari-monitor/komari/compare/1.5.0-fix1...1.5.1)。
- [上游发布列表](https://github.com/komari-monitor/komari/releases)、
  [Agent 发布列表](https://github.com/komari-monitor/komari-agent/releases)。
- [官方 API 文档](https://komari-monitor.github.io/komari-document/dev/api.html)。

源码事实优先引用本次已核实的 tag 或提交；在线文档可能继续变化。执行时若发现契约
已经改变，应补充新的来源、日期和验证结果，再调整相关条目。
