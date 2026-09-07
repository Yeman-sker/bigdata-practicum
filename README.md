# 大数据生产实习技术栈版本与环境规范

东华理工大学 2023 级数据科学与大数据技术专业生产实习统一技术栈与运行环境规范。项目主题待定，本规范用于锁定底层基础设施、大数据组件及服务端版本一致性，并提供面向 AI Agent 的自动化编排与环境验收 Skill。

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
├── README.md               # 本技术栈规范与索引指南
├── docs/                   # 教学资料与原始模板
│   ├── handbook/           # 教师实战授课手册 (PDF)
│   ├── notes/              # 课堂操作笔记与配置参考 (HTML + Assets)
│   └── templates/          # 生产日志与实习报告官方 Word 模板
└── .agents/                # Agent 原生工作规范区
    └── skills/             # 团队工程复现 Skill 库
        └── bigdata-env-setup/  # 面向 Agent 的大数据全栈环境编排规范
            └── SKILL.md        # 阶段路线、架构决策、避坑准则与 6 重质量验收闸门 (Quality Gates)
```
