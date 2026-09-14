# Day 2 文档收口记录

2026-09-14 建立规划，2026-09-15 根据 Q1–Q5 和组长“完整写好所有文档、重新分配任务”的授权收束为 v1.1。原草案的未决项已经迁入以下权威文件，本文不再维护第二份排期或字段定义。

| 交付 | 位置 | 结果 |
| --- | --- | --- |
| 领域术语 | [CONTEXT](../../CONTEXT.md) | 当前、预测、调度、两种回放与标识符 |
| 产品 | [product](../product.md) | 五条 P0、线框、默认值与异常行为 |
| 架构 | [architecture](../architecture.md) | 进程、交接、唯一写入者与目录责任 |
| 数据/事件/规则 | [契约索引](../contracts/README.md) | ID/时间、DWD/DWS/服务表、完整批次、风险与整数调度 |
| HTTP | [OpenAPI](../contracts/openapi.yaml) | 三个 GET、字段、参数、null、错误、上限与样例 |
| 共享数据 | [fixture](../../fixtures/day2/README.md) | 原始输入、预期表、API/Kafka 与异常算例 |
| 并行任务 | [delivery](delivery.md) | 四位实现负责人，Day 3 同时开始，逐日可演示切片 |
| 运行/验收 | [runbook](../runbook.md) | 现有检查、目标入口、真实验收与证据边界 |
| 旧文档承接 | [ADR-0004](../adr/0004-parallel-development-baseline.md) | 明确取代关系，保留 Day 1 历史证据 |

讨论确定：当前库存和未来风险分别表达；地图和列表按同一口径切换；粒子始终代表实际库存；调度按预测需求但受当前可行性限制；过期/无效/停服暂停相关预测与建议。剩余实现选择按敏捷和最小复杂度完成并写入上述文档，不再要求组长逐条确认常规细节。

文档 PR 的完成是交付可审查的规格、样例与任务安排。组长评审/合并、各组员实际消费签收、真实业务实现与最终验收仍要在 [#31](https://github.com/Yeman-sker/bigdata-practicum/issues/31) / 各实现 Issue 中留下真实证据，不能预先勾为完成。
