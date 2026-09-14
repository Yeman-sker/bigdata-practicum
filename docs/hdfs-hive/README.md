# Day 1 / Track D — HDFS RAW 与 Hive ODS

本文记录 Issue #9 的 Historical Trip landing contract、可复现命令和
Track E 可直接使用的 Hive 入口。真实 ZIP/CSV 只允许出现在本地 staging
和 HDFS，不提交到 GitHub。

## 交付入口

| 项目 | 约定 |
| --- | --- |
| source month | `2025-01` |
| HDFS RAW | `/raw/citibike/trips/year=2025/month=01/` |
| HDFS ODS files | `/warehouse/ods/ods_trip_raw/year=2025/month=01/` |
| Hive database | `citibike_ods` |
| Hive table | `citibike_ods.ods_trip_raw` |
| partition keys | ``year INT`` + ``month INT`` |
| source record count | `2,124,475`（Track A manifest） |

Historical ZIP 内的 3 个 CSV 都落在同一个 `year=2025/month=01` 分区；文件名
保持 provider 原名，不按文件拆出业务分区，也不假定一个月只有一个 CSV。

## 数据边界

```text
官方 ZIP
   ↓ 解压（本地 staging，仅临时）
extracted/*.csv
   ↓ land_historical_trips.py：校验 header/manifest，不改内容
/raw/citibike/trips/year=2025/month=01/
   ↓ HDFS cp（原样副本，不使用 mv 或 LOAD DATA）
/warehouse/ods/ods_trip_raw/year=2025/month=01/
   ↓ Hive external table
citibike_ods.ods_trip_raw
```

RAW 与 ODS 文件均保留原始 CSV 字节。ODS 只在 Hive SerDe 读取时映射列类型：

- 13 个 source 列全部保留并可追溯；
- `start_station_id`、`end_station_id` 是 `STRING`；
- `started_at`、`ended_at` 在 ODS 中仍是 `STRING`，不提前解释为 timestamp；
- 经纬度是 `DOUBLE`，不做业务派生；
- `skip.header.line.count=1` 对每个 CSV 生效，CSV header 不会成为数据行；
- Track A 已验证的 2025-01 CSV 不含 quoted delimiter，Hive 原生 delimited SerDe
  因而可以按 DDL 暴露 `DOUBLE` 坐标列；若后续月份出现带引号的逗号字段，应先落
  STRING source table，再通过显式 CAST 进入 typed ODS，不得静默错列；
- 不删除空 station id、异常记录或未知枚举，质量问题由 Track A manifest 记录，DWD
  阶段再按契约处理。

`ods_trip_raw` 是 `EXTERNAL` 表，并设置了
`external.table.purge=false`。删除 Hive metadata 不会删除 ODS 文件，更不会删除
`/raw` 原始数据。

## 可复现加载

以下命令假定 Track A 已将完整 CSV 解压到
`/tmp/citibike-day1/202501/extracted`，并已生成仓库中的
[`citibike-202501-manifest.json`](../source/citibike-202501-manifest.json)。首次准备
source 的下载、解压和验证方法见
[`docs/source/citibike-202501.md`](../source/citibike-202501.md)。

### 1. 预检并落 HDFS RAW + ODS 文件

```bash
STAGING=/tmp/citibike-day1/202501

python3 scripts/citibike/land_historical_trips.py \
  --staging-dir "$STAGING/extracted" \
  --source-month 2025-01 \
  --manifest docs/source/citibike-202501-manifest.json
```

脚本在执行任何 HDFS 写操作前会：

1. 递归发现所有 `.csv` 文件并拒绝重名 basename；
2. 对每个文件检查精确的 13 列 Historical Trips header；
3. 对照 manifest 的月份、文件清单和字节数；
4. 使用 `hdfs dfs -put -f` 写入 RAW，并用 `hdfs dfs -stat %b` 与
   `hdfs dfs -checksum` 核对每个文件的大小和完整性；
5. 使用 `hdfs dfs -cp -f` 从 RAW 复制到 ODS，再次核对每个文件的大小和 checksum；
6. 输出 RAW/ODS 的 `hdfs dfs -count` 结果和机器可读 JSON。

如果单节点 HDFS 的 `/raw`、`/warehouse` 由 `bigdata` 服务用户拥有，应使用具备
相应 HDFS 权限的账号运行。当前验证机的 simple-auth 示例是：

```bash
HADOOP_USER_NAME=bigdata python3 scripts/citibike/land_historical_trips.py \
  --staging-dir "$STAGING/extracted" \
  --source-month 2025-01 \
  --manifest docs/source/citibike-202501-manifest.json
```

`HADOOP_USER_NAME` 只适用于本机 simple-auth 配置，不由脚本自动设置，也不应作为
生产集群的认证方案。

仅需要 RAW（例如另一个作业负责 ODS 物化）时，可以显式使用 `--raw-only`；Track D
完整交接应使用默认的 RAW + ODS 流程。

### 2. 建立 Hive ODS 表并注册分区

```bash
HIVE_LOCAL=/tmp/citibike-day1/hive-local
mkdir -p "$HIVE_LOCAL"

hive \
  --hiveconf hive.exec.local.scratchdir="$HIVE_LOCAL" \
  -f hive/ods/ods_trip_raw.sql
```

`MSCK REPAIR TABLE` 会发现所有形如 `year=YYYY/month=MM` 的分区；因此一个月内的
多个 CSV 由同一分区自动读取。DDL 位于
[`hive/ods/ods_trip_raw.sql`](../../hive/ods/ods_trip_raw.sql)。

在当前验证机上如需以 HDFS 服务用户读取：

```bash
HADOOP_USER_NAME=bigdata hive \
  --hiveconf hive.exec.local.scratchdir="$HIVE_LOCAL" \
  -f hive/ods/ods_trip_raw.sql
```

### 3. 运行最小验证 SQL

```bash
hive \
  --hiveconf hive.exec.local.scratchdir="$HIVE_LOCAL" \
  --hiveconf source_year=2025 \
  --hiveconf source_month=1 \
  -f hive/queries/verify_ods_trip_raw.sql
```

验证查询位于 [`hive/queries/verify_ods_trip_raw.sql`](../../hive/queries/verify_ods_trip_raw.sql)，
依次提供：

- `DESCRIBE` 类型契约证据；
- 按 source month 的 `COUNT(*)`；
- `ride_id`、两个 station id 和 `started_at` 的样例；
- CSV header 误读计数；
- duplicate `ride_id` group 计数。

## 2025-01 验证记录

Track A manifest 记录的 source 侧事实为：3 个 CSV、总计 `2,124,475` 行，三个文件
header 完全一致，CSV 字节总数为 `414,212,882`。其中：

- `start_station_id` 缺失 `564` 行，`end_station_id` 缺失 `4,322` 行；
- `started_at` / `ended_at` parse failure 为 `0`；
- 非正 duration 为 `0`；
- 坐标越界/非法值为 `0`；
- unknown `rideable_type` 和 `member_casual` 为 `0`；
- duplicate `ride_id` 为 `0`。

### 实际 HDFS 结果（2026-09-13）

落地脚本返回 `PASS`，3 个 CSV 的本地、RAW、ODS 字节数逐一一致：

```text
source_month: 2025-01
source_record_count: 2124475
raw_path: /raw/citibike/trips/year=2025/month=01
ods_path: /warehouse/ods/ods_trip_raw/year=2025/month=01
file_count: 3
raw_hdfs_count: 1  3  414212882 /raw/citibike/trips/year=2025/month=01
ods_hdfs_count: 1  3  414212882 /warehouse/ods/ods_trip_raw/year=2025/month=01
```

RAW 文件清单：

```text
202501-citibike-tripdata_1.csv  195009639 bytes
202501-citibike-tripdata_2.csv  194993655 bytes
202501-citibike-tripdata_3.csv   24209588 bytes
```

`hdfs fsck /raw/citibike/trips/year=2025/month=01 -files -blocks -locations` 返回
`Status: HEALTHY`：3 files、5 blocks、Missing blocks `0`、Corrupt blocks `0`，单节点
验证实例的 replication factor 为 `1`。

### 实际 Hive 结果（2026-09-13）

正式 DDL 的 `DESCRIBE FORMATTED` 确认：

```text
start_station_id  string
end_station_id    string
started_at        string
ended_at          string
start_lat         double
start_lng         double
end_lat           double
end_lng           double
year              int
month             int
Table Type        EXTERNAL_TABLE
Location          hdfs://localhost:9000/warehouse/ods/ods_trip_raw
skip.header.line.count  1
external.table.purge   false
```

`hive/queries/verify_ods_trip_raw.sql` 的实际结果：

```text
year  month  ride_count
2025  1      2124475

partition_ride_count
2124475

csv_header_rows
0

duplicate_ride_id_groups
0
```

样例查询返回的 station id 保留了 decimal-looking identifier（例如 `6182.02`、
`5746.02`），没有 numeric cast；`started_at` 仍保留 source wall-clock 字符串。

用于复核的命令如下：

```text
HADOOP_USER_NAME=bigdata hdfs dfs -ls -h /raw/citibike/trips/year=2025/month=01
HADOOP_USER_NAME=bigdata hdfs dfs -count /raw/citibike/trips/year=2025/month=01
HADOOP_USER_NAME=bigdata hive \
  --hiveconf hive.exec.local.scratchdir=/tmp/citibike-day1/202501/hive-local \
  --hiveconf source_year=2025 --hiveconf source_month=1 \
  -f hive/queries/verify_ods_trip_raw.sql
```

对账规则是：Hive `partition_ride_count` 必须为 `2,124,475`，且 `csv_header_rows`
与 `duplicate_ride_id_groups` 均为 `0`；HDFS RAW 三个文件的字节数之和必须为
`414,212,882`。落地脚本不做去重或坏行删除，因此 Hive 计数应与 Track A 的物理
source count 基本一致；发现差异时先停在 ingestion 层排查，不在 ODS 静默修复。

## 明确不做

本 Track 只建设 Historical Trip RAW/ODS landing zone，不实现 `dwd_trip`、小时聚合、
风险/调度指标、Kafka、Sqoop、Spring Boot 或前端。`started_at` / `ended_at` 的
America/New_York 语义解析和业务质量清洗留给 DWD 阶段。
