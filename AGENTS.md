# 仓库协作说明

## 开发规范 skill

涉及本仓库代码、配置、文档或其他开发改动时，必须先读取并遵循 [repository-development-workflow](.agents/skills/repository-development-workflow/SKILL.md)。该 skill 规定远程同步、Issue 驱动的敏捷开发、Codex worktree、PR 关联 Issue 以及仓库负责人评估和合并流程。

## `practicum-daily-log` 日志任务的临时文件隔离

调用 `.agents/skills/practicum-daily-log` 生成实习日志时，凡是任务执行过程中涉及代码或产生的临时内容，必须放在 WSL 临时目录中，不得污染本代码库。

- 临时任务目录统一使用 `/tmp/practicum-daily-log/`，可在其下按日期或任务名继续分目录。
- 临时编写、复制或修改的 Java/Python/Shell 代码、样例数据、构建目录、编译产物、运行日志、截图、中间文档和转换文件，全部放在该临时目录中。
- 不要在仓库根目录或仓库内新增临时任务目录、源码副本、`target/`、缓存、截图和运行日志；不要为验证任务把临时文件写入 `src/`、`docs/` 或其他代码库目录。
- 执行构建、测试或服务启动时，将工作目录、输出目录和日志路径显式指向 `/tmp/practicum-daily-log/` 下的对应任务目录。
- 只有用户明确要求保留的最终交付物，才能从 `/tmp/practicum-daily-log/` 复制回代码库；复制前确认不包含临时产物、个人信息或无关文件。
- 任务结束后优先保留临时目录供用户复核；如需清理，必须只清理本次任务创建的明确目录，不得影响仓库或其他临时任务。

## Agent skills

### Issue tracker

本项目使用 GitHub Issues 驱动任务。组长发布 Issue，组员提交 PR，组长审核并合并。详见 `docs/agents/issue-tracker.md`。

### Domain docs

本项目采用 single-context 文档布局。详见 `docs/agents/domain.md`。
