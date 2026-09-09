# 生产实习每日日志自动化实操环境与前置工具规范 (Prerequisites & Tooling)

本文档定义了 AI Agent（或组员）在执行 `practicum-daily-log` Skill 之前必须就绪的本地工具链、运行时依赖与权限配置。Agent 在启动视频解析或实操任务前，必须依据本文档进行前置环境检查（Pre-flight Check），若缺少任一组件应立刻给出明确的安装与配置指引。

---

## 一、 必需工具与依赖清单矩阵

| 组件类别 | 工具/依赖项 | 作用说明 | 推荐版本 | 安装/配置命令 |
| :--- | :--- | :--- | :--- | :--- |
| **音视频预处理** | `ffmpeg` | 极速剥离视频并压缩音频至 32k mono mp3（原生高效支持老师的 `.wmv` 及 `.mp4` 格式） | 4.x+ / 6.x+ | **macOS**: `brew install ffmpeg`<br>**Ubuntu/WSL2**: `sudo apt update && sudo apt install -y ffmpeg` |
| **电脑操作驱动** | `cua-driver`<br>(trycua/cua) | 跨平台驱动宿主机浏览器、终端与 VSCode 截图 | 0.23+ | `pip install cua-driver` 或从 [trycua/cua Releases](https://github.com/trycua/cua/releases) 下载二进制放至 PATH |
| **文档处理** | `python-docx` | 动态填充封面表头、增量追加表格、插入图片与排版 | 1.1.0+ | `pip install python-docx` |
| **环境解析** | `python-dotenv` | 跨平台安全解析 `.practicum.env` 身份配置 | 1.0+ | `pip install python-dotenv` |
| **宿主机桌面工具** | Google Chrome / Edge | 打开 HDFS 9870、YARN 8088 等 Web UI 供独立单窗口截图 | 最新稳定版 | 系统原生安装并设为默认或就绪 |
| **宿主机代码工具** | VSCode | 原生打开当前项目，供独立截取高保真代码片段 | 最新稳定版 | 系统原生安装，建议具备 `code` 命令行别名 |
| **推荐终端工具** | Ghostty (优先) / Windows Terminal | 运行大数据与集群实操命令，供独立单窗口截图 | 最新稳定版 | **macOS**: `brew install --cask ghostty`<br>**Windows**: Windows Terminal |

---

## 二、 环境准备与安装显式确认规范 (Confirmation Gate)

**核心铁律：严禁 Agent 在组员系统上静默擅自执行安装命令。**

当 Agent 运行自检发现缺少必要依赖时，必须严格执行以下确认机制：

1. **报告缺失现状**：清晰列出检测到缺失的具体工具（如：`cua-driver`、`ffmpeg`、`python-docx`）。
2. **说明影响与计划执行命令**：
   - 工具作用（如：“`cua-driver`：用于驱动宿主机浏览器与终端截图存证”）。
   - 拟在宿主机执行的命令（如：`pip install cua-driver python-docx python-dotenv`）。
3. **向组员主动发送确认询问**：
   ```text
   【环境依赖检查提示】
   检测到当前环境尚未安装以下必要工具：
   - cua-driver: 用于驱动宿主机浏览器与终端截图存证
   - python-docx: 用于向实习日志 Word 表格追加内容与图片

   拟执行的安装命令为：
   pip install cua-driver python-docx python-dotenv

   请问是否允许我为您自动执行上述安装？
   [请输入 y 确认执行 / n 拒绝自行安装]
   ```
4. **分支处理**：
   - **组员确认（y/yes/允许/安装）**：Agent 执行安装并打印安装结果验证。
   - **组员拒绝或未明确同意（n/我自己来/不用装）**：Agent 严禁执行命令，保持等待，并提示组员在终端手动安装完毕后告知 Agent 继续。

---

## 三、 虚拟开发环境通道就绪指标 (根据 PRACTICUM_DEV_ENV)

根据 `.practicum.env` 中的 `PRACTICUM_DEV_ENV` 设定，验证底层执行通道：

### 1. 分支 A：macOS + OrbStack (`PRACTICUM_DEV_ENV=orbstack`)
* **宿主机 CLI 检查**：
  ```bash
  which orb
  orb list  # 必须包含名为 bigdata 且为 Ubuntu 22.04 jammy 的运行实例
  ```
* **实例运行状态**：若 `bigdata` 处于 `stopped`，Agent 需先执行 `orb start bigdata` 拉起。
* **单向执行通道**：
  ```bash
  # 测试无密码直接进入 bigdata 实例执行
  orb -m bigdata bash -c "whoami && uname -a"
  ```

### 2. 分支 B：Windows + WSL2 (`PRACTICUM_DEV_ENV=wsl2`)
* **宿主机 CLI 检查**：
  ```cmd
  wsl --list --verbose  # 确保默认发行版或 Ubuntu-22.04 状态为 Running
  ```
* **单向执行通道**：
  ```cmd
  wsl -d Ubuntu-22.04 -u bigdata bash -c "whoami && uname -a"
  ```
* **系统守护检查**：确保 `/etc/wsl.conf` 已配置 `[boot]\nsystemd=true`。

---

## 三、 Cua Computer-Use 权限与连通性检查

Cua 需要直接控制宿主机的窗口与截屏能力：

1. **CLI 连通性测试**：
   ```bash
   cua-driver --version
   cua-driver list-tools
   ```
2. **操作系统权限授予 (重点)**：
   * **macOS 宿主机**：
     - 需要在「系统设置 $\to$ 隐私与安全性」中，为当前运行终端（如 Terminal / iTerm2 / VSCode）授予 **辅助功能 (Accessibility)** 和 **屏幕录制 (Screen Recording)** 权限。
     - 检查命令：`cua-driver permissions status`
   * **Windows 宿主机**：
     - 确保当前运行 Agent 的命令行终端拥有桌面交互权限，防止窗口激活被后台安全策略静默拦截。

---

## 四、 虚拟机内部大数据基础配置自检 (内部对齐)

Agent 在控制开发机运行实操前，需自检 Linux 实例内部的大数据就绪状态（严格遵循 `bigdata-env-setup` Skill）：
* **双 JDK 脚本存在性**：
  - `~/use-jdk8.sh` 能够切入 JDK 8（验证：`source ~/use-jdk8.sh && java -version 2>&1 | grep "1.8"`）。
  - `~/use-jdk17.sh` 能够切入 JDK 17（验证：`source ~/use-jdk17.sh && java --version`）。
* **软件与数据挂载根目录**：
  - 软件部署目录：`/opt/soft` 存在且拥有读写权限。
  - 大数据存储目录：`/data/hadoop` 存在且属主为工作用户。

---

## 五、 Agent 自动化就绪检查命令 (Pre-flight Script)

Agent 在执行前可单行评估环境就绪状态：

```bash
python3 -c "
import sys, shutil

def check(name, ok, tip):
    status = '[\033[32mOK\033[0m]' if ok else '[\033[31mFAIL\033[0m]'
    print(f'{status} {name}: {tip}')
    return ok

all_ok = True
all_ok &= check('ffmpeg', shutil.which('ffmpeg') is not None, '用于抽取视频音轨')
all_ok &= check('cua-driver', shutil.which('cua-driver') is not None, '用于跨平台电脑控制与截图')

try:
    import docx
    check('python-docx', True, '用于 Word 日志表格追加')
except ImportError:
    all_ok &= check('python-docx', False, '需执行 pip install python-docx')

try:
    import dotenv
    check('python-dotenv', True, '用于解析 .practicum.env')
except ImportError:
    all_ok &= check('python-dotenv', False, '需执行 pip install python-dotenv')

if not all_ok:
    print('\n\033[33m提示：部分前置工具缺失，请根据上述提示安装后再执行实操流水线。\033[0m')
    sys.exit(1)
else:
    print('\n\033[32m所有核心前置工具与依赖已全部就绪！\033[0m')
"
```
