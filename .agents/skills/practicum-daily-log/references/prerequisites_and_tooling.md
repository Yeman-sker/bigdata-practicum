# 日志任务工具准备

仅检查本次步骤需要的能力。生成日志无需 GitHub、Git 仓库、项目依赖或运行中的完整集群；已有材料可用时继续编写。

| 当前步骤 | 优先使用 | 缺失时如何处理 |
| --- | --- | --- |
| 视频音轨 / 关键帧提取 | 已有 `ffmpeg`，或已提供的转录与截图 | 只列缺少的能力和拟执行命令；已有转录时不必安装 ffmpeg |
| Word 编辑与渲染 | 当前环境的文档工具、已安装的 `python-docx` 与渲染器 | Codex 可先查询 `load_workspace_dependencies`；其他环境检查实际可用工具 |
| 浏览器 / 桌面截图 | WSL 控制 Windows 时使用安装在 Windows 的开源 Cua Driver；其他环境使用用户指定的可用工具 | 读取 [WSL 宿主机参考](wsl_cua_host_setup.md)；工具不可用时说明证据缺口，继续处理已有录屏或截图 |
| 个人字段 | 用户信息、已有文档或已提供的配置 | 仅补充缺失字段，不要求安装 dotenv 或创建仓库内配置 |
| 复现实操 | 用户选定的现有 WSL2 / OrbStack 或远程环境 | 仅检查任务涉及的组件，不运行仓库开发流程或全套环境验收 |

## 安装与授权

复用现有工具和已授予的安装权限。只有确实需要、且不在已有授权范围内的宿主机 / 系统安装才提出确认，列出工具用途和完整命令；先完成能独立推进的材料整理。不要自动安装浏览器、编辑器或新终端来满足示例偏好。

用户已同意的安装无需重复索要确认。任务范围内的临时文档脚本及其隔离环境放 `/tmp/practicum-daily-log/<task>/`；不修改仓库或全局 Python 来运行脚本。

## 需要复现实操时

先确认实际实例名、运行用户和路径，不假定一定叫 `bigdata`。OrbStack 可用 `orb list` 查看、`orb -m <instance> ...` 执行；Windows 可用 `wsl --list --verbose` 查看、`wsl -d <distribution> -u <user> ...` 执行。不为写日志自动创建或重启实例。

Linux 组件和数据保留在 Linux 本地；所有本次脚本、构建、截图与中间文档放任务目录。只检查涉及的 Java / Hadoop 等依赖，不将完整大数据部署作为写日志的前置条件。

## WSL 控制 Windows 桌面时

执行环境为 WSL2 且需要控制 Windows 原生桌面时，使用 [WSL 宿主机参考](wsl_cua_host_setup.md)。开源 Cua Driver 安装并运行在 Windows，WSL 通过 Windows PowerShell 调用；WSLg 窗口不能证明 Windows Chrome 可被捕获。先核验已安装版本的实际 API，`cua do` 与 `cua-driver call` 的命令不能混用。
