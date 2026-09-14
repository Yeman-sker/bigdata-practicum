# Issue tracker: GitHub

本项目的任务范围和验收标准以 GitHub Issues 为准，使用 `gh` CLI 管理。

工作流任务统一参考仓库中的
[`workstream-task.md`](../../.github/ISSUE_TEMPLATE/workstream-task.md)。任务至少要写清楚负责人、项目协调人、契约输入/输出、并行策略、里程碑、给定/当/那么验收、异常降级和可复制的验证证据；不能只写一句功能名称。

## 组长与任务负责人

- 组长是项目协调人：负责发布任务、统一排期、维护契约门禁、处理跨组冲突、组织联调、执行最终验收和决定是否降级。
- 主负责人负责自己工作流的实现、测试、运行证据、下游 handoff 和 PR；不能把“组长负责协调”理解成组长包办所有模块。
- 契约维护人负责字段矩阵、样例和版本追踪；他/她可以维护契约质量，但不自动成为所有模块的实现负责人。
- 每张工作流 Issue 必须把这三个角色分别写出来；如果一个人同时承担多个角色，也必须逐项写明。

## 发布详细 Issue 的最小顺序

1. 先关联父 Issue #4、适用 ADR 和启动门禁 #31。
2. 写清任务目标、用户价值、负责人/协作人、时间盒、Checkpoint A/B/C 和最终截止。
3. 列出输入/输出契约：版本、行粒度、生产者、消费者、字段类型、nullable、时区、单位、主键/去重键和样例。
4. 写出“契约门禁后如何并行”“哪些是运行时依赖但不是启动阻塞”“不能等待谁”和真实数据未就绪时使用什么 fixture。
5. 至少写三条可复现的 Given/When/Then 验收、空数据/非法字段/重复/上游不可用处理、验证命令和预期证据。
6. 写明明确不做、风险、最小降级和完成定义；实现范围发生变化时回到 Issue/Contract Change Issue。

旧的简短 Issue 只能作为导航，不能作为实现依据；实现 PR 以详细 Issue、ADR 和冻结契约为准。

## 团队协作约定

- 所有开发工作从组长发布的 Issue 开始。
- 组员根据 Issue 实现并提交 PR。
- PR 由组长审核，通过后由组长合并。
- PR 可以引用相关 Issue 便于追踪，但 Issue 关联不是合并硬门禁。

## Conventions

- **Create an issue**: `gh issue create --title "..." --body "..."`. Use a heredoc for multi-line bodies.
- **Read an issue**: `gh issue view <number> --comments`, filtering comments by `jq` and also fetching labels.
- **List issues**: `gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'` with appropriate `--label` and `--state` filters.
- **Comment on an issue**: `gh issue comment <number> --body "..."`
- **Apply / remove labels**: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **Close**: `gh issue close <number> --comment "..."`

Infer the repo from `git remote -v`; `gh` does this automatically when run inside a clone.

## Pull requests as a triage surface

**PRs as a request surface: no.** _(Set to `yes` if this repo treats external PRs as feature requests; `/triage` reads this flag.)_

## When a skill says "publish to the issue tracker"

Create a GitHub issue.

## When a skill says "fetch the relevant ticket"

Run `gh issue view <number> --comments`.

## Wayfinding operations

Used by `/wayfinder`. The **map** is a single issue with **child** issues as tickets.

- **Map**: a single issue labelled `wayfinder:map`, holding the Notes / Decisions-so-far / Fog body. `gh issue create --label wayfinder:map`.
- **Child ticket**: an issue linked to the map as a GitHub sub-issue (`gh api` on the sub-issues endpoint). Where sub-issues aren't enabled, add the child to a task list in the map body and put `Part of #<map>` at the top of the child body. Labels: `wayfinder:<type>` (`research`/`prototype`/`grilling`/`task`). Once claimed, the ticket is assigned to the driving dev.
- **Blocking**: GitHub's **native issue dependencies**, the canonical, UI-visible representation. Add an edge with `gh api --method POST repos/<owner>/<repo>/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>`, where `<blocker-db-id>` is the blocker's numeric database id (`gh api repos/<owner>/<repo>/issues/<n> --jq .id`, _not_ the `#number` or `node_id`). GitHub reports `issue_dependencies_summary.blocked_by` (open blockers only, the live gate). Where dependencies aren't available, fall back to a `Blocked by: #<n>, #<n>` line at the top of the child body. A ticket is unblocked when every blocker is closed.
- **Frontier query**: list the map's open children (`gh issue list --state open`, scoped to the map's sub-issues / task list), drop any with an open blocker (`issue_dependencies_summary.blocked_by > 0`, or an open issue in the `Blocked by` line) or an assignee; first in map order wins.
- **Claim**: `gh issue edit <n> --add-assignee @me`, the session's first write.
- **Resolve**: `gh issue comment <n> --body "<answer>"`, then `gh issue close <n>`, then append a context pointer (gist + link) to the map's Decisions-so-far.
