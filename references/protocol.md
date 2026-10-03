# 任务与结果协议 v1

## 不变信封

`next` 返回 `schema=novel-ledger-v2.task/1`，以及 `task_id/stage/chapter/branch/input_hash/project/input_path/output_path`。所有路由（`input_path/output_path/brief_path`）是 **PROJECT 相对 posix 路径**（如 `work/ch0005/facts-ab12cd34/input.json`），`project` 字段给出拼接基准；宿主组合二者读写，不要期待绝对盘符路径。任务输入已经存于数据库，重复 next 会重建同一输入；不能修改输入文件来改变指令。

任务产物归集在 `work/ch<章号零填四位>/<阶段>-<任务ID前8位>/`；派发新任务时自动清理已完成任务的目录（保留最近 3 个排查用，`tidy` 命令全部清空）。输入与回执的真源始终是 SQLite，work 目录只是派生视图。

结果写入 output_path，提交示例：

```json
{
  "schema": "novel-ledger-v2.result/1",
  "task_id": "复制本次任务 ID",
  "input_hash": "复制本次输入哈希",
  "data": {"prose": "本章完整正文"}
}
```

外部 driver 必须另回传任务信封中的 `lease_owner`。fresh 模式还需要 `session: {"id": "宿主真实会话ID", "origin": "empty"}`。不能随机生成 ID 来假装独立会话。重复提交同一完整结果是幂等操作；同任务不同内容会被拒绝。

`submit` 端做**结构归一**：worker 把阶段字段直接写在顶层（缺 `schema/task_id/input_hash/data` 嵌套）时，只要当前恰有一个 pending 任务（或裸回执重试最近任务），会自动补齐信封并记录 `receipt_normalized` 日志；内容本身从不改写，`input_version` 与重放哈希护栏照常生效。显式但错误的 task_id 且无 pending 任务时仍按 `task` 报错。因此 worker 端不需要外部 normalize 脚本。

`work/<task_id>/` 保存任务文件。runner 的每次尝试另用 `attempt-<token>/`，只使用环境变量所指的输入输出，不能猜测旧 result.json 路径。

## 各阶段 data

v1.4.1 新书约定可在每个 `volumes[]` 项提供 `word_budget`（正整数汉字数）：全填或全不填，合计须落入 `word_range`。新书指南与默认模板要求准备分配；无预算的旧项目保持兼容。规划/整体复核输入及 status 的 `volume_budget` 是只读派生建议，字段 `baseline_han/written_han/deviation_han/suggested_remaining_han/closed` 分别表示参考总量、已验收量、实际减参考、建议剩余量和已结束状态。所有数值按当前工作分支计算，未验收草稿不计入，不能直接提交该视图改约。单卷偏差不是交稿门槛，全书验收区间不变。

### world_plan / volume_plan

分层模式先完成全书设计，再准备本卷；data 字段见 [分层设计协议](design-and-brief.md)。产物仅为未来提案，不能入事实账本。

### character_plan

人物模式在章计划后、执笔前按需派发。data 为 `{"profiles":[...]}`，恰好覆盖 payload.entities；完整字段见 [人物协议](characters.md)。它保存创作小传与声线解读，不直接改变事实。

### plan

`chapters` 为从任务章号开始的连续 1～10 章数组，一般 5～10 章。每章：

- 必填 `number / volume / goal / conflict / change / entities`；entities 为已登记实体 ID，新增人物在正文 facts 阶段登记，再用于下一次细化。
- 可选 `scenes`：数组，每项为 `{"place":"已登记地点ID","present":["人物ID"]}`；未登记者改用 `{"label":"候选新场所","present":[]}`，不能把提案当事实。
- 可选 `topics / hooks / dependencies`；dependencies 只能指已提交历史章；此处指章节号，不是事件 ID。
- 可选 `major_turn: true`，此时 `alternatives` 至少两个不同字符串，`selection_reason` 说明为什么选当前方案。
- `volume_end: true` 触发卷审；末卷终章用 `book_end: true` 且必须同时 volume_end。

卷 ID 来自创作约定，不能倒序或在未标卷末的位置跨卷。阶段变化由目标、冲突、人物决定，不额外制造必填场数。

分层模式一批不能跨卷；另需 reader_experience、character_choices 和含 intent/conflict/turn 的 scenes，可选 expression_space。章计划还须 `sequence_refs`（推进本卷 sequence 的节拍序号数组，序号合法）与 `ending_shape`（“类别:说明”的收束形态，类别受控且不与近三章重复）。设计版本由控制器绑定，不能用提案覆盖已知事实。开新卷的 volume_plan 工作包含前卷设计交接面（prior_volume：climax/handoff/ending_setup）——设计是意图，carries_in 须按账本实际写成的前卷对齐。profiled 模式 scenes 可选 speakers（省略为 present 中人物），需要各发言者 dialogue_intents，静默场景明确空数组。

### draft / repair / redesign

首稿及 redesign 的 `prose` 为完整正文字符串，可含章标题。repair 和已有原稿的候选修订 draft 可提交 `base_hash/patches/repair_note`，避免整章重复；确无需改文的候选 draft 可用 `base_hash/reuse_original:true`。三种正文方式不能混用，完整字段与扩大上下文见 [局部修订](repairs.md)。整章 prose 模式的 repair 另需 `repair_note`，必须实际修改原稿；redesign 另需 `design_change` 说明设计改变。仅 redesign 可选 `plan_update`，draft/repair 提交此字段会报错；它仅更新当前章 goal/conflict/change/entities/topics/hooks/dependencies/scenes/reader_experience/character_choices/expression_space，不能变更约定、卷序或终局标记。

### facts / reader / *_review

共同字段：

```json
{
  "verdict": "pass",
  "checks": [{
    "id": "从 input.payload.checks 读取",
    "result": "pass",
    "reason": "结合实际正文说明判断",
    "evidence": [{"chapter": 1, "start": 0, "quote": "精确连续原文"}]
  }],
  "findings": []
}
```

所有要求的检查项都必须出现一次。无相关情形时可以 `evidence: []`，并用 `not_applicable` 字符串具体说明；不要对核心项滥用不适用。

fix 时可附 `repair_scope: "local"`，只对可定位、无需全章重排的问题使用；其余用 chapter 或省略。

发现格式：`{"severity":"quality", "impact":"读者受何影响", "goal":"修订要实现什么", "evidence":{...}}`。severity 为 blocker/quality/note，前两者必须给 fix。检查项 fix 也必须有具体发现，不能只有判词。

- facts 的 pass 另需 `events: []`、`summary`（1～1600 字符）。没有变化可空事件，但必须核对计划外重要变化。**返修/重设计后的 facts 复审**：payload 会附 `prior_events`（改动前该章已登记事件，来源 repair 回执或修订分支祖先）与 `prior_events_note`；pass 必须附 `prior_dispositions`，对每条前次事件逐项交代 `{index, disposition: kept|dropped|replaced, reason}`——kept 的事件必须原样出现在新 events 中（证据 start 漂移会被自动重定位），dropped/replaced 必须说明正文如何变化。禁止只登记本次改动而静默丢弃未受影响的事件，否则报 `event_accounting`。
- reader 另需 `first_read`，四个非空字段 `desire / obstacle / change / read_on_reason`。先读正文再补读约定。
- 整体 `batch/volume/revision/book` 复核的 pass 另需 `memory: {"text":"1～2400 字符的提炼", "sources":[证据对象]}`；逐章 `_chunk` 不需要 memory。memory 也接受纯字符串形式（来源以分段审稿回执为准）。
- 整体复核接收覆盖范围和此前分段审稿的实际结论与来源；超大材料先经扫描归并。正文疑点用定向分页的 recall 补读或 context_more 获取；证据必须来自本次范围内当前有效正文。覆盖哈希不是审读内容。

## 事件

每项必须有 `op` 与 `evidence: {chapter,start,quote}`，start 是 Python 字符位置，包含标题和换行，不是 UTF-8 字节位置。程序精确匹配原文；生产证据时可通过 `prose.index(quote)` 获取位置。start 漂移（如补丁使后文整体位移）时，若 quote 在本章正文中**唯一**出现，submit 会自动重定位并写回正确 start；引文不存在或出现多次仍报 `evidence` 错误，不能靠模糊匹配过关。

| op | 必填业务字段 | 含义 |
|---|---|---|
| entity | entity:{id,name,kind,aliases?} | 注册稳定实体，称呼不能与已登记者冲突 |
| alias | entity,alias | 为同一实体增加唯一称呼 |
| state | entity,key,before,after | 前态校验；关系、伤势、能力等动态状态；已有地图站位须用专用事件 |
| knowledge | entity,topic,before,claim,stance,source | before 是旧 claim 或 null；stance 为 knows/believes/suspects/disproved |
| resource | asset,unit,from,to,quantity,reason，以及存在两侧的 from_before/to_before | 同单位资源转移；外部获得/消耗一端可 null |
| hook | id,before,status；首次 text,due | status 为 open/paid/closed；due 为最迟章号；延期和 closed 需要 reason |
| clock | before,after；可选 thread | 世界当前时钟，非负整数单调；单位由创作约定明确(clock_unit)。clock 事件的证据必须引用本章真实时间语句(含 日/夜/晨/时 等时间词)；带 thread 的时钟为并行线独立日历，不影响全局时钟 |

事件可附 `refs`（实体 ID 数组）和 `topics`（话题数组）辅助召回。resource 数量必须为十进制字符串，最多 120 位有效数字和绝对值 120 的指数，超过就明确报错，不能悄悄取整。

所有变化以前态为条件，同一章多个事件按顺序应用。state 的 `before:null` 表示尚未登记此属性，不代表历史上一定不存在。复活（alive false→true）额外要求 exception_reason。不认识的事件类型被拒绝。

v1.1 新增 `anchor/anchor_assert/anchor_change/place/place_change/link/link_change/placement/move/perceive`；字段、约定初始化方式、`address_responses` 与 `map` 用法见 [空间协议](spatial-continuity.md)。任务/结果信封版本不变，审稿按实际 checks 返回；新增事件需要 v1.1+ 工具读取。

v1.2 新增 world_plan/volume_plan 阶段与 writer_brief 场景任务书；任务可额外返回 brief_path。信封版本不变，适配器应按 stage 动态派发，不把阶段名单写死。设计查询与失效重规划见 [分层设计协议](design-and-brief.md)。

v1.3 新增 character_plan、character 查询/重审、character_context 以及 character_development 事件。facts/reader/整体复核分别动态增加 character_consistency/dialogue_identity/voice_evolution；按本次 payload.checks 回执。详见 [人物协议](characters.md)。

v1.4 增加版本绑定 patches、局部 repair 工作包、candidate 分段检索与原稿 reuse_original。信封版本不变；补丁合成后仍重新完整审稿，见 [局部修订](repairs.md)。

v1.6 增加 `style_anchor` 文风治理与用量观测：约定可带 `style: {"traits":[...], "sample":"...", "sample_close":"..."}`；未给时 `approve` 自动快照试章开头/结尾。有锚点时执笔类任务与 reader 工作包携带锚点，reader 检查项增加 `voice_match`（核心项，必须有证据），整体复核合成任务增加 `repetition`。旧项目用 `config --key style_anchor --value '<JSON>'` 补设。工作包材料按需选择：历史事件有界纳入（每实体至少最新一条，总计量约 6000 字符，超出计入 omitted 可定向 recall），提炼记忆最多带 2 条。`status`/`doctor` 返回 `usage`（tasks、session_handoffs、input/result/read 字符累计），用于核对与校准消耗。

v1.7 任务路由全部改为 PROJECT 相对 posix 路径（信封附 `project` 基准；`submit --result` 相对路径按 `--project` 解析），产物归集 `work/ch<章号>/<阶段>-<短ID>/`，完成目录自动清理保留最近 3 个并新增 `tidy` 命令。`style_anchor` 增加 `voice` 声线名字段（内置六声线见 [声线手册](voices.md)，开书时提炼为 traits）。

v1.8 分层设计吸收卷纲与事件脊柱：`world_plan` 必须提交四层 `event_spine`（main/subplot/timeline/tension，触点 `{"id":"sp:...","span","event"}`，ID 全书唯一，幕级粒度）；`volume_plan` 必须提交卷纲三件套 `carries_in/climax/handoff`；分层模式下章计划的 `major_turn` 必须以 `spine_refs` 引用其推进的脊柱触点（可选字段，引用不存在的触点会被拒绝）。脊柱less 的旧世界设计保持有效（豁免），重新提交时才要求补齐；字段详见 [分层设计协议](design-and-brief.md)。`html` 视图渲染脊柱表与卷纲。

v1.9 返修事实自动续算与审稿粒度：facts 任务的 payload 附 `auto_carry`——证据仍唯一命中新正文的前序事件由**引擎自动续算并重锚定**，回执 `events` 只提交新增/变更事件（重复提交已续算事件会被拒）；证据断裂的前序事件须在 `prior_dispositions` 中逐条声明 `dropped/replaced` 及理由，也可用同样声明覆盖自动续算。覆盖范围不变：审稿检查仍覆盖整章。`config --key review_chunk`（1~5，默认 1）调整 batch/volume/book 复核的分段粒度，逐章版本绑定与全文覆盖保证不变，只减少任务数。

v1.10 时间治理中间层：卷纲/脊柱管顺序与因果，本版补上日级数量层——(1) `clock` 事件的证据必须引用本章真实时间语句（`time_evidence` 拒绝凭空声明），把时钟从"声明"变成"推算"；(2) 章计划的场景 `time_hint` 可写 `D+N`（以计划签发时的时钟为锚），facts 结算时章末时钟须落在 `[锚+N_min, 锚+N_max+1]` 区间（`time_schedule`；旧计划无锚则豁免）；(3) `clock` 事件可选 `thread` 字段登记并行线独立日历（全局时钟是主线，不被支线事件推动），工作包 `state.clock_threads` 让执笔与审稿看到各线进度。同步点（两线会合）用一次双登记显式表达。

v1.11 纪律随包下发与自助报错：每个工作包内置 `discipline` 块（review 取值白名单、账本前值照抄、move 引用已登记 links、resource 只记已入账资产、hooks 延期上限、clock 证据时间词、patches 锚定），worker/subagent 先读它，不靠报错学规则。错误信息可自助：resource 期初不符会报出当前库存，未入账资产的**非零**流水会被点名（`to_before:'0'` 的初次收款仍合法）；move 的 route_unknown 列出该段已登记连接 ID；hook_extension 报出已延期次数与出路。`status` 返回 `skill_version/db_version`，`doctor` 返回 `skill_root`；派发约定 `NOVEL_LEDGER_SKILL_ROOT` 固定安装路径。

v1.12 依赖章按环节注入：`dependencies` 的历史章全文只注入 draft/repair/redesign（执笔需要原文）；facts/character_plan 等审稿与档案环节改为 `dependent_chapter`（章摘要 + 版本哈希 + 定向 recall 指引），前情状态由 `state` 承担、疑点按 recall 分页补读——同一章流水线不再重复装载同一批全文，serial 交接频率显著下降。reader 的 `discipline` 只保留 review 行（盲读不关心执笔与账本规则）。覆盖核验不变：facts 的 `coverage` 仍以账本与 `recall` 为准，未选入不表示不存在。

v1.13 修订传播定界：**未改章免复验**。修订分支内提交 `reuse_original` 且正文与原章逐字相同、原事件在新上游状态下全部可复算（前值校验通过）时，引擎直接提交该章（`fast_committed`），不再派发 facts/reader——逐字相同的事件与已过审的原章可证等价；上游变化破坏前置条件时自动回退为正常 facts 审稿。配套指引：合成复核的发现应标 `repair_route`——表达类（重复/收束/节奏/声线）标 `"patch"` 并指向可定位单章证据，修订时只定点改被点名章、其余章原文复用；事实/因果/认知/时间类标 `"revise"`，从最早受影响章修订。此前"最早证据章"一刀切开分支导致表达类发现也级联全后缀重验（如收束同质化令 6-10 章全部重审），现在级联成本只剩真正改动的章。

## 错误与修正

非零退出码返回 `{ok:false,code,message,details}`。格式、引用或前态错误：读实际错误和原文后修正候选结果，原任务保持 pending。阅读缺陷：按 repair/redesign 任务修正文，不能只改审稿结论。v1.5 的 context_scan/context_reduce、context_more 回执、容量计量与 await_session 交接详见 [上下文协议](context-runtime.md)，信封版本仍为 /1。context_capacity 表示不可拆分核心过大，context_unresolved 表示无法安全提炼；不能直接原样重试。context_overflow 仅保留在显式 legacy 模式。

`stale_task/input_version/lease` 表示结果不再属于有效任务，不能把旧结果的 ID/哈希改成新任务后重交；重新读任务和材料。无权限改约、核心设定无法调和、修订次数耗尽时，报告具体阻碍与可选处理。

核心项 coverage/continuity/read_on/character/change/language/causality/character_arcs/payoffs/pacing/ending/length/core_promises/stable_facts/spatial/character_consistency/voice_evolution 不能使用 not_applicable 绕过证据；其余项不适用也必须提供非空字符串理由。
