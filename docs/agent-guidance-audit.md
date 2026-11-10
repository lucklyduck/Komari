# 代理指导与工作流审计

核对日期：2026-09-07。范围为 `komari/` 仓库的代理入口、技能、开发说明、
`.github/` 和 `script/`。个人全局技能、Codex 模型设置和远端仓库设置不在修改范围内。

## 官方依据与采用方式

- [OpenAI 当前模型指导](https://developers.openai.com/api/docs/guides/latest-model)：
  核对时默认页为 GPT-6 Astra。采用明确任务范围、避免重复确认、解释阻塞指令和按风险验证的建议。
  本仓库没有模型调用配置，本次不迁移模型或调整推理参数。
- [AGENTS.md 发现规则](https://learn.chatgpt.com/docs/agent-configuration/agents-md)：
  补齐仓库根入口；原有 `AI_README.md` 不是默认发现文件，改为按需参考。
- [官方技能指南](https://learn.chatgpt.com/docs/build-skills)：
  使用 `.agents/skills/<name>/SKILL.md`、明确的 name/description 和渐进加载。
  单个技能只负责本集成功能开发，纯文档和只读审计不自动加载开发流程。
- [uv 锁定与同步](https://docs.astral.sh/uv/concepts/projects/sync/)和
  [setup-uv 缓存说明](https://github.com/astral-sh/setup-uv/blob/main/docs/caching.md)：
  使用 `--locked` 拒绝自动改锁，并保留 CI 设置的缓存目录。
- [GitHub Actions 安全使用](https://docs.github.com/en/actions/reference/security/secure-use)和
  [Dependabot Actions 更新](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/secure-your-dependencies/auto-update-actions)：
  固定 action 提交、保留最小权限，并配置每周版本检查。

以上是本次查阅的依据；具体不变量和验证范围来自本仓库源码、配置与实际执行结果。

## 发现与修改

| 发现 | 处理 |
| --- | --- |
| 仓库没有 AGENTS.md 或 SKILL.md；规则集中在 548 行 AI_README | 新增简短根入口和一个开发技能，保留架构文档作为按需参考 |
| AI_README 要求每次读完全文；规则、质量门、交付清单和开发步骤重复 | 删除重复章节；常驻约定归 AGENTS，操作归 DEVELOPMENT，改动映射归技能 |
| 将源码、设计计划和官方文档排成同一个“事实优先级” | 分清当前行为证据、历史目标和外部 API 契约，避免拿实现本身证明兼容性 |
| 部分“每次”“至少”要求与具体修改无关 | 用修改类型决定检查；身份、隐私、批量请求及迁移约束继续保留 |
| AI_README 的 Options、调度及 Repairs 列表遗漏动态 Metric | 对照 const.py、config_flow.py、repairs.py 补齐现有项 |
| `_common` 覆盖外部 UV_CACHE_DIR/UV_PROJECT_ENVIRONMENT | 保留 WSL 默认值，允许 CI 或调用者传入目录 |
| 普通 uv run 可以自动改锁，bootstrap 在缺锁时重新解析 | 所有执行入口使用 --locked，移除缺锁时自动生成的分支 |
| 已有候选版本修改未同步锁文件，bootstrap 严格检查失败 | uv 在临时副本生成结果，确认只改变虚拟项目版本后同步至 0.3.0rc1；第三方依赖不变 |
| HACS/Hassfest action 使用可变分支；任务无显式超时 | 固定已核实的上游提交，验证任务超时 10 分钟，quality 超时 20 分钟 |
| HACS 默认尝试向 PR 发评论；checkout 持久化凭证 | 配置 comment: false 和 persist-credentials: false，沿用 contents: read |
| 同一 ref 的 schedule、push、手动运行会互相取消 | concurrency 按 workflow、event、ref 分组，保留同组取消旧运行 |

保留现有三个 CI job 和触发范围，不添加路径跳过、ignore 或降低覆盖率门槛。
整理后 AGENTS 为 57 行、技能为 44 行、AI_README 为 348 行；架构文档减少 200 行，
包含新增入口和技能后，这三份文件合计仍比原 AI_README 少 99 行。
AI_README 中已有的 `0.3.0-rc.1` 候选版本文字，以及 DEVELOPMENT 开头的已有修改均保留。
其他原有未提交文件不重写；新增的锁文件变动仅一行项目版本。

## 验证记录

- 在既有 Ubuntu-22.04 / WSL2 环境执行 bootstrap、Ruff、Pyright 和完整 Pytest。
  Ruff 与格式检查通过，Pyright 0 errors，244 项测试通过，含分支的总覆盖率为 95.22%。
- Bash 语法检查覆盖全部 script 文件；实测 `_common` 的默认目录和显式目录覆盖行为。
- 使用 skill-creator 的 `quick_validate.py` 验证技能；检查 YAML、相对文档链接及差异空白。
- actionlint 1.7.12 通过，下载包已核对发布校验和；它发现的 job env 上下文错误已修复。
  本次关闭 actionlint 的 ShellCheck 联动，Bash 语法单独检查。假面板 CLI 帮助加载通过，未启动服务。
- 17 个本地文档链接有效；6 个未涉及的原有脏文件 SHA-256 与开始时一致，`git diff --check` 通过。

上述结果是本地证据。未触发 GitHub workflow，未运行真实 HA UI、HACS 安装或真实面板验收，
也未进行模型行为对比评测；不能把格式校验或文本缩短等同于模型效果已经得到实测提升。

## 后续维护

- 将 Codex 项目根目录设为 `komari/`，在该目录开启新会话以加载根 AGENTS 和仓库技能。
  从外层 HomeAssistance 启动不保证自动发现嵌套仓库的指导文件。
- HACS/Hassfest 的 action 包装代码虽固定到提交，其 Docker 镜像仍跟随上游。
  每周 CI 可发现验证规则变化；Dependabot 不保证追踪无版本标签的分支提交，维护时需单独核对。
- 架构文档中的发布状态是现有记录；真正发布时仍需检查 GitHub Release 和目标提交的 CI。
  本次没有验证或改变远端发布状态。
