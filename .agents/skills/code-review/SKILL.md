---
name: code-review
description: Review a PR, branch, or diff against its requirements and current repository contracts; distinguish blockers from optional improvements.
---

# 收敛型代码审查

判断改动能否按既定范围交付，报告可定位、可验证的问题。审查结果不自动授权发布评论、批准 PR 或合并。

## 审查范围

- 确认 base、head 和实际 diff；默认 `origin/main...HEAD`。本地未提交改动需同时查看 staged、unstaged 与本次新增文件。
- 读取对应 Issue / 用户请求及已确认的后续决定。没有 Issue 的维护任务按用户请求审查，不为了编号阻塞。
- 只查阅变更相关的规范。数据、事件、HTTP 或业务语义从 [现行契约索引](../../../docs/contracts/README.md) 进入，并遵循 ADR 的显式后继关系；不能把 ADR-0001 已被取代的条款当作现行门禁。`skills-lock.json` 是安装来源记录，不是产品规范。
- 同时核验需求完成度（Spec）和正确性、契约及仓库约定（Standards）。简单 diff 一次审完；复杂且可独立验证的部分可在已授权时分给子 agent，不固定要求两个 agent。

## 阻断与建议

**Blocker**：有证据的功能错误、未完成的验收项、现行契约破坏、安全或隐私问题、数据丢失风险，以及本次引入的构建 / 测试失败。说明触发条件、位置、影响与最小修复方向。无关的既有失败单列，不能归罪于本次改动或伪装成通过。

**Non-blocking / Nit**：命名偏好、代码异味、可选重构与推测性扩展。相同根因合并报告；不限制真实阻断项的数量，也不因风格建议延迟交付。

复审优先检查上轮阻断项及修复影响，目标是在两轮内收敛。不得追加无关需求；修复引入的回归或新证据揭示的实质缺陷仍需报告，并说明为何本轮新增。轮数和数量上限不能强制放过已知错误。

## 结论

- `APPROVE`：审查范围内无已知阻断项；注明哪些验证已执行、跳过或仍待 CI。
- `REQUEST_CHANGES`：存在阻断项，逐项给出证据与修复方向。

报告至少包含：范围 / 轮次、验证状态、阻断项、可选建议、结论。没有问题时简短说明即可；未运行的检查不补写成功。完成本地自检后按开发工作流继续交付；仅在用户要求发布审查时才调用 GitHub review API / CLI。
