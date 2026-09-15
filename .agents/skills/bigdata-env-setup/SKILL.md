---
name: bigdata-env-setup
description: Set up or verify requested practicum infrastructure components on Ubuntu 22.04 under WSL2 or OrbStack.
---

# 大数据环境搭建与验收

在用户选定的环境中部署或验证本次需要的组件。仅验证 Hadoop 时不扩展到 Kafka 或 Spring Boot；完整环境搭建才覆盖全链路。

## 基准与边界

- 组件版本以 [README 版本矩阵](../../../README.md#1-基础环境与版本矩阵) 为准，不在 skill 复制另一份版本表。保留课堂版本，版本变更需说明兼容性影响。
- 先识别宿主机、WSL2 / OrbStack 实例、CPU 架构和已有安装；复用已有组件。`/opt/soft` 用于安装包，组件安装目录如 `/opt/hadoop`，运行数据放 Linux 本地 `/data/hadoop`，不跨宿主机共享数据库、元数据或 SSH 私钥。
- Hadoop / Hive / Spark / Flume / Sqoop 使用 `/opt/jdk8`；Kafka / Spring Boot / Maven 使用 `/opt/jdk17`。按组件进程设置 `JAVA_HOME`，用 `java -version` 检查（Java 8 不支持 `--version`）。
- 格式化 NameNode、KRaft 存储或初始化元数据库前确认目标为新建且无数据。已有数据时先诊断，不能靠重新格式化恢复服务。安装、重启或其他系统变更复用本次授权，超出范围才询问。
- 只在遇到相关组件时读 [兼容性记录](references/compatibility.md)。本地测试与完整项目验收是不同范围，项目运行入口见 [runbook](../../../docs/runbook.md)。

## 依赖与验收

依据实际依赖推进：系统 / JDK / MySQL → Hadoop → Hive / Spark；Flume、Sqoop 和 Kafka / 应用按需求接入，不固定要求所有步骤串行重做。每步复用已验证结果，仅复跑失败或受改动影响的检查。

| 本次涉及组件 | 可观察的完成证据 |
| --- | --- |
| WSL2 / OrbStack | 目标实例与用户可执行命令；需要 systemd 的服务能正常管理 |
| Hadoop / YARN | 核心进程及服务响应；隔离 HDFS 路径上的 wordcount 成功并产出结果 |
| Hive / MySQL | 测试库表可读写，HDFS 数据与 Metastore 元数据对应 |
| Spark | 能读取本次 Hive / 文件样例并核对结果 |
| Flume / Sqoop | 测试日志进入 HDFS / 测试数据完成数据库交换并对账 |
| Kafka | 隔离测试 topic 能生产与消费相同消息 |
| 宿主机访问 / 应用 | 所需 Web UI 或已实现的 API 可访问；接口与端口以实际配置为准 |

完整搭建请求完成全部相关验收；局部任务只报告本次范围。记录版本、命令、结果与剩余故障，不能以 `jps` 存活代替数据链路成功。测试使用独立库 / topic / 路径，保留证据后仅清理本次创建的资源。
