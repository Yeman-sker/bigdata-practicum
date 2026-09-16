# 运行与验收手册

版本：Day 2 v1.1。执行者是对应 Issue 的负责人，组长汇总端到端证据。本文提供可立即执行的契约检查，以及 #26–#30 必须交付的运行入口；后者在实现 PR 合并前不能当作已实现或已验证。

## 现在即可执行：文档与样例

从仓库根目录运行，临时环境与输出放仓库外：

```bash
uv run --with-requirements requirements-contracts.txt python -m unittest discover -s tests -p 'test_*.py' -v
python3 -m citibike.contract_validator historical fixtures/day2/trips/part-a.csv
python3 -m citibike.contract_validator historical fixtures/day2/trips/part-b.csv
python3 -m citibike.contract_validator station_information fixtures/day2/station_information.json
python3 -m citibike.contract_validator station_status fixtures/day2/station_status.json
git diff --check
```

无 uv 时可在外部 venv 中 `python -m pip install -r requirements-contracts.txt` 后运行相同 unittest。依赖只用于文档/契约验证，不引入产品运行时组件。OpenAPI/Kafka schema、fixture 关联、行数与数值对账通过，不代表 Spark/服务/页面已经实现。无 PySpark 的环境会跳过既有 runtime smoke tests，必须如实报告。

## 环境与配置

运行基础使用 [README 版本矩阵](../README.md)；已有环境交接见 [HDFS/Hive](hdfs-hive/README.md)、[Spark](spark/README.md)、[GBFS](gbfs/README.md)。JDK 8 运行 Hadoop/Hive/Spark/Sqoop/Flume，JDK 17 运行 Kafka/Spring Boot/Maven，各进程独立切换，不修改同一 shell 的全局服务环境来控制已运行进程。

| 配置 | 用途 / 默认 |
| --- | --- |
| DATA_DIR | 必填绝对目录，例如 /tmp/citibike-v11；raw、metadata、日志、证据，不放 Git |
| KAFKA_BOOTSTRAP_SERVERS | localhost:9092；topic bike.station.status.v1，单分区 |
| SPRING_KAFKA_CONSUMER_GROUP_ID | live 固定 citibike-live-v1；recorded 每次独立演练使用新组名，重启同一次演练沿用原组 |
| SPRING_DATASOURCE_URL | jdbc:mysql://localhost:3306/citibike；Hive metastore 使用不同数据库 |
| SPRING_DATASOURCE_USERNAME / SPRING_DATASOURCE_PASSWORD | 本机服务账号环境变量，不写进 fixture/日志/仓库 |
| MYSQL_PASSWORD_FILE | Sqoop 用的外部凭据文件；由本机配置，命令只传文件路径 |
| METADATA_FILE | collector 报告的版本文件绝对路径，不能随意选择另一个快照的文件 |
| DATASET_ID | offline 成功报告的历史版本，用于导出与对账 |
| server.port | backend 默认 8080；frontend 开发端口 5173，/api 代理到 backend |

不强制更换已有依赖库；新增 Maven/npm/Python 客户端依赖在对应首个实现 PR 锁定，提交锁文件及构建方法。

## 初始化、启动、停止

在专用空业务库初始化，不向 Hive metastore 装载这些表：

```bash
mysql -u root -p -e 'CREATE DATABASE IF NOT EXISTS citibike CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;'
mysql -u root -p citibike < sql/serving.sql
```

库账号由环境负责人配置。只在独立的样例库装载 `fixtures/day2/seed.sql`，例如创建 citibike_fixture 后执行同一 DDL/seed，再令 backend 数据源指向它。seed 使用 INSERT，重复装载会因主键冲突明确失败，不覆盖已有真实业务数据。

首次真实启动顺序：HDFS/YARN、MySQL/Hive metastore 和业务 DDL → Kafka/topic → backend live → collector → 取得 metadata 文件 → 离线作业及 Sqoop 发布 → 下一完整快照 → frontend。collector 必须先生成离线所需 metadata；backend 在没有 historical_release 时仍能处理当前库存，预测为 NO_BASELINE。不能等历史发布才启动 collector，也不能把缺发布行当作 backend 启动失败。

API 在尚无实时/历史发布时分别返回 503；第一批实时数据后 live 可用，历史接口仍可为 503；历史发布后，下一完整快照才补上预测。frontend 可在任一阶段启动，展示对应加载/缺数据状态。启动已有环境时不重做格式化或装载 seed。

fixture 联调只需 MySQL 样例库、backend 和 frontend。三种 backend 启动方式如下；profile 必须显式选择，禁止同一业务库同时运行两个 writer：

| profile | Kafka / 数据源 | 业务时钟 |
| --- | --- | --- |
| fixture | 禁用 consumer；读取已装载 seed 的独立库 | 从 live_release.as_of_utc 恢复并冻结 |
| live | 固定消费组；只接纳 GBFS_LIVE；新组 earliest，关闭自动提交 | 当前 UTC |
| recorded | 演练专用库/消费组；只接纳 GBFS_REPLAY 或 FIXTURE；按下文设起点 | 从 live_release 恢复，成功发布下一完整批时才推进 |

Flume 在日志目录存在后启动，其失败不伪装成业务数据失败。停止顺序为 frontend → collector → backend → Flume → Kafka/Hive/Hadoop；只停止本次任务启动的进程。

已有 source、landing、Spark PoC 的准确命令直接沿用各 handoff，输出 manifest/summary 改到 DATA_DIR。它们只覆盖 Day 1 边界，不能代替下面的 offline/实时实现。

## 实现任务必须提供的入口

以下命令是交付约定，当前文档 PR 不实现这些程序。各主负责人完成时将实际运行环境、输出与 PR 链接记入自己的 Issue；若确需改入口，更新本表与对应 Issue，不能保留失效命令。

| 负责人 | 目标入口 | 必须观察到 |
| --- | --- | --- |
| #28 | `PYTHONPATH=. spark-submit --master 'local[2]' citibike/offline.py --hive-table citibike_ods.ods_trip_raw --raw-root /raw/citibike/trips --source-month 2025-01 --manifest "$DATA_DIR/historical/manifest.json" --metadata "$METADATA_FILE" --output-root /warehouse --evidence "$DATA_DIR/offline.json"` | 从 RAW 保留记录位置、与 ODS 核对；DWD/DIM/DWS Parquet、Hive 分区、dataset_id、对账与失败状态 |
| #28 | `python3 -m citibike.serving_export --dataset-id "$DATASET_ID" --hdfs-root /warehouse --password-file "$MYSQL_PASSWORD_FILE" --evidence "$DATA_DIR/export.json"` | 读取 SPRING_DATASOURCE_URL/USERNAME；Sqoop staging 校验、事务发布和相同 dataset_id |
| #27 | `python3 -m citibike.gbfs_stream --output-dir "$DATA_DIR/gbfs" --bootstrap-servers "$KAFKA_BOOTSTRAP_SERVERS" --interval-seconds 60` | 复用 gbfs.py；metadata 版本文件、raw、station/end Kafka 记录、批次日志 |
| #26/#29 | `mvn -f backend/pom.xml spring-boot:run -Dspring-boot.run.profiles=fixture` | 读取样例 MySQL；禁用 Kafka consumer；按已存 recorded 时钟查询 |
| #26/#29 | `mvn -f backend/pom.xml spring-boot:run -Dspring-boot.run.profiles=live` | 同 JVM 启用 consumer/规则与三个 HTTP API，墙钟判新鲜度 |
| #26/#29 | `mvn -f backend/pom.xml spring-boot:run -Dspring-boot.run.profiles=recorded` | 独立演练库/组的录制库存；消费、原子发布和录制时钟，不伪装成实时 |
| #30 | `npm --prefix frontend ci`；`npm --prefix frontend run dev` | 开发根地址默认共享 live 样例；全屏地图、风险/调度拨盘、日期/小时及站点透镜；`?api=1` 切换 /api 代理 |
| #30 | `npm --prefix frontend run build` | TypeScript 与静态构建成功 |
| #26/#29 | `mvn -f backend/pom.xml test` | HTTP 契约、规则算例、批次/事务边界测试通过 |

无 MySQL 时前端开发根地址直接加载 http-examples.json；`?fixture=...` 选择其他样例状态，`?api=1` 显式连接 backend。API 可先用 MockMvc 和同一输入做控制器测试，不新增替代数据库。生产构建与完整服务演示仍使用真实 API/MySQL。

## Kafka 与录制事件

使用 Kafka 已有脚本创建与检查 topic：

```bash
kafka-topics.sh --bootstrap-server localhost:9092 --create --if-not-exists --topic bike.station.status.v1 --partitions 1 --replication-factor 1
kafka-topics.sh --bootstrap-server localhost:9092 --describe --topic bike.station.status.v1
```

#27 还必须交付 `python3 -m citibike.gbfs_stream --replay PATH --bootstrap-servers "$KAFKA_BOOTSTRAP_SERVERS"`：读取与 events.ndjson 相同的 key/headers/value 封装，保留源时间，把 data_origin 标为 GBFS_REPLAY。每次独立重放按以下顺序执行：

1. 停止该实例的实时 producer 和 backend；使用独立演练库。可装载 seed 提供历史基线，但发送事件前只在该演练库用一次事务清空两张 ADS 与 live_release，避免相同 snapshot_id 被判为已经发布；metadata 文件按其 hash 名放到该库实例的 DATA_DIR。
2. 选择从未使用的新消费组，例如 `citibike-replay-20260915-a`，设置 SPRING_KAFKA_CONSUMER_GROUP_ID；保留 live 组和业务库。**在发送任何录制记录之前**，用 Kafka 原生命令为这个无活动消费者的新组设置起点：

```bash
kafka-consumer-groups.sh --bootstrap-server "$KAFKA_BOOTSTRAP_SERVERS" \
  --group "$SPRING_KAFKA_CONSUMER_GROUP_ID" --topic bike.station.status.v1 \
  --reset-offsets --to-latest --execute
```

3. 启动 recorded backend，再发送录制文件。起点已经提交，即使 producer 比 consumer 完成分配更早发送也不会漏掉本次记录；记录开始 offset、snapshot_id 和结果。
4. 同一次演练崩溃重启时沿用库和组，**不清库、不重置 offset**；从 live_release 恢复录制时钟。成功发布才提交 offset，重复重送按 snapshot_id 去重。再次从头演练则另选库/组，重复上述初始化。
5. 停止录制 producer/backend 后恢复原 live 库、组和 profile，再启动 live collector。live consumer 拒绝录制来源，不能把演练数据发布为当前事实。

这里复用现有单分区 topic 与 Kafka offset 管理，不增加重放服务或自建 checkpoint。消费组起点规则见 [Kafka 3.9 配置](https://kafka.apache.org/39/configuration/consumer-configs/)。

当前 `citibike.gbfs` 已有三快照采集命令继续用于真实 source 证据。完整 feed 和 Kafka dump 保存在 DATA_DIR，只提交小型统计与已脱敏的关键输出。

## 五条 P0 验收路径

以下 curl 在 backend 实现启动后执行；日期从 availability 选择，示例日期仅用于本仓库 fixture。

```bash
curl --fail-with-body 'http://localhost:8080/api/v1/history/availability'
curl --fail-with-body 'http://localhost:8080/api/v1/map?mode=live'
curl --fail-with-body 'http://localhost:8080/api/v1/map?mode=replay&service_date=2025-01-15&hour=8'
curl --fail-with-body 'http://localhost:8080/api/v1/stations/5484.09/history?day_of_week=3&service_date=2025-01-15'
```

| Given / When | Then / 最低证据 | 责任 |
| --- | --- | --- |
| 样例/真实当前库存，打开实时地图 | 数量与 API 相同，光晕默认当前；切换未来只改光晕；站点透镜同时显示当前与预测 | #30/#26 |
| 真实 2025-01 已发布，切换日期/小时及四档倍速 | 非零 OD 与查询一致；历史不显示今日库存/风险；到末小时暂停 | #30/#28 |
| 查询站点历史 | profile/实际均可手算；sample_days 按活跃日；无数据不假填零 | #30/#26/#28 |
| 当前有效且基线可用，生成一小时估计 | raw p、两项状态、目标时间与规则算例一致 | #29 |
| 来源与目标满足约束，查看调度 | fixture 搬 8 辆/84 米；Top 5 与其余可巡览建议同批；切换风险视图不重算建议 | #30/#29 |

## 必跑异常与恢复

- 非法 mode/hour、replay 缺参数、未知站点/日期、查询超过上限：正确 HTTP 状态及 ErrorResponse。
- 无历史发布、无成功实时批次、MySQL 断连：503；已发布空集合为 200，二者有不同页面状态。
- NO_BASELINE、负库存、零 S、缺 C、停服、过期：使用 cases.json 与 http-examples.json；不出现伪造健康或可执行建议。
- 重复、部分、冲突、旧批、空批、消费重启：按 events 的发布/保留/去重结果；MySQL 写入失败回滚风险与建议，成功后才提交 offset。
- 冷启动无基线、错误运行来源、拒绝的结束记录、录制进程重启：当前库存可独立发布；坏批不推进录制时钟，重启读回已发布时钟；重复演练不得复用旧发布行冒充本次消费成功。
- offline 导出中途失败：正式四张表与 historical_release 不变；重跑相同数据不累加。
- 接口刷新失败、前端后台恢复、切换模式后旧请求晚到：页面保留正确选择，标出失败/过期，过期建议消失。
- 无坐标/无历史映射：数据仍可检查，地图说明不可定位/未绘制数量；记录 metadata 命中分母和分子。

## 证据与最终完成标准

每个切片在对应 Issue/PR 写：提交号、时间、环境版本、输入来源（真实/录制/fixture）、命令、预期/实际关键输出、dataset/snapshot ID、失败与恢复结果、消费者复核链接。截图/日志原件放 DATA_DIR，Git 只保留适量已脱敏汇总。

最终要求：真实 2025-01 三份 CSV 的 source/ODS/DWD 保留行数对账；有效流量/OD/profile 对账；至少三次真实 GBFS 采集；canonical 映射报告且有真实基线命中站点；实际 Kafka→规则→MySQL→API→浏览器；Sqoop 真实导出；Flume 至少一条 collector/backend 日志到 HDFS 的证据；五条 P0 与上述异常可演示；相关 PR/CI 通过。P1 单独标为交付或延期。

既有 Day 1 source/Hive/Spark=2,124,475 的记录可以引用。新增 DWD、Kafka、Sqoop、API、前端的运行证据不能由该数字或本次契约 CI 代替。
