# 第一阶段修复方案：TCP 计数与 Ping 实体预算

规划及实施日期：2026-11-10。状态：第一阶段代码与本地自动化验证已完成。
下文保留设计与验收要求，实际执行结果见[实施与验证记录](#9-实施与验证记录)。

本文细化[总处理计划](upstream-update-action-plan.md)的 A 批次，按当前检出源码制定。
实施起点为 `a45a77b76dd42c3d7ce1ba8beba935bbcb5dc0b8`，集成版本为
`0.3.0-rc.1`。工作区已有文档、脚本、CI 和代理指导改动，实施时继续保留。
本文中的节点、任务与数值示例均为合成数据。

## 1. 阶段目标与范围

第一阶段完成两个已有问题：TCP 实体报告真实 TCP 数量；Ping 选项按任务的实际适用
范围估算，允许合法的稀疏分配和空任务，同时保留每 Entry 256 个 Ping 实体的保护。

| 事项 | 本阶段交付 | 关闭依据 |
| --- | --- | --- |
| KUP-01 | 修正状态解析中的 TCP 口径，补齐边界和实体回归 | 正确输入得到差值，异常输入保持未知，实体身份稳定 |
| KUP-02 | 区分已知与未知分配，统一预算中的身份集合，修正表单估算及运行时容量核算 | 稀疏配置可保存；历史实体不漏算；超限受到阻止并提示 |
| KUP-08 的相关部分 | 将这两个问题的合成复现转为正式测试 | 新增回归及原有完整质量门通过 |
| 相关说明 | 同步本阶段的口径、预估文案、变更记录与完成台账 | 只记录实际完成与实际验证结果 |

KUP-03 的 Ping 响应大小、GPU、统计算法与质量标志、uptime、完整版本矩阵和真实
HA/HACS 验收继续按总计划后续批次处理。第一阶段沿用现有只读请求、刷新周期和
查询窗口；版本与依赖也沿用当前基线。

## 2. 已核实的实现依据

### 2.1 TCP 的历史契约已经补齐

最低支持版 `1.0.7` 的源码位置是 `api/jsonRpc/common.go`，其中
`common:getNodesLatestStatus` 明确返回：

```text
connections     = report.Connections.TCP + report.Connections.UDP
connections_udp = report.Connections.UDP
```

来源：[1.0.7 状态映射](https://github.com/komari-monitor/komari/blob/1.0.7/api/jsonRpc/common.go#L211)，
本次读取的文件 blob 为 `231225a0b42a2045f9da02cdf033caea9f08b77e`。
[1.6.1 固定提交的映射](https://github.com/komari-monitor/komari/blob/96cf0f79d8cfdf6cc8be54fd1976bbc026d58f7f/web/rpc/jsonrpc/common.go#L389)
仍采用相同口径。因此，TCP 修正可以统一在解析边界完成，运行时无需按版本分支。

本地问题在 [models.py](../custom_components/komari/models.py) 的 `_parse_status()`：
目前直接把 `connections` 赋给 `tcp_connections`。TCP 和 UDP 的描述位于
[sensor.py](../custom_components/komari/sensor.py)，两个诊断实体均默认禁用。

### 2.2 Ping 的分配语义已经核实

官方公开任务接口把 `task.Clients` 直接投影为 `clients: []string`，没有省略该字段。
调度器也直接使用 `task.Clients`。模型注释明确说明 `default_on` 仅用于新加入的
服务器，不代表任务已经分配给所有现有节点。

来源：[1.4.3 公开任务响应](https://github.com/komari-monitor/komari/blob/1.4.3/web/rpc/jsonrpc/public.go#L208)、
[1.4.3 任务模型](https://github.com/komari-monitor/komari/blob/1.4.3/database/models/pingTask.go#L15)、
[1.4.3 调度目标](https://github.com/komari-monitor/komari/blob/1.4.3/utils/pingSchedule.go#L85)、
[1.6.1 公开任务响应](https://github.com/komari-monitor/komari/blob/96cf0f79d8cfdf6cc8be54fd1976bbc026d58f7f/web/rpc/jsonrpc/public.go#L214)、
[1.6.1 调度目标](https://github.com/komari-monitor/komari/blob/96cf0f79d8cfdf6cc8be54fd1976bbc026d58f7f/utils/pingSchedule.go#L73)。

由这些实现可确定：官方响应中显式的 `[]` 或 nil slice 序列化得到的 `null`，都表示
当前没有分配节点；字段缺失或结构损坏属于未知。`1.6.1` 的
[写入归一化](https://github.com/komari-monitor/komari/blob/96cf0f79d8cfdf6cc8be54fd1976bbc026d58f7f/database/tasks/ping.go#L80)
还会把 nil 转为空数组。解析行为依据字段契约，运行时仍按 capabilities 选择方法。

### 2.3 修复前有三种不同的计数路径

| 位置 | 当前行为 | 本阶段需要处理的边界 |
| --- | --- | --- |
| [config_flow.py](../custom_components/komari/config_flow.py) `_ping_entity_estimate()` | 可见节点数 × 所选现存任务数 × 统计项数 | 未使用 `clients`，会误拦稀疏分配与空任务 |
| [sensor.py](../custom_components/komari/sensor.py) 平台发现 listener | 从真实 stats 创建，并用本次加载的 `known_ping_entity_keys` 限制 256 | 重载后局部集合重新开始，需把仍保留的注册表身份纳入容量 |
| [repairs.py](../custom_components/komari/repairs.py) `update_repairs()` | 当前 stats 对数 × 统计项数 | 当前 stats 暂时缩小时，仍保留的历史实体可能占用容量 |

后两项是源码显示的容量核算风险，需要在实施中先补回归确认具体行为；当前没有真实
面板超限事故的证据。将它们纳入 KUP-02，是为了使新的估算与既有运行时上限配合。
表单预测未来可能创建的实体，Repair 反映已占用和已观测候选，两者无需始终数值相等。

## 3. KUP-01：TCP 计数修正

### 3.1 修改方案

在 `_parse_status()` 中分别用现有 `_optional_non_negative_int()` 解析总数和 UDP。
仅在两者均有效且 `total >= udp` 时返回 `tcp = total - udp`；其余情况令 TCP 为
`None`。UDP 仍按自己的字段独立解析，合法 UDP 不因总数字段损坏而丢失。

继续拒绝布尔值、负数、字符串和浮点数；合法的 0 保留。异常数据不钳制成 0，
单项异常也不扩大为整个状态响应失败。

保留现有模型字段、实体 unique ID、单位、状态类、翻译键和默认启用状态。
本次修正影响后续采集值，HA 中已有的历史值保持原记录。

### 3.2 用例与文件

| 输入总数 / UDP | 期望 TCP | 期望 UDP |
| --- | --- | --- |
| `13 / 3` | `10` | `3` |
| `10 / 0` | `10` | `0` |
| `0 / 0` | `0` | `0` |
| 缺失或非法 / `3` | 未知 | `3` |
| `13` / 缺失或非法 | 未知 | 未知 |
| `2 / 3` | 未知 | `3` |

在 [tests/test_api.py](../tests/test_api.py) 增加参数化解析测试，覆盖映射和数组状态形态。
检查 [latest_status.json](../tests/fixtures/latest_status.json) 及实体测试构造器的说明和
断言；当前 fixture 中的 `10 / 2` 应解释为 TCP=8、UDP=2，不能继续断言 TCP=10。

在 [tests/test_entities.py](../tests/test_entities.py) 实际启用这两个默认禁用的诊断实体，
验证数值、单字段未知、离线状态、重载前后 unique ID 与 entity_id 保持稳定；两个
Entry 使用同一节点 UUID、不同计数时分别取值。避免只检查默认禁用的注册项存在。

## 4. KUP-02：Ping 分配与预算设计

### 4.1 模型保留“未知”和“权威空”的区别

建议将 `KomariPingTask.clients` 定义为 `frozenset[str] | None`：集合表示已知分配，
`None` 表示未知。当前该字段的业务读取点很少，可以在解析边界完成此类型收紧，
避免同时维护集合与额外布尔标记而产生矛盾状态。

| 原始 `clients` | 规范化结果 | 估算行为 |
| --- | --- | --- |
| 合法非空字符串列表 | 去空白、去重的不可变集合 | 与当前 Entry 可见节点取交集 |
| `[]` | 已知空集合 | 分配预测为 0，仍合并历史候选和注册项 |
| 显式 `null` | 已知空集合 | 按已核实的官方 slice 契约处理 |
| 字段缺失 | `None` | 使用保守分配预测 |
| 非列表，或列表中含非法成员 | `None` | 使用保守分配预测，避免部分过滤造成低估 |

空白字符串、布尔值、数值、对象和嵌套数组均是非法成员。合法重复字符串只去重。
保留任务 ID、名称和其他合法元数据；仅分配信息损坏时降为未知，任务 ID 或顶层
结构损坏仍按现有规则报协议错误。

### 4.2 何时信任缓存中的分配

使用分配交集前，检查当前 Entry 的节点清单与 Ping 任务分支：有成功时间、无当前
错误、未超过各自现有刷新周期，且该任务不处于权威缺失宽限中。复用现有
`branch_states`、`ping_task_missing_counts` 和确认删除集合，时间作为显式输入，
便于固定时钟测试；不在实体属性中做 I/O。

分配未知、分支失败、时间无效或清单过期时，对可编辑的已选未确认删除任务，按当前
Entry 的全部可见节点保守预测。清单首次或第二次缺失不当作删除；第三次成功缺失后的
排除和清理沿用现有生命周期规则。

当 runtime、capability 或发现资料整体不可用，Ping 编辑入口继续保持现有的保留选择
行为；预算至少包含仍可确认的历史候选与注册项。没有资料可预测的部分属于未知，
不能把显示的 0 写成“已证明没有实体”。后续恢复和新发现仍受运行时上限保护。

### 4.3 表单按完整实体身份取并集

以当前 Entry 为范围，使用 `(node_uuid, task_id, stat_key)` 表示一个 Ping 实体。
所有集合先按待保存的任务和统计项过滤，再去重：

- `P`：按可信分配交集，或未知分配的保守规则，预测的完整实体身份。
- `O`：快照中已返回的可创建 stats series，展开为待保存的统计项；沿用平台对可见节点、
  所选任务及任务清单的过滤。即使某统计字段值缺失，实体仍可能存在，不能据此减数。
- `R`：当前 Entry 中保存后仍会保留的 Ping 注册项，包含用户禁用的实体。

```text
form_estimate = |P ∪ O ∪ R|
```

`R` 使用实际三元组，不把一个仅有 `latest` 的历史注册项无条件扩成全部统计项。
从 [lifecycle.py](../custom_components/komari/lifecycle.py) 复用
`ping_identity_from_unique_id()`，只枚举当前 Config Entry 的 Komari sensor 注册项。
取消选择的任务/统计项、已确认删除且将按现有规则清理的对象不再占用保存后的预算；
其他 Entry、其他集成以及无法解析的身份不能混入。

任务刚移除某节点分配时，一小时窗口中仍可能返回该节点的历史 series；已经登记的
实体还可能在 stats 暂时消失后继续保留。因此 `O` 和 `R` 均不能只按新的 `clients`
过滤，不能因分配缩小、离线或 TTL 到期就删除历史实体。

示例：32 个可见节点，任务 A 分配一个节点、任务 B 为新空任务，选择全部 8 个统计项，
且无额外历史候选和注册项时，估算由 512 修正为 8。若另有两个旧节点的相同任务
完整历史 series，则估算应为 24，而不能仍显示 8。

首次选择一个从未查询过的任务时，现有快照可能没有它的历史数据。本阶段不增加
专门的 Ping 预览请求；表单结果明确是基于当前分配与已发现数据的估算，后续发现
超限由平台保护。超过 256 的已知预算仍必须阻止保存。

### 4.4 两个保存入口采用相同规则

`async_step_confirm()` 与 `async_step_metric_preview()` 在保存前调用同一预算逻辑。
分别在展示和确认提交时读取当前数据，避免使用长时间停留前的旧估算。

普通确认页显示 Ping 估算；Metric 预览页继续显示 Metric 自己的数量，并采用同一个
Ping 保存检查。保留两种实体的独立上限与各自 placeholder 含义。
保存继续使用 `OptionsFlowWithReload`，只重载当前 Entry 一次。

更新 `translations/en.json` 和 `translations/zh-Hans.json` 的 Ping 预估说明，将
“最多创建”改为基于当前已发现数据的估算，并交代后续发现仍受上限保护；键与
placeholders 保持一致。现有错误键 `too_many_ping_entities` 可以继续使用。

### 4.5 运行时以实际占用守住上限

平台继续从真实 stats 创建实体，不能把当前分配预测当作已观测数据，也不能因某个
历史 series 已不在 `clients` 中就忽略它。

容量核算需要区分“已经登记、占用身份”和“本次加载已提交给 HA 添加”的集合：
前者用于预留容量，后者用于避免重复添加。若把注册表身份全部当作本次已添加，会
导致重载时原有实体没有重新实例化。

设 `K` 为本次平台已添加或正在添加的身份，`R` 为仍保留的注册身份，`O` 为本次
可创建的观测候选，则采用以下规则：

1. 已有身份重新实例化不占新名额；相同身份只添加一次。
2. 新身份只有在 `|R ∪ K| < 256` 时才能加入，提交添加时立即预留名额。
3. 同一数据下按稳定顺序处理新增候选；优先保留已有身份，不轮换淘汰历史实体。
4. Repair 用 `|R ∪ K ∪ O|` 判断是否超限；保留注册项占满容量、stats 缩小时，
   仍能对新候选被阻止的原因给出正确提示。
5. 用户减少选择或权威删除完成后重新核算，条件消失自动清除当前 Entry 的 Repair。
6. 若升级时已有历史注册项超过 256，保留这些身份、停止新增并提示处理；不通过
   随机删除已存在实体把历史状态伪装成已合规。

建议参照现有动态 Metric 路径，由平台计算完整运行时候选并调用独立的
`update_ping_entity_limit_repair()`。`update_repairs()` 中移除旧 Ping 数量公式，
避免同周期另一个 listener 用旧计数覆盖结果；任务缺失和其他 Repair 继续走原路径。
复用现有平台 listener，不增加 Options update listener。

## 5. 代码组织与改动边界

| 文件 | 预计修改 |
| --- | --- |
| [models.py](../custom_components/komari/models.py) | TCP 差值解析；Ping `clients` 已知/未知建模和严格规范化 |
| `custom_components/komari/ping.py`（拟新增） | 纯集合计算：分配预测、观测候选及身份并集；不依赖网络或 HA 注册表 |
| [lifecycle.py](../custom_components/komari/lifecycle.py) | 复用现有身份解析，按需增加 Entry 范围的注册身份读取 helper |
| [config_flow.py](../custom_components/komari/config_flow.py) | 获取当前分支可信度和注册身份，替换估算，统一两个保存入口 |
| [sensor.py](../custom_components/komari/sensor.py) | 分离登记占用与本次添加，保留 256 上限，输出实际候选数 |
| [repairs.py](../custom_components/komari/repairs.py) | 平台拥有 Ping 容量判断，Repair 只同步安全计数与上限 |
| [coordinator.py](../custom_components/komari/coordinator.py)、[__init__.py](../custom_components/komari/__init__.py) | 以现有分支状态和删除信息为输入；仅在联动测试证明必要时调整接线 |
| [英文翻译](../custom_components/komari/translations/en.json)、[中文翻译](../custom_components/komari/translations/zh-Hans.json) | 精确描述预估和历史占用，维持键及 placeholders 一致 |
| 测试、fixture 与相关 Markdown | 补正式回归，记录已核实契约和本阶段实际结果 |

纯计算 helper 只接收类型明确的值，不反向引用 Coordinator、OptionsFlow 或实体类。
此处的新模型字段与计算过程均在运行时；沿用现有 Entry schema、Device/Entity 身份，
无需仅为这两个问题增加配置迁移、依赖、RPC 或额外持久化状态。

## 6. 回归矩阵

### 6.1 Ping 必须覆盖的行为

| 场景 | 期望结果 | 主要测试位置 |
| --- | --- | --- |
| 32 节点、稀疏任务加空任务、8 统计项 | 预估 8，可保存 | `test_options_flow.py` |
| 仅空任务，分别使用 `[]` 和 `null` | 无历史时预估 0；保留选择 | `test_api.py`、`test_options_flow.py` |
| `default_on=true` 且当前分配为空 | 不扩成全节点；以后分配可动态发现 | `test_options_flow.py`、`test_entities.py` |
| 重复客户端、只含不可见客户端、重复 series | 取交集并去重，不泄漏或串入其他节点 | parser 与预算测试 |
| 缺字段、非法结构、混合非法成员 | 分配未知，具备编辑能力时保守预测 | parser 与 Options 测试 |
| 清单失败、过期、无成功时间、未来时间或缺失宽限 | 不把旧空分配当作权威空；不增加删除次数 | Options、Coordinator 测试 |
| 当前分配缩小，但仍有旧 stats / 已登记实体 | `P/O/R` 去重合并，历史占用不漏算 | 预算与实体测试 |
| 历史注册项只有部分统计项，或用户禁用 | 以实际身份计数；禁用不释放注册容量 | 预算与实体测试 |
| 新选任务尚无 stats；空任务后来开始上报 | 表单采用分配预测，运行时按真实 series 创建 | Options、实体测试 |
| 普通确认页与 Metric 预览页，期间清单变化 | 提交时重新计算，使用同一 Ping 限制 | `test_options_flow.py` |
| 同一个旧实体在重载后重新出现 | 正常实例化，unique ID/entity_id 不变 | `test_entities.py` |
| 旧注册项占满 256，重载后出现新 series | 旧身份保留，新身份不突破容量，Repair 正确 | `test_entities.py` |
| stats 缩小但旧注册项仍占容量 | Repair 不因仅看最新 stats 而误清除 | `test_entities.py` |
| 取消选择或第三次权威缺失，随后同 ID 恢复 | 按既有规则清理、释放容量，恢复不重复 | Coordinator、生命周期与实体测试 |
| 两个 Entry 使用相同 node UUID/task ID/stat | 估算、实例化、容量和 Repair 均各自隔离 | Options、实体测试 |
| 整体发现不可用，修改其他选项 | 保留原任务与统计选择，重载当前 Entry 一次 | `test_options_flow.py` |

255、256、257 的边界使用单个统计项构造，避免“全部 8 个统计项”只能得到 8 的
倍数。255/256 允许，257 阻止保存；另测合并后恰好 256、以及两个 Entry 各 256。

运行时单独覆盖“当前已占用数 + 新身份数”，包括全部 8 个统计项导致新增过程在
256 停止、重复 listener 通知、重载和先前已超限的升级场景。

### 6.2 测试落点与来源

- [test_api.py](../tests/test_api.py)：TCP 数值与异常边界、`clients` 各种原始形态。
- `tests/test_ping.py`（拟新增）：以合成输入测试集合并集、保留身份与容量决策，
  断言具体缺陷场景的结果，避免仅复刻函数实现。
- [test_options_flow.py](../tests/test_options_flow.py)：真实多步表单、两条保存路径、
  提交前变化、超限错误和只重载一次。
- [test_entities.py](../tests/test_entities.py)：启用 TCP 诊断实体、Ping 实体实例化、
  注册表容量、Repair、重载与多 Entry 隔离。
- [test_coordinator.py](../tests/test_coordinator.py)：沿用清单失败不计删除、三次权威
  缺失、空任务后分配与恢复；只在现有覆盖不足处补用例。

来源矩阵保持小而明确：`1.0.7` 覆盖本次确认的核心计数字段，`1.4.3` 与固定
`1.6.1` 覆盖任务分配契约，再加入空任务合成场景。fixture 标注来源与合成性质；
缺少可选方法时仍按 capabilities 降级，不据此声称全部版本已经通过真实兼容验收。

## 7. 实施顺序与阶段交付

| 顺序 | 工作包 | 产物与进入下一步的条件 |
| --- | --- | --- |
| A0 | 固定契约和回归输入 | 本文已补源码依据；实施时把依据转成 fixture 注释和失败用例，记录现有工作区与基线结果 |
| A1 | TCP 独立修正 | 解析、诊断实体和同 UUID 双 Entry 用例通过；形成独立可审查差异 |
| A2 | Ping 模型与纯预算 | 已知空/未知保真，`P/O/R` 算法与边界测试通过 |
| A3 | Ping 表单、平台与 Repair 联动 | 两个保存入口一致；重载、历史占用与 Repair 回归通过；A2/A3 共同构成完整 KUP-02 补丁 |
| A4 | 质量门与说明收口 | 运行开发指南要求的完整检查，更新实际变更与关闭台账，交付第一阶段结果 |

A1 可以独立交付；A2/A3 联动完成后再将 KUP-02 标为代码修复完成。
如果容量回归暴露更大的生命周期问题，先明确失败场景和必要改动，再在本阶段的
同一问题范围内修复，不顺带重写动态 Metric 或共享生命周期。

执行入口与命令统一使用 [DEVELOPMENT.md](../DEVELOPMENT.md#wsl2-开发环境)。
实施功能修改时加载仓库的 komari-development 技能，按实际修改选定回归；之后必须
完成 lint、type-check、完整 test，保留含分支的 95% 覆盖率门槛，并使用 Windows Git
执行差异检查。定向用例不替代完整质量门；已通过后仅因新改动或未解决失败重跑。

若实际修改了 fake panel 的数据或交互，还需按开发指南执行对应本机 smoke。
本阶段的测试全部使用脱敏合成数据；真实 HA UI、真实面板和 HACS 结果单独记录。
本次实现已执行上述功能质量门；新的 GitHub CI 与真实环境验收仍需单独执行。

## 8. 完成条件与结果记录

- [x] KUP-01：最低版本依据已转为可追溯测试，TCP 正确值及未知值规则均通过。
- [x] KUP-02：稀疏配置与空任务可保存，未知与过期分配保持既定保守行为。
- [x] 预算并集覆盖历史 stats、注册项、禁用项和 Entry 隔离，重载不会绕过容量。
- [x] 两条保存路径在提交时使用同一估算；保存只重载当前 Entry 一次。
- [x] 正常起始状态不新增到 256 以上；历史超限状态不再增长，Repair 可随处理清除。
- [x] 重载、删除、同 ID 恢复、离线和分支失败不改变既有身份及故障隔离规则。
- [x] 中英文翻译键和 placeholders 一致，预估文案与真实行为一致。
- [x] 完整本地质量门与差异检查通过；结果绑定实际源码状态和日期。
- [x] CHANGELOG 与总计划只关闭已完成部分；KUP-08 完整矩阵及 KUP-09 仍独立跟踪。

| 工作包 | 当前状态 | 实施后的记录要求 |
| --- | --- | --- |
| A0 | 已完成 | 测试注释记录固定版本来源，244 项原有测试基线通过 |
| A1 | 已完成 | 解析与已启用诊断实体回归通过，包含重载及同 UUID 双 Entry |
| A2/A3 | 已完成 | 稀疏/空/未知/历史/重载/上限/双 Entry/Repair 回归通过 |
| A4 | 本地验证与说明已完成 | Ruff、Pyright、325 项测试及 95.52% 覆盖率；CI 与真实验收未执行 |

第一阶段验收通过后，再进入总计划 B 批次的 Ping 请求资源边界处理。

## 9. 实施与验证记录

本记录与第一阶段修复位于同一 Git 存档提交，父提交为 `a45a77b`，保留集成版本
`0.3.0-rc.1` 与锁定依赖。实际提交标识以 Git 记录为准。

- `models.py` 严格解析总数与 UDP，计算 TCP 差值；`clients=None` 表示分配未知，
  显式 `null` 与空列表归一为已知空集合。
- 新增 `ping.py` 维护预测、观测及保留身份的纯集合运算；注册表读取限定当前 Entry
  的 Komari sensor，包含禁用项。表单两条确认路径均在提交时重新核算。
- 平台分别维护注册占用与本次添加身份，优先重新实例化旧身份，立即预留新增名额；
  Repair 由平台的占用与候选并集驱动，普通 Repair listener 不再覆盖其计数。
- 回归先在旧实现中复现 TCP、稀疏配置与重载容量问题，再验证修正；正式测试总数
  从 244 增至 325，增加 81 项用例。原有清理、恢复与隐私测试继续通过。
- 完整本地 `lint`、`type-check`、`test` 通过，Pyright 为 0 errors / 0 warnings，
  含分支的覆盖率为 95.52%。两个新增测试/实现模块均进入完整质量门。
- README、CHANGELOG 和中英文预估/Repair 说明已同步；本次修改的文档链接、翻译
  键与 placeholders、差异空白一并检查。

没有修改假面板服务或其交互，本次没有另外运行 live fake-panel smoke。测试使用
HA 的真实平台加载器和合成 RPC 响应，属于本地自动化。新的 GitHub CI、真实 HA UI、
真实面板和 HACS 生命周期均未执行，KUP-09 保持待验收。

当前工作区已有的脚本、CI 和代理指导改动保留，不合并进第一阶段修复存档。
