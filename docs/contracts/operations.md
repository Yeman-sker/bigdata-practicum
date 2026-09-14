# 风险与调度规则

版本：Day 2 v1.1。主维护人 1giaowoligiaogiao（#29），GBFS/API 与数仓负责人交叉评审。术语见 [CONTEXT](../../CONTEXT.md)，传输字段见 [OpenAPI](openapi.yaml)，例子见 [共享样例](../../fixtures/day2/README.md)。按 [ADR-0004](../adr/0004-parallel-development-baseline.md) 取代旧 risk_type 及数量公式。

## 输入与时钟

每批使用完整快照、对应 metadata_version、一份已发布历史 dataset_id 与 as_of_utc 时钟。站点先完成 ID 映射。新鲜度、完整性及重放时钟见 [events](events.md)。

令 b=num_bikes_available、d=num_docks_available、S=b+d。S 为可服务容量，metadata.capacity 为名义容量 C，二者不必相等。disabled 数量不进入 S，不为对齐 C 改写 b/d。

## 当前状态与预测状态

按下表从上向下判定；预测先复用当前异常，再检查基线。reason 为固定编码，正常分类时为 null。

| 优先次序与条件 | current_status / current_reason | forecast_status / forecast_reason |
| --- | --- | --- |
| 必需时间错误，或源/上报时间比 as_of 超前超过 60 秒 | INVALID_DATA / INVALID_TIME | 同左 |
| 源或非空站点上报时间过期 | STALE_DATA / STALE_OBSERVATION | 同左 |
| 新鲜观测的任一服务标志为 false | SERVICE_UNAVAILABLE / SERVICE_FLAGS | 同左 |
| b 或非空 d 为负数 | INVALID_DATA / NEGATIVE_INVENTORY | 同左 |
| b/d 或服务标志缺失（结构错误本应在接入层拒绝） | INSUFFICIENT_DATA / MISSING_INVENTORY | 同左 |
| S=0 | INSUFFICIENT_DATA / ZERO_SERVICEABLE_CAPACITY | 同左 |
| 当前有效，但该星期/小时 profile 不存在或 sample_days<1 | 按比例分类 / null | INSUFFICIENT_DATA / NO_BASELINE |
| 以上均不成立 | 用 b/S 分类 / null | 用 p/S 分类 / null |

非正常当前状态的 fill_ratio 为 null，预测期望与 p 均为 null。无基线只令预测字段为 null；C 缺失/非正、坐标缺失只阻止调度，不阻止用 S 分类。

| 比例 r（当前 b/S，预测 p/S） | 状态 |
| --- | --- |
| r<=0.15 | SHORTAGE_RISK |
| 0.15<r<=0.30 | LOW_INVENTORY |
| 0.30<r<0.70 | HEALTHY |
| 0.70<=r<0.85 | HIGH_INVENTORY |
| r>=0.85 | OVERFLOW_RISK |

不输出含义混合的 risk_type/risk_level，改为 current_status 与 forecast_status。历史 OD 响应使用 NOT_APPLICABLE，不倒推库存。

## 一小时估计

snapshot_at_utc 转纽约时间，取该时刻 ISO 星期（周一 1）与 hour，读对应 profile：

```text
expected_inbound_1h  = avg_inbound
expected_outbound_1h = avg_outbound
expected_net_flow_1h = avg_net_flow
p = projected_bikes_1h = b + expected_net_flow_1h
forecast_for_utc = snapshot_at_utc + 3600 秒
```

用当前小时的完整历史均值近似接下来 60 分钟的净流量，不做分钟插值或跨小时加权，页面称“一小时估计”。分类和调度均使用未截断的 p，允许负数或超过 S。图形可限制在 [0,S]，详情保留原始估计和时间。sample_days 是活跃样本日期数，不是预测置信度。

均值与 p 使用有限 double，过程不按展示小数位取整；严格按上述区间分类。fixture 数值容差 1e-9，页面保留 1 位小数不影响判定。

## 调度：预测决定需求，当前状态约束可行性

候选必须同时满足：新鲜可运营、预测可用、C 为正整数、坐标有效、b/d 有效。只有预测 SHORTAGE_RISK 接车、预测 OVERFLOW_RISK 供车；LOW/HIGH 只展示。候选集合固定，不将接车站再变成供车站。

```text
target_low  = 0.30 * C
target_high = 0.70 * C
need   = max(0, ceil(target_low - p))
supply = max(0, floor(p - target_high))

source_safe = max(0, b - ceil(0.30 * S))
target_safe = max(0, floor(0.70 * S) - b)
move = min(supply_remaining, need_remaining,
           source_safe_remaining, target_safe_remaining,
           source_bikes_remaining, target_docks_remaining)
```

预测目标用 C，当前保留量用 S。来源至少保留 ceil(30% S) 辆，目标接车后最多 floor(70% S) 辆，避免造成或加重当前偏低/偏高。30%/70% 称“调度目标边界”，恰好在 LOW/HIGH 的状态不改称 HEALTHY。

缺口向上取整，可能超过小数目标不足 1 辆；富余向下取整，不搬不足一辆的富余。无可行正整数时无建议，未解决的风险继续展示。

1. 目标按初始 need 降序，再按 station_id 字符串升序。
2. 对每个目标，来源按 Haversine 距离升序，再按 station_id 升序。地球半径 6,371,000 米，用未取整距离排序，输出四舍五入到整数米。
3. move>0 才输出，同时扣减两站 need/supply、安全量、实际车辆/空桩余额。一个来源可服务多个目标，同一对最多一条。
4. priority 按生成顺序从 1 开始；suggestion_id=`snapshot_id:priority`。from_surplus/to_deficit 为配对前整数剩余 supply/need。

逐目标扫描来源的 greedy 最坏为候选数平方；本周期最多 5,000 站，只有实测超过 60 秒刷新周期才优化。没有路网、载重、司机或全局求解。

## 算例与生命周期

| 情景 | 预期 |
| --- | --- |
| C=S=40，当前 2、预测 20 | 当前缺车、预测健康；不因当前告警补车 |
| C=S=40，当前 20、预测 2 | 当前健康、预测缺车，need=10；寻找来源 |
| 来源 b=34、p=36，目标 b=6、p=4，C=S=40 | supply=8、need=8、安全量各 22；搬 8 辆 |
| 来源 b=2、p=36，C=S=40 | source_safe=0，不从当前缺车站继续搬车 |
| 来源 b=35、p=36.6，目标 b=6、p=4.4，C=S=41 | supply=7、need=8，搬 7 辆；保留整数余额 |

新批次成功发布整体替换风险与建议；失败批次保留上个成功结果及原时间，不延长有效期。建议 expires_at_utc 取两端较早值；请求时到期的不返回，前端也按该时间失效。baseline 更新在下一完整快照生效。旧建议不保留执行状态。

共享样例覆盖阈值、反向当前/预测、停服、无基线、过期、负库存、零 S、缺 C、无匹配、多目标余额。实现 PR 必须实际运行规则；样例自检不能替代算法测试。
