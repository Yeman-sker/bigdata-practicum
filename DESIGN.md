---
version: alpha
name: "Citi Bike Operations Map"
description: "A calm, map-first visual system for real-time bike inventory, forecast risk, dispatch, and historical replay."
colors:
  primary: "#0B1741"
  on-primary: "#FFFFFF"
  surface: "#FFFFFF"
  surface-translucent: "rgba(255,255,255,0.92)"
  surface-muted: "#F2F5F7"
  on-surface-muted: "#667085"
  border: "#D9E1EA"
  map-ground: "#F7F5EF"
  water: "#BFE3F6"
  green-space: "#DDEBD7"
  inventory: "#F5A000"
  shortage: "#FF5A5F"
  shortage-soft: "rgba(255,90,95,0.16)"
  overflow: "#5B5CE2"
  overflow-soft: "rgba(91,92,226,0.16)"
  replay: "#11A8A5"
  neutral-station: "#667085"
typography:
  headline-lg:
    fontFamily: "Inter, PingFang SC, Microsoft YaHei, sans-serif"
    fontSize: 24px
    fontWeight: 700
    lineHeight: 1.25
  headline-md:
    fontFamily: "Inter, PingFang SC, Microsoft YaHei, sans-serif"
    fontSize: 20px
    fontWeight: 700
    lineHeight: 1.3
  body-lg:
    fontFamily: "Inter, PingFang SC, Microsoft YaHei, sans-serif"
    fontSize: 16px
    fontWeight: 400
    lineHeight: 1.5
  body-md:
    fontFamily: "Inter, PingFang SC, Microsoft YaHei, sans-serif"
    fontSize: 14px
    fontWeight: 400
    lineHeight: 1.5
  label-lg:
    fontFamily: "Inter, PingFang SC, Microsoft YaHei, sans-serif"
    fontSize: 16px
    fontWeight: 600
    lineHeight: 1.25
  label-md:
    fontFamily: "Inter, PingFang SC, Microsoft YaHei, sans-serif"
    fontSize: 14px
    fontWeight: 600
    lineHeight: 1.3
  label-sm:
    fontFamily: "Inter, PingFang SC, Microsoft YaHei, sans-serif"
    fontSize: 12px
    fontWeight: 500
    lineHeight: 1.4
  data-lg:
    fontFamily: "Inter, PingFang SC, Microsoft YaHei, sans-serif"
    fontSize: 40px
    fontWeight: 700
    lineHeight: 1
    fontFeature: "'tnum'"
  data-md:
    fontFamily: "Inter, PingFang SC, Microsoft YaHei, sans-serif"
    fontSize: 20px
    fontWeight: 700
    lineHeight: 1.2
    fontFeature: "'tnum'"
spacing:
  micro: 4px
  xs: 8px
  sm: 12px
  md: 16px
  lg: 24px
  xl: 32px
  2xl: 48px
  safe-area: 24px
  control-gap: 12px
rounded:
  sm: 8px
  md: 16px
  lg: 24px
  full: 9999px
components:
  icon-button:
    backgroundColor: "{colors.surface-translucent}"
    textColor: "{colors.primary}"
    rounded: "{rounded.full}"
    size: 44px
  icon-button-hover:
    backgroundColor: "{colors.surface-muted}"
    textColor: "{colors.primary}"
    rounded: "{rounded.full}"
    size: 44px
  status-capsule:
    backgroundColor: "{colors.surface-translucent}"
    textColor: "{colors.primary}"
    typography: "{typography.label-md}"
    rounded: "{rounded.full}"
    padding: "{spacing.sm}"
    height: 44px
  surface-panel:
    backgroundColor: "{colors.surface-translucent}"
    textColor: "{colors.primary}"
    typography: "{typography.body-md}"
    rounded: "{rounded.md}"
    padding: "{spacing.md}"
  search-result-selected:
    backgroundColor: "{colors.surface-muted}"
    textColor: "{colors.primary}"
    typography: "{typography.label-md}"
    rounded: "{rounded.sm}"
    padding: "{spacing.sm}"
  retry-button:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    typography: "{typography.label-md}"
    rounded: "{rounded.full}"
    padding: "{spacing.sm}"
    height: 44px
  station-lens:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.primary}"
    typography: "{typography.body-md}"
    rounded: "{rounded.full}"
    padding: "{spacing.lg}"
    width: 400px
    height: 400px
  metadata:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.on-surface-muted}"
    typography: "{typography.label-sm}"
    rounded: "{rounded.sm}"
    padding: "{spacing.xs}"
  divider:
    backgroundColor: "{colors.border}"
    size: 1px
  map-ground:
    backgroundColor: "{colors.map-ground}"
  map-water:
    backgroundColor: "{colors.water}"
  map-green-space:
    backgroundColor: "{colors.green-space}"
  inventory-particle:
    backgroundColor: "{colors.inventory}"
    rounded: "{rounded.full}"
    size: 8px
  dispatch-route:
    backgroundColor: "{colors.inventory}"
    rounded: "{rounded.full}"
    size: 4px
  shortage-indicator:
    backgroundColor: "{colors.shortage}"
    rounded: "{rounded.full}"
    size: 12px
  shortage-halo:
    backgroundColor: "{colors.shortage-soft}"
    rounded: "{rounded.full}"
  overflow-indicator:
    backgroundColor: "{colors.overflow}"
    rounded: "{rounded.sm}"
    size: 12px
  overflow-halo:
    backgroundColor: "{colors.overflow-soft}"
    rounded: "{rounded.full}"
  replay-route:
    backgroundColor: "{colors.replay}"
    rounded: "{rounded.full}"
    size: 4px
  stale-station:
    backgroundColor: "{colors.neutral-station}"
    rounded: "{rounded.full}"
    size: 12px
---

# 共享单车运营地图设计系统

## Overview

本产品是面向共享单车运营人员的克制型空间工具。地图就是页面：道路、站点、库存、风险、调度和历史流向承担信息表达，控件只在用户需要时出现。界面应当直接、可信，不做成堆叠卡片的仪表盘，也不追求装饰性。

视觉层级依次是地图、业务标记、上下文解释、全局控件。文字应靠近它所解释的站点、路线或时间。静止状态只显示左上角三个圆形控件和右上角一个数据状态胶囊。

本文件是视觉 token 的规范来源。产品行为和完整状态转换见[前端交互规范](docs/frontend-design.md)；API 值与业务分类仍以[契约索引](docs/contracts/README.md)为准。

## Colors

配色首先区分业务含义，其次才服务于装饰：

- **主海军蓝（`#0B1741`）**：核心文字、图标、焦点指示和确定性操作。
- **暖白地面（`#F7F5EF`）**、**水域（`#BFE3F6`）**和**绿地（`#DDEBD7`）**：构成低对比度底图，不与业务数据争夺注意力。
- **库存琥珀色（`#F5A000`）**：仅表示实际可用车辆粒子和调度路线，不能表示预测车辆或历史骑行。
- **缺车珊瑚色（`#FF5A5F`）**：缺车及低库存；**满桩蓝紫色（`#5B5CE2`）**：满桩及高库存。两者必须同时使用形状、线型和文字加强区分。
- **回放青绿色（`#11A8A5`）**：仅用于历史 OD 路线、方向箭头和回放选择。
- **中性站点色（`#667085`）**：用于过期、非法、停服或无法定位且不能给出业务结论的数据。
- **浮层表面**：白色或 92% 不透明白色。低对比灰只用于选择背景、边线、元数据和禁用态，不能创造新的业务分类。

实时琥珀粒子、实时风险光晕和历史青绿路线不能出现在同一模式。进入历史回放时必须移除全部实时库存、预测、风险和调度图层。

## Typography

使用系统字体栈 `Inter, PingFang SC, Microsoft YaHei, sans-serif`，不下载品牌字体。中文标签、站点 ID、时间戳和库存数字必须在桌面地图比例下保持清晰。

- **Headline**：只用于站点透镜或阻断错误卡片，不作为页面标题。
- **Body**：解释状态和次要操作。
- **Label**：命名站点、路线、控件和元数据；中文界面不使用全大写样式。
- **Data**：使用等宽数字强调库存与预测值；数字可以在透镜中放大，但不能脱离地图形成 KPI 卡片。

同一视图最多使用常规、半粗和粗体三种字重。界面最小文字为 `12px`，关键状态和操作文字至少为 `14px`。

## Layout

视口由地图完全铺满，不设常驻顶栏、右侧栏、Tab、列表或 KPI 条。P0 只面向 `1280×720` 及以上桌面视口，移动端重排明确不在当前范围内。

采用 `8px` 节奏，`4px` 只用于视觉微调。所有全局控件位于 `24px` 安全边距内。左上角三个 `44px` 按钮间隔 `12px`；数据状态胶囊对齐右上安全区。回放控制器底部居中，并在播放时收成紧凑胶囊。

同一时刻只允许一个富信息临时浮层展开：搜索、视角拨盘、站点透镜、状态说明或回放控制器。路线选择和紧凑回放胶囊可以作为地图上下文保留。临时浮层应先翻转到锚点另一侧或沿路线移动，不能直接覆盖关键业务标记。

语义堆叠顺序固定为：底图、地名、非选中路线、站点粒子与光晕、选中路线、对象标签、富信息浮层、全局控件、首次加载失败卡片。

## Elevation & Depth

地图保持视觉扁平；景深只用于区分临时控件和地理信息。浮层使用 `rgba(255,255,255,0.92)`、`1px #D9E1EA` 边线、`backdrop-filter: blur(12px)` 和克制的 `0 8px 24px rgba(11,23,65,0.14)` 阴影。

选中路线和站点通过透明度、线宽和层级抬升，不增加更大的阴影。不要使用深色遮罩；非模态浮层周围的地图始终可读。只有首次加载失败可以使用居中卡片，因为此时没有可操作的业务图层。

## Shapes

圆形用于全局图标按钮、站点锚点、粒子和站点透镜。胶囊用于短时状态、时间、数量、距离和路线解释。搜索与错误浮层使用 `16px` 圆角，小型选中行使用 `8px` 圆角。

缺车使用圆形中心和珊瑚色环；满桩使用不同的方形中心和蓝紫色环，避免仅靠颜色区分。较低风险使用单层虚线环，严重风险使用实线双环。过期或非法站点使用中性空心环或斜纹环，不显示库存粒子。

站点透镜最大为 `400×400px` 圆形，并保持 `24px` 视口间距。即使可见路线或标记更小，实际交互命中区也不得小于 `44×44px`。

## Components

### 全屏地图

道路和建筑保持暖白、低对比。普通地图标签只在有助于定位时保留；业务标签始终位于地名之上。缩放可以减小粒子半径和未选中标签，但不能改变表达的库存数量。

### 全局图标按钮

搜索、实时/回放、地图视角使用三个原生 `button`。按钮为 `44×44px` 圆形、半透明白底和海军蓝图标；悬停使用柔和表面色。激活态增加 `2px` 当前业务色环；键盘焦点始终增加可见的 `2px` 海军蓝焦点环和 `2px` 偏移。每个图标都有 `aria-label`，悬停或聚焦时显示工具提示。

### 状态胶囊

右上角胶囊最小高 `44px`，显示来源、观测时间和新鲜度。`FIXTURE` 与 `GBFS_REPLAY` 来源不能隐藏。警告或失败向下展开一行解释和一个重试操作，不能扩张为通知中心。

### 站点标记与路线

库存粒子使用由 `station_id` 派生的稳定偏移；相同数量刷新后不能重新散布。风险使用站点锚点周围的光晕。调度使用静态琥珀色方向线。历史 OD 使用青绿曲线、重复方向箭头和可选运动点。路线宽度可以表达数量，但准确数量必须同时提供文字。

选中调度路线位于业务图层最上方，两行胶囊包含来源到目标、车辆数、距离和原因。未选中的 Top 5 使用中等透明度，其他有效路线使用低透明度。细路线额外提供 `12px` 透明指针命中区。

### 搜索

搜索是位于搜索按钮下方的 `360px` 临时浮层，内边距 `16px`，结果行最小高 `64px`。选中行使用柔和表面 token。结果展示站名、字符串 ID 和一条与当前模式相关的文字状态；无坐标站点仍可选择，并显示“无法定位”。

### 站点透镜

透镜锚定所选站点，当前值与预测值使用大号数据样式。图表用颜色加线型区分流入、流出和净流量。没有坐标时，同一个透镜居中显示、移除锚点指针，并标记“无法定位”。

### 回放控制器

展开的回放控件使用原生日期输入、播放/暂停按钮、24 小时时间轴、当前小时和一个倍速按钮。播放且三秒无交互后，控件收为 `08:00 · 1×`。减少动态效果时移除粒子漂移、路线运动点和光晕呼吸，保留静态方向箭头及全部数量。

实现参考状态见[高保真原型索引](docs/frontend-design.md#高保真原型索引)。

## Do's and Don'ts

- Do：让地图占满视口，只在用户选择后展示细节。
- Do：保持库存琥珀、缺车珊瑚、满桩蓝紫和历史回放青绿的语义互斥。
- Do：用可见文字保留来源、观测时间、过期状态、坐标缺失和未绘制 OD 数量。
- Do：使用原生按钮与输入、可见焦点、状态 `aria-live` 和 `prefers-reduced-motion`。
- Do：临时浮层关闭后，将焦点返回触发控件或原站点。
- Don't：增加常驻顶栏、侧栏、Tab 行、站点列表、仪表盘卡片网格或装饰性图例。
- Don't：把聚合库存粒子或历史 OD 描述成单车 GPS 轨迹。
- Don't：只用颜色表示风险、选择、失败或方向。
- Don't：在历史回放中保留实时库存、风险、预测或调度。
- Don't：在正式需求出现前增加字体、图标框架、组件库、动效引擎或移动端布局。
