# 任务与结果协议 v1
何时读：需要任务信封、结果回执、事件 op、连续会话或错误码的精确定义时。

## 任务信封与输入交付

next 返回 schema=novel-ledger-v2.task/1 及 task_id/stage/chapter/branch/input_hash/project/input_path/output_path。input_path/output_path/brief_path 是 PROJECT 相对 posix 路径，读写时与 project 拼接。输入存于数据库，重复 next 重建同一输入，修改文件不能改变任务。

input.json 是多行缩进的交付视图（真源在库，逐行读取即可）；context_scan 单页按 payload.prose_path/prose_hash，跨文档批次按 payload.pages 中各项 prose_path/prose_hash 文件交付，evidence.start 为文档绝对字符位置，不是页内位置。

正文按内容寻址文件交付：包内 `prose_path`/`prose_hash`（chapter_review/repair 的 payload）与 `prose_files`（分段审稿每章一项 {chapter,prose_path,prose_hash}）、`selected[].prose_path`（依赖章原文），文件在 `work/prose/<哈希前16>.txt`，字节与账本库存正文逐字一致，`{chapter,start,quote}` 坐标以文件内容为准；文件缺失或损坏时重新 next 即可重建（task 信封的 prose_files 字段列出已写文件）。

`context_delivery.mode=exact-records/1` 表示所列字段逐字保留冻结来源，`sources` 只是版本目录。完整记录仍在对应字段，需实际阅读核对；与模型准备结论及质量通过无关。路径选择与中断恢复见 [上下文协议](context-runtime.md)。

## 结果格式与提交

审稿及候选事件的重复原文可用 `data.evidence_catalog` 无损去重：例如 `{"e1":{"chapter":12,"start":34,"quote":"完整原文语境"}}`，相应证据项写 `{"ref":"e1"}`。直接填写完整证据仍兼容；一个编号只代表本回执的一条原文，不能自动为所有检查选证据。

引用只在既有证据位置展开：顶层或 facts/reader 的 checks/findings/obligations 中 evidence、challenge.evidence、memory.sources，以及 facts.events/candidate_events 中 evidence/cause_evidence/change_evidence。事件 before/after 等事实值不展开。引用只能含 ref，目录条目只能含 chapter/start/quote；未知编号、混入字段或嵌套引用拒绝。展开后照常核对正文版本、原文坐标、章节、具体理由、全部检查与约定义务，不补填判断或 pass。库中同时保留原始 wire 回执和展开后的审计回执；幂等重提须使用原始提交内容，不能拿展开的审计副本替换原文件重提。

结果写入 output_path（建议多行缩进书写，便于主控与作者复查；submit 只程序化解析，格式不影响提交）：

```json
{
  "schema": "novel-ledger-v2.result/1",
  "task_id": "复制本次任务 ID",
  "input_hash": "复制本次输入哈希",
  "data": {"prose": "本章完整正文"}
}
```

重复提交同一完整结果是幂等操作；同任务不同内容会被拒绝。

submit 可为顶层阶段字段补齐信封：本任务指定目录内的结果绑定该目录的原任务，不因下一任务已派发而改投。目录外的裸回执首次尝试按唯一 pending 绑定，后续相同内容沿用持久绑定；新任务应使用自己的指定目录或完整 task_id/input_hash。已知 task_id 的缺失字段从该任务补齐，不从当前 pending 借用。只归一结构，不改内容，input_version、取消状态与重放哈希照常校验；旧任务的改稿重提拒绝，显式错误 task_id 且无绑定或 pending 时报 task 错误。preflight 不写绑定或其他运行状态。

## 主控交接（advance）

默认主控入口 `advance` 返回 `schema=novel-ledger-v2.handoff/1`、`action`、短 `status`，以及 `task` 信封或 `transition`；同时写恢复点。

`advance/status/first-read/submit/preflight --compact` 返回 `response_view=compact/1`：状态省略预算、用量和详细恢复信息，任务省略已保存在完整 task.json 的模板与说明，以 task_path 交付；交接另给 controller_path。Worker 必须先从 task_path 恢复完整信封，再执行当前阶段 commands，包括连续会话和首读解锁后的响应。任务 ID/hash、会话绑定、实际阶段不变；完整 CLI 响应仍为默认。settlement 保留全部资源、时钟与状态哈希，steps 改为 step_count；numeric_correction 的 patches 改为 patch_count，完整修正仍保存在审计记录。

启动或恢复可不带 after；宿主 Worker 结束后带 `--after 任务ID`，引擎在派工事务内核对该任务已真实提交且为当前分支最近完成任务。未提交返回 `wait_for_submit`，未知 ID 报 `handoff_task`，过时、取消或异分支返回 `resync`，均不生成下一任务；重复有效交接恢复同一 pending。

`task_ready` 表示可以按信封派工，其他 action 包括 `await_author`、`revision_promoted`、`paused`、`completed`。主控负责确认宿主执行已结束；交接不代替首章作者批准或审稿校验。

`status.recovery` 区分工作稿与正式稿位置，并保留未完成修订链；target 不等于已完成卷次，传播范围结束时停留在 revision_boundary。见 [运行与恢复](runtime.md)。

## 连续会话

### 章节批次 chapter_session

`chapter_session={protocol:"chapter-batch/1",batch:ID,role:writer|reviewer,first:N,last:M}` 绑定最多五章与单一角色。生成的 submit 命令带 `--continue-chapter-batch`；与其他连续处理选项互斥，只允许标记任务。响应 `schema=novel-ledger-v2.chapter-batch/1`，含刚提交 task_id/session；`continue_chapter_batch` 含同批同角色下一 task，`chapter_batch_done` 交回主控，其他 action 依暂停/过时回调边界处理。重复提交恢复同一 pending，不跨批次、角色或作者边界。主控 after 使用最后提交的 ID。

批内写作/返修的 data 在原契约外必含 `summary`、`candidate_events` 数组和非空具体 `self_check`。临时事件按正式事件的坐标与前态校验，但仅保存 drafts；input 的 state/continuity 已叠加前章候选变化，`candidate_ledger` 标明未审前缀与来源。带当前 `--task` 的 recall --state / --chapter 可读候选前态与前章原文，不带 task 时始终读正式账本；普通事件检索仍查正式事件，候选事件完整列在 candidate_ledger。审稿者不读取执笔自评或候选事件提案，独立登记正式事件。
返修冻结输入有 `payload.candidate_events_snapshot` 时，可选择 `reuse_candidate_events:true` 代替 candidate_events。程序仅续算全部本章证据的完整句语境未变的事件，并重定位、重验前态；其他情况完整重报，不同时使用两种字段。

批内 input.json 为冻结输入的拆分交付视图：`shared_materials` 各字段含 `{path,hash,encoding:"json"}`，完整值在 PROJECT/work/shared/<哈希>.json。哈希按标准 dump 的逻辑值计算，真源仍在任务 input；恢复会重建缺失或损坏文件。guide 也可从该目录取得。首次接手须阅读全部所需来源，同一 Worker 只复用已读且未变的版本；新 Worker 不沿用别人的已读判断。

人物、设计和较大的 state/continuity 字段另可含 `parts:[{pointer:[],path,hash,encoding},…]`，作为整文件的等价读法：pointer=[] 的完整对象为基础，其余完整记录按 pointer 的逐层键路径填回。人物档案与发展按实体分别寻址，设计按 world/volume 分片，账本与连续性按完整命名集合分片；跨章只有变化集合需要重读，未变记录仍须是当前 Worker 自己已读的版本。小型 state/continuity 直接交付，减少文件和目录开销；该路径选择不删减内容。首次读取选全部分片或完整字段文件之一，不两者重复读、不漏任一分片。拼回值与字段 hash 对应的冻结完整值一致，元数据也保留；新 Worker 或上下文不足须完整恢复必要资料。
`selected` 的历史事件通过 `material` 引用完整原记录；候选前缀通过 `events_material` 引用完整事件数组。每份记录独立寻址，未变事件不会因窗口变化而重新打包；语义判断与审稿检查仍逐章执行。旧库内冻结输入保持不变，交付视图可恢复重建。

带冻结正文的 chapter_review 信封携带 `payload.numeric_correction`（`numeric-patch/1` 能力声明，只含基稿哈希与约束，不公开候选事件或执笔自评）。综合审稿 data 可选同名对象，字段恰为 base_hash/patches/repair_note；每个补丁仅替换完整简单数字。两类检查及约定义务须针对合成稿全部通过，原首读保持不变；批次审稿的事件效果及章末账本状态须与原候选一致，单章/修订审稿的事件按合成稿举证、结算以本回执为准。正式章节留存原稿/改稿哈希及补丁审计记录；详见 [局部修订](repairs.md)。

### 材料准备 preparation_session

新材料信封的 `preparation_session={protocol:"same-bundle/1",bundle:ID}` 允许生成的提交命令使用 `--continue-preparation`。正常提交校验成功后响应 `schema=novel-ledger-v2.preparation/1`、`accepted=true`、刚提交的 `task_id` 与 `bundle`；`action=continue_preparation` 时含同批次下一 `task`，否则含结束状态或 transition。重复提交恢复同一 pending，过时回调不新派工；该入口不创建执笔、审稿、另一批次或执行作者边界。无标记的旧材料任务和正式任务拒绝此选项。主控用最后已提交 ID 交接。材料提交不刷新整本 HTML。

### 跨章复审 review_session

`review_session={protocol:"review-run/1",id:依赖指纹,branch,through,role:"reviewer"}` 绑定正式版本下的独立跨章复审。submit 的 `--continue-review` 与章节/材料连续选项互斥，只有带该标记的 batch/volume/revision/book 复审和继承标记的准备任务可使用。响应 `schema=novel-ledger-v2.review-run/1` 含 accepted/task_id/session；continue_review 提供下一 task，review_done 或 paused/resync 等交回主控。最后提交 ID 用于主控 advance --after。旧信封不补写标记或改变 input_hash。

同一会话可跨复审 scope，不能跨正式版本或进入执笔/合并/完本。各 scope 保留独立检查、正文证据、版本签名和综合 memory；不自动复用文学 pass。新复审输入的公共字段也可用 shared_materials 引用完整 JSON；恢复重建缺失或损坏文件。Worker 仅复用本次自己已读且哈希未变的资料，新 Worker 完整读取必要来源。材料准备继承 review_session 时，生成命令以 continue-review 为准，不使用 continue-preparation 跳过正式复审交接。

## 文件与目录

任务文件在 `work/ch<四位章号>/<阶段>-<ID前8位>/`。派新任务时已完成目录只留最近 3 个，tidy 可清空；真源仍为 SQLite。

项目根目录只应出现引擎产物：`contract.json`、`novel.sqlite3`（及其 -wal/-shm）、`book.html` 与 `work/`；`export/`、`evaluation/` 按需生成，作者自建目录（如旧稿归档）互不干扰。

旧版驱动或引擎遗留的散落文件（`novel.db`、`st*.err`、`tmp_context_*.json`、`workch*.json`、`work/next_task.json`、`work/plans_data.json`、`work/vol_data.json`、`work/_tmp_*`、`_review_tmp/`）由 `tidy` 一并清扫：只按精确名单与模式匹配，绝不触碰上述引擎与作者文件；`novel.db` 非空时保留并在返回的 `legacy_kept` 中上报。`doctor` 以 `legacy_litter` 字段报告未清理的遗留物。

## 各阶段 data

### 运行模式与综合审稿 chapter_review

统一使用 chapter_review，writer_batch 默认 10，review_chunk 默认 3。`config --key writer_batch --value N`（N 为 1～10）只能在没有 pending 时修改。writer_batch 是主控复用执笔会话的章数上限，单次任务仍提交一章。本版本数据库 schema=novel-ledger-v2/2，不兼容旧项目，不提供分阶段审稿模式或迁移。

新 chapter_review 冻结 `review_protocol=sealed-review/1`。信封初始 `delivery_phase=first_read`，只有 first_read_path/first_read_hash、首读模板和命令；input_path 尚未生成。审稿者只读当前正文，将 first_read_template_path 填写到 first_read_result_path，执行 commands.first_read。

首读文件为 `{task_id,input_hash,prose_hash,first_read:{desire,obstacle,change,read_on_reason}}`，四项均非空文字。成功后按 task.task_path 读取已解锁完整信封，取得 input_path/template_path/event_contract_path。正式 reader 可按模板提交 `first_read_ref:本任务持久首读哈希`，或原样保留 `first_read` 全文，二者只能选一。首读全文仍在 first_read_receipt_path，恢复或上下文未保留原体验时须读取；只复用自己实际读过的当前章首读。引用先核对本任务 input_hash/prose_hash 与记录完整性，再恢复原首读供原有校验和审计，不生成或改写体验，不补填其他审稿项。缺首读记录、跨任务/版本引用、同时给全文均拒绝；旧无持久门禁任务仍交原 first_read 全文。原始 wire 回执保存与幂等规则同证据目录。相同首读重试幂等，改写被拒；版本变化取新任务，不改旧记录。

未带该协议的已有 pending 沿用冻结格式。同版本正文不重复加载。input.payload.facts_checks 与 reader_checks 分别列出两类必查项，事实输入同时提供账本、人物、连续性及前序事件。

批次首读模板额外含 `first_reads:[{chapter,prose_hash,first_read:{desire,obstacle,change,read_on_reason}}]`，须覆盖 first_read_files 全部章节；顶层 first_read 对应当前章。只读整批正文后执行一次 first-read；后续章任务直接绑定同一批次已冻结的对应首读，仍校验当前 input/prose 版本、作者约定、证据与前态。前章返修/正式事件改变或依赖失效时，新批次重新首读。

结果 data={facts:{verdict,checks,findings,events,summary,...},reader:{verdict,checks,findings,first_read,...}}，两类子回执格式见下面审稿字段。

新协议另要求 facts/reader.obligations 恰好覆盖 review_obligations 对应类别，每项为 `{id,result,reason,evidence:[正文引用],challenge:{disposition:satisfied|violated,reason,evidence:正文引用}}`。violated 必须 result=fix、对应检查 fix、类别 verdict=fix，并有标记 obligation_id 的 quality/blocker finding；模板没有预填 pass。

facts 的 pass 必须提供 events/summary，reader 必须提供 first_read；两类分别逐项验证。两类 pass 且事件语义校验通过时在同一事务入库；任一 fix 合并两类发现进入一次返修，不写事实账本。缺失检查、无效证据或过期输入拒绝提交，原 pending 与候选稿保留。正文变化后重新执行两类检查。

正式审稿输入的 review_context_hash 冻结作者约定、声线、范围计划、正文/事件前缀及相关人物与 world/本卷设计版本，本地只计算签名，不向模型重复倾倒整套依赖。提交时依赖已变化报 review_context_stale；重新 next 在旧租约结束后派有效任务，旧结果不能改信封冒用。无关未来卷、人物和计划不使旧范围无故失效。旧无签名 pending 保持原 ID/input/hash，回执可接受并标记需补派核验，不临时补签当已验证。相关档案缺历史归因时保守复审；失效的旧卷在终章集中补核。

### 卷预算

volumes[].word_budget 为正整数汉字数，全填或全不填，合计落入 word_range；新书需分配，未配置预算仍审查实际展开。volume_budget 为只读建议：baseline_han/written_han/deviation_han/suggested_remaining_han/closed 分别为参考量、验收量、实际减参考、建议余量和关闭状态。按工作分支统计，草稿不计；单卷偏差不是配额，全书区间仍有效。

### world_plan / volume_plan

分层模式先完成全书设计，再准备本卷；data 字段见 [分层设计协议](design-and-brief.md)。产物仅为未来提案，不能入事实账本。

### character_plan

人物模式在章计划后、执笔前按需派发。data 为 `{"profiles":[...]}`，恰好覆盖 payload.entities；完整字段见 [人物协议](characters.md)。它保存创作小传与声线解读，不直接改变事实。

### character_index

character_views=scoped 的新档案先派 character_index，data={"indexes":[...]}，字段见 [人物索引](characters.md)。未指定视图默认 full，旧 pending 不追加字段。

### plan

`chapters` 为从任务章号开始的连续 1～10 章数组，一般 5～6 章。每章：

- 必填 `number / volume / goal / conflict / change / entities`；entities 为已登记实体 ID，新增人物在综合审稿的 facts 子回执中登记，再用于下一次细化。
- 可选 `scenes`：数组，每项为 `{"place":"已登记地点ID","present":["人物ID"]}`；未登记者改用 `{"label":"候选新场所","present":[]}`，不能把提案当事实。
- 可选 `topics / hooks / dependencies`；topics 与对白 knowledge_topics 共同限定需核验的认知，profiled 模式也遵守，未声明时保留全部该人物认知。漏选话题不等于人物未知；用 recall --state --entity ID --topic TOPIC 核对。dependencies 只指确需原文的已提交章节号。
- 可选 `major_turn: true`，此时 `alternatives` 至少两个不同字符串，`selection_reason` 说明为什么选当前方案。
- `volume_end: true` 触发卷审；末卷终章用 `book_end: true` 且必须同时 volume_end。

卷 ID 来自约定，不倒序，不在未标卷末处跨卷。

分层模式一批不能跨卷；另需 reader_experience、character_choices 和含 intent/conflict/turn 的 scenes，可选 expression_space。章计划还须 `sequence_refs`（推进本卷 sequence 的节拍序号数组，序号合法）与 `ending_shape`（“类别:说明”的收束形态，类别受控，可连续使用）。设计版本由控制器绑定，不能用提案覆盖已知事实。

开新卷的 volume_plan 工作包含前卷设计交接面（prior_volume：climax/handoff/ending_setup）——设计是意图，carries_in 须按账本实际写成的前卷对齐。profiled 模式 scenes 可选 speakers（省略为 present 中人物），需要各发言者 dialogue_intents，静默场景明确空数组。

可选 `execution / rhythm / scene.dramatization` 的完整结构见 [章纲展开与节奏](design-and-brief.md)。有声明则验证完整类型与场景引用；这些字段未声明时不执行对应结构检查。`contract.chapter_target_han` 为可选通常目标，`execution.target_han` 覆盖本章目标，均为正整数汉字数，不设置正文上限。低于通常目标的刻意短章需事先 `target_reason`。使用的计数范围为完整 prose（含标题）中的汉字，标点、数字及英文不计入。

执笔/返修包的 plan 视图不再重复 writer_brief 已承载的创作字段（reader_experience/goal/conflict/change/ending_shape/character_choices/scenes/expression_space/execution/rhythm）；库内计划不变，chapter_review 与分段审稿提供完整计划。brief_path 为替代视图。包内 guide 单节按序给出本阶段提交契约、任务指令与适用纪律；discipline 不再整册携带。

### draft / repair / redesign

首稿及 redesign 的 `prose` 为完整正文字符串，可含章标题。repair 和已有原稿的候选修订 draft 可提交 `base_hash/patches/repair_note`，避免整章重复；确无需改文的候选 draft 可用 `base_hash/reuse_original:true`。三种正文方式不能混用，完整字段与扩大上下文见 [局部修订](repairs.md)。

整章 prose 模式的 repair 另需 `repair_note`，必须实际修改原稿；redesign 另需 `design_change` 说明设计改变，必须实际改文。

仅 redesign 可选 `plan_update`，draft/repair 提交此字段会报错；它仅更新当前章 goal/conflict/change/entities/topics/hooks/dependencies/scenes/reader_experience/character_choices/expression_space/execution/rhythm，不能变更约定、卷序、终局标记或自行下调已定目标。

reuse_original 仅省去正文重传；免逐章审稿还要求本章与全部前文版本未变、原 facts/reader verdict 为 pass、两次已验证审稿的持久上下文签名与当前依赖一致、正文哈希有效且事件可复算。作者约定、声线、相关人物/设计或事实依赖变化时进入正常审稿。没有完整历史读依赖集合，事件前值通过不足以证明后文语义安全。

submit 自动校验全文或补丁合成稿的实际篇幅。measure --result <output_path> 为可选只读预检，返回 chapter_length。不足稿保留并派 repair，两轮仍不足转 redesign，再失败报告阻碍；超过目标不截断。综合审稿与入库记录实际 chapter_length；有目标增 substance，有 rhythm 增 pacing，均须正文证据。

### facts / reader 子回执与跨章复审的共同字段

共同字段：

```json
{
  "verdict": "pass",
  "checks": [{
    "id": "子回执从 payload.facts_checks 或 reader_checks 读取；跨章复审从 payload.checks 读取",
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

- facts 的 pass 另需 `events: []`、`summary`（完整覆盖本章核心变化，不设字符限额）。没有变化可空事件，但必须核对计划外重要变化。
- **返修/重设计后的 facts 复审**：payload 会附 `prior_events`（改动前该章完整已登记事件，包含自动续算项，空快照也有效）与 `prior_events_note`。
- 仅全部本章证据的完整句语境未变时可进入 auto_carry；引文存留但否定或来源语境已变、或引用历史章原因证据时，须在 prior_dispositions 逐项明确 kept/dropped/replaced。kept 需要原样重报事件并核对新语境；dropped/replaced 说明实际变化。也可用该声明覆盖 auto_carry，不能重复提交自动续算项。
- 历史原因证据保留所属章坐标。机械语境相等仍不证明跨句语义相同，须独立核验。综合审稿提供 auto_carry，前序事件去向必须按本次冻结输入核验。
- reader 另需 `first_read`，四个非空字段 `desire / obstacle / change / read_on_reason`，以实际首读感受及其依据为准，不设字符限额。先读正文再补读约定。
- 整体 `batch/volume/revision/book` 复核的 pass 另需 `memory: {"text":"保留必要因果、分歧与未决压力的提炼，不限篇幅", "sources":[证据对象]}`；逐章 `_chunk` 不需要 memory。memory 也接受纯字符串形式（来源以分段审稿回执为准）。
- 整体复核接收覆盖范围和此前分段审稿的实际结论与来源；超大材料先经扫描归并。正文疑点用定向分页的 recall 补读或 context_more 获取；证据必须来自本次范围内当前有效正文。覆盖哈希不是审读内容。
- 整体 volume_review（scope=volume，不含 volume_chunk）须查 volume_development，材料在同名 payload；要求见 [卷末复核](editorial.md)。旧 pending 保持原检查项和哈希。
- 新整体 revision_review（scope=revision）的 revision_intent 需要正文证据，核对全部父子目标；旧 pending 保留冻结检查项。新修订任务携带 revision_context；旧冻结任务不改变 input_hash，在首读门禁之后由信封补交修订目标。

## 事件

每项必须有 `op` 与 `evidence: {chapter,start,quote}`，start 是 Python 字符位置，包含标题和换行，不是 UTF-8 字节位置。程序精确匹配原文；生产证据时用信封 `commands.evidence` 或 `evidence --task TASK --quote "精确原文"` 定位；多章/文档可加 --chapter/--document，重复引文需补足语境，不能任取第一次。start 漂移（如补丁使后文整体位移）时，若 quote 在本章正文中**唯一**出现，submit 会自动重定位并写回正确 start；引文不存在或出现多次仍报 `evidence` 错误，不能靠模糊匹配过关。

| op | 必填业务字段 | 含义 |
|---|---|---|
| entity | entity:{id,name,kind,aliases?} | 注册稳定实体，称呼不能与已登记者冲突 |
| alias | entity,alias | 为同一实体增加唯一称呼 |
| state | entity,key,before,after | 前态校验；关系、伤势、能力等动态状态；已有地图站位须用专用事件 |
| relation | from,to,key,before,after | 有向关系边（from 为视角方）；键不含 : 或 >；两端实体须已注册；前态校验同 state |
| knowledge | entity,topic,before,claim,stance,source | before 是旧 claim 或 null；stance 为 knows/believes/suspects/disproved |
| resource | asset,unit,from,to,quantity,reason，以及存在两侧的 from_before/to_before | 同单位资源转移；外部获得/消耗一端可 null |
| hook | id,before,status；首次 text,due | status 为 open/paid/closed；due 为最迟章号；延期和 closed 需要 reason |
| clock | before,after；可选 thread | 世界当前时钟，非负整数单调；单位由创作约定明确(clock_unit)。clock 事件的证据必须引用本章真实时间语句(含 日/夜/晨/时 等时间词)；带 thread 的时钟为并行线独立日历，不影响全局时钟 |

事件可附 `refs`（实体 ID 数组）和 `topics`（话题数组）辅助召回。resource 数量必须为十进制字符串，最多 120 位有效数字和绝对值 120 的指数，超过就明确报错，不能悄悄取整。

所有变化以前态为条件，同一章多个事件按顺序应用。state 的 `before:null` 表示尚未登记此属性，不代表历史上一定不存在。复活（alive false→true）额外要求 exception_reason。不认识的事件类型被拒绝。

clock 可用 thread 维护独立支线日历；主线时间不因支线事件自动变化。场景 time_hint 的 D+N 以计划签发时的时钟为锚；有 day_span 的章结算须满足 `[锚+N_min,锚+N_max+1]`。这些是日程和引用校验，时间词存在不能证明推算语义正确。

## 按需扩展

空间事件 `anchor/anchor_assert/anchor_change/place/place_change/link/link_change/placement/move/perceive`、address_responses 和地图查询见 [空间协议](spatial-continuity.md)。人物小传、character_development、对白意图见 [人物协议](characters.md)。event_spine、sequence_refs、设计失效与重规划见 [分层设计](design-and-brief.md)。chapter_review 按 payload.facts_checks/reader_checks 返回检查项，跨章复审按 payload.checks，不写死列表。

dependencies 原文只注入执笔，其他阶段给摘要/哈希和 recall 指引。chapter_review 首读只读正文，之后读取含完整计划的输入包，不读执笔自评。补丁与正文复用见 [repairs](repairs.md)。

## 配置与用量观测

contract.style 可设 voice/traits/sample/sample_close；无锚点时 approve 从试章取开头/结尾。config 可更新 style_anchor；reader 增 voice_match，整体查 repetition。traits 与 language 遵守 [声线规则](voices.md)，不改写样本引文。

status/doctor.usage 返回 task_input_chars、accepted_receipt_chars、attempted_receipt_chars、preparation_input_chars、supplemental_read_chars、tasks。accepted 只计已接受结果，attempted 包括可识别任务的失败提交；不含系统提示、聊天或协议外读写。billed_tokens/session_handoffs 无宿主证据为 null，字符不换算费用。doctor 不验证宿主隔离或窗口，见 [runtime](runtime.md)。

## 修订影响查询

`impact --fact <坐标>` 返回某事实坐标被断言过的全部已提交章节与事件摘要，以及其后仍列出该实体的未来章计划。坐标即 `recall --state` 显示的键：state 事实 `entity:key`、knowledge `entity:topic`、关系 `from>to:key`（任一端查询都命中）。历史修订前先查影响面；查无记录不等于没有牵连。

## 错误与修正

CLI stdout 为单行紧凑 JSON：宿主按输出总量截断、不按行，紧凑格式最省 token；嵌入字符串（如 recall 的正文）pretty 化也不会换行，长文本阅读一律走文件通道（prose/prose_path 文件、material 页文件）加分页游标，不靠加宽 stdout。非零退出码的 {ok:false,code,message,details} 同样走 stdout（退出码 2 标失败，stderr 不承载答案）。审稿回执、事件对账等逐行校验的 details 另含 issues 数组：一次列出全部不合格行（各带自身 code 与定位字段）与 count，逐项修完再重提，不逐条试探。格式、引用或前态错误按原文修正结果，任务保持 pending；阅读缺陷走 repair/redesign，不只改判词。context_scan/context_reduce/context_more 见 [上下文协议](context-runtime.md)，信封仍为 /1。

`patch --task TASK --old 原文 --new 替换文 [--start N]` 返回完整 base_hash 与精确补丁，不写草稿。重复原文须指定目标坐标，错误 details.matches 为实际匹配位置；局部任务仍校验允许范围。

`preflight --result 文件` 用于当前 pending 的 draft/repair/redesign/chapter_review，按真实 submit 完整验证后回滚，返回 checked/submitted:false/would_commit 与可用的 settlement；不修改任务、草稿、正式账本、用量或恢复点。settlement 含资源 before/after、主线与支线时钟和逐事件运算，不代表语义 pass。正常 submit 也返回结算，主控以程序实际值和任务 state 交接。事件错误 details 含 event_index/chapter、实际前态、类型或缺字段；据任务事件契约修正，两轮失败交回主控，不查全库或源码推测格式。

`stale_task/input_version/lease` 表示结果不再属于有效任务，不能把旧结果的 ID/哈希改成新任务后重交；重新读任务和材料。无权限改约、核心设定无法调和、修订次数耗尽时，报告具体阻碍与可选处理。

`replay_conflict` 表示该任务已成功入库，修正稿不能覆盖：停止重提并交回主控核实。同一任务修正两轮仍被其他错误拒绝时同样停止，保留现场、返回最后已提交任务 ID 与错误原文，由主控 advance 接续。

修订链护栏：整体审稿 fix 自动建链，嵌套候选默认最多两层（`revision_chain_limit`，config 可调，调整属作者决策）；连续两次整体 fix 或链深触顶时暂停，`pause_reason` 带 code=revision_exhausted、chain_depth、链摘要、发现与 directive。`begin_revision` 触顶返回 `revision_chain_limit`，details 附链深度、链摘要与三条出路（完成并合并当前链、`revise --discard` 丢弃顶层候选、作者调整上限）。

`revision_exhausted` 暂停属于需作者决策的阻碍：主控报告链摘要与全部发现后结束会话，不得 resume 重派同一整体复审，也不得再开新链。嵌套候选被 `revise --discard` 丢弃时回到父候选续作，而非直接跳回正式分支。

核心项 coverage/continuity/read_on/character/change/language/substance/causality/character_arcs/payoffs/pacing/ending/length/core_promises/stable_facts/spatial/character_consistency/voice_evolution 不能使用 not_applicable 绕过证据；其余项不适用也必须提供非空字符串理由。
