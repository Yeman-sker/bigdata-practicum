# 共享单车智能运营与调度大数据平台

东华理工大学 2023 级数据科学与大数据技术专业生产实习项目。平台面向共享单车运营与调度人员，结合历史骑行记录和实时站点库存，识别站点供需规律、缺车/满桩风险，并给出可解释的调度建议。

项目主题见 [Issue #3](https://github.com/Yeman-sker/bigdata-practicum/issues/3)。Day 2 v1.1 文档基线由 [ADR-0004](docs/adr/0004-parallel-development-baseline.md) 承接既有决策，任务与评审记录见 [Issue #4](https://github.com/Yeman-sker/bigdata-practicum/issues/4) 和 [#31](https://github.com/Yeman-sker/bigdata-practicum/issues/31)。

## 组员从这里开始

1. 阅读 [产品与交互](docs/product.md)，了解五条 P0 使用路径。
2. 阅读 [架构与模块边界](docs/architecture.md)，找到自己生产/消费的数据。
3. 按 [契约索引](docs/contracts/README.md) 查看字段、表、事件、规则和 [OpenAPI](docs/contracts/openapi.yaml)。
4. 从 [交付计划](docs/plans/delivery.md) 进入自己的 Issue，用 [共享样例](fixtures/day2/README.md) 独立开工。
5. 按 [运行与验收手册](docs/runbook.md) 提交实际证据。文档和样例通过不代表业务程序已实现。

## 项目开发内容

### 核心业务闭环

1. 查看全部站点当前车辆、空桩、可借/可还状态及风险。
2. 统计站点在不同日期、星期和小时的历史流入、流出与净流量。
3. 用历史同时段基线估计未来一小时的库存与缺车/满桩风险。
4. 从富余站点向短缺站点生成按距离和风险排序的调度建议。

### 技术链路

```text
历史骑行 ZIP/CSV → HDFS → Hive → Spark → DWS/ADS → Sqoop → MySQL → Spring Boot → Vite + React
实时 GBFS → 采集器/标准化适配器 → Kafka → 实时风险计算 → ADS/MySQL → Spring Boot → Vite + React
```

### v1 范围

- **P0**：实时地图、单日历史 OD 回放、站点历史分析、未来一小时风险、调度建议；历史清洗、Kafka、数仓和 API 为这些用户路径提供数据。
- **P1**：运营总览及核心 KPI。
- **暂不做**：登录注册、AI/LLM、天气融合、复杂机器学习预测、VRP 路径优化、逐车追踪、逐笔实时骑行事件和旧版 Citi Bike schema 全兼容。

### 数据契约摘要

| 数据对象 | 一行/粒度 | 主要用途 |
| :--- | :--- | :--- |
| Historical Trips | 一次骑行 | `station_id × service_date × hour` 流量与历史基线 |
| GBFS 2.3 station status | 一个站点在一个时刻的库存快照 | 当前库存、风险与实时调度 |
| DWS | 站点 × 日期 × 小时，或站点 × 星期 × 小时 | Spark 聚合分析 |
| ADS | 当前站点风险、调度建议、运营总览薄表 | MySQL、Spring Boot、Vite + React |

跨层统一使用字符串类型的 `station_id`；历史时间按 `America/New_York` 本地时间处理，实时 POSIX 时间同时保存 UTC 和本地派生值。GBFS 快照不是官方逐笔骑行事件流，内部 Kafka topic 为 `bike.station.status.v1`，key 为 `station_id`。

现行字段、阈值、分层和变更规则见 [契约索引](docs/contracts/README.md)；[ADR-0001](docs/adr/0001-product-data-contract-v1.md) 保留初始决策及后继指针。

Day 1 Historical Trip 的 HDFS RAW / Hive ODS 入口、DDL、验证 SQL 与真实数据对账
方法见 [Track D handoff](docs/hdfs-hive/README.md)。

Track E 的 Spark 受控读取、契约断言、count 对账和可复现命令见
[Spark integration handoff](docs/spark/README.md)。

Source schema validator、字段映射和契约 fixtures 见
[Track C handoff](docs/contracts/README.md)。

现有 Python 运行入口统一收纳在 `citibike/` 包；Spark 读取 PoC 使用
`spark-submit citibike/spark.py`，其他 CLI 使用 `python3 -m citibike.<module>`。
现有 Python source 常量继续维护在 `citibike/contracts.py`；跨模块表、事件、HTTP 与业务规则以契约索引所指文件为准，避免多套定义。

## 基础环境与技术栈

本节锁定底层基础设施、大数据组件及服务端版本一致性，并提供面向 AI Agent 的自动化编排与环境验收 Skill。

---

## 1. 基础环境与版本矩阵

| 组件类别 | 组件名称 | 课堂基准版本 | 依赖 JDK | 核心作用说明 |
| :--- | :--- | :--- | :--- | :--- |
| **宿主环境** | Windows (WSL2) / macOS (OrbStack) | Ubuntu 22.04 LTS | - | 基础 Linux 开发与集群运行环境（开启 systemd） |
| **基础运行环境** | OpenJDK | JDK 8u502 | 8 | Hadoop、Hive、Spark、Flume、Sqoop 运行基准 |
| **基础运行环境** | OpenJDK | JDK 17 LTS | 17 | Spring Boot 3.x、Kafka 3.9.x、Maven 构建基准 |
| **分布式文件系统** | Apache Hadoop | 3.3.6 | JDK 8 | HDFS 分布式存储 + YARN 资源调度 + MapReduce |
| **数据仓库** | Apache Hive | 3.1.3 | JDK 8 | 数仓建模与 SQL 分析引擎 (MySQL Metastore) |
| **日志采集** | Apache Flume | 1.11.0 | JDK 8 | 日志流管道采集与 HDFS 汇聚 |
| **消息中间件** | Apache Kafka | 3.9.2 | JDK 17 | 消息队列与事件流分发 (KRaft 模式免 ZK) |
| **离线/流计算** | Apache Spark | 3.5.7 (bin-hadoop3) | JDK 8 | 分布式通用内存计算引擎 (直读 Hive 数仓) |
| **数据交换** | Apache Sqoop | 1.4.7 | JDK 8 | RDBMS (MySQL) 与 Hadoop/Hive 批量数据迁移 |
| **关系型数据库** | MySQL Community | 8.0.x | - | 业务数据存储与 Hive Metastore 元数据库 |
| **服务层框架** | Spring Boot | 3.2.x+ | JDK 17 | 后端数据服务与 API 暴露 (提供 RESTful 接口) |
| **前端应用** | Vite + React + TypeScript | v1 | - | 地图大屏、回放、风险与调度展示；图表库可替换 |

---

## 2. 运行策略：双 JDK 共存与切换约定

Spring Boot 3.0+ 及 Kafka 3.9+ 依赖 Java 17，而 Hadoop 3.3.6 / Hive 3.1.3 / Spark 3.5 核心生态以 Java 8 为基准环境，采用双 JDK 隔离策略：

- **JDK 8 路径**：`/opt/jdk8`  
  - 适用组件：Hadoop, Hive, Flume, Sqoop, Spark
- **JDK 17 路径**：`/opt/jdk17`  
  - 适用组件：Kafka, Spring Boot, Maven 构建

**环境切换脚本**：
```bash
# 进入大数据生态实验
source ~/use-jdk8.sh

# 进入服务层 / 消息队列开发
source ~/use-jdk17.sh
```

---

## 3. 项目目录与 Agent Skill 索引

下列目录均已落盘；标注“占位”的目录目前只含 `.gitkeep`，用于明确并行开发边界，尚不能构建或启动应用。目录责任见 [架构与模块边界](docs/architecture.md#目录与冲突边界)。

```text
bigdata-practicum/
├── citibike/                         # Python 源数据、采集、离线作业与 CLI
│   ├── contracts.py                  # 既有 Source 字段常量
│   ├── contract_validator.py
│   ├── historical_source.py
│   ├── historical_landing.py
│   ├── gbfs.py
│   ├── gbfs_fixture.py
│   └── spark.py
├── backend/                          # 一个 Spring Boot / Maven 工程，待初始化
│   └── src/
│       ├── main/
│       │   ├── java/citibike/         # 共享 Java 根包，应用入口由 #26 交付
│       │   │   ├── api/               # 占位：#26 HTTP 查询
│       │   │   └── operations/        # 占位：#29 规则、消费与实时发布
│       │   └── resources/            # 占位：#26 公共应用配置
│       └── test/java/citibike/
│           ├── api/                  # 占位：#26 API 测试
│           └── operations/           # 占位：#29 规则与消费测试
├── frontend/                         # #30 Yeman-sker：UI 设计与完整前端
│   ├── src/                          # 占位：React / TypeScript 页面、交互与样式
│   └── public/                       # 占位：直接提供给浏览器的静态资源
├── hive/                             # Hive 表定义与数据校验
│   ├── ods/                          # 既有 RAW → ODS 表定义
│   ├── queries/                      # 校验 SQL
│   └── warehouse_v1.sql              # DWD / DIM / DWS 表定义
├── sql/
│   └── serving.sql                   # MySQL 服务表、staging 与发布状态
├── tests/                            # Python 单元测试与跨模块契约检查
├── fixtures/                         # 可提交的小型共享输入及期望结果
│   ├── day2/                         # 同源 CSV、metadata、Kafka、HTTP、seed 与算例
│   ├── citibike/                     # Day 1 历史读取样例
│   ├── contracts/                    # Source schema 校验样例
│   └── gbfs/                         # Day 1 GBFS 交接样例
├── docs/
│   ├── product.md                    # 产品与 UI 行为
│   ├── architecture.md               # 进程、目录责任与交接
│   ├── runbook.md                    # 现有/待实现入口与运行验收
│   ├── contracts/                    # 字段、数仓、Kafka、规则和 OpenAPI
│   ├── plans/                        # 交付计划与文档规划
│   ├── adr/                          # 决策及后继关系
│   ├── agents/                       # 仓库协作规范
│   ├── source/                       # Day 1 历史数据证据
│   ├── gbfs/                         # Day 1 采集交接
│   ├── hdfs-hive/                    # Day 1 RAW / ODS 交接
│   ├── spark/                        # Day 1 Spark 读取交接
│   ├── concepts/                     # 既有概念预览
│   ├── handbook/                     # 教师手册
│   ├── notes/                        # 课堂笔记
│   └── templates/                    # 实习日志与报告模板
├── .github/                          # Issue / PR 模板与 CI
├── .agents/skills/                   # 团队开发与环境复现 Skill
├── AGENTS.md                         # Agent 仓库规则入口
├── CONTEXT.md                        # 单一业务上下文与术语
├── requirements-contracts.txt        # 契约检查依赖
└── README.md
```

- #26 首个实现 PR 创建 `backend/pom.xml` 和 `citibike` 根包下的应用入口；`api` 与 `operations` 共用这个工程和进程，测试目录按包镜像组织。
- #30 首个实现 PR 创建 `frontend/package.json`、锁文件和 Vite 配置；页面组件、样式和内部目录随实际 UI 切片补充。Java / 前端构建 CI 分别随两个工程首次实现进入。
- `citibike/` 继续承接 [runbook](docs/runbook.md) 中待实现的 Python 入口；真实数据、运行日志和证据放仓库外的 `$DATA_DIR`，共享样例统一放 `fixtures/`。
- 首个真实文件进入占位目录时，删除该目录的 `.gitkeep`。构建输出 `backend/target/`、`frontend/node_modules/` 和 `frontend/dist/` 已加入忽略规则。
