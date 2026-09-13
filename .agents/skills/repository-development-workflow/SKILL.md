---
name: repository-development-workflow
description: Use for code, configuration, documentation, or maintenance changes in this repository; enforces GitHub CLI checks, safe cleanup of merged Codex worktrees, remote synchronization, and issue-driven development.
---

# 仓库开发规范

本规范适用于本仓库的开发类任务。Issue 是范围、验收标准和后续反馈的唯一事实来源；不要把未经确认的额外需求带进实现。

## `gh` 先验门禁与 worktree 回收

每次任务开始时，以及执行 `git worktree add` 前，必须执行一次完整的 `gh` 先验门禁。门禁未通过时，不得删除任何 worktree，也不得创建本次任务的 worktree。

1. **确认 GitHub CLI**：执行 `command -v gh`。未安装时：
   - macOS 且存在 Homebrew：执行 `brew install gh`；
   - Ubuntu/WSL 且存在 `apt-get`：执行 `sudo apt-get update` 和 `sudo apt-get install -y gh`（已是 root 时去掉 `sudo`）；
   - 其他情况，提供 [GitHub CLI 官方安装指引](https://cli.github.com/manual/installation) 并停止流程。
   安装命令失败或安装后仍找不到 `gh` 时停止，不继续后续 worktree 操作。
2. **确认认证**：执行 `gh auth status --hostname github.com`。未认证或认证失效时，引导组员执行 `gh auth login --hostname github.com --web`，随后再次执行上述状态检查。Agent 不代替组员输入凭据；状态检查未成功时停止，不删除、不创建 worktree。
3. **确认仓库权限**：执行 `gh repo view --json nameWithOwner --jq '.nameWithOwner'` 获取当前仓库的 `owner/name`。命令因私有仓库权限不足、网络异常或其他原因失败时，视为门禁失败并停止，不绕过认证。

认证和仓库权限确认成功后，先执行 `git worktree list --porcelain` 回收已完成的并行任务：

- 只考虑同一条 porcelain 记录中同时明确给出路径和 `refs/heads/codex/*` 分支的 worktree；detached HEAD、`main`、非 `codex/*` 分支、路径不可访问或归属无法确认的全部跳过。传给 GitHub CLI 的分支名去掉 `refs/heads/` 前缀。
- 将当前 `git rev-parse --show-toplevel` 对应的 worktree 跳过；比较前将路径规范化为绝对路径。对候选路径执行 `git -C <path> rev-parse --show-toplevel` 确认归属，再执行 `git -C <path> status --porcelain --untracked-files=all --ignored=matching`；任一命令失败或状态有任何输出时保留该 worktree。
- 用 `gh pr list --repo <owner/name> --head <codex-branch> --state all --json number,state,mergedAt,url` 查询 PR。查询失败、无 PR、存在多个 PR，或唯一 PR 不是 `state == MERGED` 且 `mergedAt` 非空时，安全跳过并报告原因。
- 只有候选 worktree 干净且对应唯一 PR 已合并时，才执行 `git worktree remove <path>`。禁止使用 `--force`，不删除本地分支；删除命令失败时保留并报告。

单个候选的 GitHub 查询失败只影响该候选，不能据此删除；重复门禁仍需在创建新 worktree 前通过。门禁通过但清理查询遇到网络或状态不明确时，可以继续任务并报告保留的 worktree。

## 工作流

1. **接收 Issue**：确认 Issue 编号、目标、验收标准和影响范围。没有对应 Issue 时，先请用户提供或确认 Issue；纯阅读、答疑和本规范维护可例外。
2. **同步远程**：开始修改前先检查工作区并同步远程代码。
   - 工作区干净时：执行 `git fetch origin`，再对默认分支（本仓库为 `main`）执行 `git pull --ff-only origin main`。
   - 工作区有本地改动时：不得覆盖、重置或强行合并这些改动；至少执行 `git fetch origin`，并从最新的 `origin/main` 创建后续 worktree。
   - 同步失败或远程历史无法快进时，停止开发并报告原因，不绕过同步门禁。
3. **创建 Codex worktree**：创建前重复完整 `gh` 先验门禁；然后从已同步的 `origin/main` 创建独立分支和 worktree，分支命名为 `codex/<issue-number>-<short-slug>`；所有实现和验证都在该 worktree 中进行，避免直接修改 `main`。
4. **敏捷实现**：以 Issue 验收标准为最小交付切片，优先复用仓库现有实现；每次只做能形成可验证反馈的改动。范围变化先回到 Issue 记录并确认，不做推测性功能。
5. **验证与交付**：运行与改动匹配的最小测试、检查或构建，并把实际结果记录在 PR 中。提交聚焦的变更，推送 `codex/...` 分支，创建 PR 到默认分支。
6. **负责人评估**：Agent 不自行合并 PR。仓库负责人评估代码质量后：
   - 通过：由负责人合并 PR；
   - 未通过或需补充：在 Issue 下发布评估报告，后续修改继续更新同一 Issue/PR，不另起无关分支。

## 交付门禁

- 未同步远程、未在 Codex worktree 中开发或未记录验证结果，不得发布“完成”的 PR。
- 不提交密钥、个人信息、构建产物、缓存和临时截图。
- 未得到负责人合并结果或后续指示前，不关闭 Issue，也不删除仍需复核的 worktree。
