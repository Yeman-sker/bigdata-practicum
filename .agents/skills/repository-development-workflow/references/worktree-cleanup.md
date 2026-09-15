# 安全回收旧 Codex worktree

仅在需要清理时执行；清理失败不阻塞其他开发。先确认 `gh` 认证及目标仓库权限。

从 `git worktree list --porcelain` 的同一条记录取得路径和 `refs/heads/codex/*` 分支。以下条件全部满足才允许删除：

- 路径可访问、规范化后不是当前 worktree；非 detached HEAD、非 `main`，且确认没有活跃任务使用它。
- `git -C <path> rev-parse --show-toplevel` 与候选路径一致；`--git-common-dir` 解析后的绝对路径与当前仓库相同。
- `git -C <path> status --porcelain --untracked-files=all --ignored=matching` 成功且无输出；任何未跟踪或忽略文件都保留。
- `gh pr list --repo <owner/name> --head <codex-branch> --state all --json number,state,mergedAt,url` 成功，只返回一个 PR，且 `state == MERGED`、`mergedAt` 非空。分支名去掉 `refs/heads/`。

满足后运行 `git worktree remove <path>`，不加 `--force`，不删除本地分支。任一查询失败、状态不明确或删除失败，都保留并简述原因。不要删除缓存来凑出“干净”的候选。
