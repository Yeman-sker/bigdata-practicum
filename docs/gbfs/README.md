# GBFS 2.3 实时站点数据 PoC

本文保留 Day 1 实测与源适配行为。Day 2 v1.1 的 canonical ID 映射、Kafka 封装与批次发布见 [字段映射](../contracts/field_mapping.md) 和 [事件契约](../contracts/events.md)；本文 UUID event 样例只作旧 PoC 验证，不能直接当作新的跨历史关联输入。

本文档说明 Issue #7 交付的 Citi Bike GBFS 采集器。GBFS 提供的是站点
库存快照，不是逐笔骑行事件流。

## 已验证的数据源

- Discovery：`https://gbfs.citibikenyc.com/gbfs/2.3/gbfs.json`
- 实际观测到的 provider 版本：`2.3`
- Discovery 当前为英文环境发布以下 feed：
  - `station_information`
  - `station_status`
  - `vehicle_types`
- 采集器会先解析 discovery，再使用其中的 URL，不依赖 provider 当前的
  重定向域名或路径。
- 当前观测到的 `vehicle_types` 记录包含 `vehicle_type_id`、`form_factor`
  和 `propulsion_type`，但没有 `name` 字段。fixture 保留 provider 的真实
  结构，下游不得自行猜测车辆名称。

仓库中的 `fixtures/gbfs/` 只保存从真实 feed 提取的小样本。完整 raw 快照
必须保存在 Git 仓库之外。采集器会同时保存 raw 响应和 provider 元数据，
便于审计标准化结果。

## 执行三次快照采集

```bash
python3 -m citibike.gbfs \
  --output-dir data/gbfs/citibike/$(date -u +%F) \
  --snapshots 3 \
  --interval-seconds 60 \
  --sample-size 5 \
  --sample-station-id 0c923abb-298a-4a47-b132-9fae73cc59e6
```

`--snapshots` 至少必须为 3。`--sample-station-id` 可以重复指定，使同一
站点优先出现在每次 sample 中，便于比较状态变化。

输出目录包含：

- `discovery.json`
- `feed_manifest.json`
- 三个带时间戳的 snapshot 目录
- 每个 snapshot 中的 `station_status_event_v1.sample.json`
- `collection_log.json`

`data/gbfs/` 已加入 `.gitignore`，完整 raw 数据不会被提交。

每条 collection log 会记录 provider 的 `last_updated`、本地
`ingested_at_utc`、站点数量、sample station 状态和 provider 质量 warning。

本次真实采集跟踪站点
`0c923abb-298a-4a47-b132-9fae73cc59e6`。三次 provider
`last_updated` 分别为 `1789118479`、`1789118539`、`1789118600`；该站点
从 `1 bike / 37 docks` 变为第三次的 `2 bikes / 36 docks`。这证明了 feed
刷新和站点库存变化，不是复制同一个响应。

第二次快照中，provider 的 `num_bikes_available` 为 1，但
`vehicle_types_available[].count` 合计为 2。该不一致被保留在
`quality_warnings` 中；标准化事件使用 `num_bikes_available` 作为站点库存
总数权威值，不静默覆盖 provider raw 数据。

在本次 `station_information` 响应中，2507 个站点有 13 个的 `region_id`
缺失或为 `null`。本次采集中的 `capacity` 都有值，但 validator 和下游
契约仍允许该字段为 `null`。

## canonical metadata 与 Kafka 批次

Issue #27 的正式入口为 `citibike.gbfs_stream`。它先从 discovery 动态取得
三个 feed，使用同一次 `station_information` 的唯一 `short_name` 映射
canonical `station_id`；缺失或冲突的映射使用 `gbfs:<provider_station_id>`
隔离，并在 metadata 中记录原因。status 中没有 metadata 的站点也会补入
`MISSING_METADATA` 占位行，不能猜测历史身份。

```bash
DATA_DIR=/tmp/citibike-issue-27
python3 -m citibike.gbfs_stream \
  --output-dir "$DATA_DIR/gbfs" \
  --bootstrap-servers "${KAFKA_BOOTSTRAP_SERVERS:-localhost:9092}" \
  --interval-seconds 60
```

采集器先以临时文件原子写入 `metadata/<metadata_version>.json` 和
`snapshots/<time>-<snapshot_prefix>/`，再顺序发送 station 记录；所有 station
得到 broker 确认后才发送 key 为 `__snapshot_end__` 的结束记录。批次失败时保留
raw 和失败日志，不发送虚假的完整结束记录。`collection_log.json` 记录源时间、
raw hash、映射/质量告警、批次状态和 snapshot/metadata hash。

录制库存使用同一封装，且不允许把实时来源冒充录制来源：

```bash
python3 -m citibike.gbfs_stream \
  --replay "$DATA_DIR/events.ndjson" \
  --metadata-file "$DATA_DIR/gbfs/metadata/<metadata_version>.json" \
  --bootstrap-servers "${KAFKA_BOOTSTRAP_SERVERS:-localhost:9092}"
```

replay 会保留 snapshot、metadata、源时间和事件 value，仅将来源 header
规范为 `GBFS_REPLAY`；输入必须包含完整的 station + `snapshot_end` 批次。
完整 raw 与运行证据放在 `DATA_DIR`，不提交 Git。

## 校验 normalized fixture

```bash
python3 -m citibike.gbfs_fixture \
  fixtures/gbfs/station_status_event_v1.sample.json
```

## Provider 字段到内部契约的映射

| GBFS provider 字段 | 内部字段 | 规则 |
| --- | --- | --- |
| `data.stations[].station_id` | `station_id` | 保留为字符串，包括看起来像数字的 ID |
| `station_status.last_updated` | `snapshot_at_utc` | POSIX 秒转换为 UTC RFC 3339 |
| `snapshot_at_utc` | `snapshot_at_local` | 使用 `America/New_York` 时区转换 |
| `num_bikes_available` | `num_bikes_available` | 必填整数 |
| `num_bikes_disabled` | `num_bikes_disabled` | 可为空整数 |
| `num_docks_available` | `num_docks_available` | 可为空整数 |
| `num_docks_disabled` | `num_docks_disabled` | 可为空整数 |
| `is_installed`、`is_renting`、`is_returning` | 同名字段 | 接受 GBFS `0/1`，输出布尔值 |
| `last_reported` | `last_reported_at_utc` | 可为空的 POSIX 秒转换为 UTC |
| 本地请求时间 | `ingested_at_utc` | 采集器时钟，统一使用 UTC |
| 响应 `version` | `source_version` | 必填，Issue #4 固定为 `2.3` |

`capacity` 和 `region_id` 属于站点信息元数据，不属于
`station_status_event_v1`；两者在站点维度中都允许为空。
