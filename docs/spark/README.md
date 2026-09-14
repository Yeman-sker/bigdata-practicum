# Day 1 / Track E — Spark 读取 PoC

`citibike.spark` 是 Day 1 的下游集成门禁。它只做
Historical Trips 的受控读取和验证，不实现 DWD ETL。

## 可复现运行

先用仓库内 synthetic fixture 验证脚本和 Spark runtime：

```bash
mkdir -p /tmp/citibike-day1
PYTHONPATH=. spark-submit --master 'local[2]' \
  citibike/spark.py \
  --input fixtures/citibike/202501_sample.csv \
  --source-month 2025-01 \
  --expected-count 20 \
  --sample-rows 3 \
  --output-json /tmp/citibike-day1/spark-fixture-summary.json
```

对 Track D 已提供的真实 Hive ODS，使用 Hive Metastore 入口完成三方对账：

```bash
HADOOP_USER_NAME=bigdata PYTHONPATH=. spark-submit --master 'local[2]' \
  citibike/spark.py \
  --hive-table citibike_ods.ods_trip_raw \
  --source-month 2025-01 \
  --manifest docs/source/citibike-202501-manifest.json \
  --sample-rows 10 \
  --output-json /tmp/citibike-day1/spark-hive-summary.json
```

如果当天只能直接读 HDFS ODS 文件，使用同一脚本并在验收记录中标明这是
`csv` fallback；该模式仍能验证多 CSV、schema、count 和样例，但不会伪造
Hive Metastore count：

```bash
HADOOP_USER_NAME=bigdata PYTHONPATH=. spark-submit --master 'local[2]' \
  citibike/spark.py \
  --input /warehouse/ods/ods_trip_raw/year=2025/month=01 \
  --source-month 2025-01 \
  --manifest docs/source/citibike-202501-manifest.json \
  --sample-rows 10
```

## Gate 输出

脚本会输出 `printSchema`、source sample、派生时间 sample，以及 JSON summary，
其中包含：

- 13 个 source columns；`start_station_id` / `end_station_id` 必须为 `string`；
- 坐标显式映射为 `double`，并记录 null、非法数值和越界数量；
- `started_at` / `ended_at` 按实际格式解析，session timezone 固定为
  `America/New_York`，临时派生 `started_at_local`、`ended_at_local`、
  `service_date`、`start_hour`、`day_of_week`、`is_weekend`；其中
  `day_of_week` 使用 ISO 约定（周一为 1，周日为 7）；
- `total_rows`、station id null、时间 parse failure、时间范围和枚举 distinct
  值；
- `source` / `hive` / `spark` 三方 count。三者都提供且相等时为 `PASS`；只有
  两方可见时明确标为 `PARTIAL`；完全没有 source count 证据时为 `NOT_RUN`，
  整体 gate 返回 `FAIL`。

Track A 的 2025-01 source count 为 `2,124,475`，Track D handoff 已记录 Hive
ODS count 为 `2,124,475`。具备 Hive Metastore 的环境应直接执行上面的
`--hive-table` 命令，并将 `counts.status` 为 `PASS` 的结果回填 Day 1 #5 的
Integration Result。

## 本地检查结果

```bash
python3 -m unittest tests/test_spark.py
```

没有 PySpark 的 CI 环境会跳过 runtime smoke test，但仍检查 13 列契约、月份
格式和 manifest count 校验；安装 Spark 后运行上面的 fixture 命令即可完成
runtime gate。

## 2026-09-14 macOS host 真实源回放

官方 2025-01 ZIP 已按 Track A manifest 校验 SHA-256 和三个 CSV 文件大小，解压
到 `/tmp/citibike-day1/202501/extracted` 后运行 local CSV replay：

```text
source count: 2,124,475
Spark count: 2,124,475
null start_station_id: 564
null end_station_id: 4,322
invalid started_at / ended_at: 0 / 0
started_at range: 2024-12-30 23:41:25.635 → 2025-01-31 23:58:14.634
rideable_type: classic_bike, electric_bike
member_casual: casual, member
station_id: string / string
coordinates: double / double / double / double
count reconciliation: PARTIAL (Hive 未在当前 macOS 验证机安装)
```

这次 host 回放只验证 CSV 读取路径；最终三方门禁见下节。

## 2026-09-14 OrbStack 真实集成门禁

按 `bigdata-env-setup` 使用已配置的 OrbStack `bigdata`（Ubuntu 22.04、JDK 8、
Hadoop 3.3.6、Hive 3.1.3、Spark 3.5.7）完成真实落盘和 Hive/Spark 回放：

```text
HDFS RAW/ODS files: 3 / 3
HDFS RAW/ODS bytes: 414,212,882 / 414,212,882
Hive partition_ride_count: 2,124,475
source / hive / spark: 2,124,475 / 2,124,475 / 2,124,475
counts.status: PASS
station_id: string / string
coordinates: double / double / double / double
invalid started_at / ended_at: 0 / 0
null start_station_id / end_station_id: 564 / 4,322
duplicate ride_id groups: 0
csv header rows: 0
time zone: America/New_York
contract deviations: none
```

Spark 读取 Hive 表时显式剔除 CSV header 记录；Hive CLI 的
`skip.header.line.count` 不会可靠地传递给 Spark 的 Hive text reader。

## 边界

本 Track 不做 `dwd_trip_v1`、小时流量、风险估计、调度、Kafka、Spring Boot
或前端。真实 ZIP/CSV、Spark warehouse 和运行 summary 默认都放在 `/tmp` 或
HDFS，不进入 Git。
