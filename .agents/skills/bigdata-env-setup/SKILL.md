---
name: bigdata-env-setup
description: Use when setting up or verifying the Big Data practicum infrastructure on Ubuntu 22.04 LTS (Windows WSL2 or macOS OrbStack). Directs the deployment order, version locking, dual-JDK switching strategy, architectural caveats, and end-to-end verification gates.
---

# 大数据全栈开发环境搭建与验收规范 (Agent 指南)

本规范专为自主 AI Agent（或协作者 Agent）设计，用于指导其在 **Windows WSL2 (Ubuntu 22.04 LTS)** 或 **macOS OrbStack (Ubuntu 22.04 LTS)** 环境下，自主完成大数据生产实习底层基础设施的编排部署与端到端验证。

---

## 1. 目标环境与版本基准

所有组件必须严格按以下版本基准锁定，严禁擅自升级次版本或降级：

| 组件类别 | 目标组件 | 锁定版本 | 运行 JDK 约束 | 关键配置要求 |
| :--- | :--- | :--- | :--- | :--- |
| **底层环境** | Ubuntu | 22.04 LTS | - | WSL2 必须开启 systemd；统一工作用户 `bigdata` |
| **基础运行时**| OpenJDK | JDK 8 / JDK 17 | - | 部署在 `/opt/jdk8` 与 `/opt/jdk17`，提供脚本随时切换 |
| **关系型库** | MySQL Community | 8.0.x | - | 预建 `metastore` (latin1) 与 `traveldb` (utf8mb4) |
| **分布式存储**| Apache Hadoop | 3.3.6 | JDK 8 | 伪分布式；数据与元数据统一挂载在 `/data/hadoop` |
| **数据仓库** | Apache Hive | 3.1.3 | JDK 8 | 依赖 MySQL Metastore；必须剔除自带老版本 Guava |
| **批处理计算**| Apache Spark | 3.5.7 (bin-hadoop3) | JDK 8 | 绑定 YARN 调度器，直连 Hive Metastore |
| **日志流采集**| Apache Flume | 1.11.0 | JDK 8 | 管道模式：TAILDIR/EXEC Source $\to$ HDFS Sink |
| **数据交换** | Apache Sqoop | 1.4.7 | JDK 8 | 针对 Hadoop 3.x 必须补齐 commons-lang-2.6 依赖 |
| **事件消息流**| Apache Kafka | 3.9.2 | JDK 17 | 采用 KRaft 单机模式（免 ZooKeeper） |
| **应用服务层**| Spring Boot | 3.2.x+ | JDK 17 | 内嵌 Tomcat，提供 RESTful API，监听 8080 端口 |

---

## 2. 实施路线与因果推进阶段

Agent 在执行部署时，必须遵循严格的拓扑依赖顺序，切勿乱序执行：

```text
[阶段 0: 宿主与系统预处理]
       │ (开启 systemd、安装基础网络与构建工具、打通 localhost SSH 免密)
       ▼
[阶段 1: 运行时与存储底座]
       │ (部署 aarch64/amd64 双 JDK、启动 MySQL 8.0、划分数据目录与权限)
       ▼
[阶段 2: 分布式存储与调度核心 (Hadoop 3.3.6)]
       │ (配置 core/hdfs/mapred/yarn-site.xml、首次 namenode 格式化、拉起 HDFS 与 YARN)
       ▼
[阶段 3: 数仓分层引擎 (Hive 3.1.3 + Spark 3.5.7)]
       │ (替换 Guava 规避类冲突、注入 MySQL 驱动、初始化 schema、打通 Spark 直读)
       ▼
[阶段 4: 双向通道组件 (Flume 1.11.0 + Sqoop 1.4.7)]
       │ (配置日志管道注入 HDFS、补齐 commons-lang 依赖打通 MySQL 导出)
       ▼
[阶段 5: 现代流与服务层 (Kafka 3.9.2 + Spring Boot 3.x)]
       │ (切 JDK 17、格式化 KRaft、启动消息队列与后端微服务)
       ▼
[阶段 6: 全链路终验 (Quality Gates)]
```

---

## 3. 关键决策与坑点治理规则

在实施过程中，Agent 必须遵循以下因果判定，规避已知崩溃点：

### 规则 3.1：WSL2 专属 systemd 守护
- **现象**：WSL2 默认不以 systemd 为 PID 1，导致 `systemctl` 无法管理服务。
- **治理**：检查 `/etc/wsl.conf`，必须包含 `[boot] \n systemd=true`。如果未开启，配置后必须提示或触发实例重启。

### 规则 3.2：双 JDK 隔离与单横线规则
- **治理**：
  - 大数据生态（Hadoop/Hive/Spark/Flume/Sqoop）强制使用 JDK 8。
  - 现代服务与流组件（Kafka/Spring Boot/Maven）强制使用 JDK 17。
  - **严禁使用 `java --version` 查询 Java 8**，JDK 1.8 仅支持单横线参数 `java -version`。

### 规则 3.3：Hive 与 Hadoop 的 Guava 版本死锁
- **现象**：Hive 3.1.3 自带 `guava-19.0.jar`，Hadoop 3.3.6 依赖 `guava-27.0-jre.jar`，两者混用会抛出致命的 `NoSuchMethodError: Preconditions.checkArgument`。
- **治理**：必须彻底删除 `/opt/hive/lib/guava-19.0.jar`，并将 Hadoop common 下的 `guava-27.0-jre.jar` 复制到 Hive lib 目录下。

### 规则 3.4：Hive Metastore 在 MySQL 中的索引长度超限
- **现象**：MySQL 8.0 默认字符集为 `utf8mb4`，在执行 Hive `hive-schema-3.1.0.mysql.sql` 建表脚本时，大量字段组合索引会超过 767 字节限制。
- **治理**：创建 `metastore` 元数据库时，必须强制指定 `CHARACTER SET latin1`；业务数据存储库（如 `traveldb`）才可使用 `utf8mb4`。

### 规则 3.5：Hadoop 3.x 下 Sqoop 缺失 commons-lang 异常
- **现象**：Sqoop 1.4.7 依赖 `org.apache.commons.lang.StringUtils`，而 Hadoop 3.x 从公共类路径移除了 `commons-lang-2.6.jar`，导致 Sqoop 报错退出。
- **治理**：必须在 `/opt/sqoop/lib/` 下单独补充放置 `commons-lang-2.6.jar`。

---

## 4. 全面验证准则 (Quality Gates)

Agent 完成部署后，必须依次通过以下 6 道自动化验收闸门，方可向用户交付：

### 闸门 1：守护进程存活检查 (`jps`)
执行 `jps`，输出中必须**同时且稳定**存在以下核心进程：
- `NameNode`
- `DataNode`
- `SecondaryNameNode`
- `ResourceManager`
- `NodeManager`
- `Kafka`

### 闸门 2：分布式存储与计算自检 (HDFS + MapReduce)
- **动作**：通过 `hdfs dfs -put` 上传测试样本文件，并提交运行 `hadoop-mapreduce-examples-3.3.6.jar` 的 `wordcount` 作业。
- **合格标准**：YARN 成功分配 Application ID，作业以 `SUCCESS` 状态退出，HDFS 成功产出 `part-r-00000` 结果文件。

### 闸门 3：数仓分层与元数据双向流通 (Hive + MySQL)
- **动作**：通过 `hive -e` 执行 DDL 创建数据库和数据表，并插入多行数据。
- **合格标准**：
  1. HDFS 对应 warehouse 目录下生成真实数据文件；
  2. 登录 MySQL 执行 SQL，在 `metastore.TBLS` 表中可查到该表的元数据记录。

### 闸门 4：Spark 统一批处理与数仓直读 (Spark SQL)
- **动作**：通过 `spark-sql -e` 执行跨库跨表 `SELECT` 查询刚才在 Hive 中创建的表。
- **合格标准**：在无需额外配置的情况下，Spark 成功利用 Hive Metastore 直接解析并返回表内数据。

### 闸门 5：事件采集与流消息验证 (Flume + Kafka)
- **动作**：
  1. **Flume 管道**：启动监听本地文件的 Flume Agent，追加日志行，检查 HDFS 目标路径是否按时切分生成数据。
  2. **Kafka 管道**：向指定 Topic 生产消息，并通过内置 Console Consumer 准确消费并打印。

### 闸门 6：宿主机网络与服务接口联通 (Host Connectivity)
- **动作**：在宿主机（Windows / macOS）命令行或浏览器直接发起网络探测：
  - HDFS 页面：`GET http://localhost:9870` 响应 200/302；
  - YARN 页面：`GET http://localhost:8088` 响应 200/302；
  - Spring Boot 服务：`GET http://localhost:8080/api/status` 成功返回包含系统状态的 JSON。
