# 七天交付与任务分工

版本：Day 2 v1.1；总周期从 Day 1 起算，Day 2 后剩 Day 3–7 五天。组长已授权重新分工，以四位实现负责人覆盖完整 P0；用户/组长 Yeman-sker 负责协调与验收。

## 任务与人员

| Issue | 唯一主负责人 | 完整交付边界 | Day 1 复用与移交 |
| --- | --- | --- | --- |
| [#26](https://github.com/Yeman-sker/bigdata-practicum/issues/26) 前端 P0 | S1lco | Vite/React 工程、实时/历史地图、历史抽屉、风险与调度展示、页面异常、浏览器验收 | 原 Historical 任务并入 #28；先给已有 source/manifest/运行证据链接，疑问集中 20 分钟交接，不再维护第二个 DWD |
| [#27](https://github.com/Yeman-sker/bigdata-practicum/issues/27) GBFS/Kafka 与 HTTP API | OGATA-LINA | canonical 适配、批次发送、metadata 交接、Spring Boot 工程与三个只读 API、API 测试 | collector/validator 已存在；补交接差距，Day 3 优先让 API 可消费 seed |
| [#28](https://github.com/Yeman-sker/bigdata-practicum/issues/28) 离线数据与服务表 | Hu-tong123 | source/RAW/ODS/DWD/DIM、flow/profile/OD、Sqoop/MySQL 发布、DDL 与对账 | 复用已验证 source、landing、Spark 读取 PoC；不重写下载/读取器 |
| [#29](https://github.com/Yeman-sker/bigdata-practicum/issues/29) 规则与实时发布 | 1giaowoligiaogiao | backend.operations：当前/预测、整数调度、Kafka 消费、事务发布与过期判定 | 用共同 Java 工程承载规则，不搭独立微服务或前端工程 |
| [#30](https://github.com/Yeman-sker/bigdata-practicum/issues/30) 集成与验收 | Yeman-sker | 日常排期、契约变更、跨模块合并、演示/真实链路证据、P1 取舍；协调 Flume 日志证据 | 不承担 API/前端唯一实现职责 |
| [#31](https://github.com/Yeman-sker/bigdata-practicum/issues/31) 文档基线 | Yeman-sker | 组织文档评审与基线记录；各领域负责人维护各自契约 | 不把矩阵汇总另压给规则负责人 |

上述是组长的任务安排，不伪造组员已阅读或完成签收。个人工时变化先调整 Issue 范围/顺序，不让另一个人默认兼任整条工作流。

## 每日可演示增量

| 日 | S1lco / #26 | OGATA-LINA / #27 | Hu-tong123 / #28 | 1giaowoligiaogiao / #29 | 组长检查 |
| --- | --- | --- | --- | --- | --- |
| Day 1 | 已有 source 成果 | 已有 GBFS 成果 | 已有落地/读取基础 | 已有契约/验证基础 | 复用已合并证据，不重复计为新实现 |
| Day 2 | 核对页面与响应 | 核对事件/API | 核对表和样例 | 核对规则算例 | 本文档 PR 评审、同版基线与文件所有权 |
| Day 3 | fixture 首页/构建 CI；模式/Tab/抽屉骨架 | 第一小时独立提交可测试 backend/Java CI；随后 seed 查询、canonical 适配 | 20 分钟交接；fixture DWD/聚合；先用期望薄表试通 Sqoop 再接 ETL 输出 | JDK 17 纯规则/到期算例；入口合并后接 Maven；随后建议 | 先合最小入口；确定集成主机/路径/连接；样例浏览器→API→MySQL 演示 |
| Day 4 | 回放、曲线、错误/过期 UI；接真实 API | 三接口完整参数/空/错误；完成 Kafka 封装发送 | 真实 2025-01 DWD/DWS + Sqoop 发布 | consumer 的完整/重复/失败批；MySQL 原子写入 | 真实历史回放可用，事件和规则可用 fixture 联调 |
| Day 5 | 接完整实时结果，核对图例/同批建议 | 三次真实采集与 Kafka/API 证据 | 对账、映射覆盖报告、修导出问题 | Kafka→规则→ADS 真链路与失效验证 | 五条 P0 首次真实集成；Flume 日志汇聚 |
| Day 6 | 浏览器回归、可访问性 | API 错误和请求一致性回归 | 重跑与事务回滚验证 | 规则/批次/过期回归 | 处理 P0 缺陷，确定是否交付 P1 |
| Day 7 | 最终演示与交接 | 运行说明与修缺陷 | 数据验收与交接 | 结果复算与交接 | 最终真实对账、文档、PR/CI、演示和交付 |

API 负责人承担两项相关职责，但按上表分上午/下午顺序推进，复用已有 collector。离线负责人承担整条批链，减少 DWD 交叉写入，工作量靠单月、全量重算、小型薄表发布控制。组长每日检查这些取舍是否仍可在时间盒内完成。

## 敏捷节奏与依赖

- 每人同时只做一个主要切片；每个切片应在半天到一天内有可验证输出，提交小 PR，避免最后一天合入完整工作流。
- 每天一次 15 分钟同步，只汇报可运行结果、阻碍和下一切片；阻碍超过半天立即在对应 Issue 记录，由组长安排帮助或减少可选项。
- #31 的文档 PR 经组长确认并合并后记录基线提交，四条实现流同时启动；不把其他实现 PR 当作启动依赖。基线评审期间可核对文档与样例。
- 运行依赖：#28 读 #27 metadata；#29 读 #27 事件与 #28 profile；#27 API 读 #28/#29 服务表；#26 读 API。每个依赖未就绪时用 [同版 fixture](../../fixtures/day2/README.md)。
- 共享 backend 按 [architecture](../architecture.md) 在第一小时拆出可测试入口；#29 可先用 JDK 17 编译/运行纯规则，#26 直接取 http-examples 中的 value，#28 直接用 CSV/metadata/expected。不因 MySQL、Kafka 或首个 PR 审核延迟暂停独立实现。
- #27 最小入口只由 #29 核对包名/测试接入，#26 的 HTTP 签收在真正的查询切片进行；#29 纯规则由 #27 核对输入输出，不等前端完成。#30 优先处理首个共享入口和交接 PR，减少整组等待；不要求消费者先完成自己的整个模块才签收。
- 首个 frontend/backend 工程 PR 分别带上 npm ci/build、无外部服务依赖的 Maven test CI。当前 Python CI 全绿只证明当前检查，不能代替未来 Java/页面构建。CI 文件按最小后端→前端的顺序合并，不增加统一脚手架任务。
- PR 以 `Refs #任务号` 跟踪分步进度，最后一个满足完整 DoD 的 PR 才申请关闭任务。不因 fixture 通过关闭真实链路验收项。
- 跨模块字段/行为变更回 #31（或后继 Contract Change Issue），同次修改权威文档、样例、受影响 Issue；生产者和消费者一起核对，不在各分支私改。

## 合并与验收责任

主负责人给出实际命令、测试与结果；直接消费者复核自己的输入；组长按仓库 code-review 技能完成两轴评审与 CI 检查后决定合并。不要求无关成员逐行审每个 PR，不为命名或装饰反复返工。

每个实现 Issue 的 A/B/C 分别是：独立 fixture 切片、真实输入替换、异常与交接完成。Day 3 用 seed 展示不冒充产品实现完成；Day 7 的最终标准以 [runbook](../runbook.md) 为准。

## 开发演练与优化记录

2026-09-15 按原候选 b46541a 从空环境、四位成员并行开工到 Day 7 推演；表中是计划验证，不是组员实现或工期保证。运行细则已回填原权威文档，不另建流程平台。

| 模拟情景 | 原堵点 / 风险 | 优化后的下一步与责任 |
| --- | --- | --- |
| Day 3 四人刚领任务 | #29 没有 Maven 入口；#27 同时背着完整查询；source 交接可能占半天 | #27 第一小时拆入口/Java CI；#29 先 JDK 算例；#26 fixture/构建；#28 读现有 handoff、20 分钟答疑 |
| 首个 PR 等待消费者完成 | 若 API 要等前端完成签收、规则要等 API 完成，则互相等待 | 消费者只验证本次小切片的输入输出；共享入口由 #29 先验，#30 优先合并 |
| 没有任何历史发布的冷启动 | offline 等 metadata，但 collector 被排在 offline 之后 | backend/collector 先启动，当前库存先发布；metadata→offline/Sqoop→下一快照补预测 |
| Day 4 历史发布延迟半天 | 四条流若只接受真实数据，全部被离线链路拖住 | 当前库存、API/前端和规则样例继续；真实预测保持 NO_BASELINE，#28 单独排障，不假填零 |
| DWD 重分区/重复数据重跑 | ODS 没有文件记录序号；shuffle 后编号会改去重胜者 | #28 从原 CSV 保留文件/记录位置；换分区数核对相同定位键，ODS 做独立对账 |
| Day 4 才首次执行 Sqoop | 驱动/权限/TSV 问题与全量 ETL 同时出现 | Day 3 先以四张期望薄表试通通道；失败只清 staging，正式发布不变；随后换实际 ETL |
| 录制重放 / 提交 offset 前崩溃 | 旧消费位置会漏读；seed 中相同快照可能被当作已发布；拒绝的 end 可能推进时钟 | 独立库/组，发送前设起点；只初始化一次；重启沿用库/组，时钟和 ADS 一起提交，live 拒绝录制来源 |
| Day 6 合并后发现不能构建 | 现有 CI 只检查 Python | 首个业务工程 PR 同时提供对应构建 CI，减少末期集中发现问题 |

实际检查证据随本次 PR 记录：共享契约检查；隔离 Kafka 3.9.2 验证新组起点及未提交重启；Spark 3.5.7 验证源文件位置与分区变化。桌面推演只证明上述依赖有下一步，真实 Sqoop、业务 consumer、API 和浏览器结果仍由各实现 Issue 提交。

## 降级顺序

先移除全部 P2，再移除 P1；动画只保留可解释的粒子/静态线与四档时间节奏，历史抽屉只保留一张 24 小时图。最低真实历史范围始终为 2025-01，全年延期。

不能删掉 P0 用户路径、原始数据质量、异常标识、同批读取、真实 HDFS/Kafka/MySQL 证据来制造完成。基础设施失效时仍可用 fixture 开发，真实验收项明确保持未完成；组长据证据调整交付结论。
