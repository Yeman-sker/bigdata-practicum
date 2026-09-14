# ADR-0001：冻结共享单车平台 Product & Data Contract v1

- 状态：Accepted / Frozen
- 日期：2026-09-11
- 关联：[#3 确定项目主题](https://github.com/Yeman-sker/bigdata-practicum/issues/3)、[#4 Product & Data Contract v1](https://github.com/Yeman-sker/bigdata-practicum/issues/4)
- 适用范围：7 天敏捷交付周期的 v1 产品、数据、分析和服务接口

## 背景

项目由数据采集、HDFS/Hive、Spark、Kafka、Spring Boot 和前端应用等模块组成。若各模块自行解释数据源、标识符、时间或聚合粒度，Spark、Hive、后端和前端之间会产生接口不一致。

Issue #4 已冻结产品范围和数据契约。本 ADR 将这些约定固化为仓库内可长期引用的决策记录；实现分支不得“顺手”改变契约。

## 决策

### 1. 产品目标与范围

平台服务共享单车运营/调度人员，v1 必须支持以下运营闭环：

1. 查看哪些站点当前车辆不足或空桩不足。
2. 查看站点在不同日期/小时通常是净流入还是净流出。
3. 结合当前库存和历史同时段规律，估计未来一小时的缺车/满桩风险。
4. 给出从富余站点向短缺站点调多少辆车的建议。

P0 用户故事为当前站点库存态势、站点历史供需规律、一小时供需风险和调度建议；运营总览为 P1。以下能力不进入 v1 P0：

- 登录/注册和用户中心；
- AI/LLM 分析、天气融合和复杂机器学习预测；
- 复杂车辆路径优化（VRP/OR-Tools）；
- 逐辆自行车追踪和实时逐笔骑行事件分析；
- 2013—2020 等 legacy Citi Bike 历史 schema 的全兼容。

原则是先完成可解释、可验证、可演示的运营闭环，再扩展高级能力。

### 2. 官方数据源

v1 只消费两个官方数据域：

| 数据域 | 来源与范围 | 约定 |
| :--- | :--- | :--- |
| Historical Trips | [Citi Bike Trip Histories](https://citibikenyc.com/system-data)，正式分析范围 2025-01—2025-12 | 月度 ZIP 内可能有多个 CSV；downloader 必须解压并处理全部 CSV。开发先用 1 个月跑通，再扩全年。 |
| Live Station State | [Citi Bike GBFS 2.3](https://gbfs.citibikenyc.com/gbfs/2.3/gbfs.json) | 消费 station_information、station_status、vehicle_types；MVP 不消费 free_bike_status。 |

现代 Historical Trips 的核心原始字段为：

```text
ride_id, rideable_type, started_at, ended_at,
start_station_name, start_station_id, end_station_name, end_station_id,
start_lat, start_lng, end_lat, end_lng, member_casual
```

GBFS station_status 是站点级库存快照。它不是实时骑行订单或官方逐笔骑行事件流；下游设计、报告和答辩均不得将其描述为逐笔骑行事件流。

### 3. 标识符与时间语义

#### 3.1 station_id

所有历史、实时、Hive、Spark、Kafka、MySQL 和 API 层统一使用：

```text
station_id: STRING
```

不得转换为 INT、FLOAT、DOUBLE 或 DECIMAL。现代站点 ID 可能形如 5484.09、4199.12、7354.01；其语义是标识符，不是数值。历史 Trip 与实时 GBFS 的核心 join key 固定为 station_id。

#### 3.2 时间

- Historical Trips 的 started_at/ended_at 是没有 offset 的纽约本地 wall-clock time，内部字段命名为 started_at_local/ended_at_local，语义为 America/New_York。
- GBFS 的 last_updated/last_reported 是 POSIX timestamp。内部统一保存 snapshot_at_utc、snapshot_at_local、last_reported_at_utc 和 ingested_at_utc，其中 snapshot_at_local 由 UTC 转换到 America/New_York 得到。
- 不得用两条数据链的原始 timestamp 直接 join。统一派生 service_date、day_of_week、hour、is_weekend；历史模式与当前状态以 station_id + America/New_York day_of_week + hour 匹配。
- v1 聚焦站点小时级规律，不处理复杂 DST ambiguous timestamp 修复。

### 4. 内部实时事件契约

GBFS provider schema 与内部 Kafka schema 隔离，只有 ingestion adapter 负责吸收未来 provider 版本变化：

```text
GBFS provider JSON
        ↓
ingestion adapter
        ↓
station_status_event_v1
        ↓
Kafka
```

- Topic：bike.station.status.v1
- Key：station_id
- 事件语义：station inventory snapshot events

station_status_event_v1 字段如下：

| 字段 | 类型 | 可空 | 说明 |
| :--- | :--- | :---: | :--- |
| station_id | STRING | 否 | 站点标识符 |
| snapshot_at_utc | TIMESTAMP | 否 | 快照 UTC 时间 |
| snapshot_at_local | TIMESTAMP | 否 | America/New_York 本地时间 |
| num_bikes_available | INT | 否 | 可用车辆数 |
| num_bikes_disabled | INT | 是 | 禁用车辆数 |
| num_docks_available | INT | 是 | 可用空桩数 |
| num_docks_disabled | INT | 是 | 禁用空桩数 |
| is_installed | BOOLEAN | 否 | 是否已安装 |
| is_renting | BOOLEAN | 否 | 是否可借车 |
| is_returning | BOOLEAN | 否 | 是否可还车 |
| last_reported_at_utc | TIMESTAMP | 是 | 站点最后上报时间 |
| ingested_at_utc | TIMESTAMP | 否 | 平台接入时间 |
| source_version | STRING | 否 | v1 固定为 2.3 |

### 5. 历史 DWD 契约

dwd_trip_v1 每行代表一次有效或可追踪的历史骑行记录：

```text
ride_id                  STRING      NOT NULL
rideable_type            STRING      NOT NULL

started_at_local         TIMESTAMP   NOT NULL
ended_at_local           TIMESTAMP   NOT NULL
duration_seconds         BIGINT      NOT NULL

start_station_id         STRING      NULL
start_station_name       STRING      NULL
start_lat                DOUBLE      NULL
start_lng                DOUBLE      NULL

end_station_id           STRING      NULL
end_station_name         STRING      NULL
end_lat                  DOUBLE      NULL
end_lng                  DOUBLE      NULL

member_casual            STRING      NOT NULL

service_date             DATE        NOT NULL
start_hour               TINYINT     NOT NULL   // 0~23
day_of_week              TINYINT     NOT NULL   // 1~7
is_weekend               BOOLEAN     NOT NULL

source_year              INT         NOT NULL
source_month             INT         NOT NULL
ingest_batch_id          STRING      NOT NULL
is_valid_station_trip    BOOLEAN     NOT NULL
```

用于站点级 flow 分析时，is_valid_station_trip 至少要求：起点和终点 station_id 非空，且 started_at_local、ended_at_local 可解析。无效记录不静默删除，保留并通过该字段标识，以便质量统计和追溯。

### 6. 站点维度

dim_station_v1 不能只由当前 station_information 构成，因为历史站点可能已下线：

```text
Historical Trip 中出现的 station
            UNION
Current GBFS station_information
            ↓
        dim_station
```

字段契约：

```text
station_id              STRING      NOT NULL
station_name            STRING      NULL
lat                     DOUBLE      NULL
lon                     DOUBLE      NULL
capacity                INT         NULL
region_id               STRING      NULL
is_current              BOOLEAN     NOT NULL
metadata_source         STRING      NOT NULL
metadata_updated_at     TIMESTAMP   NOT NULL
```

元数据优先级为 当前 GBFS metadata > Historical Trip metadata；capacity 不得设为 NOT NULL。

### 7. 核心分析模型

#### 7.1 dws_station_hourly_flow_v1

粒度为 station_id × service_date × hour，字段为：

```text
station_id, service_date, hour,
inbound_rides, outbound_rides, net_flow, total_activity,
electric_outbound, classic_outbound, member_outbound, casual_outbound
```

定义：net_flow = inbound_rides - outbound_rides。小于 0 表示净流出，大于 0 表示净流入。

#### 7.2 dws_station_hour_profile_v1

粒度为 station_id × day_of_week × hour，字段为：

```text
station_id, day_of_week, hour,
avg_inbound, avg_outbound, avg_net_flow, median_net_flow, sample_days
```

该表是 v1 historical baseline，用于回答某站点在正常“星期几 + 几点”通常发生的供需变化。

### 8. 风险估计与调度建议

v1 使用可解释的历史基线，不做复杂 ML：

```text
expected_net_flow_1h = historical avg_net_flow
projected_bikes_1h = current_bikes + expected_net_flow_1h
```

展示用的 projected_bikes_1h 可以使用 max(0, projected_bikes_1h)。当前库存比例为：

```text
serviceable_capacity = num_bikes_available + num_docks_available
fill_ratio = num_bikes_available / serviceable_capacity
```

v1 暂忽略 disabled bikes/docks 对 capacity 的复杂修正。初始风险区间为：

| 条件 | risk_type |
| :--- | :--- |
| fill_ratio <= 0.15 | SHORTAGE_RISK |
| 0.15 < fill_ratio <= 0.30 | LOW_INVENTORY |
| 0.30 < fill_ratio < 0.70 | HEALTHY |
| 0.70 <= fill_ratio < 0.85 | HIGH_INVENTORY |
| fill_ratio >= 0.85 | OVERFLOW_RISK |

历史净流量可用于增强风险判断，但实现必须保持可解释。若 is_installed = false、is_renting = false 或 is_returning = false，应优先标记为服务异常/离线，不得误报为普通缺车或满桩风险。

调度建议采用 Haversine 距离和简单 greedy matching：根据 projected inventory 计算 surplus/deficit，匹配短缺站点与富余站点，按风险、缺口和距离排序。v1 的最小输出为：

```text
from_station_id → to_station_id → move_bikes
```

不引入 OR-Tools/VRP，不追求全局最优路径。

### 9. ADS 与服务接口

Spring Boot 不直接扫描原始 Trip 或 DWD 大表，只读取聚合后的薄表。

ads_station_current_risk：

```text
station_id, station_name, lat, lon,
current_bikes, current_docks, fill_ratio,
expected_inbound_1h, expected_outbound_1h, expected_net_flow_1h,
projected_bikes_1h, risk_type, risk_level, calculated_at
```

ads_rebalance_suggestion：

```text
suggestion_id, from_station_id, to_station_id, move_bikes,
from_surplus, to_deficit, distance_meters, priority, generated_at
```

ads_operation_overview 至少支持当前可运营站点数、可用车辆总数、可用空桩总数、shortage risk 站点数、overflow risk 站点数和今日/所选日期历史骑行量（数据已入库时）。

### 10. 存储分层与服务链路

HDFS/Hive 目录语义固定为：

```text
/raw
  /citibike/trips/year=2025/month=XX/
  /citibike/gbfs/station_information/dt=YYYY-MM-DD/
  /citibike/gbfs/station_status/dt=YYYY-MM-DD/hour=HH/

/warehouse
  /ods/   ods_trip_raw, ods_station_status_raw
  /dwd/   dwd_trip, dwd_station_status_snapshot
  /dim/   dim_station, dim_vehicle_type
  /dws/   dws_station_hourly_flow, dws_station_hour_profile
  /ads/   ads_station_current_risk, ads_rebalance_suggestion,
          ads_operation_overview
```

```text
离线：Citi Bike ZIP/CSV → HDFS → Hive → Spark → DWS/ADS → Sqoop → MySQL → Spring Boot → Vite + React
实时：GBFS → Collector → Normalization Adapter → Kafka → Risk Calculation → ADS/MySQL → Spring Boot → Station Map
```

分层语义为：RAW 原样落地，ODS source faithful 可查询，DWD 类型统一/清洗/派生，DIM 稳定维度，DWS 分析聚合，ADS 直接面向 API/产品。

### 11. 数据质量、真实数据与验证门禁

质量指标至少包括原始总数、ride_id 空值/重复数、起终点站点 ID 空值比例、时间解析失败数、非正骑行时长、经纬度空值/异常值、未知 rideable_type 和未知 member_casual 数量。清洗不得静默吞数据，异常记录必须可统计、可追踪。

真实 Citi Bike 原始数据不得提交 GitHub。仓库只保存采集器、manifest、schema、契约、小型 synthetic/sanitized fixture 和 ETL/Spark/Hive/API 代码；真实大规模数据进入本地工作目录和 HDFS。

Day 1 实现门禁：

1. 下载并解压至少一个真实月度 ZIP，记录所有 CSV 的 header。
2. 真实请求并保存 station_information、station_status、vehicle_types。
3. 保存至少 3 次不同时间的 station_status 快照，并确认状态可变化。
4. 验证字段、类型、nullable、字符串 station_id 和时间语义可落地。
5. Spark 成功读取至少一个真实 Trip CSV，并输出 schema、count 和样例行。
6. 团队统一四个粒度：ride、station inventory snapshot、station × hour flow/profile、station operational risk/rebalance result。

## 后果

### 收益

- 所有层使用同一 station_id、时区、粒度和事件语义，降低跨模块接口错配。
- provider schema 变化被限制在 ingestion adapter 内，下游依赖稳定的内部事件。
- DWS/ADS 薄表使服务层边界清晰，风险和调度规则可解释、可验证。
- 保留异常标记和质量指标，且不把真实大规模原始数据带入 Git。

### 成本与已知边界

- v1 不支持 legacy schema，正式历史范围先锁定 2025 年。
- 历史均值只是 baseline，不等同于复杂预测模型。
- greedy 调度不是全局最优路径；小时级目标也不覆盖复杂 DST 修复。
- fill ratio 暂不对 disabled bikes/docks 做复杂 capacity 修正。

## 被否决的方案

- 把 GBFS station_status 当作逐笔骑行事件流：与数据源事实不符。
- 将 station_id 转成数值类型：会损失 identifier 语义并破坏 join。
- 使用原始 timestamp 直接跨历史/实时 join：两条数据链的时间语义不同。
- 让 Spring Boot 扫描原始 Trip/DWD 大表：服务层不应承担离线分析。
- 在 MVP 引入 ML 或 VRP：增加不可解释性和实现成本，超出 7 天交付周期的 P0。

## 变更策略

本文是 Product & Data Contract v1 的仓库决策记录。修改以下任一内容时，必须显式更新 Issue #4 或新建 ADR/Contract Change Issue：

- 数据源；
- 时间语义；
- station_id 类型；
- Kafka event schema；
- Hive/DWS 粒度；
- risk 定义；
- User Story 的 P0 范围；
- ADS 对外字段语义。

禁止只在某个成员的实现分支中修改上述契约。任何变更都必须让 Spark、Hive、Backend 和 Frontend 可见并重新验证。
