---
name: repository-development-workflow
description: Develop changes to repository source, configuration, shared docs, or skills and deliver a verified PR. Excludes personal practicum log generation.
---

# 仓库开发工作流

`practicum-daily-log` 生成或更新个人日志不适用本流程：不检查 GitHub、不同步仓库、不创建 worktree / Issue / PR、不运行仓库 CI。日志中的临时代码、截图和文档也不算仓库开发。混合请求只对明确要求修改仓库文件的部分应用本流程。

交付目标：完成用户要求的改动、适当验证与本地审查，提交可复核的 PR 并检查 CI。用户只要求本地改动或审查时，以该范围为完成条件。不要在首次实现后提前停工，也不要无限扩展任务。

## 范围与授权

- 团队实现以组长发布的 Issue 和已确认的后续决定为依据；用户本轮明确要求优先于旧说明。缺少 Issue 时先查已有任务；只有范围仍不清楚时才询问。
- 纯阅读、答疑、协作规范维护和小型文档修正可直接按用户请求执行，在 PR 写明依据，无需为了编号额外建 Issue。跨模块任务采用 [Issue 约定](../../../docs/agents/issue-tracker.md) 和详细模板。
- 本 skill 的开发交付流程包含推送任务分支、创建 PR，以及记录本任务的验证结果；不扩大到无关 Issue、评论或外部发布。用户限定“本地”时不执行这些远程写入。
- 默认由仓库负责人决定合并；只有负责人明确授权时才代为执行合并。未获合并结果，不关闭 Issue 或回收本任务 worktree。

## 分支与同步

1. 查看 `git status --short --branch`、`git worktree list --porcelain` 和远程地址，保留用户及其他任务的改动。
2. 需要 GitHub 操作时检查一次 `command -v gh`、`gh auth status --hostname github.com`、`gh repo view --json nameWithOwner`。认证或仓库发生变化、调用失败后再重查；不在同一流程重复门禁。不代填凭据。缺失工具时复用已授权的安装范围，否则说明缺口。
3. 修改前 `git fetch origin`。当前在干净的 `main` 才执行 `git pull --ff-only origin main`；在任务分支不要把这条命令当作同步默认分支。同步失败时保留现场，继续可做的阅读或诊断，说明基线未更新，不发布为已同步 PR。
4. 从最新 `origin/main` 建 `codex/<issue-number>-<slug>` 分支和独立 worktree；维护例外可用 `codex/<slug>`。若当前已是本任务的 Codex worktree，检查基线后继续使用，不再嵌套创建。其他任务的分支不复用。

旧 worktree 回收独立于开发；需要清理时才读 [安全回收规则](references/worktree-cleanup.md)，不让清理查询失败阻塞实现。

## 验证与审查

- 按改动选择验证。skill / AGENTS / 文档路由改动运行 `python -B -m unittest discover -s tests -p 'test_agent_skills.py' -v`，并用真实任务场景检查触发、授权与停止条件；其他改动运行受影响测试。依赖与完整本地命令见 [runbook](../../../docs/runbook.md)。
- 提交前用 [code-review](../code-review/SKILL.md) 对实际交付 diff 自检。已提交内容用 `origin/main...HEAD`；尚未提交时还要包含 staged / unstaged / 本次新增文件，避免审到空 diff。
- 修复本次引入的阻断问题，复跑受影响检查；无新增改动、失败或疑点时不重复全套验证。命名偏好、可选重构不阻塞交付。
- 不提交密钥、个人资料、全量源数据、缓存、构建产物或临时截图。报告实际命令、关键结果及跳过项；检查未运行和检查失败都不能写成通过。

## PR 与完成条件

按 [PR 模板](../../../.github/pull_request_template.md) 推送并创建 PR。已有 Issue 则引用；维护例外写明用户请求。数据契约未受影响时标为不适用，无需伪造契约核验。

PR 创建后检查其最新提交的 CI（定义在 [.github/workflows/ci.yml](../../../.github/workflows/ci.yml)），修复本次改动导致的失败。创建 PR 是触发检查的步骤，不能要求 PR 创建前已有 CI 结果。排队、未运行或网络不可达如实标明，不宣称全绿。

完成时给出 PR / 本地改动位置、验证结果和实际限制。合并条件是内容审查无阻断、最新 CI 通过及负责人决定；本地审查通过不等于已批准或已合并 GitHub PR。
