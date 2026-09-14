# Day 2 v1.1 共享样例

全部为小型 synthetic 数据。站点名/UUID/骑行 ID 为教学构造，不能当作真实业务证据。旧 fixtures/citibike、contracts、gbfs 保留供 Day 1 测试，新并行交接使用本目录。

| 文件 | 使用者 / 含义 |
| --- | --- |
| [trips/part-a.csv](trips/part-a.csv)、[part-b.csv](trips/part-b.csv) | #28：共 8 条物理行，包含重复、缺失站点、零时长与跨月到达 |
| [manifest.json](manifest.json) | #28：两个 CSV、bytes、rows、synthetic ZIP hash；example.invalid 为来源标记，不下载 |
| [station_information.json](station_information.json)、[station_status.json](station_status.json) | #27：provider ID 与 canonical 不同；可直接用现有 source validator |
| [metadata.json](metadata.json) | #27 输出/#28/#29 输入：已映射、按 station_id 排序；文件字节 hash 是 metadata_version |
| [events.ndjson](events.ndjson) | #27/#29：逐行 key/headers/value，两个 station + 一个 snapshot_end；不是一条 events 数组消息 |
| [expected.json](expected.json) | #28/#29：完整小型 DWD、DIM、DWS、ADS 与发布行，tables 对象按实际表名索引 |
| [seed.sql](seed.sql) | #27：在空样例 MySQL 库中，先执行 sql/serving.sql，再装载；包含实际薄表与发布行 |
| [http-examples.json](http-examples.json) | #26/#27：OpenAPI Example Objects，取 `<名称>.value` 即完整响应；x-path/x-query/x-status 指明请求与 HTTP 状态 |
| [cases.json](cases.json) | #29：22 个风险、9 个匹配、12 个批次与 4 个身份映射输入/期望；不得用这里的期望结果替代生产规则 |

## 可手算链路

1. 1 月 8 日/15 日两个星期三各有两次甲→乙，均在 08 时出发/到达。
2. 第五条有效骑行于 1 月 31 日 23:55 乙→丙，2 月 1 日 00:05 到达；计出发日 OD、到达日 inbound。
3. 另外三条为零时长、缺目标站和第一条的重复，均留在 DWD，排除站点聚合。
4. 两个星期三甲/乙各补齐 24 小时，加 1 月 31 日乙和 2 月 1 日丙，共 144 行 flow；四个站点/星期组合共 96 行 profile；3 行非零 OD。流入/流出/OD 总骑行数均为 5。
5. 星期三 08 时基线：甲净流出 2，乙净流入 2，sample_days=2。2 月 5 日 08:00 录制快照甲 6/34、乙 34/6，C=S=40，预测甲 4、乙 36，建议乙→甲 8 辆、距离 84 米。

snapshot_id、metadata_version、dataset_id 按正式契约派生；HTTP/ADS/事件使用相同输入。边界 examples 是独立场景替换：no_baseline 保留当前状态，stale 推进时钟并清除预测/建议，empty_live 是另一份空快照。

cases.risk 的 flags 表示三个服务标志同时取该值，age_seconds 是 as_of 距最早观测的秒数；其他未列出的时间/metadata 均有效。cases.rebalance 默认新鲜、可运营、基线可用，docks 显式给出，predicted 值以 projected 字段输入。cases.batches 的 published_* 表示上个已发布状态，advance_wall_seconds 用于不完整批超时；backend_profile 未指定时按 recorded 规则测 FIXTURE。mixed-origin / live-rejects-replay 必须拒绝；partial 的 expected_as_of_utc 验证坏批不推进已发布时钟。

## 验证

```bash
uv run --with-requirements requirements-contracts.txt python -m unittest discover -s tests -p 'test_day2_contracts.py' -v
```

该检查验证 schema、fixture 引用/行数/聚合一致性、源映射和 DDL 字段匹配。#28/#29 必须另运行自己的实现与这些预期比较；#26/#27 必须让真实组件/接口消费同一数据。这里不提供假装业务实现的第二套算法。

多 CSV 的 ZIP 可在外部临时目录用 Python zipfile 的 ZIP_STORED 打包，成员按 part-a/part-b 顺序、时间固定 2025-01-01 00:00:00；manifest 的 hash 对应该可复现档案。不要为运行样例把输出、编译产物或真实下载存回本目录。
