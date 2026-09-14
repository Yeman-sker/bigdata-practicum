# ADR-0002：Day 2 地图首页与运营建议契约 v1

- 状态：Accepted
- 日期：2026-09-14
- 关联：[Issue #4：Product & Data Contract v1](https://github.com/Yeman-sker/bigdata-practicum/issues/4)
- 上位决策：[ADR-0001](0001-product-data-contract-v1.md)
- 适用范围：从 Day 1 起共 7 天的敏捷开发周期中的 Day 2 产品与接口设计

## 背景

ADR-0001 已冻结数据源、标识符、时间、数仓和服务层基础契约。本 ADR 只补充本轮已经确认的地图首页、历史回放、风险展示和调度建议规则，不改变 ADR-0001 的数据语义。

真实数据决定了两个边界：GBFS 提供的是站点级库存快照，不提供可用的逐辆自行车 GPS；历史骑行数据提供起终点站和时间，可聚合为 OD 流。因此，实时地图使用站点锚定的可视化粒子，历史地图使用站点到站点的聚合流动，二者都不得宣称为逐车 GPS 轨迹。

## 决策

### 1. 开发与前端基线

- 项目总开发周期为 7 天，从 Day 1 计算；采用敏捷切片，每个切片都应形成可验证结果。
- 前端使用 Vite + React + TypeScript。
- 组件库保持可替换，当前不为组件库增加额外契约。
- ECharts 不是前端或 API 契约的强依赖；后续可以保留、替换或移除，不影响数据接口。
- 服务链路的前端终点统一表述为 Vite + React 应用；ECharts 仅是可替换的图表实现。

### 2. 地图首页

首页是完整的地图大屏，地图为主视图，侧栏只承载必要的风险和调度信息。

#### 实时模式

- 以站点为锚点绘制黄色粒子群，而不是用一个黄色点代表整个聚集地。
- 粒子数量表达当前可用车辆数量；站点周围的轻微分散只是视觉表达，不是真实车辆位置。
- 当前库存和服务状态来自 GBFS station_status；站点坐标来自 canonical station metadata。

#### 历史回放模式

- 使用真实历史 OD 聚合结果驱动站点间流动。
- 粒子从起点站向终点站插值移动，路径是可视化插值，不是 GPS 轨迹。
- 时间轴先限定为单日，并按数据库中实际可用的日期、小时和筛选结果返回；不得在前端硬编码日期范围。
- 倍速固定提供 0.5x、1x、2x、5x。
- API 返回聚合流，粒子数量和动画细节由前端生成，API 不返回逐粒子数据。

### 3. OD 与地图 API 最小契约

历史 OD 只存储非零 OD 对，粒度为：

    service_date × hour × from_station_id × to_station_id

对应模型为 dws_station_od_hourly_v1，最小字段为：

    service_date
    hour
    from_station_id
    to_station_id
    ride_count

地图接口使用统一响应结构：

    mode
    service_date
    hour
    observed_at_utc
    stations
    flows

| 字段 | 说明 |
| :--- | :--- |
| mode | live 或 replay |
| service_date | 查询日期；由数据库可用数据决定 |
| hour | 回放小时；实时模式可为空 |
| observed_at_utc | 实时快照时间；回放模式可为空 |
| stations | 站点数组，使用下方最小字段 |
| flows | 非零 OD 流数组，使用下方最小字段 |

stations 最小字段：

| 字段 | 说明 |
| :--- | :--- |
| station_id | canonical station ID，STRING |
| station_name | 站点名称 |
| lat / lon | 站点坐标 |
| num_bikes_available | 当前可用车辆数；历史回放可为 null |
| num_docks_available | 当前可用空桩数；历史回放可为 null |

flows 最小字段：

| 字段 | 说明 |
| :--- | :--- |
| from_station_id | 起点站 |
| to_station_id | 终点站 |
| ride_count | 所选日期和小时内的骑行次数 |

接口不返回逐车 GPS、粒子坐标、动画路径或前端专用粒子字段。实时和回放都使用 canonical station_id；日期和小时由数据库查询结果决定。

### 4. 风险最小展示契约

风险卡片至少展示：

    station_id
    station_name
    risk_type
    current_bikes
    current_docks
    fill_ratio
    expected_net_flow_1h
    projected_bikes_1h
    calculated_at

解释文案由前端根据上述结构化字段生成，使用固定模板，不在 ADS 中持久化自由文本原因。SERVICE_UNAVAILABLE 和 INSUFFICIENT_DATA 必须可区分展示；数据不足时不生成调度建议。

### 5. 调度/调车建议边界

风险识别和调度建议是两个独立能力：

    风险摘要
        ↓
    调度建议生成器
        ↓
    from_station → to_station → move_bikes

#### 触发与目标

- 只有 SHORTAGE_RISK 和 OVERFLOW_RISK 进入调度生成器。
- LOW_INVENTORY、HIGH_INVENTORY 只展示，不触发调度。
- 短缺站补到健康区间下界 30%；过剩站移到健康区间上界 70%。
- 调度目标使用站点 capacity；容量或必要库存数据缺失时不生成建议。

计算口径：

    target_low  = 0.30 × capacity
    target_high = 0.70 × capacity

    deficit = max(0, target_low - projected_bikes_1h)
    surplus = max(0, projected_bikes_1h - target_high)

    move_bikes = min(
      source_surplus,
      target_deficit,
      source_current_bikes,
      target_current_docks
    )

#### 匹配与生命周期

- 使用 Haversine 距离和简单 greedy matching。
- 先处理缺口最大的短缺站，再选择距离最近的过剩站。
- 一个过剩站可以服务多个短缺站。
- 每次实时快照重新计算；旧建议自动失效。
- 不保存“已确认、执行中、已完成”等调度状态。

ads_rebalance_suggestion 保持以下字段：

    suggestion_id
    from_station_id
    to_station_id
    move_bikes
    from_surplus
    to_deficit
    distance_meters
    priority
    generated_at

#### 首页展示

- 地图默认只突出 Top 5 调度建议。
- 用静态方向线表示调度方向，线宽按 move_bikes 分级。
- 点击方向线查看建议详情，完整结果放在侧边列表。
- 暂不加入路径动画、车辆容量、司机任务、自动执行或 VRP/全局最优求解。

## 验收边界

Day 2 设计完成至少应能验证：

1. Vite + React 前端可以承载全屏地图首页。
2. 实时模式展示站点锚定的黄色粒子群，并明确不是逐车 GPS。
3. 回放模式按单日、小时粒度读取真实非零 OD，并支持四档倍速。
4. API 只返回约定的 stations 和 flows 最小字段。
5. 风险卡片可由结构化字段生成固定解释。
6. 调度建议只由两类风险触发，按 30%/70% 边界、缺口优先和距离优先生成，并在快照刷新后失效。

## 不做的事情

- 不引入逐辆自行车追踪或 GPS 模拟数据。
- 不为前端动画设计独立的后端粒子服务。
- 不提前锁定组件库或图表库。
- 不把风险解释扩展为 AI/LLM 文案系统。
- 不把调度建议扩展为完整车队调度系统。

## 变更规则

本 ADR 是 ADR-0001 的 Day 2 产品与接口补充。若修改模式、最小字段、风险触发条件、健康区间、调度算法或 P0 范围，必须同步更新 Issue #4 或新增 Contract Change Issue，并重新检查前后端契约。
