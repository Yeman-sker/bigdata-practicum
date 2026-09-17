# 七天交付与任务分工

版本：Day 2 v1.1；总周期从 Day 1 起算，Day 2 后剩 Day 3–7 五天。按组长最新要求，Yeman-sker 负责 UI 设计、完整前端及协调验收，四位组员分别负责 API、GBFS/Kafka、离线、规则/消费，形成五条可并行工作流。

## 任务与人员

| Issue | 唯一主负责人 | 完整交付边界 | Day 1 复用与移交 |
| --- | --- | --- | --- |
| [#26](https://github.com/Yeman-sker/bigdata-practicum/issues/26) HTTP API 与后端入口 | S1lco | 三个只读 API、OpenAPI、共享 Maven 工程/配置、Java CI、查询与失效处理 | 原前端交给组长；已有 source/manifest 交给 #28，疑问集中 20 分钟，不再维护第二个 DWD |
| [#27](https://github.com/Yeman-sker/bigdata-practicum/issues/27) GBFS/Kafka | OGATA-LINA | canonical 适配、raw/metadata、完整批次发送、录制事件发送与真实采集证据 | 复用 collector/validator；原 API 和 Maven 入口移交 #26，不再跨两条开发流 |
| [#28](https://github.com/Yeman-sker/bigdata-practicum/issues/28) 离线数据与服务表 | Hu-tong123 | source/RAW/ODS/DWD/DIM、flow/profile/OD、Sqoop/MySQL 发布、DDL 与对账 | 复用已验证 source、landing、Spark 读取 PoC |
| [#29](https://github.com/Yeman-sker/bigdata-practicum/issues/29) 规则与实时发布 | 1giaowoligiaogiao | `citibike.operations` 包：当前/预测、整数调度、Kafka 消费、事务发布与到期判定 | 与 #26 共用一个 Java 工程/进程 |
| [#30](https://github.com/Yeman-sker/bigdata-practicum/issues/30) UI / 前端与集成 | Yeman-sker | UI 设计、Vite/React、地图/回放/站点详情/风险/调度、前端 CI 与浏览器验收；协调合并和最终集成 | 在同一前端原型迭代 UI；各组提供自己的证据，组长汇总；协调 Flume 日志证据 |
| [#31](https://github.com/Yeman-sker/bigdata-practicum/issues/31) 文档基线 | Yeman-sker | 组织文档评审与基线记录；各领域负责人维护自己的契约 | 文档签收不等于实现完成 |

这是组长的任务安排，不预先代替组员签收。UI 设计归 Yeman-sker；API、采集、离线、规则按既定字段与样例开工，不等待视觉定稿。

## 每日可演示增量

| 日 | S1lco / #26 | OGATA-LINA / #27 | Hu-tong123 / #28 | 1giaowoligiaogiao / #29 | Yeman-sker / #30 |
| --- | --- | --- | --- | --- | --- |
| Day 1 | 已有 source 成果 | 已有 GBFS 成果 | 已有落地/读取基础 | 已有契约/验证基础 | 复用已合并证据，不重复计为新实现 |
| Day 2 | 核对 HTTP/表和后端入口 | 核对身份/事件 | 核对表和样例 | 核对规则算例 | 确定 UI 方向；文档基线与文件责任 |
| Day 3 | 第一小时独立提交可测试 backend/Java CI；随后 seed 查询 | canonical/metadata；复用 collector 验证 station/end 发送 | 20 分钟交接；fixture DWD/聚合；先试通小样本 Sqoop 再接 ETL | JDK 17 纯规则/到期算例；接 Maven；随后建议 | UI 原型：棱镜空间三维地图/右侧操作面板/站点详情；fixture 可交互、前端 CI；优先合后端入口并确定集成环境 |
| Day 4 | 三接口完整参数/空/错误；接真实历史表 | Kafka 完整批次与录制发送，交接 metadata 文件 | 真实 2025-01 DWD/DWS + Sqoop 发布 | consumer 完整/重复/失败批、原子写入 | 回放/曲线/异常 UI；接真实历史 API，演示样例及真实历史 |
| Day 5 | 实时 ADS 查询、同批/过期/录制标识联调 | 三次真实采集与 Kafka 交接证据 | 对账、映射覆盖、修导出问题 | Kafka→规则→ADS 真链路与失效验证 | 接完整实时结果，五条 P0 首次真实集成；汇总 Flume 日志证据 |
| Day 6 | API 错误/一致性回归 | 重放、失败发送与元数据恢复 | 重跑与事务回滚验证 | 规则/批次/过期回归 | 浏览器/可访问性回归、视觉收口；判定 P1 |
| Day 7 | 运行说明与修缺陷 | 采集运行说明与交接 | 数据验收与交接 | 结果复算与交接 | 最终 UI/功能演示，汇总真实对账、PR/CI 和交付 |

组长优先完成一个前端切片，协调采用每日 15 分钟同步和必要交接审核；各负责人承担自身排障、运行证据与下游交接。离线按单月重算控制范围；采集与 API 已拆开，避免同一负责人反复切换。

## 敏捷节奏与依赖

- 每人 WIP=1；每半天到一天给出可验证的小 PR，首个共享入口优先，不把整条工作流留到最后合并。
- 每天一次 15 分钟同步；阻碍超过半天在对应 Issue 记录，安排帮助或减少可选项。
- #31 文档 PR 经组长确认并合并、记录基线后，#26–#30 同时启动；不把其他完整实现或 UI 视觉定稿当作开工依赖。
- 运行依赖：#28 读 #27 metadata；#29 读 #27 事件和 #28 profile；#26 API 读 #28/#29 服务表；#30 前端读 #26 API。未就绪时用 [同版 fixture](../../fixtures/day2/README.md)。
- #26 第一小时拆共享 Maven 入口；#29 先 JDK 算例；#27 先 raw/metadata 和事件样例；#28 用 CSV/metadata/expected；#30 用 HTTP examples 的 value 做 UI 原型。五条首切片无需等待真实上游。
- #26 最小入口由 #29 核对包名/测试接入；#30 在真正的查询切片签收 HTTP，#29 的规则输出由 #26 核对。消费者只验当前交接，不必先完成自己的整个模块。
- Java CI 随 #26 入口、npm ci/build CI 随 #30 前端工程交付；共享 CI 文件按后端→前端顺序合并，后者同步主干。现有 Python 绿灯不代替未来业务构建。
- 内部 UI 布局、视觉与组件选择由 Yeman-sker 推进；改变字段、含义或 P0 行为时回 #31/后继变更 Issue，同步权威文档、样例和受影响任务。
- 分步 PR 使用 `Refs #任务号`，完整 DoD 满足后才申请关闭；fixture 通过不关闭真实验收项。

## 合并与验收责任

各负责人给出命令、测试和实际结果，直接消费者核对输入，组长按 code-review 与 CI 决定合并。Yeman-sker 的前端 PR 由 S1lco 复核 HTTP/页面集成，涉及规则解释时由 #29 核对；UI 方案仍由 Yeman-sker 负责，不以自己签收代替交叉检查。无关成员不逐行审核每个 PR，不为命名或装饰反复返工。

A/B/C 分别是独立 fixture 切片、真实输入替换、异常与交接完成。最终标准以 [runbook](../runbook.md) 为准。

## 开发演练与优化记录

2026-09-15 已在候选 b10e3d8 完成八类场景推演及 Kafka/Spark 专项验证；本次按五人新分工核对责任与开工依赖。表中是计划验证，不代表产品完成或工期保证。

| 模拟情景 | 原堵点 / 风险 | 当前责任与下一步 |
| --- | --- | --- |
| 刚领任务，共享入口未合并 | 规则缺 Maven 入口；采集/API 挤在一人；交接可能占半天 | #26 第一小时入口/Java CI；#27 专注采集；#29 JDK 算例；#28 20 分钟交接；#30 fixture/UI |
| 首个 PR 等消费者完整实现 | API、前端、规则互等 | 只核对当前切片；入口由 #29 先验，#30 优先合并 |
| 没有历史发布的冷启动 | offline 等 metadata，collector 却排在后面 | backend/collector 先启动，当前库存先发布；metadata→offline/Sqoop→下一快照补预测 |
| 历史发布或 UI 定稿延迟 | 其他工作被误认为必须一起等 | API/采集/规则/前端可用共享样例继续；真实预测维持 NO_BASELINE，#28 单独排障 |
| DWD 重分区/重跑 | ODS 没有源记录序号 | #28 从原 CSV 保留位置；换分区数核对相同键，ODS 独立对账 |
| 很晚才首次执行 Sqoop | 驱动/权限/TSV 问题与全量 ETL 同时暴露 | #28 Day 3 先用期望薄表试通通道，失败只清 staging，随后换实际 ETL |
| 重放 / 提交 offset 前崩溃 | 旧位置、旧 seed 或错误时钟使结果失真 | 独立库/组、发送前设起点；重启沿用库/组；#27 发事件，#29 原子发布，#26 查询恢复后的时钟 |
| 合并后才发现不能构建 | 现有 CI 只检查 Python | #26/#30 首个工程 PR 分别补业务构建 CI |

既有实测证据保留在 PR #32：Kafka 新组起点/未提交重启、Spark 1/3 分区来源键一致；这次人员调整不重复宣称新的基础设施实测。真实 Sqoop、业务 consumer、API、UI 与浏览器仍由各实现 Issue 提交结果。

## 降级顺序

先 P2，再 P1和装饰动画；保留可解释粒子/静态线、四档节奏、一张历史曲线以及全部 P0。最低真实历史始终为 2025-01，全年延期。

不能删掉数据真实性、异常标识、同批读取和真实基础设施证据来制造完成。环境不可用仍可独立开发，最终缺口如实记录。
