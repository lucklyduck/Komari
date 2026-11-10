# Komari 仓库协作约定

本文件适用于整个仓库。项目是只读、异步 JSON-RPC 2.0 的 Home Assistant 自定义集成，
Domain 为 `komari`，实体平台为 `sensor` 和 `binary_sensor`。

## 任务范围

- 先查看 `git status --short --branch` 和相关源码，保留已有未提交改动。
- 仅审查或解释的请求保持只读；实现、修复、优化请求直接完成范围内的修改和验证。
  常规实现选择自行判断；缺少会实质影响结果的信息时才询问，并继续不依赖答案的工作。
- 用户的明确要求优先于仓库和技能中的工作建议；系统、运行环境的权限限制仍然有效。
  已授权的操作不重复确认。提交、推送、合并、打标签、发布和远程配置需有对应授权。
- 若本地指令导致暂停或要求确认，给出文件链接、具体条款和适用原因。

## 按需查阅

- 判断现状以检出源码和测试为证据；版本与依赖看 `manifest.json`、`pyproject.toml`
  和 `uv.lock`。验证外部契约时查对应版本的官方文档或上游源码，不用现有实现证明自身正确。
- [AI_README.md](AI_README.md)：按主题读取架构、身份、故障和隐私约束；无需每次全文加载。
- [DEVELOPMENT.md](DEVELOPMENT.md)：环境、质量门、本地验收和发布流程的唯一操作入口。
- [komari-development 技能](.agents/skills/komari-development/SKILL.md)：功能实现与回归验证的文件映射。
- 两份 `KOMARI_*_PLAN.md` 是设计与历史目标，`COMPATIBILITY.md` 是验收记录；均不证明当前状态。

## 代码审查重点

- 一 Config Entry 一面板：Client、Coordinator、快照、故障状态和注册表操作按 Entry 隔离。
  改动身份或共享生命周期时，覆盖两个 Entry 使用相同 node UUID/task ID 的情况。
- Device/Entity identity 不依赖名称、URL 或当前值；改变既有身份或配置 schema 需幂等迁移、
  冲突处理与升级测试。动态 Metric 身份包含 `entity_id + tags`，不从 labels 推断节点。
- 网络请求集中在 Client/Coordinator；实体和属性访问器不做 I/O。复用 HA ClientSession，
  保持只读、有超时、批量且有界；可选能力按 `rpc.methods` 探测，不按版本号猜测。
- 保留 `const.py` 中 Ping/Metric 的查询窗口、响应和实体数量限制。
  核心失败、可选分支失败和权威清单删除分别处理，清理不能跨 Entry。
- 日志、diagnostics、fixture 和报告不得泄漏凭证、私有面板/节点信息或真实监控值。
  具体脱敏字段见 AI_README 的隐私章节；测试使用脱敏数据，不访问公网面板。
  真实面板验收仅在用户授权后执行匿名只读检查。
- Options 使用 `OptionsFlowWithReload`，不要另加导致重复重载的 update listener。
  翻译维护完整的 `translations/en.json` 和 `translations/zh-Hans.json`，保持键和
  placeholders 一致；本项目不创建 `strings.json`。

## 验证与交付

Windows 工作区使用 Windows Git；Python/HA 默认在 Ubuntu-22.04 的 WSL2 环境运行，
Linux CI 直接运行相同脚本。依赖由 `uv.lock` 管理，初始化用 `bash script/bootstrap`。

| 改动 | 本地验证 |
| --- | --- |
| 仅 Markdown/技能说明 | 链接、指令冲突、技能 frontmatter、`git diff --check` |
| Python、测试、依赖或影响执行的脚本 | `bash script/lint`、`bash script/type-check`、`bash script/test` |
| CI/YAML | YAML/Actions 语法、触发与权限审查，执行涉及的本地命令 |
| 集成/HACS 元数据 | JSON/TOML 一致性、对应测试；HACS/Hassfest 由 CI 验证 |
| 发布 | DEVELOPMENT 中的发布门禁，按用户授权执行 |

按变化补充有意义的回归测试；不为纯措辞变化新增测试。完整测试保留分支覆盖率至少 95%。
必要检查通过后，只有新改动或未解决问题才扩大或重复测试；每次交付检查 `git diff --check`。
用简洁中文说明结果、关键文件、实际验证和未完成项；区分本地静态/自动化、GitHub CI、
真实 HA UI 和真实面板证据。失败时给出脱敏根因，不能用跳过检查替代通过。
