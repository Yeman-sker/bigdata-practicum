# 实时事件与快照发布

版本：Day 2 v1.1；主维护 OGATA-LINA（#27），消费者 1giaowoligiaogiao（#29）。沿用 topic `bike.station.status.v1`，本次传输封装增加明确的批次边界，由 [ADR-0004](../adr/0004-parallel-development-baseline.md) 记录变更。

## Producer 到 Consumer

一个集成实例、一个 producer、一个分区、一个顺序 consumer。普通记录仍是“一站一快照”；快照结束记录是显式控制记录，不当作站点。副本数在单机为 1，不声称高可用。

Kafka headers（UTF-8 string）每条必须包含：

| Header | 值 |
| --- | --- |
| contract_version | `1.1` |
| record_type | `station` 或 `snapshot_end` |
| snapshot_id | 64 位小写 SHA-256 hex |
| metadata_version | 64 位小写 SHA-256 hex |
| data_origin | `GBFS_LIVE`、`GBFS_REPLAY` 或 `FIXTURE` |

station 记录的 key 是 canonical station_id，value 为 UTF-8 JSON 的 station_status_event_v1 对象（字段见 [映射表](field_mapping.md)）。不发送 events 数组或 HTTP 响应。

snapshot_end 的 key 固定 `__snapshot_end__`，value 必含：

| 字段 | 类型/可空 | 含义 |
| --- | --- | --- |
| station_count | integer / 否 | 该批应有的 distinct canonical station_id 数，允许 0 |
| snapshot_at_utc | UTC timestamp / 否 | station_status.last_updated |
| ingested_at_utc | UTC timestamp / 否 | 本次采集时间 |

header 中的 snapshot_id 与 metadata_version 对整批相同。metadata_version 为 canonical metadata JSON 文件原始字节的 SHA-256；snapshot_id 为 `SHA256(raw station_status bytes + LF + metadata_version ASCII)`。快照依赖文件先原子写完，才能开始发送。

producer 顺序发送全部 station 并确认成功，再发送 snapshot_end。相同 provider last_updated 与相同原始内容无需重复发布；同源时间内容不同记冲突，不覆盖上个成功结果，等待更新时刻。metadata 变化在下一次更新的状态快照生效。不要跨批并发发送。

设置可靠确认与重试，Java Kafka producer 使用 `acks=all`、`enable.idempotence=true`；其他客户端使用其等价能力。Kafka 幂等不能替代应用在重启后的去重。[官方 producer 说明](https://kafka.apache.org/39/javadoc/org/apache/kafka/clients/producer/KafkaProducer.html)。

## Consumer 完整性与失败

1. 按 snapshot_id 缓冲站点，最多 5,000 站；相同 station_id 与相同 value 重复只计一次，不同 value 为冲突，整批拒绝。
2. 结束记录到达后核对版本、源时间、metadata 文件、distinct count；每个 station 都必须对应 metadata。未知版本、结构/必填类型错误、缺 metadata、数量不符均整批失败。负库存等可观察异常保留，交规则分类，不静默删除。
3. 首条到达后 120 秒仍未结束，或下一批已开始而上一批未结束：上一批失败；记录 snapshot_id、原因、收到/预期条数，清掉该批缓冲。不能拿上一批缺失站点补齐新快照。
4. 完整批以 MySQL 单事务发布站点风险、建议和实时发布状态，成功后才提交 Kafka offset。失败先回滚再重试；重启重放相同已发布 snapshot_id 不重复计算或累加建议。
5. 若源时间不晚于已发布快照，按相同 snapshot_id 去重或按旧/冲突批拒绝；拒绝后记录原因并提交相应已处理 offset，避免永久阻塞。部分批 offset 不能先于完整性判定被自动提交。
6. 完整的 0 站快照合法发布空风险/建议；如果某个 metadata 站点没有本批状态，不沿用为当前站点。前端仅显示本批站点，详情可另查历史站点。

最少指标：raw 数、映射成功/隔离数、重复/冲突数、完整/失败批数、最后成功源时间、消费滞后、各异常状态数、建议数。v1 不建 DLQ 平台；失败证据进入本地日志与保留的 raw，必要时人工重放。

## 新鲜度与时间

| 项目 | 固定约定 |
| --- | --- |
| 采集/前端刷新 | 60 秒 |
| 有效窗口 | 180 秒；`as_of_utc >= expires_at_utc` 即过期 |
| 站点观测依据 | min(snapshot_at_utc, last_reported_at_utc)，后者 null 时只用 snapshot_at_utc |
| expires_at_utc | 上述依据 +180 秒 |
| 快照有效期 | snapshot_at_utc +180 秒，即使没有站点也可判断 |
| 时钟偏差 | 源/上报时间超前 as_of 超过 60 秒为 INVALID_TIME，不能通过刷新 ingested_at 延长有效性 |
| 服务停用 | 仅新鲜数据的 flags 可判 SERVICE_UNAVAILABLE；旧 flags 显示过期 |

API 在每次请求时判定到期，返回过期状态、null 预测值和过滤后的建议；原库存可以保留作“上次观测”。前端根据 served_at_utc 后经过的时间推进 as_of_utc，不依赖浏览器绝对时钟与服务器完全一致。录制模式见下节。

## 两种回放

- `mode=replay` 是产品的历史 OD 回放：选日期/小时读聚合表，库存与风险均 NOT_APPLICABLE。
- `data_origin=GBFS_REPLAY` 是录制库存事件重放：产品仍为 mode=live，界面必须显示“录制快照”。使用与 Kafka 完全相同的 NDJSON 封装（key、headers、value），按原顺序发送，原 snapshot/last_reported 不改写。
- 业务时钟 `clock_mode=recorded` 只在开发/演示启动配置启用：处理 snapshot_end 时使用记录的 ingested_at_utc，读 API 时保持该时钟直到下一条结束记录。前端不以墙钟推进其有效期。测试可显式推进时钟验证过期。线上 `clock_mode=wall` 使用真实 UTC，不能通过 HTTP 请求切换。
- `served_at_utc` 始终为实际 HTTP 响应时间，`as_of_utc` 为上述业务时钟；两者与 data_origin 一起解释结果。FIXTURE 也使用 recorded 时钟。
- file replay 证明封装/规则；Kafka replay 另证明 broker 消费；均不证明刚采到了实时数据。

## 验证入口

[fixture](../../fixtures/day2/README.md) 提供完整、重复、部分、乱序、空批和错误控制记录的输入/期望。旧 [GBFS PoC](../gbfs/README.md) 仍用于源观察；其未做 canonical 转换的 UUID 事件不能直接作为新的 Kafka 交接样例。

原始源规范参考 [GBFS 2.3](https://github.com/MobilityData/gbfs/blob/v2.3/gbfs.md)。本项目的 60/180/120 秒及单分区是本周期的实现约束，并非 GBFS 的保证。
