# 共享单车智能运营与调度大数据平台

东华理工大学 2023 级数据科学与大数据技术专业生产实习项目。平台面向共享单车运营与调度人员，结合历史骑行记录和实时站点库存，识别站点供需规律、缺车/满桩风险，并给出可解释的调度建议。

项目主题见 [Issue #3](https://github.com/Yeman-sker/bigdata-practicum/issues/3)，产品范围与数据契约以 [Issue #4](https://github.com/Yeman-sker/bigdata-practicum/issues/4) 和 [ADR-0001](docs/adr/0001-product-data-contract-v1.md) 为准。

## 项目开发内容

### 核心业务闭环

1. 查看全部站点当前车辆、空桩、可借/可还状态及风险。
2. 统计站点在不同日期、星期和小时的历史流入、流出与净流量。
3. 用历史同时段基线估计未来一小时的库存与缺车/满桩风险。
4. 从富余站点向短缺站点生成按距离和风险排序的调度建议。

### 技术链路

```text
历史骑行 ZIP/CSV → HDFS → Hive → Spark → DWS/ADS → Sqoop → MySQL → Spring Boot → ECharts
实时 GBFS → 采集器/标准化适配器 → Kafka → 实时风险计算 → ADS/MySQL → Spring Boot → ECharts
```

### v1 范围

- **P0**：历史数据下载与清洗、站点小时供需分析、GBFS 站点库存采集、Kafka 标准化事件、当前风险、调度建议、API 和实时站点展示。
- **P1**：运营总览及核心 KPI。
- **暂不做**：登录注册、AI/LLM、天气融合、复杂机器学习预测、VRP 路径优化、逐车追踪、逐笔实时骑行事件和旧版 Citi Bike schema 全兼容。

### 数据契约摘要

| 数据对象 | 一行/粒度 | 主要用途 |
| :--- | :--- | :--- |
| Historical Trips | 一次骑行 | `station_id × service_date × hour` 流量与历史基线 |
| GBFS 2.3 station status | 一个站点在一个时刻的库存快照 | 当前库存、风险与实时调度 |
| DWS | 站点 × 日期 × 小时，或站点 × 星期 × 小时 | Spark 聚合分析 |
| ADS | 当前站点风险、调度建议、运营总览薄表 | MySQL、Spring Boot、ECharts |

跨层统一使用字符串类型的 `station_id`；历史时间按 `America/New_York` 本地时间处理，实时 POSIX 时间同时保存 UTC 和本地派生值。GBFS 快照不是官方逐笔骑行事件流，内部 Kafka topic 为 `bike.station.status.v1`，key 为 `station_id`。

字段、阈值、分层和变更规则见 [ADR-0001](docs/adr/0001-product-data-contract-v1.md)。

Day 1 Historical Trip 的 HDFS RAW / Hive ODS 入口、DDL、验证 SQL 与真实数据对账
方法见 [Track D handoff](docs/hdfs-hive/README.md)。

Track E 的 Spark 受控读取、契约断言、count 对账和可复现命令见
[Spark integration handoff](docs/spark/README.md)。

Source schema validator、字段映射和契约 fixtures 见
[Track C handoff](docs/contracts/README.md)。

运行入口统一为 `python3 -m citibike.<module>`；共享数据契约只维护在
`citibike/contracts.py`，不再新增散落的独立脚本。

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
| **前端可视化** | ECharts | 5.x | - | 数据可视化图表呈现 |

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

```text
bigdata-practicum/
├── docs/                   # 契约、各 Track 交接、教学资料与模板
│   ├── adr/                # 架构与数据契约决策记录
│   ├── agents/             # 仓库协作规范
│   ├── concepts/           # 产品概念预览
│   ├── contracts/          # Source schema validator 与字段映射
│   ├── gbfs/               # GBFS 采集交接
│   ├── handbook/           # 教师实战授课手册
│   ├── hdfs-hive/          # HDFS RAW / Hive ODS 交接
│   ├── notes/              # 课堂操作笔记
│   ├── source/             # Historical Trips source 交接
│   ├── spark/              # Spark 集成交接
│   └── templates/          # 实习日志与报告模板
├── fixtures/               # 轻量、可提交的测试样例
│   ├── citibike/
│   ├── contracts/
│   └── gbfs/
├── hive/                   # ODS DDL 与验证 SQL
├── citibike/               # 共享契约、领域模块与 CLI 入口
│   ├── contracts.py        # 唯一的数据契约常量
│   ├── historical_source.py
│   ├── historical_landing.py
│   ├── contract_validator.py
│   ├── gbfs.py
│   ├── gbfs_fixture.py
│   └── spark.py
├── tests/                  # 全部自动化测试
├── README.md               # 项目说明、技术栈规范与索引指南
└── .agents/                # Agent 原生工作规范区
    └── skills/             # 团队工程复现 Skill 库
```
