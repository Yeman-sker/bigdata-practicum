# WSL2 下 Cua 宿主机安装与排障记录

本参考仅在用户明确选择开源 Cua Driver、环境为 WSL2 且需要控制 Windows 原生桌面时使用；它不是 Codex `cua_repl` 的安装前置条件。以下是已有排障记录，具体 API 先核对本机版本。核心结论：WSL 中运行的驱动不等于 Windows 桌面驱动。

## 1. 已验证的运行边界

- WSLg 的 `DISPLAY`、`WAYLAND_DISPLAY` 正常，只能证明 Linux GUI 通道存在。
- WSL 中的 Linux 版 `cua-driver` 可能只能枚举 WSLg/X11 窗口；即使 Windows Chrome 已打开，`list_windows` 也可能返回空列表。
- 不要把 WSL 中 `pip install cua` 成功当成 `cua do ...` CLI 可用。PyPI 的 `cua` 包可能只是旧库接口，没有技能文档中的 `cua do` 命令。
- WSL 中的 Cua daemon 若提示 Unix socket、AT-SPI 或 D-Bus 权限/连接错误，先确认当前会话是否有可用的 WSLg/桌面 DBus；这类错误不能通过全屏截图绕过。

## 2. Windows 侧安装（推荐路径）

在 Windows PowerShell 中执行，使用 Windows 自己的 Python：

```powershell
py -m pip install --user cua-driver
& "$env:APPDATA\Python\Python312\Scripts\cua-driver.exe" --version
```

Python 版本不是 3.12 时，脚本路径中的 `Python312` 按实际版本调整。也可以查找安装位置：

```powershell
Get-Command py,python,pip,cua-driver -ErrorAction SilentlyContinue
Get-ChildItem "$env:APPDATA\Python" -Recurse -Filter cua-driver.exe -ErrorAction SilentlyContinue
```

如果脚本目录不在 PATH，不要重复在 WSL 安装；直接使用 `cua-driver.exe` 的绝对路径。

## 3. 启动与验证 Windows daemon

```powershell
$cua = "$env:APPDATA\Python\Python312\Scripts\cua-driver.exe"
Start-Process -FilePath $cua -ArgumentList 'serve' -WindowStyle Hidden
Start-Sleep -Seconds 3
& $cua status
```

看到 `Cua Driver daemon is running` 后，再枚举可见窗口。PowerShell 5.1 可能剥掉 JSON 对象字段名的引号，因此把 JSON 通过 stdin 传入：

```powershell
'{"on_screen_only":true}' | & $cua call list_windows
```

不要只检查 WSL 的 Unix socket；Windows daemon 使用 Windows named pipe（通常为 `\\.\pipe\cua-driver`）。

## 4. Chrome 窗口级截图

从 `list_windows` 结果中取得 Chrome 的 `pid`、`window_id` 和 bounds。禁止调用 `get_desktop_state`，因为它是全屏桌面捕获。应用级截图使用 `get_window_state`：

```powershell
'{"pid":10136,"window_id":724892,"include_accessibility_tree":false,"screenshot_out_file":"C:\\Users\\<user>\\Pictures\\chrome-cua-window.png","max_dimension":1600}' |
  & $cua call get_window_state
```

结果中的 `window_bounds`、`screenshot_width`、`screenshot_height` 必须记录，用于确认捕获对象确实是 Chrome 窗口，而不是桌面。

## 5. 本次实测故障与修复

1. WSL 中 `cua` 命令不存在；安装的 PyPI `cua` 包没有同名 CLI。
2. WSL `cua-driver` 的 Python wrapper 尝试对 site-packages 内置二进制执行 `chmod`，在受保护环境中报 `Read-only file system`。
3. WSL daemon 启动后，`list_windows` 返回空列表，无法看到 Windows 原生 Chrome。
4. 改为 Windows 侧执行 `py -m pip install --user cua-driver`，启动 Windows daemon 后成功枚举 `chrome.exe` 的窗口句柄，并通过 `get_window_state` 生成独立 Chrome PNG。

后续 Agent 应按以下顺序决策：

```text
WSL2 + Windows 原生桌面需求
        ↓
检查 Windows py/python 与 cua-driver
        ↓
缺失且已获安装授权时在 Windows 侧安装
        ↓
启动 Windows cua-driver daemon
        ↓
Windows 侧 list_windows → 取 Chrome window_id
        ↓
get_window_state（窗口级）
```

## 6. 安全与权限门禁

- 安装 Windows 包前核对已有授权；尚未授权才说明命令并询问，不重复索要确认。
- WSL 与 Windows 的路径、权限和 socket 不要混用。日志任务将截图输出指向 Windows 可访问的 WSL `/tmp/practicum-daily-log/<task>/` 路径，不采用示例中的个人 Pictures 目录或仓库目录；示例路径和窗口 ID 均需替换。
- 截图只捕获目标应用窗口；禁止使用 `get_desktop_state` 或包含任务栏/桌面的替代方案。
- 若 Windows daemon 能运行但窗口列表为空，检查是否处于同一个交互式 Windows 用户桌面、Chrome 是否实际显示，再检查 Cua 权限；不要盲目重试全屏捕获。
