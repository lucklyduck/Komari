# Komari Home Assistant 开发与验收指南

本文档说明集成开发、协议调试和本地验收流程。安装、配置和常见问题见
[README](README.md)。

最新已发布版本为 `0.2.0-alpha.1`。当前 checkout 的候选版本为 `0.3.0-rc.1`，在 P1
多面板监控 MVP 上增加了可配置调度、到期与流量限额、Ping 统计、动态 Metric、生命周期
清理、Diagnostics 和 Repairs；它尚不是 GitHub Prerelease 证据。

每次“添加集成”对应一个独立 Komari 面板（一个 Config Entry）。每个面板在
Home Assistant 中创建一个服务设备，面板下的每个 Komari 节点创建一个子设备。
因此可以重复添加多个面板；即使不同面板使用相同节点 UUID，设备和实体也不会
互相合并。

## 已实现范围

- 通过 `{panel_url}/api/rpc2` 使用异步 JSON-RPC 2.0 通信。
- 支持匿名面板和可选 API Key；API Key 以 Bearer Token 发送。
- 支持 TLS 证书验证开关、URL 规范化、重复面板阻止和 API Key 重认证。
- 每个面板拥有独立 API Client、Coordinator、故障状态和数据快照。
- 核心状态、节点清单、Ping 任务、Ping 统计和能力探测分别调度；核心与 Ping 周期
  可按面板配置。
- 节点动态新增；清单刷新失败时保留上次成功的节点集合，连续三次权威清单缺失
  后才清理陈旧节点设备和实体。
- 提供完整 Options Flow 和 URL/TLS Reconfigure；API Key 失效时自动发起 Reauth。
- 到期、续费、计费周期、五种流量限额口径和重置敏感的 Agent 流量计数。
- 发现并选择 Ping 任务，按节点提供最新延迟、丢包率以及可选的
  min/max/avg/P50/P99/stddev；每个面板最多 256 个 Ping 实体。
- 发现、选择并受限查询动态 Metric Definitions；按 `entity_id + tags` 建立稳定节点
  实体，支持精确 tag 过滤、2 MiB 响应上限、128 实体上限及对应 Repairs。
- 提供不含 URL、Key、IP、UUID、名称、任务目标和真实指标值的 Diagnostics。
- 为已选 Ping 任务缺失、Ping 实体超限和 HTTPS 关闭 TLS 验证创建可自动清除的
  Repairs。
- 中英文配置流程和实体翻译。

面板设备提供：

- RPC 连接状态和“任一节点故障”状态。
- 节点总数、在线数和离线数。
- Komari 版本、RPC 版本和构建哈希。

节点设备提供：

- 在线状态和最后采集时间。
- CPU、GPU、内存、Swap、磁盘、1/5/15 分钟负载和温度。
- 网络上下行速率、Agent 启动后累计上传/下载流量。
- 进程数、TCP 连接数和 UDP 连接数。
- 可选的到期、续费、计费周期和流量限额实体。
- 用户选择的 Ping 任务及统计实体；任务名称只用于显示，任务 ID 才参与实体标识。

面板 RPC 断开时，面板连接状态显示为 `off`。节点明确报告离线时，节点连接
状态显示为 `off`、动态数值实体不可用、最后采集时间保留；没有取得节点状态时，
连接状态和动态数值实体均不可用。

## 安装与配置

发布版本可以通过 HACS 自定义存储库
`https://github.com/lucklyduck/Komari` 安装和升级。开发 checkout 仍应将
`custom_components/komari` 复制或链接到 Home Assistant 配置目录的
`custom_components/komari`，重启 Home Assistant，然后在“设置 → 设备与服务”
中添加 **Komari**；不要用 HACS 下载内容覆盖正在开发的源码链接。

配置表单需要：

- 面板 URL，例如 `https://monitor.example.com`。输入末尾的 `/api/rpc2` 会被自动
  移除。
- 可选 API Key。公开面板可以留空。
- 是否验证 TLS 证书；生产环境应保持启用。

同一面板只能添加一次。若要监控多个面板，请分别重复“添加集成”。

## 每面板选项

在集成页面打开“配置”可分别设置：

- 核心状态周期：15～300 秒，默认 30 秒。
- Ping 统计周期：60～1800 秒，默认 300 秒。
- 是否启用到期/续费和流量限额实体。
- 最多 8 个 Ping 任务，以及需要创建的统计项。默认统计项为最新延迟和丢包率。

保存选项只重载当前面板。若所选组合超过 256 个 Ping 实体，表单会阻止保存；
如果后续节点增长导致实际组合超过上限，超出部分不会创建，并会出现 Repair。

“重新配置”可以修改 URL 和 TLS 验证，不会显示或修改 API Key。实际更换 URL 前
需要二次确认，现有 Config Entry、设备和实体标识保持不变。API Key 只能通过
自动发起的 Reauth 更新。

累计上传/下载以及按限额口径折算的流量来自 Komari Agent 计数器。Agent 重启可
能令计数归零，因此集成不会把它当作权威账期用量，也不会在 Home Assistant 本地
拼接账期流量。

## WSL2 开发环境

Windows 工作目录是唯一权威源码和 Git 仓库；Git 命令使用 Windows Git。
Python、测试和 Home Assistant 在 `Ubuntu-22.04 / WSL2` 中运行。虚拟环境、uv
缓存和 HA 配置位于 WSL 的 ext4 用户目录，避免在 `/mnt/c` 下创建大量小文件。

在 WSL 中进入仓库根目录后执行：

```bash
cd /mnt/c/path/to/komari-home-assistant
bash script/bootstrap
```

该脚本安装或复用 uv，安装 Python 3.14.6，并从 `uv.lock` 同步依赖。质量门为：

```bash
bash script/lint
bash script/type-check
bash script/test
```

GitHub 的 `Validate` workflow 会在 push、pull request、每周定时任务和手动触发时
运行 HACS repository validation、Hassfest、Ruff、Pyright 和 Pytest。HACS 与
Hassfest validation 不使用 ignore 项；出现失败时应修复仓库或集成元数据，不能只为
发布而跳过检查。

## 版本发布

发布前必须：

1. 同步 `pyproject.toml`、`custom_components/komari/manifest.json`、README 版本
   徽章、`AI_README.md`、`DEVELOPMENT.md`、测试版本断言和 `CHANGELOG.md`。
2. 运行本地完整质量门和 `git diff --check`。
3. 确认 GitHub `Validate` workflow 的 HACS、Hassfest 和 quality jobs 全部通过。
4. 从待发布提交创建 `v<version>` 标签和完整 GitHub Release；只有标签而没有 Release
   不作为正式 HACS 发布。
5. 在全新 Home Assistant 实例中通过 HACS 自定义存储库验证安装、重启、配置、升级
   和移除。自动化检查通过不能代替该运行时验收。

GitHub 仓库必须保持公开并启用 Issues，同时配置简短 description 和 topics；这些是
GitHub 仓库设置，不保存在 checkout 中。topics 可设为 `home-assistant`、
`hacs`、`komari`、`custom-integration` 和 `server-monitoring`。

启动开发 Home Assistant：

```bash
bash script/develop --log-no-color
```

开发配置保存在 `$HOME/.local/share/komari-ha/config`，源码会自动链接到其中。
Web UI 通常位于 <http://127.0.0.1:8123>。

## 三面板本地验收

第一个 WSL 终端启动三个只监听本机、使用脱敏测试数据的假 Komari 面板：

```bash
bash script/fake-panels
```

它们分别位于 `http://127.0.0.1:18881`、`http://127.0.0.1:18882` 和
`http://127.0.0.1:18883`。三者故意包含相同节点 UUID 和 Ping 任务 ID；A/B 分别
使用 RPC 节点清单的映射与数组响应，C 是使用固定脱敏测试 Key 的私有面板。
第二个终端执行协议级验收：

```bash
bash script/smoke-fake-panels
```

该检查覆盖共享身份隔离、整面板与 Ping/Metric 分支故障、Method Not Found、畸形
Metric 响应、高基数 Metric 响应、节点和任务增删改名、短延迟以及私有面板 Key
轮换。假面板还提供本地验收控制端点：

- `POST /_control/fail`：模拟该面板 RPC 故障。
- `POST /_control/recover`：恢复该面板。
- `POST /_control/add-node`：加入一个固定测试节点。
- `POST /_control/delete-node`、`rename-node`：删除或改名测试节点。
- `POST /_control/add-task`、`remove-task`、`rename-task`：控制测试 Ping 任务。
- `POST /_control/fail-ping`、`recover-ping`：只影响 Ping 分支。
- `POST /_control/fail-metrics`、`recover-metrics`：只影响动态 Metric 分支。
- `POST /_control/method-not-found`、`restore-method`：模拟首选 Ping 方法失效。
- `POST /_control/metric-method-not-found`、`restore-metric-method`：模拟动态 Metric
  查询方法失效。
- `POST /_control/malformed-metrics`、`restore-metrics`：切换畸形/正常的 Metric 响应。
- `POST /_control/high-cardinality-metrics`、`restore-metric-cardinality`：切换 129 个
  series 的高基数 Metric 响应与普通响应。
- `POST /_control/delay`、`clear-delay`：增加或清除短响应延迟。
- `POST /_control/rotate-key`：轮换私有 Fake C 的固定脱敏测试 Key。

如需对获准测试的公开 Komari 面板执行一次匿名只读检查，可运行：

```bash
bash script/smoke-public https://monitor.example.com
```

该命令只执行 RPC2 bootstrap 验证，不打印或持久化面板名称、节点 UUID、地址或
指标值。

## 暂不支持

HACS 默认仓库收录和 HA Core 提交不属于当前阶段。WebSocket、JSON-RPC batch、历史
数据全量导入和在 HA 本地重建账期流量也不在当前范围内。24 小时长稳验收被明确排除
在 `v0.3.0` 发布门禁外，不能将其标记为通过。

当前 checkout 正在准备 `v0.3.0`，但尚未创建 Release。公开面板只应用于匿名只读
兼容性验证；不要在日志、测试 fixture 或报告中保存面板名称、节点 UUID、地址、任务
目标和指标值。生产环境的 HTTPS 面板应保持 TLS 证书验证启用。

## 设计文档

- [总体设计与调研](KOMARI_HOME_ASSISTANT_INTEGRATION_PLAN.md)
- [逐功能代码实现方案](KOMARI_HOME_ASSISTANT_IMPLEMENTATION_PLAN.md)
- [兼容性验收记录](COMPATIBILITY.md)
