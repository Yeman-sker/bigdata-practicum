# 实时规则与原子发布交接

Refs #29，PR #36。单个 Spring Boot 进程接收 Kafka 原生 key/headers/value，完成校验、风险计算、整数调度和 MySQL 发布，现有 HTTP API 读取同一发布版本。未改事件、SQL 或 HTTP 契约。

## 运行

在专用业务库执行 `sql/serving.sql`，创建单分区 topic；完整步骤见 [runbook](../runbook.md)。配置数据库账号后，从仓库根目录启动：

```bash
export DATA_DIR=/tmp/citibike-v11
export KAFKA_BOOTSTRAP_SERVERS=localhost:9092
export SPRING_KAFKA_CONSUMER_GROUP_ID=citibike-live-v1
mvn -f backend/pom.xml spring-boot:run -Dspring-boot.run.profiles=live
```

collector 使用同一 `DATA_DIR`，先写 `gbfs/metadata/<hash>.json`，再发送该批。录制模式改用 `recorded`、独立数据库和消费组；fixture 模式只读取 seed，不启动消费者。没有 historical_release 时仍发布当前库存，有效站点预测为 `NO_BASELINE`。不可把 fixture 基线装入真实业务库冒充历史交付。

三处实现边界：

- `OperationsWire` 校验完整 wire 值、原始 metadata 字节及纽约本地时间；同站不同完整 value 会拒绝整批。
- `OperationsRuntime` 顺序接收、超时与坏批次排空。已完整批次保留到发布成功；坏批次到结束或下一批边界才提交 offset，重启不能跳过错误前缀。
- `JdbcOperationsStore` 一致读取 historical_release/profile，单事务替换两张 ADS 和 live_release；相同已发布 ID 不写入、不刷新时间。新基线在下一完整快照生效。

每库只有一个 writer。Kafka 手动分配单分区并保留消费组 offset，不做消费者组再平衡；扩展多实例前需要重新设计分配与写入隔离。应用停止用 wakeup 等待消费线程退出。数据库连接、socket 和查询设置 10 秒超时，失败批次按秒重试。

业务时钟与接收计时分开：recorded 使用成功批的 ingested_at，live 使用 UTC；120 秒接收期限使用单调时钟，完整批的发布重试不再受该期限影响。HTTP 请求到期会保留上次库存、清空预测并过滤建议。

## 可复跑检查

不依赖外部服务的检查：

```bash
mvn -f backend/pom.xml --batch-mode test
```

该命令自动运行 22 个风险、9 个调度、12 个批次共享案例，以及 wire、offset、超时、延迟重试、metadata 换版和整数边界检查。外部服务测试默认跳过。

真实存储与 Kafka 检查只接受固定名称的专用测试库，会重建其中测试数据。先创建空库 `citibike_operations_it`、`citibike_kafka_it`；Kafka 允许测试创建单分区 topic。测试自行装载 DDL/seed，Kafka 测试结束删除自己创建的 topic/group。

```bash
export CITIBIKE_OPERATIONS_IT=true
export CITIBIKE_OPERATIONS_IT_URL='jdbc:mysql://127.0.0.1:3306/citibike_operations_it?serverTimezone=UTC'
export CITIBIKE_OPERATIONS_IT_USERNAME=root
export CITIBIKE_OPERATIONS_IT_PASSWORD="$SPRING_DATASOURCE_PASSWORD"
export CITIBIKE_KAFKA_IT=true
export CITIBIKE_KAFKA_IT_BOOTSTRAP=localhost:9092
export CITIBIKE_KAFKA_IT_URL='jdbc:mysql://127.0.0.1:3306/citibike_kafka_it?serverTimezone=UTC'
export CITIBIKE_KAFKA_IT_USERNAME=root
export CITIBIKE_KAFKA_IT_PASSWORD="$SPRING_DATASOURCE_PASSWORD"
mvn -f backend/pom.xml --batch-mode \
  -Dtest=JdbcOperationsStoreMySqlTest,OperationsKafkaIntegrationTest test
```

CI 同时启动 MySQL 8.0.46 和 Kafka 3.9.2，并启用上述检查及现有 API MySQL 检查。三个测试库相互独立；前端和 Python 门禁保留。

## 2026-09-17 验证结果

环境为 macOS Java 17、OrbStack Ubuntu Kafka 3.9.2、隔离 Docker MySQL 8.0.46。代码 `aa6da71` 对应的本地 Maven 全量结果为 **43 tests, 0 failures, 0 errors, 0 skipped**，包含下列真实服务检查：

| 路径 | 观察到的结果 |
| --- | --- |
| 原生 Kafka → MySQL → HTTP，共享 fixture | 2 个站点，预测 36/4，4199.12 → 5484.09 搬 8 辆、84 米，结束 offset 后才提交 |
| 未完成/冲突批 | 未完成前缀不提交；不同 disabled 字段也构成冲突；重启后重放错误前缀，完整排空才提交，三张表和录制时钟不变 |
| 正常重启及重复投递 | 相同数据库/组不重置 offset；相同 snapshot 不刷新 as_of/published_at，不累加建议 |
| MySQL 注入失败 | suggestion INSERT 触发器报错；此前 DELETE 和 risk INSERT 一并回滚，offset 不前进；移除故障后同批重试成功 |
| 请求过期与空批 | 显式推进隔离 recorded 时钟到过期边界，原库存保留、预测为空、建议清空；随后 0 站完整批返回 HTTP 200 |
| 历史基线一致性 | 并发切换 historical_release/profile 时仍读取同一版本；无基线、SQL NULL、UTC、空发布和旧批拒绝通过 |
| offset 提交失败单测 | 数据库发布一次；只重试相同 offset，不继续 poll、不重复发布 |

真实 Kafka 测试观察的连续提交位置为 `3 → 7 → 10 → 13 → 14`；两次 Spring 应用重启。失败后 121 秒仍能发布的检查使用可控单调时钟，不以真实等待代替边界断言。存储检查另在 `Asia/Shanghai` JVM 时区通过。

### 新采集的 GBFS 输入

使用现有 `citibike.gbfs_stream` 从 `https://gbfs.citibikenyc.com/gbfs/2.3/gbfs.json` 采集 3 批，经真实 Kafka → 同一 Spring API/MySQL 发布。每批 2,519 站，映射成功 2,519、隔离 0，最终 offset `7560/7560`、lag 0。三批 metadata hash 均不同，消费者逐批加载成功。

| 采集时间 UTC | snapshot_id | metadata_version |
| --- | --- | --- |
| 09:39:13 | a6f7032fc4f3576b5950b5f4d2e3cc534b9fbdf3517ecafe7965e37240e70398 | 82f2b819744a0de8202d032f0b88a1fb5111217feec979611e9f698e5ff9bc7e |
| 09:40:20 | 8084ebe3ce0d0cc531cd2009f02a0682657aa6bb7551a282eb073860e1a486d5 | 6927835396f0a48e0980a7d9f0921d1a7f88e6a5e9280ec07c3b827f2f76bce6 |
| 09:41:31 | f81fe712646fda707e7567782a4af0faede60012843061946d91d45e41db3c2f | 99d8253b86253795db070b80739f7f33a60e5f3f3c60c88d659d4adc253e4f69 |

上述 live 采集在 `2e1489b` 运行；`aa6da71` 随后修复整数边界和关闭线程，并通过全量真实集成检查。真实业务库没有历史基线，未产生调度建议。HTTP 明确返回 `GBFS_LIVE / wall`，有效站点返回 `NO_BASELINE`，已过期的源观测仍标为过期。

本地原始 feed、producer/app 日志、HTTP 响应和检查输出保留在 `/tmp/pr36-completion/`，未提交全量数据或凭据。录制样例标为 `GBFS_REPLAY`，自动集成样例标为 `FIXTURE`；两者不作为真实输入证据。

## Issue #29 后续集成状态

2026-09-21 在合并基线 `3b362de` 上重新运行完整 2025-01，Sqoop 发布真实历史基线后，新采集的三批 GBFS 已经 Kafka → 规则 → MySQL → HTTP 消费。dataset_id 为 `44ff32b770d259c871d6d532bd890be01f34a959bc2e1fe109b210f96696515f`；一次 HTTP 核验观察到 980 站有效预测、231 条建议，数量随源数据与过期时间变化。真实 MySQL/Kafka 的 43 项测试及 live 重启检查通过，详见 [集成记录](../integration/2026-09-21.md)。

这补齐了旧记录中的真实历史基线缺口，不代表负责人和直接消费者已签收。Hermes 后续真实浏览器检查发现回放超时，索引修复后该小时已走通；全量 UI 验收尚未完成，详见 [优化交付记录](../integration/2026-09-21-follow-up.md)。保持 `Refs #29`，在交叉签收完成后再申请关闭 Issue。
