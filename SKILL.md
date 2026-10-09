---
name: novel-ledger-v2
description: >-
  创作、续写和修订长篇网络小说，以 SQLite 账本、按需检索和独立审稿支持百万字工程、分批续接与全书视图。
---

# novel-ledger-v2

## 路径与入口

`ROOT` 是 Skill 绝对路径；`PROJECT` 是独立小说项目绝对路径。小说账本、正文和运行产物只存 PROJECT，不写入 ROOT。任务路由相对 PROJECT 解析。

命令入口：`python3 "$ROOT/scripts/novel.py"`（Windows 用 `python`）。初始化、定调和入库会刷新 `$PROJECT/book.html`，汇报时提供该文件的绝对路径链接。

## 核心操作流

- **开书**：读 [creation](references/creation.md) 与 [hatch-ideas](references/hatch-ideas.md)。每步给 2～3 个候选，含至少一个可落地的高概念脑爆项；换组时切换透镜。确定约定后 `init --project "$PROJECT" --contract contract.json`，试写首章，由作者定调 `approve`。
- **续写 / 无人值守 / 恢复**：读 [runtime](references/runtime.md) 与 [context-runtime](references/context-runtime.md)，先 `status` 再串行 `next`。按实际工具委派：Claude Code / Zcode 用 `Agent`（Claude 旧版可能为 `Task`），Codex 用 `spawn_agent`，Antigravity 用 `invoke_subagent`。每项任务使用空历史 Worker，写作、事实审稿、读者审稿分别委派；有委派能力时主控只收短状态，不自行读取 input 或写稿审稿。沿用当前模型，持续推进至已授权目标。
- **局部返修与修订**：局部问题读 [局部修订](references/repairs.md) 提交版本补丁；历史大修读 [记忆与修订](references/continuity.md) 建候选分支。
- **验收与审计**：读 [审稿与完本](references/editorial.md)；执行 `audit --project "$PROJECT"` 复核全书账本，`complete` 进行完本验证。

## 核心规则

1. SQLite 为唯一真源，只通过 `submit` 更新，不手改数据库或伪造回执。
2. 正式入库须通过独立 `facts` 与 `reader`；逐项阅读判断，证据支持结论，不批量生成 pass 或套用固定理由。
3. 具体缺陷最多返修两轮，再重设计并重写一次；仍失败则保留现场、报告阻碍。
4. 不自行降低字数目标、改变主线终局或替作者执行首章 `approve`。
5. 不给输入、正文、证据、摘要或补读设字符上限。按任务、实体/话题、完整语义单元和版本补丁减少重复；形式审计不替代语义审稿。
6. 所有文风删除非必要形容词，用动作、事实、对白和后果表达；必要辨识或事实描述保留。执行 [voices](references/voices.md)，reader 用 `language` 核验。

## 任务执行流水线（Worker / 单任务）

1. 主控 `next --project "$PROJECT"` 取信封并调用宿主委派工具；`next` 本身不启动 agent。已有 pending 就恢复同一任务。
2. Worker 读取 `input_path` 一次，按阶段查必要指南和协议字段；brief 为替代视图，不重复读取。
3. 规划说明篇幅承载和压力变化；执笔按 `writer_brief` 写入 `output_path`，先 `measure --project "$PROJECT" --result <output_path>` 自查，不足时补实选择与后果。审稿保留必要语境与因果，pass 的 `findings=[]`。
4. Worker `submit --project "$PROJECT" --result <output_path>`；按真实报错修正，成功后只返回任务 ID、状态和必要短错误，退出，不取下一任务。
5. 主控等待完成、核对真实提交后再 `next`，到目标后汇报进度与全书视图。确无空历史委派能力时按 runtime 降级，并标明“同会话审稿”。

## 资料索引

| 使用时机 | 读取资料 |
|---|---|
| 选方向、开新书、试章定调 | [creation](references/creation.md) / [hatch-ideas](references/hatch-ideas.md) / [voices](references/voices.md) |
| 全书/分卷世界设计、卷纲脑爆、任务书 | [design-and-brief](references/design-and-brief.md) / [hatch-ideas](references/hatch-ideas.md) |
| 人物档案、声线、对白差异 | [characters](references/characters.md) |
| 场景执笔、表达技巧与题材 | [writing](references/writing.md) |
| 事实审查、读者体验、完本 | [editorial](references/editorial.md) |
| 账本一致性、时空连续性 | [continuity](references/continuity.md) / [spatial-continuity](references/spatial-continuity.md) |
| 段落返修、补丁格式 | [repairs](references/repairs.md) |
| JSON 字段与事件结果协议 | [protocol](references/protocol.md) |
| 调度运行、批次接力与恢复 | [runtime](references/runtime.md) / [context-runtime](references/context-runtime.md) |
| 验证方法、独立正文盲测与边界测试 | [validation](references/validation.md) / [literary-evaluation](references/literary-evaluation.md) |
