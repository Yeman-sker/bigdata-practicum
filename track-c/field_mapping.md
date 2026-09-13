# Track C 字段映射

本表将 #6/#7 的外部字段映射到 #4 冻结的内部契约。适配器可以增加内部字段，但不能把 identifier 当作数值，也不能把异常记录静默丢弃。

## Historical Trip → `dwd_trip_v1`

| Source CSV | Internal field | Type | Rule |
|---|---|---|---|
| `ride_id` | `ride_id` | STRING | 保留原值，检查空值/重复 |
| `rideable_type` | `rideable_type` | STRING | 记录未知枚举 |
| `started_at` | `started_at_local` | TIMESTAMP | 按 `America/New_York` 本地时间解析 |
| `ended_at` | `ended_at_local` | TIMESTAMP | 按 `America/New_York` 本地时间解析 |
| `start_station_id` | `start_station_id` | STRING | 强制字符串，允许 null |
| `start_station_name` | `start_station_name` | STRING | 允许 null |
| `start_lat` / `start_lng` | `start_lat` / `start_lng` | DOUBLE | 可解析性检查，异常只告警 |
| `end_station_id` | `end_station_id` | STRING | 强制字符串，允许 null |
| `end_station_name` | `end_station_name` | STRING | 允许 null |
| `end_lat` / `end_lng` | `end_lat` / `end_lng` | DOUBLE | 可解析性检查，异常只告警 |
| `member_casual` | `member_casual` | STRING | 记录未知枚举 |

`duration_seconds`、`service_date`、`start_hour`、`day_of_week`、`is_weekend` 等派生字段属于 DWD ETL，不由本 Source Validator 生成。

## GBFS → `station_status_event_v1`

| GBFS Feed/字段 | Internal field | Type | Rule |
|---|---|---|---|
| `station_status.station_id` | `station_id` | STRING | 必须是 JSON string，作为 Kafka key |
| Collector snapshot time | `snapshot_at_utc` | TIMESTAMP | 采集快照时间统一保存 UTC |
| `snapshot_at_utc` 转换 | `snapshot_at_local` | TIMESTAMP | UTC → `America/New_York` |
| `station_status.last_reported` | `last_reported_at_utc` | TIMESTAMP | 必填；POSIX 秒 → UTC |
| Collector time | `ingested_at_utc` | TIMESTAMP | 采集程序生成，不信任 provider |
| `station_status.num_bikes_available` | `num_bikes_available` | INT | 不得为负 |
| `station_status.num_bikes_disabled` | `num_bikes_disabled` | INT NULL | 可选且不得为负 |
| `station_status.num_docks_available` | `num_docks_available` | INT NULL | 可选且不得为负 |
| `station_status.num_docks_disabled` | `num_docks_disabled` | INT NULL | 可选且不得为负 |
| `station_status.is_installed` | `is_installed` | BOOLEAN | provider 的 0/1 映射为 boolean |
| `station_status.is_renting` | `is_renting` | BOOLEAN | provider 的 0/1 映射为 boolean |
| `station_status.is_returning` | `is_returning` | BOOLEAN | provider 的 0/1 映射为 boolean |
| Discovery `version` | `source_version` | STRING | 当前必须为 `2.3` |

`station_information` 提供站点名称、经纬度、可选容量和区域；它进入 `dim_station_v1`，不直接作为实时事件。`vehicle_types` 只冻结 provider 的车辆类型 ID，供适配器记录。

完整 normalized 示例见 [fixtures/station_status_event_v1.json](fixtures/station_status_event_v1.json)。
