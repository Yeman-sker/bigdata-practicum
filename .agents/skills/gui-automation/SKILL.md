---
name: gui-automation
description: Operate or visually verify a browser or desktop UI with Codex Computer Use. Use for interactions and screenshots that need a real application.
---

# GUI 操作与视觉验证

按用户指定的浏览器、应用和目标完成交互，并核验可观察结果。API、文件与命令行任务使用对应工具，不为它们额外启动桌面自动化。

## 工具选择

- 本仓库用户选择 Codex Computer Use（`cua_repl`）。先按该工具说明进入应用 / 浏览器并读取返回文档；只使用当前实际提供的 API。
- 不用 ego-browser / Ego Lite，也不因为缺少 `cua` CLI 自动安装另一套驱动。工具不可用时说明受影响步骤，继续能独立完成的工作。
- 仅用户明确选择开源 `trycua/cua` CLI 时，读取 [CLI 参考](references/command-reference.md)，先核验安装版本与帮助。它与 `cua_repl` 是不同接口，命令不能混用。

## 操作与证据

根据当前页面 / 窗口状态定位元素，执行操作后检查结果。页面跳转、弹窗或布局变化后重新获取状态；不猜测旧坐标或不存在的元素。

交付截图只保留相关应用窗口或浏览器视口，确认关键文字可读。日志任务的存放位置和文档要求由 `practicum-daily-log` 定义。

完成条件是用户所需交互成功或发现可复现的问题，并留下相应证据。仅当用户要求分享时才上传轨迹或生成公开回放链接；生成截图不自动授权对外分享。
