---
name: novel-ledger-v2
description: >-
  创作、续写和修订长篇网络小说，以 SQLite 账本、按需检索和独立审稿支持百万字工程、分批续接与全书视图。
---

# novel-ledger-v2

## 路径与入口

`ROOT` 是 Skill 绝对路径；`PROJECT` 是独立小说项目绝对路径。小说账本、正文和运行产物只存 PROJECT，不写入 ROOT。任务路由相对 PROJECT 解析。

命令入口：`python3 "$ROOT/scripts/novel.py"`（Windows 用 `python`）。派生视图 `$PROJECT/book.html` 的刷新归属主控的章节边界：初始化、定调、非轻量 `submit`，以及「有章节落地的 `advance`」都会刷新；Worker 用 `--brief`/`--compact` 交接时只更新 controller 快照、不渲染（那是响应模式，不是副作用）。需要即时页面时显式执行 `html`。汇报时提供该文件的绝对路径链接。

## 核心操作流

- **开书**：读 [creation](references/creation.md) 与 [hatch-ideas](references/hatch-ideas.md)。每步给 2～3 个候选，含至少一个可落地的高概念脑爆项；换组时切换透镜。确定约定后 `init --project "$PROJECT" --contract contract.json`，试写首章，由作者定调 `approve`。
- **续写 / 无人值守 / 恢复**：读 [runtime](references/runtime.md)，用 `advance --compact` 一次取得任务、短状态和恢复点；Worker 结束后用 `advance --compact --after 任务ID` 核实提交并接续，沿用当前模型。无人值守只用单章写审：新执笔会话只写 1 章并自查，新的独立审稿会话先首读再完成读者与事实核验，通过后写下一章。旧 v2 项目打开时自动备份升级；当前旧任务按冻结协议完成后结束，候选稿保留逐章重审；跨章复审由新的独立审稿者在同次执行内连续处理分块与综合判断，按需查 [材料准备](references/context-runtime.md)。本版本自动升级旧 v2 运行版本；不猜测不兼容账本 schema。
- **局部返修与修订**：局部问题读 [局部修订](references/repairs.md) 提交版本补丁；简单数字字面笔误可在支持的独立审稿任务内定点修正并完成两类核验。历史大修读 [记忆与修订](references/continuity.md) 建候选分支。
- **验收与审计**：读 [审稿与完本](references/editorial.md)；执行 `audit --project "$PROJECT"` 复核全书账本，`complete` 进行完本验证。

## 核心规则

1. SQLite 为唯一真源，只通过 `submit` 更新，不手改数据库或伪造回执。
2. `chapter_review` 由一个独立于执笔者的审稿 Worker 完成事实与读者两类检查，通过后才入库。逐项阅读、举证，不批量生成 pass 或套用理由；两类检查不称为两名独立盲审。
3. 具体缺陷最多返修两轮，再重设计并重写一次；仍失败则保留现场、报告阻碍。
4. 主控与 Worker 运行小说时不热修 ROOT 源码、不直连数据库、不复制旧审稿回执猜字段；用本任务模板、task-view 与命令的修正出口。
5. 不自行降低字数目标、改变主线终局或替作者执行首章 `approve`。
6. 不给输入、正文、证据、摘要或补读设字符上限。按任务、实体/话题、完整语义单元和版本补丁减少重复；形式审计不替代语义审稿。
7. 所有文风删除非必要形容词，用动作、事实、对白和后果表达；必要辨识或事实描述保留。执行 [voices](references/voices.md)，reader 用 `language` 核验。

## 任务执行流水线

恢复与汇报以完整 `status.recovery` 或 controller 快照及 `advance` 真实任务为准，分别注明正式稿与候选工作稿的章号、卷次；存在修订链时先完成修订，不根据旧版 book.html、卷末标记或传播范围宣称已进入下一卷。父级未完成的结构目标不因后加文风修订而撤销。

1. 主控 `advance --project "$PROJECT" --compact` 取 `task` 信封，有 `host_dispatch_path` 时读取其派工规格，使用程序生成的 `worker_prompt` 调用宿主委派工具；此命令本身不启动 agent。已有 pending 就恢复同一任务。委派消息只做薄包装（角色与历史、task.json 路径、一句话背景、退出回报契约），不复述流程或拼装命令示例，见 [runtime](references/runtime.md) 委派节。
2. 简洁响应先按 `task.task_path` 读取完整信封，连续响应和首读解锁后也一样。`chapter_review` Worker 先只读信封 `first_read_files`（批次存在时）或 `first_read_path` 正文，填写 `first_read.template.json` 并执行 `commands.first_read` 持久冻结首读，按成功响应恢复完整 `task` 信封取得 `input_path` 与正式模板，reader 用模板 first_read_ref 引用原首读；恢复时如未保留原体验，读 first_read_receipt_path；其他阶段有 `commands.read` 时可一次取得全部必读记录，与 `reading_view.pages` 或 JSON 入口三选一；宿主输出不完整时沿 pages 完整读取；旧任务无视图时读 `input_path`。信封是索引：`input_files` 非空时字段分片在同名文件里，按 `must_read`（needed=true）先读必读材料，`on_demand` 用 recall/character/material 按需补读，不读它不等于它不存在；索引与分片都要保持一次读完。包内 `shared_materials` 是完整文件引用，分片同样按 needed 标记取用。同一 Worker 只复用自己已读且哈希未变的记录，新 Worker 完整读取。证据填 `chapter/quote` 即可，程序批量补齐唯一 `start`；歧义或排错才调用 evidence。重复证据可用 `evidence_catalog` 定义一次、逐项引用；具体理由和检查仍逐条填写。以 `guide` 为执行入口，只在字段说明不足、报错或有疑问时查对应指南。正文按 `prose_path`/`prose_files` 阅读，同版本不重复加载；brief 为替代视图。
3. 规划说明篇幅承载和压力变化；执笔按 `writer_brief` 写入 `output_path`，自查选择、阻力、后果与重复表达，单章回执保留 summary 和具体 self_check；不提交 candidate_events，事实由独立审稿重建。程序在提交时校验合成全文的实际篇幅；计数可选 `commands.measure`，完整提交校验和资源/时钟结算用只读 `commands.preflight`，补丁位置用 `commands.patch`。主控传递程序实际结算，不凭 Worker 的口算摘要指定后章前态。审稿保留必要语境与因果，逐条回应 `review_obligations` 并核对最可能违约的语段；新任务按 `review_contract` 核对时间和文风的反证，天数声明与结算一致，冲突判 fix，不按允许区间改 clock；pass 的 `findings=[]`。
4. Worker 按信封 `commands.submit` 提交（默认 `--brief --compact`）；按真实报错修正。单章写审以 `execution_policy` 为准，返回 `worker_boundary=finished` 后结束；不得主动取下章。首读解锁在同一审稿 task 内继续，由同一个审稿者完成两类检查。有 `chapter_session` 时按 `action=continue_chapter_batch` 直接处理响应 task，直到批次或角色结束；执笔与独立审稿不共用会话。有 `review_session` 时按 `action=continue_review` 连续处理同次独立复审的分块、材料和各范围综合判断；同版本正文只复用该 Worker 自己已读的内容，各范围仍分别举证。无连续标记的正式阶段成功后返回任务 ID 与短状态并结束。材料信封带 `preparation_session` 时，提交响应的 `action=continue_preparation` 允许同一 Worker 直接处理返回的同批次材料任务，直到批次结束或上下文不足；返回最后已提交任务 ID，不进入正式阶段。完整原记录可直接交付，详见 [材料准备](references/context-runtime.md)。
5. 主控等待 Worker 结束后执行 `advance --project "$PROJECT" --compact --after 最后已提交任务ID`；按返回 `action` 接续，直到已授权目标完成、作者暂停或出现无法自行解决的阻碍。复用、恢复与宿主降级按 [runtime](references/runtime.md) 执行。

## 资料索引

| 使用时机 | 读取资料 |
|---|---|
| 修改任务交付、证据、时间或宿主交接 | [交互架构](references/interaction-architecture-design.md) |
| 修改引擎、新增命令、调整守卫之前 | [架构契约](references/architecture.md) |
| 查看整体流程、持续派工与局部返修关系 | [流程图](references/workflow.md) |
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
