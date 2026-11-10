---
name: komari-development
description: 实现或修复 Komari Home Assistant 集成的 RPC、Coordinator、实体、配置迁移与生命周期，并选择回归验证。用于本仓库功能开发；纯文档、通用 HA 使用或只读审计无需加载此技能。
---

# Komari 功能开发

目标是完成用户要求的行为变化，保持既有身份、面板隔离和故障边界，并提供对应验证。
沿用仓库 [AGENTS.md](../../../AGENTS.md) 的范围和授权约定。

## 定位改动

从仓库根目录操作。先读取相关源码和现有测试，按下面的变化类型选择文件；不要求遍历
全部模块。架构细节在 [AI_README.md](../../../AI_README.md)，环境与命令在
[DEVELOPMENT.md](../../../DEVELOPMENT.md)。

| 变化 | 实现路径（相对 `custom_components/komari/`） | 回归重点 |
| --- | --- | --- |
| RPC 字段/方法 | `const.py`、`api.py`、`models.py`、`coordinator.py` | 脱敏 fixture、0/null、非法结构、能力缺失、认证与恢复 |
| 实体或动态 Metric | `sensor.py`/`binary_sensor.py`、`entity.py`、`coordinator.py` | 创建条件、稳定身份、离线/缺失语义、数量上限、动态增删 |
| Options/schema | `options.py`、`config_flow.py`、`__init__.py` | 旧配置、幂等迁移、非法存储值、保存只重载一次 |
| Device/清理 | `device.py`、`lifecycle.py`、`__init__.py` | 两 Entry 相同远端 ID、权威缺失计数、重现恢复、清理范围 |
| 用户界面/诊断 | `translations/`、`diagnostics.py`、`repairs.py` | 双语键与 placeholders、嵌套脱敏、Repair 创建及清除 |

新增字段通过 typed parser 和快照进入实体；复用现有适配器与 EntityDescription。
对外行为变化时更新 README；流程变化时更新 DEVELOPMENT；只维护受到影响的架构章节。
不要仅因修改一个字段而扩展为全面重构、发布或真实面板验收。

## 验证与完成

开发环境未就绪时按 DEVELOPMENT 初始化。复现缺陷后补充能暴露该行为的回归测试，
并完成 AGENTS 对该类改动要求的检查。针对单文件调试可先运行：

```bash
bash script/test tests/test_coordinator.py --no-cov
```

将文件替换为实际受影响的测试。这个定向命令不提供完整覆盖率证据；交付功能修改时仍
运行完整 `bash script/test`。仅在需要验证本地协议联动时启动 Fake Panels；该 smoke
会改变假面板状态，不能指向真实面板或冒充 HA UI 验收。

如果工具或环境失败，记录脱敏错误和已完成项，继续可独立完成的检查；需要用户信息
或权限才能继续时明确指出依赖。不要降低覆盖率门槛、跳过 HACS/Hassfest 或擅自更新
依赖来让检查通过。交付行为变化、回归结果及剩余验证边界。
