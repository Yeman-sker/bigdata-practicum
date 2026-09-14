# 字段映射与站点身份

版本：Day 2 v1.1；Historical/数仓由 Hu-tong123 维护，GBFS 由 OGATA-LINA 维护。Source validator 继续保留原始事实；业务可用性由下游规则判定。

## 站点身份

所有 ID 按字符串、区分大小写处理，最长 128 字符；禁止数值转换。历史起终点 ID 去除首尾空白后，空字符串转 null，其余原样作为 canonical station_id。

GBFS provider.station_id 通常是 UUID，不能直接与历史 ID join。适配器用同次 station_information 的唯一非空 short_name 作为 canonical station_id；保留 provider_station_id 供追溯。没有 short_name、重复 short_name 或 status 无 metadata 时，使用 `gbfs:<provider_station_id>` 隔离标识，不猜测历史对应关系。重复 provider ID 是结构冲突，拒绝该 metadata 批次。

short_name 是本项目的映射依据，不是 GBFS 对跨历史文件身份一致性的保证。metadata 文件记录 SHORT_NAME/ISOLATED 及 reason；历史命中率实际计算并在 #27/#28 的真实交接报告中给出，不能声称天然 100% 匹配。已存在的真实样例 `0c923abb-298a-4a47-b132-9fae73cc59e6 → 6115.06` 可核对 [station_information](../../fixtures/gbfs/station_information.sample.json)。无映射站仍显示有效当前库存，但没有历史基线时不预测或调度。

canonical metadata 文件是数组，按 station_id 升序：

| 字段 | 类型 / null | 来源与约束 |
| --- | --- | --- |
| provider_station_id | string / 否 | 原始 GBFS ID，唯一 |
| station_id | string / 否 | canonical 或 gbfs: 隔离 ID，唯一 |
| mapping_status | string / 否 | SHORT_NAME / ISOLATED |
| mapping_reason | string / 是 | null / MISSING_SHORT_NAME / DUPLICATE_SHORT_NAME / MISSING_METADATA |
| station_name | string / 是 | name，空值为 null |
| lat、lon | double / 是 | 有效纬度 [-90,90]、经度 [-180,180]；异常转 null 并计数，raw 保留 |
| capacity | integer / 是 | 正数才是可用容量；缺失/非法为 null，原值与原因保留 raw/质量报告 |
| region_id | string / 是 | provider 字符串或 null |
| is_current | boolean / 否 | 本文件均 true；历史合并后可为 false |
| metadata_source | string / 否 | GBFS |
| metadata_updated_at | UTC timestamp / 否 | station_information.last_updated；缺 metadata 的占位用本批 snapshot 时间 |

status 中没有 metadata 的站点，在发布前补一行 ISOLATED 占位，名称/坐标/容量均 null；文件仍先于事件完整落盘。历史 DIM 的 union 与字段优先级见 [warehouse](warehouse.md)。同一快照必须绑定同一个 metadata_version，不能边处理边换映射。

## Historical → DWD

| CSV | DWD | 类型 / null | 转换 |
| --- | --- | --- | --- |
| ride_id | ride_id | string / 否 | 非空原值，重复保留并标记 |
| rideable_type | rideable_type | string / 否 | 未知枚举保留并计数 |
| started_at、ended_at | started_at_local、ended_at_local | timestamp / 否 | America/New_York wall-clock，支持现有解析格式，关键时间失败整批拒绝 |
| start_station_id、end_station_id | 同名 | string / 是 | 使用上述 canonical 规则 |
| start_station_name、end_station_name | 同名 | string / 是 | 空值为 null |
| start_lat、start_lng、end_lat、end_lng | 同名 | double / 是 | 缺失/不可解析/越界为 null，原字节在 RAW 保留并报告 |
| member_casual | member_casual | string / 否 | 未知枚举保留，不计入 member/casual 子项 |

派生列、来源定位和有效性见 warehouse，不由 Source validator 生成。13 列 source 字段清单与日期解析格式复用 [contracts.py](../../citibike/contracts.py)，不另维护第二个解析器。

## GBFS → station_status_event_v1

JSON 时间使用带时区的 RFC 3339 字符串；UTC 用 Z，local 含实际纽约 offset。所有列必须存在，nullable 列显式写 null。

| Provider / 来源 | 字段 | 类型 / null | 约束 |
| --- | --- | --- | --- |
| status.station_id + metadata | station_id | string / 否 | 转 canonical，Kafka key 同值；provider ID 在 metadata 文件保留 |
| station_status.last_updated | snapshot_at_utc | timestamp / 否 | POSIX 秒 → UTC，绝不是 collector 时间 |
| snapshot_at_utc 转纽约 | snapshot_at_local | timestamp / 否 | 对应同一瞬间、正确 offset |
| station_status.last_reported | last_reported_at_utc | timestamp / 是 | source 2.3 验证要求整数；内部列允许 null，不能写成现在 |
| collector 响应接收时间 | ingested_at_utc | timestamp / 否 | UTC，重放不修改原值 |
| num_bikes_available | num_bikes_available | integer / 否 | 负数保留并告警，规则标为 INVALID_DATA |
| num_docks_available | num_docks_available | integer / 是 | 同上；null 导致库存数据不足 |
| num_bikes_disabled、num_docks_disabled | 同名 | integer / 是 | 可空，负数只记录质量异常，不参与可用库存计算 |
| is_installed、is_renting、is_returning | 同名 | boolean / 否 | 0/1 转 boolean，其他值结构失败 |
| feed.version | source_version | string / 否 | 固定 2.3 |

capacity、坐标、region_id 留在 metadata，事件不重复携带。Kafka headers 与结束记录见 [events](events.md)。旧 [GBFS fixture](../../fixtures/gbfs/station_status_event_v1.sample.json) 为 Day 1 provider-ID PoC；新交接消费 [day2 fixture](../../fixtures/day2/README.md)，不能混用身份语义。
