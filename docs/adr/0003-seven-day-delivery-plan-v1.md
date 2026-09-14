# ADR-0003：7 天敏捷交付计划与任务分工 v1

- 状态：Proposed / 待团队确认
- 日期：2026-09-14
- 关联：[Issue #4：Product & Data Contract v1](https://github.com/Yeman-sker/bigdata-practicum/issues/4)、[Issue #31：全部接口契约矩阵与冻结门禁](https://github.com/Yeman-sker/bigdata-practicum/issues/31)、[#26–#30 工作流任务](https://github.com/Yeman-sker/bigdata-practicum/issues/26)
- 上位决策：[ADR-0001](0001-product-data-contract-v1.md)、[ADR-0002](0002-day2-dashboard-and-operations-v1.md)
- 适用范围：从 Day 1 起共 7 天的项目开发周期；本文只规定交付顺序、任务边界、负责人和验收，不改变已冻结的数据语义

## 背景

此前计划只有按天的概要，没有明确每天要交付什么、依赖谁、怎样验收以及时间不足时删什么，也没有把跨模块接口设计和实现分开。本 ADR 将 7 天计划改成“契约先行、实现并行、最后集成”的可执行敏捷切片。

当前状态如下：

- Day 1 已完成真实 Historical Trips、GBFS 2.3、Source Validator、HDFS/Hive 和 Spark 读取验证。
- Day 2 已完成产品范围、地图首页、回放、风险、调度建议、最小 API 字段和 Vite + React 基线的讨论；ADR-0002 记录产品与接口边界。全部跨模块接口契约仍由 #31 收口，尚未视为完成。
- 项目仍按 Day 1 起算共 7 天；进入 Day 3 时，剩余 6 天。

项目唯一交付目标是：

> 用真实历史骑行数据和 GBFS 站点库存，完成一个可解释、可复现、可演示的“当前态势 → 历史规律 → 一小时风险 → 调度建议 → 地图展示”闭环。

## 决策

### 1. 开发节奏

采用敏捷切片，不按“所有后端完成后再做前端”的瀑布顺序推进。每一天都必须形成一个可以运行或验证的结果：

1. 开始时确认输入契约、依赖和当天的最小结果；
2. 中途先交一个可运行的薄切片，尽早暴露数据或环境问题；
3. 当天结束前补齐测试、运行证据、文档和 PR；
4. 只有 CI 通过、负责人完成审查后，才把该切片视为完成。

Day 2 之后不再新增 P0 用户故事。新想法进入 P2 或单独的变更 Issue，不在当前 7 天内插入。

### 2. 全部接口契约先行

Day 2 的第一优先级不是写业务代码，而是完成 #31 的契约矩阵。所有成员共同设计和评审；每个接口由一名负责人维护，但任何负责人不能单方面改变跨模块语义。

契约门禁关闭前，允许用最小 fixture 讨论字段和验证样例，不进入共享业务实现。门禁关闭后，#26–#30 同时启动，各工作流使用同一版契约并行开发。

| 契约 | 维护负责人 | 生产者 → 消费者 | 必须冻结的内容 |
| :--- | :--- | :--- | :--- |
| Historical source / manifest v1 | S1lco | 官方 ZIP → #26、#28 | URL、月份、ZIP/CSV 命名、全量 CSV、count、size、checksum、质量指标、幂等和失败处理 |
| GBFS station status event v1 | OGATA-LINA | GBFS adapter → #27、#29 | provider 映射、字段类型/nullable、UTC/local 时间、version、Kafka topic/key、replay 语义 |
| HDFS RAW/ODS 与数仓 v1 | Hu-tong123 | #26/#27 → #28、#29 | HDFS 路径、分区、DDL、表名、粒度、source-faithful 边界、批次/快照标识 |
| DWD/DIM/DWS/ADS v1 | 1giaowoligiaogiao | #28 → #29、#30 | 表字段、主键/粒度、非零 OD、风险枚举、阈值、调度公式、无基线和离线状态 |
| Spring Boot API v1 | Yeman-sker | #28/#29 → #30 | 用例、请求参数、统一响应、空数据/错误状态、freshness、日期/小时语义 |
| Vite + React view model v1 | Yeman-sker + 全员评审 | API → 前端页面 | live/replay、筛选、单日时间轴、倍速、stations/flows、Tab、抽屉、loading/error 状态 |
| 运行与验收证据 v1 | Hu-tong123 + Yeman-sker | 全部工作流 → 负责人 | 版本、命令、fixture、真实数据边界、输出格式、环境缺口和验收记录 |

每项契约必须有唯一版本、生产者、消费者、负责人、字段类型/nullable/单位/时区、最小样例、异常样例和验收命令。#31 的验收完成后，才允许把 #26–#30 视为可启动的实现任务。任务正文统一参考 [`workstream-task.md`](../../.github/ISSUE_TEMPLATE/workstream-task.md)，不得只写功能标题。

### 3. 范围优先级

- P0：实时地图、单日历史回放、站点历史分析、一小时风险识别、调度建议。
- P1：地图顶部运营总览 KPI；只有 P0 稳定后才实现，时间不足时整体删除。
- P2：ML、天气、AI/LLM、VRP、逐车 GPS、登录、复杂组件库和复杂动画系统，本周期不做。

### 4. 工作流与负责人

负责人是该工作流的主交付人，不代表其他成员不参与评审。Day 2 契约阶段由全员共同参与，@Yeman-sker 作为组长负责协调、排期、冲突解决、门禁确认和最终验收，@1giaowoligiaogiao 负责 #31 的矩阵汇总与契约质量追踪；Day 3 起各工作流拿同一版契约并行实现。组长不作为任何单一模块的唯一实现人，每个工作流都必须向下游提供字段、样例、运行命令和验收证据。

| 工作流 | 负责人 | GitHub 任务 | 主要交付 | 主要日期 |
| :--- | :--- | :--- | :--- | :--- |
| Historical 数据与质量 | [@S1lco](https://github.com/S1lco) | [#26](https://github.com/Yeman-sker/bigdata-practicum/issues/26) | Historical downloader、manifest、`dwd_trip_v1`、质量对账 | Day 3–4 |
| GBFS 与实时事件 | [@OGATA-LINA](https://github.com/OGATA-LINA) | [#27](https://github.com/Yeman-sker/bigdata-practicum/issues/27) | GBFS raw 快照、`station_status_event_v1`、Kafka/replay | Day 3–5 |
| HDFS/Hive/Spark 数仓 | [@Hu-tong123](https://github.com/Hu-tong123) | [#28](https://github.com/Yeman-sker/bigdata-practicum/issues/28) | RAW/ODS、`dim_station_v1`、flow/profile/OD 聚合 | Day 3–4 |
| 风险与调度规则 | [@1giaowoligiaogiao](https://github.com/1giaowoligiaogiao) | [#29](https://github.com/Yeman-sker/bigdata-practicum/issues/29) | 风险计算、`ads_station_current_risk`、调车建议 | Day 4–5 |
| 项目协调与集成 | [@Yeman-sker](https://github.com/Yeman-sker) | [#30](https://github.com/Yeman-sker/bigdata-practicum/issues/30) | 统一排期、契约门禁、跨组冲突、集成、端到端演示与发布；不包办单一模块实现 | Day 2、6–7 |

任务分配依据已有仓库贡献方向；如果团队成员确认后需要更换负责人，只更新本 ADR 和对应 Issue，不在代码分支中隐式变更。#26–#30 没有互相阻塞的“启动依赖”；它们只有在合并真实数据时才存在数据输入依赖，等待期间必须使用 #31 中的 fixture 并行开发。

#### #30 服务与前端的协作拆分

为了避免组长成为瓶颈，#30 不是“组长一个人包办 API 和前端”，而是由组长协调以下并行子任务：

| 子任务 | 首要协作人 | 交付边界 |
| :--- | :--- | :--- |
| DWS/ADS 查询适配与 API 数据访问 | Hu-tong123 | 将已冻结的表/字段映射为 API 可消费的数据，不修改业务语义 |
| 实时模式数据接入 | OGATA-LINA | 将 `station_status_event_v1`/replay 接入实时地图所需的响应，不生成逐车 GPS |
| 历史回放数据接入 | S1lco | 将历史日期/小时 flow 接入回放响应，遵守数据库可用日期范围 |
| 风险/调度 UI 状态与解释 | 1giaowoligiaogiao | 将结构化 risk/rebalance 字段映射为固定展示状态，不新增 AI 文案 |
| 架构整合、冲突解决、联调、验收与发布 | Yeman-sker | 维护集成分支、组织联调、执行最终 DoD，不替代上述子任务的实现 |

上述子任务在 #31 通过后并行进行；子任务负责人在 #30 的 PR 或评论中留下实际提交链接和验证证据。

### 5. 七天执行计划

| 天数 | 当日目标 | 必做任务 | 当日输出 | 验收门禁 | 负责人 / 依赖 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Day 1 | 事实与契约基线 | 验证真实 Historical、GBFS 2.3、字段 nullable、`station_id` STRING、时间语义、Spark 读取 | Source manifest、GBFS 三快照、validator、HDFS/Hive 和 Spark PoC、ADR-0001 | 能说明四种粒度：ride、station snapshot、station × hour flow/profile、station risk/rebalance；真实数据不进 Git | 全员；已完成 |
| Day 2 | 全部接口契约基线 | 冻结地图首页、实时/回放、单日时间轴、四档倍速、数据层、Kafka、ADS、API、前端 view model、错误状态和运行证据 | ADR-0002、ADR-0003、白板树、#31 契约矩阵、最小 JSON/CSV/SQL fixture | 每个契约都有负责人、生产者、消费者、字段和异常样例；全员评审通过；不再新增 P0 | 全员；Yeman-sker 协调，1giaowoligiaogiao 汇总；Day 1 |
| Day 3 | 采集与 RAW/ODS 薄切片 | #26/#27/#28 同时开发：Historical 先跑 2025-01；GBFS 至少 3 个快照；validator；Historical CSV 落 HDFS RAW + source-faithful ODS；GBFS payload 保留在 RAW/staging | 可重跑命令、manifest、HDFS 路径、count/size/checksum 证据、normalized event 样例 | 多 CSV 全处理；RAW/ODS 与清单一致；快照可比较；异常可统计；没有 HDFS 时先用 fixture/fake-runner，不另造架构 | #26/#27/#28；#31 |
| Day 4 | 离线数仓与历史规律薄切片 | #26/#28 并行产出 `dwd_trip_v1`、`dim_station_v1`、`dws_station_hourly_flow_v1`、`dws_station_hour_profile_v1`、非零 `dws_station_od_hourly_v1`；#29 同步用 fixture 对齐输入 | Spark 作业、表/分区、样例 SQL、记录数对账、profile/OD/risk 输入样例 | `net_flow = inbound - outbound`；OD 只保留 `ride_count > 0`；无效记录不静默删除；风险输入字段与 #31 一致 | #26/#28/#29；#31 |
| Day 5 | 风险与调度闭环薄切片 | #27/#29 并行：GBFS normalized event 接 Kafka，或同契约 replay；计算 projected inventory、风险等级和 `ads_rebalance_suggestion` | 风险计算、调度生成器、ADS/薄表、边界 fixture、规则测试 | 离线/无基线可区分；只有 SHORTAGE/OVERFLOW 触发调度；30%/70%、Haversine、greedy 和快照失效规则正确 | #27/#29；使用 #31 fixture，可接 #28 结果 |
| Day 6 | 服务与前端联调薄切片 | #30 的 API、实时适配、历史适配、风险/调度 UI 子任务并行；先用契约 fixture 接入 DWS/ADS，再替换真实结果；完成地图主视图、实时/回放、单日时间轴、四档倍速、双 Tab、站点抽屉 | 可启动 API、可启动前端、统一 map 响应、联调记录 | 实时粒子是站点锚定视觉表达；回放使用实际可用日期/小时；API 不返回逐粒子/GPS/动画路径；P0 页面可走通 | #30；Yeman-sker 协调，消费 #28/#29，不阻塞其启动 |
| Day 7 | 集成验收与交付 | 用真实 2025-01 + GBFS replay 跑端到端；补异常状态、空数据、服务不可用、无基线；完成 P1 判断、文档、截图/演示、PR/CI | 端到端验收记录、质量对账、启动说明、最终文档和合并候选 PR | P0 五项闭环可演示；历史 count/OD/risk/rebalance 前后可追溯；CI 全绿；无未记录的契约偏差 | 全员；Day 6 |

### 6. 依赖与并行关系

```text
Day 1 数据事实/契约
        ↓
Day 2 全部接口契约 / #31
        ↓ CONTRACT GATE
   ┌────┼────┬────┬────┐
   #26  #27  #28  #29  #30
 Historical GBFS Lake Risk API/UI
   └────┴────┴────┴────┘
        ↓ 真实数据接入与集成
              Day 7 端到端验收
```

这里区分两类依赖：

- **启动依赖**：#26–#30 只依赖 #31 CONTRACT GATE，门禁通过后同时启动。
- **运行时数据依赖**：#28 最终消费 #26/#27 的落地结果，#29 最终消费 #27/#28 的结果，#30 最终消费 #28/#29 的结果；在真实结果交付前统一使用契约 fixture，不得停工等待。

允许并行的工作：

- Day 3 等待 HDFS 环境时，Historical、GBFS 和 validator 可以分别用本地 staging/fixture 开发。
- Day 4 的风险边界测试可以先用小型 DWS fixture，不等待全量 Spark 结果。
- Day 6 的前端可以先使用 Day 2 契约 fixture，与后端联调只替换数据源，不改变响应结构。

禁止并行造成契约分叉：字段、粒度、时区、风险区间和调度公式必须以 ADR-0001/0002 为准。

### 7. 数据落地边界

- Historical：Day 3 必须完成 `/raw/citibike/trips/year=YYYY/month=MM/` 和 `ods_trip_raw` 的可查询落地。
- GBFS：Day 3 必须保留完整 source payload、采集元数据和 normalized event；JSON ODS 的行粒度和快照保留策略尚未冻结，因此本周期不强行新增独立 JSON ODS 表。
- 如果团队决定必须增加 GBFS JSON ODS，先创建 Contract Change Issue，明确一行的含义、主键、分区和查询场景，再重新评估 Day 4–7 范围。
- 真实大文件只进入本地 staging/HDFS，不进入 Git；仓库只保留 manifest、schema、代码和小型 fixture。

### 8. 时间不足时的降级顺序

按以下顺序削减，不改变 P0 数据语义：

1. 删除全部 P2。
2. 删除 P1 运营总览 KPI。
3. 将复杂粒子/流动动画降为站点粒子和静态方向线，但保留实时/回放、stations/flows 和倍速控制。
4. 将站点详情保持为一张 24 小时流量图，删除额外钻取和装饰图表。
5. 只用 2025-01 跑通真实历史数据，不在周期内扩展全年。

基础设施不可用时使用 fixture/replay 继续验证业务契约，同时记录真实 HDFS/Kafka/MySQL 集成缺口；不临时引入第二套消息队列、数据库或自制替代架构。

### 9. 总体 Definition of Done

7 天结束时必须同时满足：

- P0 的数据链路、计算规则、API 和前端展示均有可运行切片；
- Historical、GBFS、DWD/DWS、风险和调度的记录可以通过 batch/snapshot 标识追溯；
- `station_id`、时区、粒度、非零 OD、风险和调度规则与 ADR-0001/0002 一致；
- 空数据、无基线、服务不可用、无可匹配站点等异常有明确状态，不静默伪造结果；
- 每个工作流有测试或契约验证、运行命令和 PR；CI 全绿；
- P1 是否保留有明确结论，P2 没有混入实现；
- 演示只使用真实数据证据或已标明的 fixture/replay，不把视觉插值描述成 GPS。

## 不做的事情

- 不把本 ADR 变成新的产品需求清单；产品字段和风险/调度语义仍以 ADR-0001/0002 为准。
- 不为计划引入项目管理平台、复杂编排器或新的依赖。
- 不因为某个工作流提前完成就扩大其范围；剩余时间优先用于联调、验证和缺陷修复。

## 变更规则

本 ADR 在团队确认前保持 Proposed。确认后，修改 7 天总周期、P0/P1/P2、负责人、依赖或降级顺序，必须在 Issue #4 或新的 Contract Change Issue 中记录，并同步更新本 ADR 与相关任务单。
