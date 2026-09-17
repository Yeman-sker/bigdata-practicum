---
version: prism-1
name: "PRISM / 棱镜空间"
description: "紫银建筑、真实城市轮廓、粒子与右侧操作面板。"
colors:
  background: "#08080d"
  surface: "#17151f"
  surface-translucent: "rgba(20,18,29,0.90)"
  text: "#eeeef4"
  text-muted: "#aaa4b2"
  border: "rgba(233,225,242,0.22)"
  accent: "#e5a9ff"
  focus: "#f4d9ff"
  map-ground: "#15121e"
  water: "#08080f"
  building: "#2d293d"
  inventory: "#f2edf8"
  shortage: "#ffad80"
  overflow: "#f28fca"
  dispatch: "#efa3ff"
  replay: "#c6bcff"
  neutral: "#9893a3"
typography:
  family: "Inter, PingFang SC, Microsoft YaHei, sans-serif"
  label: 12px
  body: 13px
  section: 14px
  title: 16px
  data: 20px
spacing:
  unit: 4px
  gap: 8px
  panel-padding: 16px
  safe-area: 24px
rounded:
  control: 3px
  panel: 4px
layout:
  desktop-min-width: 1280px
  desktop-min-height: 720px
  instrument-width: 320px
  map-tool-target: 44px
---

# 棱镜空间视觉系统

2026-09-17 用户选定棱镜空间原型作为正式前端方向。本文件取代此前暖白地图、圆形透镜、空间拨盘和后续浅色工作台的视觉规范。旧设计图已移除。数据契约仍为 v1.1。

## 城市与控件

真实城市地图铺满视口。紫银建筑以高倾角展示，深色水面保留海岸轮廓。左上角仅保留紧凑品牌、来源和时间；左侧提供地图视角操作；右侧窄面板集中模式、搜索、站点详情、风险和调度。历史控制出现在回放模式中。

标题使用 14–16px。正文和控件为 12–14px，关键数据最多 20px。没有大字号宣传语、首页巨幅标题或 KPI 卡片墙。面板采用细边线、小圆角和深色半透明背景。正文不依赖光晕获得可读性。

右侧面板可纵向滚动，地图保持固定。站点详情与站点列表在同一区域切换。地图保留足够的可操作区域，署名始终可见。1280×720 是桌面最低验收尺寸；更窄窗口不能丢失来源、错误与操作入口。

## 地图和粒子的含义

底图使用 MapLibre 渲染 OpenFreeMap 的真实道路、海岸与建筑，不绘制虚构街区代替加载失败。保留 OpenFreeMap、OpenMapTiles 和 OpenStreetMap 署名。底图与业务接口分别处理加载和失败。

| 图层 | 表达 |
| --- | --- |
| 银白库存粒子 | 一个点对应一辆当前有效库存；数量不随预测视图或装饰密度改变 |
| 风险环与文字 | 由接口的当前或预测状态决定；缺车偏琥珀，满桩偏粉紫，异常为中性虚线 |
| 粉紫调度线 | 同批建议的来源到目标；文字显示数量与直线距离，不代表已执行路线 |
| 淡紫历史 OD | 所选日期、小时的非零聚合流；方向为起点到终点，不是 GPS 轨迹 |
| 建筑轮廓扫描、城市光点 | 基于真实地图几何的装饰效果，不表达库存、风险或骑行数量 |

切换到预测只改变风险表达，库存仍为当前值。进入历史回放移除当前库存、风险、预测和调度。过期、停服、非法和缺失数据不能画成健康状态或零库存。

## 动效与操作

粒子运动、建筑扫描和路线光点使用同一播放开关。装饰密度只调节城市光效，不能改业务数量。减少动态效果时默认静止，保留方向箭头、状态、准确数量和地图操作；用户仍可手动开启。后台标签停止逐帧绘制。

按钮、输入和选择器使用原生元素，可见焦点为浅紫色。地图工具命中区至少 44px，紧凑列表保留足够的行高。状态通过颜色、文字和线型共同表达。关闭详情后恢复触发控件焦点。

## 权威边界

本文件定义视觉规范。[前端设计](docs/frontend-design.md)定义页面结构和状态，[产品与交互](docs/product.md)定义五条 P0 行为。业务字段、风险判定与调度算法只由[契约索引](docs/contracts/README.md)指向的文档定义。正式前端不迁入原型中的随机库存或模拟风险算法。
