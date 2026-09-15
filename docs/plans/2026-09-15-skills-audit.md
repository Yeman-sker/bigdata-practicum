# Skills、AGENTS 与工作流审计（2026-09-15）

依据 OpenAI 的 [Rethinking skills and prompts for GPT-6 Astra](https://developers.openai.com/blog/rethinking-skills-and-prompts-for-gpt-6-astra)：缩小 skill 触发范围、按需读取细节、去除机械流程，并用清晰的授权与完成条件支持持续交付。保留对团队使用不同模型仍有价值的契约和操作边界。

范围为仓库分发的 8 个 skill、AGENTS、协作导航和 Issue / PR 模板；未改个人全局技能或产品实现。

## 发现与修正

| 发现 | 修正 |
| --- | --- |
| 个人日志被“文档 / 代码改动”吸入仓库开发，触发 worktree 等流程 | AGENTS、开发 skill 的描述和入口、日志 skill 同时区分个人产物与仓库源码维护；日志生成不使用 Git、gh、Issue、PR 或 CI |
| 日志默认向 `docs/` 写截图和个人文件，依赖仓库模板 / 身份配置 | 日志包内资源自足，临时文件统一在 `/tmp/practicum-daily-log/<task>/`，最终位置按用户请求；身份字段模板清空示例值 |
| 缺视频、某个工具或身份文件即全面停止；自动执行整套实操 | 接受笔记、转录、已有文档和证据；只补必要缺口，实操 / 截图按请求执行，沿用已有授权 |
| GUI 路由混用 WSL 与 Windows 侧工具，要求安装及上传轨迹 | WSL 控制 Windows 原生窗口时调用安装在 Windows 的开源 Cua Driver；上传需在分享授权内 |
| 重复 GitHub 门禁、每次开发先回收 worktree、任务分支可能误 pull main | 认证按状态复用，已有任务 worktree 继续使用；回收按需读取并保留全部安全条件，pull 仅用于干净 main |
| 每个任务套用大型 Issue 流程，模板要求创建 PR 前 CI 全绿 | 区分跨模块任务与小型维护；PR 创建后检查最新 CI，合并仍由负责人决定 |
| 复审只能核验旧问题，最多三个阻断项，强制两个审查 agent | 两轮是收敛目标；实际缺陷和修复回归仍阻断，无数量限制；按复杂度选择审查方式 |
| 审查仍固定 ADR-0001，缺失的 Skill tool / 子技能导致流程中断 | 从现行契约索引追踪 ADR 后继；设计澄清 skill 自足，triage 按需调用，不依赖未安装技能 |
| 同一版本、语言规范和流程在多处重复 | 版本矩阵留在 README；条件性回收与兼容性细节进入 references；领域导航按任务选文档 |

8 个 `SKILL.md` 合计 **853 → 241 行**；frontmatter 描述合计 **1716 → 1007 字符**。这是文本体积变化，不代表实测 token、耗时或质量提升。原有三个显式调用策略保留在 `agents/openai.yaml`；`skills-lock.json` 保留上游安装来源记录，不冒充本地文件校验值。

## 验证

- 官方 `skill-creator/scripts/quick_validate.py`：8 个 skill 全部通过。
- 新增 [资源回归检查](../../tests/test_agent_skills.py)：元数据可解析、指令链接可访问、日志 skill 的本地链接不越出自身目录。已捕获并修复本次重排时的 7 个错误相对路径；不以固定措辞匹配代替行为验证。
- `uv run --python 3.11 --with-requirements requirements-contracts.txt python -B -m unittest discover -s tests -p 'test_*.py' -v`：45 项中 43 通过、2 项因未安装 PySpark 跳过。
- `uvx ruff check --no-cache --select E,F,W --ignore E501 .`、`git diff --check` 与使用外部 pycache 的 `compileall`：通过。
- 新检查由现有 CI 的 unittest discovery 自动执行，无新增测试框架或 CI 作业。

### 独立日志演练

独立 agent 从本仓库任务目录启动，输入为模拟课堂笔记，要求 150–200 字 Markdown 日志，不提供视频、不制作 Word、不补拍截图。实际仅读取 AGENTS、日志入口和语言规范，使用 Python 生成并回读 170 字符条目；未使用 Git / gh、创建 worktree、运行仓库测试或操作集群。内容区分老师演示与个人操作，没有编造报错。

演练产物保留在本机 `/tmp/practicum-daily-log/skills-forward-test-20260915/2026-09-15.md`，不提交仓库。本次验证覆盖日志路由和纯文本产出；未演练 Word 增量排版、真实截图或跨平台驱动。合并后组员需取得新版仓库指令；此改动不更新已经安装到其他位置的旧副本。

### 本地审查

按用户请求与现行仓库约定审查全部交付 diff，结论 `APPROVE`：无已知阻断项。实际 GitHub CI 和合并状态以 PR 为准；本记录不代替负责人评审。
