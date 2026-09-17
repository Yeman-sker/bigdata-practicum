# 契约入口：Day 2 v1.1

本文是开发交接索引。产品见 [product](../product.md)，进程见 [architecture](../architecture.md)，分工见 [delivery](../plans/delivery.md)，运行见 [runbook](../runbook.md)。本版本按 [ADR-0004](../adr/0004-parallel-development-baseline.md) 承接原 ADR；发布/评审证据由 [#31](https://github.com/Yeman-sker/bigdata-practicum/issues/31) 与文档 PR 记录。版本随整套文档提交，不把单份文件存在当作业务实现已完成。

## 交接矩阵

| 契约 / 粒度 | 主维护人（生产者） | 消费者 | 唯一字段/行为位置 | 可独立读取的样例 |
| --- | --- | --- | --- | --- |
| Historical source/manifest；文件与物理行 | Hu-tong123 / #28 | offline | [映射](field_mapping.md)、[数仓](warehouse.md)、现有 source manifest | day2/trips、manifest.json；旧 source validator |
| RAW/ODS/DWD；物理 Trip | Hu-tong123 / #28 | offline 聚合 | warehouse、[Hive DDL](../../hive/warehouse_v1.sql) | expected.json.tables.dwd_trip_v1 |
| canonical metadata；一站一行 | OGATA-LINA / #27 | #28、#29 | field_mapping、events | metadata.json、station_information.json |
| Kafka station/end；站点/快照 | OGATA-LINA / #27 | #29 | [events](events.md)、OpenAPI 的 KafkaRecord schema | events.ndjson、cases.json.batches |
| DIM/flow/profile/OD；站点、站点日小时、站点星期小时、OD 小时 | Hu-tong123 / #28 | #26 API、#29 | warehouse、Hive/MySQL DDL | expected.json.tables、seed.sql |
| 风险/调度；站点快照、建议 | 1giaowoligiaogiao / #29 | #26 API、#30 前端 | [operations](operations.md)、[MySQL DDL](../../sql/serving.sql) | cases.json、expected.json 两张 ADS |
| HTTP；三类 GET 响应 | S1lco / #26 | Yeman-sker / #30 | [OpenAPI](openapi.yaml) | http-examples.json |
| UI / 页面；五条 P0 路径 | Yeman-sker / #30 | 用户；S1lco 核对 API 集成 | [product](../product.md)、[frontend design](../frontend-design.md) | 相同 HTTP examples，不另建字段模型 |
| 运行证据；每个切片一次记录 | 各主负责人，组长汇总 | 下游、组长 | runbook | 命令、预期、真实证据边界 |

所有 day2 样例均位于 [fixtures/day2](../../fixtures/day2/README.md)。同一输出仅有一位写入人，相关生产者和消费者评审；负责人实际签收在 Issue/PR 记录，不以自动检查冒充人工评审。

## 公共语义

- station_id 是 canonical STRING，provider UUID 映射见 field_mapping，禁止数字化或直接把两种 ID join。
- 历史时刻为 America/New_York wall-clock；实时 UTC 与纽约 offset 明确区分。ISO 星期周一 1，小时 0..23；日期由已发布数据生成。
- 所有 nullable 字段显式 null，不能用 0、空串或默认容量冒充；数组为空表示对应业务空结果。无相应成功发布是 503。
- 版本/快照/数据集 ID 的内容、更新和新鲜度分别以 events、warehouse 为准。一次 HTTP 响应不能混合不同发布批次。
- 状态、风险阈值及数量计算只在 operations 定义；OpenAPI 是其传输编码，产品文档定义用户可见含义。

## 现有 Source Validator：继续复用

13 列 Historical 字段与解析格式在 [contracts.py](../../citibike/contracts.py)，校验实现为 [contract_validator.py](../../citibike/contract_validator.py)。字段/结构缺失、必填 null、坏关键时间、非字符串 ID、非整数 POSIX、错误版本或 feed URL 为 FAIL；可空站点、可选字段缺失、未知枚举、异常坐标和负库存为 Warn/Observe，原值不静默删除。负库存通过源观察门禁不代表业务有效。

source 中 last_reported 为整数必填，内部 event 列可空；不得混淆这两个边界。Source 时长指标不产生 DWD 字段。既有 [GBFS handoff](../gbfs/README.md) 和 [历史 handoff](../source/citibike-202501.md) 保留实测历史。

```bash
python3 -m citibike.contract_validator historical fixtures/day2/trips/part-a.csv
python3 -m citibike.contract_validator historical fixtures/day2/trips/part-b.csv
python3 -m citibike.contract_validator station_information fixtures/day2/station_information.json
python3 -m citibike.contract_validator station_status fixtures/day2/station_status.json
python3 -m citibike.gbfs_fixture fixtures/gbfs/station_status_event_v1.sample.json
uv run --with-requirements requirements-contracts.txt python -m unittest discover -s tests -p 'test_*.py' -v
```

最后一条包含新契约检查；第一组 source 命令为已有可运行入口。旧 event fixture 仅验证 Day 1 payload 结构，新的 canonical/Kafka 交接另由 day2 fixtures 验证。

## 开工与变更门禁

文档、DDL、HTTP/Kafka schema、同源样例、异常算例、分工和运行标准齐全，契约检查与仓库 CI 通过后，由组长确认文档 PR 并记录基线提交。#31 保留实际评审/签收证据。启动不要求产品提前完成；最终产品必须执行 runbook 的真实集成验收。

改变字段、nullable、ID、时间、粒度、阈值、排序或失败行为时，先在 #31/后继变更 Issue 记录影响，再同步契约、样例及任务，交受影响两端复核。内部函数、组件和样式无需另立架构文档。
