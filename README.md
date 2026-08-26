# Komari for Home Assistant

Komari 的 Home Assistant 自定义集成。当前版本为 `0.1.0-alpha.1`，实现了
P1 多面板监控 MVP。

每次“添加集成”对应一个独立 Komari 面板（一个 Config Entry）。每个面板在
Home Assistant 中创建一个服务设备，面板下的每个 Komari 节点创建一个子设备。
因此可以重复添加多个面板；即使不同面板使用相同节点 UUID，设备和实体也不会
互相合并。

## P1 已实现范围

- 通过 `{panel_url}/api/rpc2` 使用异步 JSON-RPC 2.0 通信。
- 支持匿名面板和可选 API Key；API Key 以 Bearer Token 发送。
- 支持 TLS 证书验证开关、URL 规范化、重复面板阻止和 API Key 重认证。
- 每个面板拥有独立 API Client、Coordinator、故障状态和数据快照。
- 最新状态每 30 秒刷新，节点清单每 10 分钟刷新。
- 节点动态新增；清单刷新失败时保留上次成功的节点集合。
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

面板 RPC 断开时，面板连接状态显示为 `off`。节点明确报告离线时，节点连接
状态显示为 `off`、动态数值实体不可用、最后采集时间保留；没有取得节点状态时，
连接状态和动态数值实体均不可用。

## 安装与配置

当前 alpha 版本尚未发布为 HACS 仓库。手动测试时，将
`custom_components/komari` 复制或链接到 Home Assistant 配置目录的
`custom_components/komari`，重启 Home Assistant，然后在“设置 → 设备与服务”
中添加 **Komari**。

配置表单需要：

- 面板 URL，例如 `https://monitor.example.com`。输入末尾的 `/api/rpc2` 会被自动
  移除。
- 可选 API Key。公开面板可以留空。
- 是否验证 TLS 证书；生产环境应保持启用。

同一面板只能添加一次。若要监控多个面板，请分别重复“添加集成”。

## WSL2 开发环境

Windows 工作目录是唯一权威源码和 Git 仓库；Git 命令使用 Windows Git。
Python、测试和 Home Assistant 在 `Ubuntu-22.04 / WSL2` 中运行。虚拟环境、uv
缓存和 HA 配置位于 WSL 的 ext4 用户目录，避免在 `/mnt/c` 下创建大量小文件。

在 WSL 中执行：

```bash
cd /mnt/c/Users/Elecat/Documents/Code/Python/HomeAssistance
bash script/bootstrap
```

该脚本安装或复用 uv，安装 Python 3.14.6，并从 `uv.lock` 同步依赖。质量门为：

```bash
bash script/lint
bash script/type-check
bash script/test
```

启动开发 Home Assistant：

```bash
bash script/develop --log-no-color
```

开发配置保存在 `$HOME/.local/share/komari-ha/config`，源码会自动链接到其中。
Web UI 通常位于 <http://127.0.0.1:8123>。

## 双面板本地验收

第一个 WSL 终端启动两个只监听本机、数据完全脱敏的假 Komari 面板：

```bash
bash script/fake-panels
```

它们分别位于 `http://127.0.0.1:18881` 和
`http://127.0.0.1:18882`，故意包含相同节点 UUID，并分别使用 RPC 节点清单的
映射与数组响应。第二个终端执行协议级验收：

```bash
bash script/smoke-fake-panels
```

该检查覆盖相同 UUID 的面板隔离、单面板故障与恢复、节点动态发现。假面板还提供
本地验收控制端点：

- `POST /_control/fail`：模拟该面板 RPC 故障。
- `POST /_control/recover`：恢复该面板。
- `POST /_control/add-node`：加入一个固定测试节点。

如需对获准测试的公开 Komari 面板执行一次匿名只读检查，可运行：

```bash
bash script/smoke-public https://monitor.example.com
```

该命令只执行 RPC2 bootstrap 验证，不打印或持久化面板名称、节点 UUID、地址或
指标值。

## P1 已知限制

下列功能已经纳入总体设计，但不属于本次 P1 实现：

- 到期时间、自动续费和流量限额/账期用量。
- Ping 任务发现、选择、延迟和丢包统计。
- 动态/自定义指标发现、筛选和实体映射。
- 完整 Options Flow、URL/TLS Reconfigure（P1 仅包含 API Key Reauth）。
- 远端删除节点后的陈旧实体/设备清理。
- Diagnostics、Repairs、HACS 元数据和发布流程。

网络累计流量是 Komari Agent 启动后的计数器，不应视作权威账期用量。WebSocket、
JSON-RPC batch 和历史数据全量导入也不在 P1 范围内。

## 设计文档

- [总体设计与调研](KOMARI_HOME_ASSISTANT_INTEGRATION_PLAN.md)
- [逐功能代码实现方案](KOMARI_HOME_ASSISTANT_IMPLEMENTATION_PLAN.md)
