---
name: ui-ux-browser-acceptance
description: Verify implemented web UI/UX changes in a real Codex browser with direct Computer Use, including visual states, interaction, keyboard access, and failure recovery. Use after changes that affect rendered frontend behavior or when browser acceptance is requested; static design documents alone are not browser evidence.
---

# UI/UX 浏览器验收

验证用户实际看到和操作到的结果。以 [前端交互与视觉设计](../../../docs/frontend-design.md)、[DESIGN.md](../../../DESIGN.md)、任务要求及 [runbook](../../../docs/runbook.md) 为验收依据，不从实现代码反推产品预期。

## 工具边界

- 直接使用 Codex Computer Use 的 `cua_repl` 操作和观察 UI；本地 Web 应用优先使用 Codex 内置浏览器。
- 禁止读取或调用仓库的 `gui-automation` skill，也不使用 ego-browser。除非用户另行明确要求，不以 Playwright 代替本 skill 的浏览器操作。
- shell 只用于运行既有安装、构建、测试、服务启动和日志检查；页面导航、点击、输入、键盘操作及视觉确认由 Computer Use 完成。
- Computer Use 不可用或应用无法启动时，如实记录“未执行”及原因，不能用源码审查、截图稿或单元测试冒充浏览器通过。

## 选择验收范围

- 可见前端改动：构建和受影响测试通过后，至少验收改动状态、进入路径、退出/恢复路径及一个相邻关键状态。
- 前端 PR 交付：使用仓库现有 fixture 覆盖受影响场景，并执行 `docs/runbook.md` 的五条 P0 浏览器路径和相关异常恢复项。
- 最终集成验收：连接真实 API 与本任务要求的数据链路；fixture 结果只能作为回归证据，不能替代真实验收。
- 仅修改设计文档或本 skill、且没有可运行 UI 变化时，不启动浏览器；验证文档链接、skill 元数据与仓库检查，并明确浏览器验收不适用。

不要为验收新建框架、包装 skill 或专用浏览器脚本。复用项目现有 dev server、fixture 开关和数据入口；缺少可控场景时记录缺口，由对应实现任务补齐。

## 执行

1. 读取本次需求、上述设计依据及相关接口契约；列出可观察的通过条件，不把内部实现细节当作验收结果。
2. 运行最小相关 shell 检查并启动现有前后端。记录 URL、视口、提交号和数据来源；桌面 P0 默认至少使用 `1280×720`。
3. 通过 `cua_repl` 打开应用，先确认无首屏遮挡、溢出、控制台式错误提示或错误数据来源，再按真实用户路径操作。
4. 对每条路径同时检查：内容与数据语义、布局层级、点击/键盘行为、焦点返回、加载/空/失败/恢复，以及适用时的减少动态效果。使用键盘完成主要路径，并检查可见焦点与辅助技术可读名称。
5. 刷新或切换状态后确认旧请求、旧选择和旧图层没有覆盖当前状态。只断言屏幕上或可访问性信息中实际观察到的结果；计时、竞态等不可见逻辑仍由相应自动化测试证明。
6. 对失败项保留当前页面和最小复现步骤，定位并修复本次改动导致的问题；复跑失败路径及受其影响的相邻路径，不机械重跑无关场景。

对本项目的地图界面，尤其核对实时与历史图层不会混用、`+1h` 不改变实际库存粒子、调度方向和数量正确、站点透镜与临时浮层符合互斥/焦点规则、异常数据不被画成零或健康状态。具体数值与完整状态矩阵以设计文档和 runbook 为准，不在此重复维护。

## 证据与完成条件

截图和运行日志放在仓库外的 `$DATA_DIR/frontend-acceptance/<commit>/`；未配置 `DATA_DIR` 时使用 `/tmp/bigdata-practicum-ui-ux-acceptance/<commit>/`。不要提交临时截图、浏览器缓存或含隐私的数据。

在 Issue 或 PR 汇总：提交号、URL/视口、数据来源、已执行路径、预期与实际结果、代表性截图路径、失败与未执行项。只有所有本次必需路径通过、失败项已复验，且 fixture/真实链路边界写清楚时，才能报告 UI/UX 浏览器验收通过。
