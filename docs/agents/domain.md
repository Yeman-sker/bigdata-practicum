# 领域文档导航

本仓库共享一个业务上下文，Python、Java 与前端目录属于不同运行模块。按当前任务读取相关内容；文字修正、工具配置和协作规范维护无需先通读业务文档。

| 当前问题 | 查阅位置 |
| --- | --- |
| 业务术语、当前状态与预测风险的区别 | [CONTEXT.md](../../CONTEXT.md) |
| 页面行为与用户验收 | [product.md](../product.md) |
| 颜色、字体、间距、圆角与组件视觉 token | [DESIGN.md](../../DESIGN.md) |
| 前端空间布局、控件与视觉状态 | [frontend-design.md](../frontend-design.md) |
| 进程、目录责任与模块边界 | [architecture.md](../architecture.md) |
| 字段、数仓、Kafka、规则或 HTTP 变更 | [契约索引](../contracts/README.md)，再选对应契约 |
| 历史决策与条款取代关系 | [ADR-0004](../adr/0004-parallel-development-baseline.md) 及相关 ADR |
| 启动命令、运行证据与集成验收 | [runbook.md](../runbook.md) |
| 分工、依赖和交付节奏 | [delivery.md](../plans/delivery.md) |

使用术语表已有名称；新概念或决策确实影响契约时才补充文档。遇到与现行 ADR 的冲突，明确指出冲突和拟变更范围，不能以旧文档覆盖已接受的后继决定。缺失的可选背景资料不阻塞探索；缺失的验收标准或契约才需要澄清。
