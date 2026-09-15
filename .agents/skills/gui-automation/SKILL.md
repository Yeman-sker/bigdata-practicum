---
name: gui-automation
description: Operate or visually verify a browser or desktop UI with an available GUI automation tool. Use when WSL must control Windows apps or when interactions and screenshots need a real application.
---

# GUI 操作与视觉验证

按用户指定的浏览器、应用和目标完成交互，并核验可观察结果。API、文件与命令行任务使用对应工具，不为它们额外启动桌面自动化。

## 工具选择

- WSL2 中需要控制 Windows 原生 Chrome、Edge、Windows Terminal 或 VSCode 时，通过 Windows PowerShell 调用安装在 Windows 宿主机上的开源 Cua Driver。先核验 `cua-driver --help` 和实际 API，不在 WSL 内安装或运行 Linux 驱动代替。
- 其他环境使用用户指定且当前实际可用的 GUI 工具。不因为缺少工具自动安装另一套驱动；说明受影响步骤，并继续能独立完成的工作。
- 只有已安装的开源 Cua CLI 明确提供 `cua do` 时，才读取 [CLI 参考](references/command-reference.md)；`cua do` 与 `cua-driver call` 不是同一接口，命令不能混用。

## 操作与证据

根据当前页面 / 窗口状态定位元素，执行操作后检查结果。页面跳转、弹窗或布局变化后重新获取状态；不猜测旧坐标或不存在的元素。

交付截图只保留相关应用窗口或浏览器视口，确认关键文字可读。日志任务的存放位置和文档要求由 `practicum-daily-log` 定义。

完成条件是用户所需交互成功或发现可复现的问题，并留下相应证据。仅当用户要求分享时才上传轨迹或生成公开回放链接；生成截图不自动授权对外分享。
