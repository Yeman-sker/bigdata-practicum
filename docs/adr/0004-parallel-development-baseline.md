# ADR-0004：Day 2 并行开发基线 v1.1

- 状态：Accepted（设计决定）；随本次文档 PR 合并进入仓库实施基线。
- 日期：2026-09-15。
- 关联：[#4](https://github.com/Yeman-sker/bigdata-practicum/issues/4)、[#31](https://github.com/Yeman-sker/bigdata-practicum/issues/31)、[PR #32](https://github.com/Yeman-sker/bigdata-practicum/pull/32)。
- 决策依据：组长确认 Q1–Q5，并授权直接完成其余必要文档和重新分工，要求敏捷交付、避免过度设计。组员消费签收与实际实现证据仍分别记录，不由本 ADR 代签。

## 决定与取舍

产品、架构、字段、规则、HTTP、排期和运行说明各有唯一权威位置，入口为 [契约索引](../contracts/README.md)。本 ADR 保留取舍及变更边界，不再重复全量字段表。

当前库存状态与一小时风险分别表达。预测决定调度需求，当前保留量与空桩限制可搬数量；当前缺车但预测自然恢复仍保留当前告警。以当前小时基线近似接下来一小时，不增加分钟级预测。接受 greedy 的局部解，完整取整/余额与失效规则换取可解释和可测试的结果。

Kafka 采用单 producer、单分区、顺序消费和显式 snapshot_end；风险与建议一次 MySQL 事务发布，live map 在一次查询中返回两者。选择小规模顺序处理的容量上限，避免引入分布式批次协调。离线采用 staging 校验后整体 DML 发布，接受单月薄表的事务成本，不建立增量版本平台。

根据组长追加要求，Yeman-sker 负责 UI 设计、完整前端及协调验收；S1lco 负责三个 API 和共享 backend 入口；OGATA-LINA 专注 GBFS/Kafka；Hu-tong123 负责离线，1giaowoligiaogiao 负责规则/消费。API 与规则共用一个 Spring Boot 工程/进程，五条流从 Day 3 并行，UI 定稿不作为其他模块的开工条件。具体责任与每天切片以 [delivery](../plans/delivery.md) 为准。

组长追加开发演练后补全实施顺序：先交付可测试的共享入口；collector 在首次历史计算前生成 metadata；DWD 在 RAW 读取边界保留记录位置，ODS 结构不变；库存重放隔离消费组和业务库，只有成功发布才推进录制时钟。这些补全不新增进程、业务能力或 P0，具体修正与证据见 delivery 的演练记录。

## 明确取代关系

| 原文 | v1.1 后继及变化 |
| --- | --- |
| ADR-0001 §3/§4、GBFS 字段映射 | [field_mapping](../contracts/field_mapping.md)：canonical STRING 不变，明确 provider UUID→唯一 short_name 与隔离 ID；源快照时间用 last_updated；内部 last_reported 仍可空 |
| ADR-0001 §5–§7 | [warehouse](../contracts/warehouse.md)：完整物理 DWD、重复定位/标记、非正时长、到达/出发归桶、活跃日补零、sample_days、跨月重算 |
| ADR-0001 §8/§9、ADR-0002 §4/§5 | [operations](../contracts/operations.md)、[OpenAPI](../contracts/openapi.yaml)：current_status/forecast_status 取代混合 risk_type/risk_level；完整异常表；当前/预测分别分类；整数调度及当前保留量；命名为调度目标边界 |
| ADR-0001 §4、ADR-0002 §3 | [events](../contracts/events.md)、OpenAPI：station payload 仍沿用 v1 字段；传输增加 headers 与 snapshot_end 控制记录，控制 key 为明确例外；map 扩展同批风险/建议、来源、版本及新鲜度 |
| ADR-0001 §10 | [architecture](../architecture.md)、warehouse：GBFS raw 保留，不建设无 P0 消费者的 GBFS ODS/DWD、vehicle DIM；P1 不要求独立 overview 表 |
| ADR-0002 页面与回放 | [product](../product.md)：当前/预测切换、异常展示、真实历史回放禁止混入当前库存/风险；默认值、请求竞争和倍速行为完整定义 |
| ADR-0003（Proposed）全部排期与分工 | [delivery](../plans/delivery.md)：原提案撤回，四位组员与负责 UI/前端的组长角色明确；API/UI 从 Day 3 开始，剩余实现天数为 5 |

这些是显式的接口补全/修订，不能将旧 PoC 的 UUID event 或混合 risk_type 当作新基线的最终交付。源验证代码与 Day 1 证据保留；#26–#30 在实现中完成适配，不在文档 PR 提前实现业务。

## 适用范围

保留既有技术版本、STRING ID、纽约时间语义、数据源和五条 P0。最低真实验收为 2025-01；全年与全部 P2 延期，P1 仅在 P0 稳定后加入。发布状态与组长确认见 #31；以后改变跨模块可观察行为须同步 Issue、权威文档、样例和受影响消费者。
