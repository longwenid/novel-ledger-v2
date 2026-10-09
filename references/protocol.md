# 任务与结果协议 v1

## 不变信封

next 返回 schema=novel-ledger-v2.task/1 及 task_id/stage/chapter/branch/input_hash/project/input_path/output_path。input_path/output_path/brief_path 是 PROJECT 相对 posix 路径，读写时与 project 拼接。输入存于数据库，重复 next 重建同一输入，修改文件不能改变任务。

任务文件在 `work/ch<四位章号>/<阶段>-<ID前8位>/`。派新任务时已完成目录只留最近 3 个，tidy 可清空；真源仍为 SQLite。

项目根目录只应出现引擎产物：`contract.json`、`novel.sqlite3`（及其 -wal/-shm）、`book.html` 与 `work/`；`export/`、`evaluation/` 按需生成，作者自建目录（如旧稿归档）互不干扰。旧版驱动或引擎遗留的散落文件（`novel.db`、`st*.err`、`tmp_context_*.json`、`workch*.json`、`work/next_task.json`、`work/plans_data.json`、`work/vol_data.json`、`work/_tmp_*`、`_review_tmp/`）由 `tidy` 一并清扫：只按精确名单与模式匹配，绝不触碰上述引擎与作者文件；`novel.db` 非空时保留并在返回的 `legacy_kept` 中上报。`doctor` 以 `legacy_litter` 字段报告未清理的遗留物。

结果写入 output_path，提交示例：

```json
{
  "schema": "novel-ledger-v2.result/1",
  "task_id": "复制本次任务 ID",
  "input_hash": "复制本次输入哈希",
  "data": {"prose": "本章完整正文"}
}
```

重复提交同一完整结果是幂等操作；同任务不同内容会被拒绝。

submit 可为顶层阶段字段补齐信封：须恰有一个 pending，或为最近任务的裸回执重试。只归一结构，不改内容，input_version 与重放哈希照常校验；显式错误 task_id 且无 pending 时报 task 错误。只使用本任务指定路径。

## 各阶段 data

volumes[].word_budget 为正整数汉字数，全填或全不填，合计落入 word_range；新书需分配，无预算旧项目兼容。volume_budget 为只读建议：baseline_han/written_han/deviation_han/suggested_remaining_han/closed 分别为参考量、验收量、实际减参考、建议余量和关闭状态。按工作分支统计，草稿不计；单卷偏差不是配额，全书区间仍有效。

### world_plan / volume_plan

分层模式先完成全书设计，再准备本卷；data 字段见 [分层设计协议](design-and-brief.md)。产物仅为未来提案，不能入事实账本。

### character_plan

人物模式在章计划后、执笔前按需派发。data 为 `{"profiles":[...]}`，恰好覆盖 payload.entities；完整字段见 [人物协议](characters.md)。它保存创作小传与声线解读，不直接改变事实。

### character_index

character_views=scoped 的新档案先派 character_index，data={"indexes":[...]}，字段见 [人物索引](characters.md)。未指定视图默认 full，旧 pending 不追加字段。

### plan

`chapters` 为从任务章号开始的连续 1～10 章数组，一般 5～10 章。每章：

- 必填 `number / volume / goal / conflict / change / entities`；entities 为已登记实体 ID，新增人物在正文 facts 阶段登记，再用于下一次细化。
- 可选 `scenes`：数组，每项为 `{"place":"已登记地点ID","present":["人物ID"]}`；未登记者改用 `{"label":"候选新场所","present":[]}`，不能把提案当事实。
- 可选 `topics / hooks / dependencies`；topics 与对白 knowledge_topics 共同限定需核验的认知，profiled 模式也遵守，未声明时保留全部该人物认知。漏选话题不等于人物未知；用 recall --state --entity ID --topic TOPIC 核对。dependencies 只指确需原文的已提交章节号。
- 可选 `major_turn: true`，此时 `alternatives` 至少两个不同字符串，`selection_reason` 说明为什么选当前方案。
- `volume_end: true` 触发卷审；末卷终章用 `book_end: true` 且必须同时 volume_end。

卷 ID 来自约定，不倒序，不在未标卷末处跨卷。

分层模式一批不能跨卷；另需 reader_experience、character_choices 和含 intent/conflict/turn 的 scenes，可选 expression_space。章计划还须 `sequence_refs`（推进本卷 sequence 的节拍序号数组，序号合法）与 `ending_shape`（“类别:说明”的收束形态，类别受控，可连续使用）。设计版本由控制器绑定，不能用提案覆盖已知事实。开新卷的 volume_plan 工作包含前卷设计交接面（prior_volume：climax/handoff/ending_setup）——设计是意图，carries_in 须按账本实际写成的前卷对齐。profiled 模式 scenes 可选 speakers（省略为 present 中人物），需要各发言者 dialogue_intents，静默场景明确空数组。

可选 `execution / rhythm / scene.dramatization` 的完整结构见 [章纲展开与节奏](design-and-brief.md)。有声明则验证完整类型与场景引用；旧章纲缺字段保持兼容。`contract.chapter_target_han` 为可选通常目标，`execution.target_han` 覆盖本章目标，均为正整数汉字数，不设置正文上限。低于通常目标的刻意短章需事先 `target_reason`。使用的计数范围为完整 prose（含标题）中的汉字，标点、数字及英文不计入。

执笔/返修包的 execution/rhythm 仅在 writer_brief 保存，plan_section_refs 给字段位置；库内计划不变，facts 与分段审稿按阶段提供完整计划。brief_path 为替代视图。

### draft / repair / redesign

首稿及 redesign 的 `prose` 为完整正文字符串，可含章标题。repair 和已有原稿的候选修订 draft 可提交 `base_hash/patches/repair_note`，避免整章重复；确无需改文的候选 draft 可用 `base_hash/reuse_original:true`。三种正文方式不能混用，完整字段与扩大上下文见 [局部修订](repairs.md)。整章 prose 模式的 repair 另需 `repair_note`，必须实际修改原稿；redesign 另需 `design_change` 说明设计改变，必须实际改文。仅 redesign 可选 `plan_update`，draft/repair 提交此字段会报错；它仅更新当前章 goal/conflict/change/entities/topics/hooks/dependencies/scenes/reader_experience/character_choices/expression_space/execution/rhythm，不能变更约定、卷序、终局标记或自行下调已定目标。

首次 submit 前 measure --result <output_path> 只读预检全文或补丁合成稿，返回 chapter_length。不足稿保留并派 repair，两轮仍不足转 redesign，再失败报告阻碍；超过目标不截断。facts/reader 与入库记录实际 chapter_length；有目标增 substance，有 rhythm 增 pacing，均须正文证据。

### facts / reader / *_review

共同字段：

```json
{
  "verdict": "pass",
  "checks": [{
    "id": "从 input.payload.checks 读取",
    "result": "pass",
    "reason": "充分说明核验事实、因果与阅读影响，避免重复",
    "evidence": [{"chapter": 1, "start": 0, "quote": "支持结论的精确连续原文，保留必要语境"}]
  }],
  "findings": []
}
```

要求的检查项各出现一次；不适用时 evidence=[] 并给 not_applicable 字符串理由，核心项不得绕过。pass 的 findings=[]。

fix 时可附 `repair_scope: "local"`，只对可定位、无需全章重排的问题使用；其余用 chapter 或省略。

发现格式：`{"severity":"quality", "impact":"读者受何影响", "goal":"修订要实现什么", "evidence":{...}}`。severity 为 blocker/quality/note，前两者必须给 fix。检查项 fix 也必须有具体发现，不能只有判词。

- facts 的 pass 另需 `events: []`、`summary`（完整覆盖本章核心变化，不设字符限额）。没有变化可空事件，但必须核对计划外重要变化。**返修/重设计后的 facts 复审**：payload 会附 `prior_events`（改动前该章完整已登记事件，包含自动续算项，空快照也有效）与 `prior_events_note`。仅全部本章证据的完整句语境未变时可进入 auto_carry；引文存留但否定或来源语境已变、或引用历史章原因证据时，须在 prior_dispositions 逐项明确 kept/dropped/replaced。kept 需要原样重报事件并核对新语境；dropped/replaced 说明实际变化。也可用该声明覆盖 auto_carry，不能重复提交自动续算项。历史原因证据保留所属章坐标。机械语境相等仍不证明跨句语义相同，须独立核验。旧 pending 未提供 auto_carry 时按全量去向对账。
- reader 另需 `first_read`，四个非空字段 `desire / obstacle / change / read_on_reason`，以实际首读感受及其依据为准，不设字符限额。先读正文再补读约定。
- 整体 `batch/volume/revision/book` 复核的 pass 另需 `memory: {"text":"保留必要因果、分歧与未决压力的提炼，不限篇幅", "sources":[证据对象]}`；逐章 `_chunk` 不需要 memory。memory 也接受纯字符串形式（来源以分段审稿回执为准）。
- 整体复核接收覆盖范围和此前分段审稿的实际结论与来源；超大材料先经扫描归并。正文疑点用定向分页的 recall 补读或 context_more 获取；证据必须来自本次范围内当前有效正文。覆盖哈希不是审读内容。
- 整体 volume_review（scope=volume，不含 volume_chunk）须查 volume_development，材料在同名 payload；要求见 [卷末复核](editorial.md)。旧 pending 保持原检查项和哈希。

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

## 按需扩展与成本观察

空间事件 `anchor/anchor_assert/anchor_change/place/place_change/link/link_change/placement/move/perceive`、address_responses 和地图查询见 [空间协议](spatial-continuity.md)。人物小传、character_development、对白意图见 [人物协议](characters.md)。event_spine、sequence_refs、设计失效与重规划见 [分层设计](design-and-brief.md)。按本任务 payload.checks 返回检查项，不写死旧版列表。

clock 可用 thread 维护独立支线日历；主线时间不因支线事件自动变化。场景 time_hint 的 D+N 以计划签发时的时钟为锚；有 day_span 的章结算须满足 `[锚+N_min,锚+N_max+1]`。这些是日程和引用校验，时间词存在不能证明推算语义正确。

contract.style 可设 voice/traits/sample/sample_close；无锚点时 approve 从试章取开头/结尾。config 可更新 style_anchor；reader 增 voice_match，整体查 repetition。traits 与 language 遵守 [声线规则](voices.md)，不改写样本引文。

review_chunk（1～5，默认1）调整全文分段粒度，不缩减覆盖。dependencies 原文只注入执笔，其他阶段给摘要/哈希和 recall 指引；reader 不载计划或自评。补丁与正文复用见 [repairs](repairs.md)。

reuse_original 仅省去正文重传；免逐章审稿还要求本章与全部前文版本未变、原 facts/reader verdict 为 pass、两次已验证审稿的持久上下文签名与当前依赖一致、正文哈希有效且事件可复算。作者约定、声线、相关人物/设计或事实依赖变化时进入正常审稿。没有完整历史读依赖集合，事件前值通过不足以证明后文语义安全。

正式审稿输入的 review_context_hash 冻结作者约定、声线、范围计划、正文/事件前缀及相关人物与 world/本卷设计版本，本地只计算签名，不向模型重复倾倒整套依赖。提交时依赖已变化报 review_context_stale；重新 next 在旧租约结束后派有效任务，旧结果不能改信封冒用。无关未来卷、人物和计划不使旧范围无故失效。旧无签名 pending 保持原 ID/input/hash，回执可接受并标记需补派核验，不临时补签当已验证。相关档案缺历史归因时保守复审；失效的旧卷在终章集中补核。

status/doctor.usage 返回 task_input_chars、accepted_receipt_chars、attempted_receipt_chars、preparation_input_chars、supplemental_read_chars、tasks。accepted 只计已接受结果，attempted 包括可识别任务的失败提交；不含系统提示、聊天或协议外读写。billed_tokens/session_handoffs 无宿主证据为 null，字符不换算费用。doctor 不验证宿主隔离或窗口，见 [runtime](runtime.md)。

## 错误与修正

非零退出码返回 {ok:false,code,message,details}。格式、引用或前态错误按原文修正结果，任务保持 pending；阅读缺陷走 repair/redesign，不只改判词。context_scan/context_reduce/context_more 见 [上下文协议](context-runtime.md)，信封仍为 /1。

`stale_task/input_version/lease` 表示结果不再属于有效任务，不能把旧结果的 ID/哈希改成新任务后重交；重新读任务和材料。无权限改约、核心设定无法调和、修订次数耗尽时，报告具体阻碍与可选处理。

核心项 coverage/continuity/read_on/character/change/language/substance/causality/character_arcs/payoffs/pacing/ending/length/core_promises/stable_facts/spatial/character_consistency/voice_evolution 不能使用 not_applicable 绕过证据；其余项不适用也必须提供非空字符串理由。
