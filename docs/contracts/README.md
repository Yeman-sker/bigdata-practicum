# Track C：Source Schema Validator 与 Data Contract Fixture

本目录实现 GitHub Issue #8。目标是把 #4 的数据契约变成可复现的轻量校验边界，供 Track D（HDFS/Hive）和 Track E（Spark）复用。

字段级映射见 [field_mapping.md](field_mapping.md)。校验规则的可执行实现见
[validate_sources.py](../../scripts/contracts/validate_sources.py)。

## 已冻结的输入契约

### Historical Trip

现代 Citi Bike 月度 CSV 必须包含 13 列：

```text
ride_id, rideable_type, started_at, ended_at,
start_station_name, start_station_id, end_station_name, end_station_id,
start_lat, start_lng, end_lat, end_lng, member_casual
```

`station_id` 按字符串处理，即使值看起来像 `5484.09`。时间按 `America/New_York` 本地 wall-clock 解释；原始数据不在本层强行改写时区。

### GBFS 2.3

Discovery 必须声明 `version=2.3`，并提供 `station_information`、`station_status`、`vehicle_types` 三个入口。站点编号必须是 JSON 字符串；数量字段不得为负；`last_reported` 按 POSIX 秒验证。`capacity` 等可选字段允许缺失或 null。

## Fail Fast 与 Warn/Observe

以下情况失败：必需列/结构缺失、必填字段为 null、关键时间无法解析、station_id 不是字符串、POSIX 时间不是 JSON 整数、GBFS 版本或必需 Feed/URL 不匹配。

以下情况只记录质量指标并返回 PASS：Historical 中允许为空的 station ID/名称、GBFS 可选字段缺失、未知枚举值、异常坐标或负库存值。库存负值当前明确采用 Warn/Observe 策略，必须交给上游处理，校验器不会为了“全绿”静默删除或修正原始数据；契约要求的必填字段错误仍会阻止通过。

Historical 校验还输出 `nonpositive_duration_count`：时长由已成功解析的开始/结束时间计算，`duration_seconds <= 0` 只作为可追踪质量指标，不在 Source 层生成 DWD 字段。

## 运行

```bash
python3 scripts/contracts/validate_sources.py historical fixtures/contracts/historical_trip_sample.csv
python3 scripts/contracts/validate_sources.py discovery fixtures/contracts/gbfs.json
python3 scripts/contracts/validate_sources.py station_information fixtures/contracts/station_information.json
python3 scripts/contracts/validate_sources.py station_status fixtures/contracts/station_status.json
python3 scripts/contracts/validate_sources.py vehicle_types fixtures/contracts/vehicle_types.json
python3 -m unittest tests/test_source_contracts.py
```

退出码 `0` 表示 PASS，退出码 `1` 表示存在契约错误；标准输出为带有 `status`、`errors`、`warnings` 和质量指标的 JSON，可直接保存为 CI 或 Day 1 验收证据。

正常输出示例（节选）：

```json
{
  "kind": "station_status",
  "status": "PASS",
  "errors": [],
  "warnings": [],
  "metrics": {
    "records": 2507,
    "station_id_type": "STRING",
    "distinct_station_ids": 2507,
    "invalid_station_id_records": 0
  }
}
```

错误输出示例：

```json
{
  "kind": "station_information",
  "status": "FAIL",
  "errors": ["station_id must be STRING and non-empty: 1 invalid records"]
}
```

`historical_trip_sample.csv`、GBFS Fixture 和 `station_status_event_v1.json` 均为 synthetic 小样本，仅用于复现 schema 和测试边界，不代表完整生产数据；其字段结构来自 #4 冻结契约和 #7 实测 GBFS 结构。

## 真实源观察记录

2026-09-11 访问官方入口 `https://gbfs.citibikenyc.com/gbfs/2.3/gbfs.json` 成功。Discovery 声明 GBFS 2.3，但 Feed URL 当前指向 `https://gbfs.lyft.com/gbfs/2.3/bkn/...`。这是 provider 路由变化，不改变内部 `station_status_event_v1` 契约；Track B 应把实际 URL 和采集时间写入 snapshot manifest。

原始月度 ZIP、完整 GBFS 快照和个人运行数据不进入 GitHub；仓库只保留下面的小型、脱敏结构 Fixture。
