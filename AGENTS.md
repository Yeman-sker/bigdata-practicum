# 仓库协作说明

## 任务边界

用户当前明确要求及已给出的授权优先于本仓库 skill 的默认流程。先完成已授权的实现、验证与修复；仅对影响范围、数据安全或外部操作的真实缺口提问。若某条本地指令导致暂停，说明文件路径、原文和受影响步骤，并继续不受阻的工作。

修改本仓库源码、配置、共享文档或 skill 本身时，先使用 [repository-development-workflow](.agents/skills/repository-development-workflow/SKILL.md)。它定义分支、Issue、PR 与完成条件；纯阅读和答疑无需运行发布流程。

**生成或更新个人实习日志是独立文档任务，不属于仓库开发。** 即使从本仓库启动或调用仓库分发的 `practicum-daily-log`，也不执行 Git / `gh` 门禁、同步、worktree、Issue、PR 或仓库 CI。临时代码和截图仍属于日志任务；只有用户明确要求修改仓库文件时，才对该部分使用开发流程。

## 按任务查阅

- 产品、模块或数据语义：从 [领域文档导航](docs/agents/domain.md) 选择相关文档，无需每次读取全仓库背景。
- 发布或拆解团队任务：看 [Issue 约定](docs/agents/issue-tracker.md)。跨模块工作流使用详细模板，小型维护按实际范围说明目标和验证。
- 环境部署或运行验收：看 [runbook](docs/runbook.md)；安装大数据组件时使用 `bigdata-env-setup`。
- 实习日志：使用 `practicum-daily-log`。代码、样例、构建、日志、截图和中间文档统一放在执行环境的 `/tmp/practicum-daily-log/<task>/`（WSL 任务放 WSL 内）；仅用户明确要求保留的最终交付物可复制回仓库，复制前检查隐私和无关产物。默认保留临时目录供复核。

## 工具与运行边界

- 本地 fixture 测试可直接运行并修复本次改动造成的失败；真实集群、业务库和完整数据实验按任务范围执行，不能用 fixture 通过代替真实验收。
