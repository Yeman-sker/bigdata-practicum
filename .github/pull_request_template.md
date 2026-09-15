## 关联 Issue
<!-- 必须关联具体 Issue，例如：Closes #8 或 Refs #5 -->
Closes #

## 所属赛道 / 阶段
- [ ] Day 1 / Track A — Historical Trip 数据源与清单
- [ ] Day 1 / Track B — GBFS 实时数据与快照
- [ ] Day 1 / Track C — Source Schema Validator 与 Fixtures
- [ ] Day 1 / Track D — HDFS RAW 与 Hive ODS
- [ ] Day 1 / Track E — Spark 读取 PoC 与端到端门禁
- [ ] Day 2 — 文档、契约与共享样例
- [ ] Day 3–7 — 前端 / API 与 GBFS / 离线 / 规则 / 集成切片
- [ ] 其他维护 / 基建 / 文档

## 改动概述
<!-- 简要陈述本次 PR 解决的核心问题及改动要点 -->
- 

## 数据契约与 Schema 兼容性检查
<!-- 对照 docs/contracts/README.md 与 ADR-0004 后继关系 -->
- [ ] `station_id` 强制保持 STRING 类型（未隐式转换为 INT/FLOAT）
- [ ] 时间语义与时区规范对齐（本地墙上时间 vs UTC POSIX）
- [ ] 新增或修改的数据结构与现行契约一致；修订已同步文档、样例和受影响消费者
- [ ] 仅提交轻量 sample fixtures，未将大文件数据直接写入仓库

## 本地验证证据 (必填)
<!-- 必须提供真实的执行命令及控制台输出或截图依据 -->
运行的验证命令：
```bash
python3 ...
```

验证结果输出：
```text
(粘贴实际命令输出)
```

## 提交前自查清单
- [ ] 代码在独立的 `codex/<issue-number>-<slug>` 或特性分支中开发
- [ ] 已同步最新 `origin/main` 代码
- [ ] 无无关文件、临时日志、编译产物或敏感信息提交
- [ ] CI 流水线检查全部通过
