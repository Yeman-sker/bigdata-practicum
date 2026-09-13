---
name: code-review
description: "Convergence-focused code review along two axes: Standards (documented repo standards and data contracts) and Spec (originating issue/spec acceptance criteria). Categorises findings strictly into Blockers vs Non-blocking Nits, enforces a two-round convergence ceiling to prevent moving goalposts, and issues an actionable verdict (APPROVE or REQUEST_CHANGES). Use when reviewing a branch, PR, or diff."
---

# Convergence-Focused Code Review (收敛型代码审查规范)

本规范用于对代码变更（PR、分支、worktree 或针对基线的 diff）进行两轴审查：
- **Standards（规范与契约轴）**：代码是否遵循本仓库已冻结的 ADR 架构决策、数据契约及开发规范？
- **Spec（业务与规格轴）**：代码是否忠实实现了对应 Issue 规定的 Acceptance Criteria（验收标准）？

---

## 核心原则：交付导向与止血机制 (Core Philosophy)

1. **只有 3 类问题允许作为阻断项 (Blockers)**：
   - 破坏核心数据契约（如 ADR 规定的 Schema 字段、`station_id` 类型、时区语义）。
   - 破坏主干健康（CI 流水线失败、单元测试不通过、将数 GB 真实原始大文件/密钥提交入库）。
   - 核心业务功能未满足 Issue 的主要验收标准（Acceptance Criteria）。
   除上述 3 类外，所有代码风格、Fowler 异味、非关键边缘情况、命名喜好**一律归为 Non-blocking / Nits，绝不阻断合并**。

2. **焊死球门，严禁后移 (No Moving Goalposts)**：
   复审（Round 2+）时，审查者的唯一职责是核验上一轮提出的 Blocker 是否解决。**严禁在复审轮次抛出第一轮未提及的新代码问题作为阻断项**。上一轮阻断项只要修复且测试通过，必须立即给出 `APPROVE`。

3. **手持剪刀，不拿显微镜**：
   审查的目标是“让代码在可控质量下尽快流向主干”，而不是借审查追求无休止的代码洁癖。

---

## 审查流程 (Process)

### 1. 确定比对基线与审查轮次
- 确定比对点：`git diff <fixed-point>...HEAD`（默认对比 `origin/main...HEAD`）。
- **识别审查轮次**：
  - 检查 PR 历史评论或提交记录。
  - 若该 PR 此前已有审查记录且提交者发起了修复，进入 **Round-2+ 复审增量模式**。
  - 若该 PR 为首次审查，进入 **Round-1 初审基线模式**。

### 2. 锚定 Spec 与 Standards 事实来源
- **Spec 来源**：通过提交信息中的 Issue 编号（`#<id>`、`Closes #<id>`）拉取 Issue 完整内容，以其中的 **Acceptance Criteria** 与 **Scope** 为唯一规格基准。
- **Standards 来源**：仓库内已冻结的 ADR 文档（如 `docs/adr/`）、`AGENTS.md`、`skills-lock.json`。
- **Fowler 异味基线处理原则**：Fowler 代码异味（如 Repeated Switches、Duplicated Code、Mysterious Name）**仅作为非阻断参考**。严禁将任何代码异味升级为 Blocker。

### 3. 双轴并行审查 (Two-Axis Sub-agents)

#### Spec Sub-agent 职责：
- 严格对照 Issue 中的 Acceptance Criteria 清单逐项打勾。
- 区分“核心验收项未完成”（Blocker）与“边缘健壮性优化建议”（Nit）。
- 检查是否存在未经确认的严重 Scope Creep（推测性功能）。

#### Standards & Contract Sub-agent 职责：
- 严格核验是否违反 ADR-0001 等冻结契约（例如 `station_id: STRING`、时间格式定义、目录分层）。
- 核验是否引入未脱敏真实大文件数据或环境硬编码。
- 检查代码异味，且最多只提取 1~2 条最具维护价值的重构建议作为 `[Non-blocking / Nit]`。

### 4. 裁决规则 (Verdict Gate)

#### 若处于 Round-2+ 复审增量模式：
- 仅对照上一轮审查列出的 Blocker 清单。
- 若上一轮 Blocker 均已修复，且 CI / 单元测试全绿：**强制给出 `VERDICT: APPROVE`**。
- 本轮新发现的任何微小瑕疵只能记入 `[Non-blocking / Nits]`，不得推翻通过结论。

#### 若处于 Round-1 初审基线模式：
- 若存在 1 个及以上致命契约破坏、CI 测试挂掉或核心验收项缺失：给出 **`VERDICT: REQUEST_CHANGES`**（Blocker 列表力求聚焦，严禁罗列超过 3 条）。
- 若核心功能闭环、无致命破坏（即使存在多条 Nits）：给出 **`VERDICT: APPROVE`**。

---

## 统一输出模板 (Standard Output Format)

审查报告必须严格按以下结构输出，拒绝模糊不清的漫谈：

```markdown
## Review 结论: [APPROVE | REQUEST_CHANGES]

**审查分支**: `<base>...<head>` (PR #<number>)
**审查轮次**: [初审 Round 1 | 复审 Round 2+]
**自动化门禁**: [✅ CI / 单测全绿 (X/X tests) | ❌ 存在失败用例]

---

### 🔴 阻断项 (Blockers - 必须修复方可合入)
<!-- 严格限制为契约破坏、CI失败或核心验收项缺失。若无阻断项，写“无阻断项，满足合入标准”。最多不超过3条。 -->
1. **[契约/功能/安全]**: 明确说明位置与违背的规范，给出具体修改方案。

---

### 🟡 建议项 (Non-blocking / Nits - 允许带入主干)
<!-- Fowler 异味、命名美化、轻微重复、边界防御。明确标注不阻断合入，建议后续或本次可选优化。 -->
- **[优化建议]**: ...

---

### 💡 组长裁决动作 (Actionable CLI)
<!-- 提供直接可执行的 GitHub CLI 指令 -->
若通过：
gh pr review <number> --approve --body "LGTM! 契约与核心测试已验证通过。"
gh pr merge <number> --merge

若需修改：
gh pr review <number> --request-changes --body "请仅修复上述 Blocker，其余建议项不阻断合入。"
```
