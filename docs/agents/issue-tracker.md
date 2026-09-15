# Issue tracker: GitHub

团队任务和验收标准记录在 GitHub Issues，使用 `gh` CLI。开发交付与维护例外以 [repository-development-workflow](../../.agents/skills/repository-development-workflow/SKILL.md) 为准；已确认的后续决定应更新 Issue，避免实现与正文分离。

## 按任务规模记录

- 跨模块工作流使用 [workstream-task.md](../../.github/ISSUE_TEMPLATE/workstream-task.md)：列出主负责人、项目协调人、契约维护人、输入 / 输出、并行依赖、可运行切片、验收与降级。Day 2–7 任务关联 #4、#31 和适用 ADR；这些编号不套用到无关维护。
- 小型修复或维护写清问题、目标行为、范围和验证即可；无需复制完整工作流模板或凑足三条验收。
- 有对应 Issue 的 PR 引用该 Issue。协作规范等维护例外在 PR 写明用户请求，不填空的 `Closes #`，不为编号创建占位 Issue。

组长负责排期、跨组冲突和最终验收；主负责人负责实现、验证、下游交接和 PR；契约维护人负责字段、样例与版本追踪。兼任角色也应在跨模块任务中明确。PR 默认由组长审核并决定合并。

改变跨模块字段、粒度、时间或错误语义时，先在相关 Issue 记录并确认契约变更，再同步权威文档、样例及消费者。只暂停依赖这个决定的实现，其他已明确的工作继续。

## 常用操作

在仓库内运行，`gh` 从远程地址推断仓库；跨仓库操作显式指定 `--repo <owner/name>`。

- 查看：`gh issue view <number> --comments`
- 查找：`gh issue list --state open --json number,title,labels`
- 创建：`gh issue create --title "..." --body-file <body.md>`
- 评论：`gh issue comment <number> --body-file <body.md>`
- 标签：`gh issue edit <number> --add-label "..." --remove-label "..."`

多行正文写入临时文件，保留真实换行后用 `--body-file` 提交。只有任务授权包含对应操作时才创建、评论、改标签或关闭；查阅任务不自动授权处理其他 Issue。

## Pull requests as a triage surface

**PRs as a request surface: no.** 默认不将贡献者 PR 纳入需求分诊；用户明确指定的 PR 可以审查或分诊。是否关闭、发布 review 或合并按本次授权决定。
