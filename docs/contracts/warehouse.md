# 数据、数仓与服务表

版本：Day 2 v1.1。主维护人 Hu-tong123（#28），GBFS/API 与规则负责人交叉评审。Hive DDL 见 [warehouse_v1.sql](../../hive/warehouse_v1.sql)，MySQL DDL 见 [serving.sql](../../sql/serving.sql)，共享输入和预期表见 [fixture](../../fixtures/day2/README.md)。

## 公共约定与版本

字符串 ID 不转数值。计数为非负 BIGINT，净流量为有符号 BIGINT；均值/中位数为有限 DOUBLE；ISO 星期周一=1、周日=7，hour=0..23。历史 wall-clock 用 TIMESTAMP（Spark session America/New_York）；MySQL UTC 列为 DATETIME(6)，连接时区 +00:00，API 补 Z。数值 null 不等于 0。

一批历史源的 ingest_batch_id 取官方 ZIP SHA-256。dataset_id 为 `SHA256('1.1\n' + metadata_version + '\n' + 按月份升序的 'YYYY-MM:zip_sha256\n' 串)`，代表本次接纳月份集合及 metadata 版本。CSV 重跑不改变物理数据；版本号相同则可校验后跳过，不累加。

## Source、RAW、ODS

复用 [Day 1 source](../source/citibike-202501.md) 和 [landing](../hdfs-hive/README.md)：

| 对象 | 粒度、键与路径 | 完整性 |
| --- | --- | --- |
| source manifest | 一月一个 ZIP；成员 filename 唯一 | source_month、source_url、downloaded_at、zip_sha256、zip_size_bytes、zip_members、csv_files、csv_file_count、record_count |
| csv_files 条目 | 一个实际 CSV | filename、size_bytes、header、record_count、缺失/枚举/时间/坐标质量指标；参照现有 manifest，不重命名 |
| RAW | 原始 CSV；/raw/citibike/trips/year=YYYY/month=MM/ | 全部 ZIP CSV，字节与 checksum 对账，ZIP 在外部 staging 保留 |
| ODS | 原始 Trip 行；citibike_ods.ods_trip_raw | 原 CSV 字节副本，13 列；year/month 是源月份；header 不进入数据行 |

source gate 在任何新 DWD 发布前检查全部成员、必需字段、关键时间；失败整批不发布，raw/错误报告保留。空的合法 CSV 允许形成空结果；下载失败不等同于空 CSV。ODS quoted delimiter 限制沿用 Day 1，新增月份预检不符合时必须明确失败或用 Spark CSV 正确读取，不能错列。

Day 1 manifest 保持历史证据原样；重跑将新 manifest 写 DATA_DIR，ingest_batch_id 由 zip_sha256 派生。GBFS RAW 继续保留完整 JSON 与 manifest，不新增 GBFS JSON ODS/DWD、vehicle DIM 或流计算平台。

## DWD：全部物理记录可追踪

`citibike_dw.dwd_trip_v1` 为 Parquet external table，分区 source_year/source_month，根 /warehouse/dwd/dwd_trip_v1。每行一条通过 source gate 的物理 CSV 记录；定位键 `(ingest_batch_id, source_file, source_row_number)`，source_row_number 从首条数据记录 1 起，不是文件物理行号。

Day 1 的 ODS 只有 13 列和月份分区，`citibike.spark` 的读取结果没有稳定文件记录序号，不能直接充当 DWD 来源定位。#28 从 manifest 对应 RAW CSV 的读取边界保留文件名与原记录顺序，再做转换/去重；ODS 继续用于 schema、分区与行数核对，不修改旧表或重新下载。本地 fixture 输入显式使用 file:/// 绝对 URI，HDFS 输入使用对应 /raw 路径，避免默认文件系统误解本地路径。单行 CSV 可按每个文件的输入字节偏移排序后编号，header 不计数；跨行 CSV 必须先用 CSV 解析器逐记录编号，不能按换行数编号。禁止在 shuffle 后用任意 row_number/monotonically_increasing_id 伪造源序号。分区数变化后的定位键与去重胜者必须相同；[Spark zipWithIndex](https://spark.apache.org/docs/3.5.7/api/python/reference/api/pyspark.RDD.zipWithIndex.html) 只继承输入分区/记录顺序，不自动恢复文件顺序。

| 列 | 类型 / nullable | 规则 |
| --- | --- | --- |
| ride_id、rideable_type、member_casual | STRING / 否 | 原值；未知枚举保留 |
| started_at_local、ended_at_local | TIMESTAMP / 否 | 原始纽约 wall-clock |
| duration_seconds | BIGINT / 否 | floor(结束减开始的秒数)；有效性按原时间先后判断，正数不足一秒允许显示 0 秒 |
| start_station_id、end_station_id、start_station_name、end_station_name | STRING / 是 | 映射表规则 |
| start_lat、start_lng、end_lat、end_lng | DOUBLE / 是 | 非法为 null 并计数 |
| service_date、start_hour、day_of_week、is_weekend | DATE、TINYINT、TINYINT、BOOLEAN / 否 | 由 started_at_local 派生；ISO 星期 |
| source_year、source_month | INT / 否 | 来源文件月份，不能由 service_date 替换 |
| ingest_batch_id、source_file、source_row_number | STRING、STRING、BIGINT / 否 | ZIP hash、成员文件名、CSV 记录序号 |
| is_duplicate_ride | BOOLEAN / 否 | 接纳月份集合内按 ride_id 分组，source_year/month、source_file、source_row_number 升序第一条为 false，其余 true |
| is_valid_station_trip | BOOLEAN / 否 | 两端 ID 非空、ended_at_local>started_at_local、非重复；坐标缺失和未知车型不否定站点流量 |

无效与重复行不从 DWD 删除。相同 ride_id 内容冲突仍采用上述可复现首条，另计 duplicate_conflict_count；DWS 仅消费 is_valid_station_trip=true。源文件包含跨月日期时不裁剪；改变某个月的接纳版本后，重新计算接纳月份集合的重复标记和全部 DWS，v1 不做复杂增量补丁。

## DIM

`dim_station_v1`：一站一行，键 station_id。GBFS canonical metadata 与全部 DWD 非空端点 union。字段 station_id STRING 非空；station_name STRING、lat/lon DOUBLE、capacity INT、region_id STRING 可空；is_current BOOLEAN、metadata_source STRING、metadata_updated_at TIMESTAMP(UTC) 非空。

当前有效 GBFS 字段优先，名称/坐标缺失可回退到历史最新有效值。历史候选按端点时刻降序、source_file/row 升序、同记录 start 优先 end，逐字段取非空有效值。历史没有 capacity/region，禁止推算；is_current 由当前 metadata 是否存在决定。metadata_source 为 GBFS（含字段回退）或 HISTORICAL；历史 metadata_updated_at 使用构建时 UTC，原事件时间仍在 DWD。实时 ADS 不依赖此表更新频率。

## DWS

| 表 | 键 / 粒度 | 字段（除键外，均非空） |
| --- | --- | --- |
| dws_station_hourly_flow_v1 | station_id STRING × service_date DATE × hour TINYINT | inbound_rides、outbound_rides、net_flow、total_activity、electric_outbound、classic_outbound、member_outbound、casual_outbound：BIGINT |
| dws_station_hour_profile_v1 | station_id STRING × day_of_week TINYINT × hour TINYINT | avg_inbound、avg_outbound、avg_net_flow、median_net_flow：DOUBLE；sample_days BIGINT |
| dws_station_od_hourly_v1 | service_date DATE × hour TINYINT × from_station_id STRING × to_station_id STRING | ride_count BIGINT，严格 >0 |

- 流出按出发站/出发日期/小时，流入按到达站/到达日期/小时；net=inbound-outbound，activity=inbound+outbound。
- 一个站点在某日有至少一次有效流入或流出，该日为活跃日，为它补齐 0..23 的 24 行；没有活动的站点日不补。子类型只按出发端计数，未知类型不计入已知子项。
- profile 用当前接纳数据中该站点、该星期的所有活跃日期，含当天零小时；sample_days 为这些 distinct 日期数，各小时一致。median 为排序后的中值，偶数时取中间两项均值；不采用近似 percentile。
- OD 按出发日期/小时归桶，仅非零，包含自环；自环计流入和流出，地图可画站点脉冲，不能丢失计数。
- Hive flow/OD 按 service_date 分区；profile 与 DIM 全量小表。所有新表 Parquet external，路径见 DDL，不删除 RAW。

全体已接纳有效行有 `sum(inbound)=sum(outbound)=sum(OD.ride_count)=有效去重 Trip 数`。单小时 inbound 不必等于出发小时 OD；跨日和跨月同理。不得只筛“2025-01”业务日期后声称与整个 202501 文件行数守恒。availability 从已发布 flow 的 distinct service_date/hour 得到；边缘日期可能只有样本覆盖，页面称“已入库历史”，不宣称完整运营日。

## MySQL 服务表与发布

MySQL 数据库 `citibike` 与 Hive metastore 数据库分开。所有服务表 InnoDB、utf8mb4_bin，ID 排序与业务字符串顺序一致。DDL 是 [serving.sql](../../sql/serving.sql)，禁止 API 私自增列改义。

| 表 | 写入者 | 主键 / 读取者 |
| --- | --- | --- |
| dim_station_v1 | #28 | station_id；历史地图、history API |
| dws_station_hourly_flow_v1 | #28 | station_id/date/hour；availability、history API |
| dws_station_hour_profile_v1 | #28 | station_id/weekday/hour；history API、operations |
| dws_station_od_hourly_v1 | #28 | date/hour/from/to；历史地图 |
| historical_release | #28 | singleton=1；dataset_id、接纳月份、日期边界、published_at_utc |
| ads_station_current_risk | #29 | station_id；包含 snapshot_id 和完整 Station 字段供 live map；history API 也可用它识别尚未进入历史 DIM 的当前站点；字段名与 OpenAPI 一致 |
| ads_rebalance_suggestion | #29 | suggestion_id，unique priority；包含 snapshot_id、搬运量/余额/距离/有效期 |
| live_release | #29 | singleton=1；snapshot/metadata/baseline 版本、来源、时钟、源/接入/计算/发布时间 |

同一历史发布内四张表的行附带同一个 dataset_id；实时站点/建议附带同一个 snapshot_id。只保存当前发布的业务表，原始可追溯性留在 HDFS/外部文件，不建立多版本查询平台。

### 离线发布步骤

1. Spark 计算全部表，写新 HDFS 输出目录并校验完整计数；导出为 UTF-8 TSV，null 编码 `\\N`。名称等字符串中的 tab/CR/LF 在服务导出时替换为空格，HDFS 事实保留原值。
2. 清空本次专用 `<table>_load` staging 表，通过 Sqoop `export --export-dir ... --table <table>_load --input-fields-terminated-by '\t' --input-null-string '\\N' --input-null-non-string '\\N' --num-mappers 1` 写入；schema、列顺序与正式表一致，明确 --columns。失败不碰正式表。
3. 校验每张 staging 行数、dataset_id、主键唯一性、流量守恒、profile 日期数和 OD 非零。单个 export 成功不代表多表整体发布成功。
4. 单个 MySQL 事务中 DELETE 四张正式表、INSERT SELECT 对应 load 表、更新 historical_release，COMMIT；失败 ROLLBACK。禁止在事务中 TRUNCATE/DDL，禁止 API 读 load 表。

Day 3 先用 expected.json 的四张服务表做最小 TSV→Sqoop→staging→事务发布试跑，提前验证驱动、权限、null 与列顺序；随后换成实际 Spark 输出。该试跑只证明导出通道，不能作为 ETL 通过证据。同一库一次只运行一个 export；重试先清空本次 staging，保留正式发布。

Sqoop export 可能提交部分数据，使用专用 staging 将失败隔离。[Sqoop 1.4.7 官方说明](https://sqoop.apache.org/docs/1.4.7/SqoopUserGuide.html#_exports_and_transactions)。这里只做单月/小型薄表整体替换；扩数导致发布事务过大时，再由实测提出后继方案。

### 实时发布与查询

operations 每批先在一次一致性读中取得 historical_release 与对应 profile；生成全部站点及建议后，在一次写事务中 DELETE 两张 ADS、INSERT 新行、更新 live_release，COMMIT 后提交 Kafka offset。失败回滚，两张表不得分开发布。live 没有基线时 baseline_dataset_id 为 null。

API 使用只读 REPEATABLE READ 事务读取发布行与相关数据；live 不 join 新的历史 DIM 改写本批 metadata。请求到期判断可屏蔽预测/建议，不能更新原库存或基线。历史/实时发布可独立进行。API 不直接读 DWD 或重算业务聚合。

## 质量证据与验收

每个批次报告 source/ODS/DWD 行数、有效/无效/重复/冲突数、缺失站点/坐标数、非法时间与非正时长、各 DWS 行数及守恒式、metadata 映射/历史命中数、发布 dataset_id 和 checksum。失败保留 raw 与报告，不发布虚假的空结果。

共享样例最低对账：8 条物理 Trip → 8 条 DWD → 5 条有效去重 Trip；144 行 flow、96 行 profile、3 行非零 OD，总流入/流出/OD 均为 5。具体每一行由 expected.json 提供，跨月到达也计入。2025-01 真实 source/ODS 的既有证据为 2,124,475 行，真实 DWD/DWS 仍须由 #28 实现后提供，不预报结果。
