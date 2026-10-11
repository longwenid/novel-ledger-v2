# 运行、委派与恢复
何时读：主控派工循环、宿主委派适配、章节批次、无人值守与中断恢复。

需要 Python 3.9+ 和项目读写权限，脚本仅依赖标准库。ROOT、PROJECT 为绝对路径，任务路由相对 PROJECT。

```bash
python3 "$ROOT/scripts/novel.py" doctor --project "$PROJECT"
python3 "$ROOT/scripts/novel.py" advance --project "$PROJECT" --compact
```

`advance` 返回 `action`、短 `status` 和可执行的 `task` 或阶段 `transition`，同时刷新恢复点。通常不再单独调用 status/next；这两个入口仍可用于诊断和兼容。短状态不携带全书预算或累计用量，需要这些观测时用完整 status/doctor。doctor 检查 Python/SQLite 和配置；宿主窗口、费用和历史隔离由宿主核实。阶段仍由引擎决定。

宿主包装文档若仍描述旧的逐项 one-shot 或固定 status/next 流程，按当前冻结任务的 guide、worker_prompt、commands 和连续会话标记执行；无需每项重读全部阶段指南。

常规交接用 `--compact`，支持 advance/status/first-read/submit/preflight；它只改变响应视图，不改变任务、输入哈希、校验或推进。简洁响应带 `response_view=compact/1`，只保留调度所需状态和任务身份、路径；Worker 先读返回的 `task.task_path` 取得完整信封，再读当前阶段允许的材料。连续响应也按此规则读取新信封，文件内 commands 已带简洁选项。恢复点仍完整保存，主控需要正式/候选稿位置时读 controller_path；诊断用不带此选项的完整 status。结算简洁视图保留所有资源、时钟与状态哈希，只省逐事件明细；需要明细时执行不带 `--compact` 的 preflight。

## 主控与 Worker 会话

主控负责目标、交接、委派和进度，只读信封与短状态；有委派能力时由 Worker 执行创作和审稿。一个项目只有一个 pending，不并行写连续章节；候选稿逐章保存，审稿通过前不进入正式账本。

写到卷末须核对约定体量与剧情承载，派工批次不等于卷末。未来设计用 replan，已写结构问题走修订；不改库或取消有效任务来强制转阶段。

新建 Worker 为空历史；批内执笔会话可复用，同一章节批次的独立审稿与同一材料批次可连续处理返回任务。每个章节批次的新审稿者均独立于执笔者；跨章复审在每次运行开始派新的独立 Worker，运行内可连续处理分块与各范围综合判断；设计与规划均派新的 Worker；材料 Worker 每批新建，不带执笔记忆或旧稿解释，不能进入正式审稿。

## 宿主适配与真实派工

按当前工具描述和参数 schema 选择入口，模型名称不决定宿主工具。

| 宿主 | 常见委派入口 | 新 Worker 与历史隔离 | 等待完成 |
|---|---|---|---|
| Claude Code | `Agent`，旧版可能为 `Task` | 新建用 `subagent_type: "general-purpose"` 或空历史自定义 Worker，不选 `fork`；批内执笔续派可用宿主支持的 `resume`，材料在单次执行内按响应连续处理 | 前台返回或后台完成通知/等待工具 |
| Zcode | `Agent` | 新建 `general-purpose` 或等价 Worker，角色字段按 schema 填写 | 前台返回或后台完成通知 |
| Codex | `spawn_agent`，如 `collaboration.spawn_agent` | 支持 `fork_turns` 时设为 `"none"`，核实历史隔离 | 当前等待工具或完成通知，如可用的 `collaboration.wait_agent` |
| Antigravity | `invoke_subagent` | `Subagents` 仅一个规格：`TypeName: "self"`、`Workspace: "inherit"`、`Model: "inherit"`；`Role` 标阶段，`Prompt` 带任务材料 | 子会话完成通知或状态工具 |

入口与隔离说明：[Claude Code](https://code.claude.com/docs/en/sub-agents)、[Zcode](https://zcode.z.ai/cn/docs/subagents)、[Antigravity](https://antigravity.google/docs/subagents)。Antigravity 的 Workspace 继承工作目录，不继承聊天历史。

Worker 须能读 ROOT、读写同一 PROJECT、执行命令和返回完成状态；只读 Explore/Plan 不适用。共享目录与历史隔离分别核验，不另建账本副本。

沿用当前模型和推理设置，省略模型覆盖或填工具支持的继承值。

其他入口按相同能力核验；隔离不明则报告未知，不杜撰工具或把工具调用当终端命令。doctor/advance/next 只生成和检查任务，主控须真实调用委派工具。

委派使用信封中程序生成的 `worker_prompt`，不用另写一套阶段协议。它包含 ROOT 的命令入口、PROJECT、task.json 绝对路径、真实提交命令和任务边界。首读阶段的 input.json 尚未交付：只读当前正文，写首读文件并执行 `commands.first_read`；成功后按响应的 task_path 读取已解锁的完整信封，取得正式模板和材料路径。

**委派消息是薄包装**，只携带信封不可能知道的四类信息：①角色与历史（one-shot 空历史，或连续会话标记 chapter_session/review_session/preparation_session）；②task.json 绝对路径；③一句话任务背景（分支/章范围/作者目标）；④退出回报契约——最后已提交任务 ID、stage、状态、简短错误，不回显整章正文。ROOT、PROJECT、命令示例、执行流程一律不写进委派消息：它们已内置于 worker_prompt 与 commands，复述会形成第二真源并诱发自拼命令（生成命令按宿主平台给出 python/python3，照抄即可执行）。blocked、返修次数耗尽或 lease/stale_task 异常由 Worker 保留现场回报，主控按恢复规则接手。

正式回执按 `template_path` 填写。空字符串与空证据是待填项，不代表通过。不要查库复制旧审稿回执来猜字段或补写判断；事件字段在 `event_contract_path`，引文坐标用 `commands.evidence`。
补丁位置用 `commands.patch`；完整回执预检用 `commands.preflight`，它运行与 submit 相同的校验后回滚，不入库、不记用量、不推进任务。成功返回的 settlement 是程序按十进制计算的资源与时钟结算，主控按该值交接，不把 Worker 的口算当作后章前态。失败 details 给出事件下标、实际前值或补丁匹配位置；依本任务 state/commands.state 和事件契约修正，两轮仍失败就交回主控，不读源码、全库或目录树猜字段。
Worker 仅在 guide 字段说明不足、报错或有疑问时查对应阶段指南：规划查 design-and-brief，人物与索引查 characters，执笔查 writing，审稿查 editorial，准备查 context-runtime；协议只查相关字段。

命令用绝对路径，不依赖父环境变量、不嵌套派工。结果写成 `result/1` JSON 文件后直接执行 `commands.submit`；程序合成全文并核对实际篇幅，短稿保留并派定向返修。需提前查看计数时可选只读 `commands.measure`，纯 expand_context/context_more 请求无需测量。表达、人物选择和因果仍由执笔自查与独立审稿核验。

材料信封带 `preparation_session` 时，生成的提交命令附 `--continue-preparation`：成功后只返回同一 bundle 的下一准备任务，或结束状态。Worker 按响应 task 连续处理，简洁视图先读新 task_path，不重复读已读且未变的来源；批次结束、暂停、依赖变化、上下文不足时返回最后已提交任务 ID。正式执笔/审稿、作者批准或另一材料批次均由主控接手。旧信封仍单页交接；中断时主控确认旧执行结束后恢复现有 pending。

character_index 须完整阅读档案和来源章，不能证明适用范围时标 unknown/global，保留原文。

## 无人值守的持续派工循环

目标沿用作者已授权的章节、字数或完本范围；仅要求“持续写作/无人值守”时按小说约定推进至完本，不自行设章数或派工上限。

主控在同一活动任务内重复以下循环：

1. 启动或恢复时执行 `advance`。`action=task_ready` 时按会话复用规则派发 `task.worker_prompt`，保留 Worker ID 和 task_id。
2. 等待宿主确认 Worker 结束；超时继续等待。运行中不重复派工、不抢写 output、不取下一任务；启动成功、partial 或轮数耗尽不代表提交成功。
3. 执行 `advance --after Worker报告的最后已提交任务ID`，一次核对数据库真实提交并取得下一任务。章节或材料 Worker 可能已连续提交多项，不能沿用最初派发的 ID。`task_ready` 时直接派发返回的 `task.worker_prompt`，回到第 2 步等待；`wait_for_submit` 时按真实错误恢复同一 pending。`resync` 表示回调已过时、任务取消或分支变化，读返回状态与恢复点，核实旧执行结束后用不带 after 的 `advance` 恢复；不重放过时回调。`revision_promoted` 后继续 advance；`target_reached` 表示已达指定章范围；`await_author`、`paused`、`completed` 按相应边界处理。返回任务只证明账本可派工，不证明宿主会话空闲。
4. 已结束且不再复用的 Worker 若占用宿主并发名额，使用宿主实际提供的释放/关闭能力回收。保留当前执笔会话且确保能派独立审稿者；不能因名额不足让执笔者自审。

章节、卷、规划批次或 Worker 完成均继续循环。可每 3～5 章用非终结消息汇报进度，随后续派；运行中收到进度询问时简短回答后继续。上下文压缩后从保存的目标和 `advance` 恢复，不要求作者再次说“继续”。

仅在目标完成、作者明确暂停，或出现需作者决策/无法自行恢复的阻碍时结束。首章 `await_author` 由作者定调；返修耗尽、无法解决的事实冲突、未授权改约或宿主能力不足时保存恢复点并报告。

修订链耗尽（`revision_exhausted`）或触顶（`revision_chain_limit`）同属需作者决策的阻碍：报告 `pause_reason` 中的链摘要与全部发现后结束会话，不 resume 重派同一整体复审，也不再开新链；出路见 [协议](protocol.md) 错误与修正。

循环依赖主任务仍在运行；宿主强制终止或聊天结束时保留恢复点，不自动唤醒。

## 快速写作与质量门禁

作者要求快速写作时，优先减少调度和重复阅读开销：

- `plan` 一次规划未来 5～6 章，按任务窗口与剧情承载选择；已有有效计划继续使用。
- 默认按来源直读完整结构化资料；长材料每批只派一个准备 Worker 连续处理，避免逐页启动与重复加载。资料齐备的常规章节直接执笔、独立审稿；大型材料或新疑点才进入准备流程。
- 跨章复审用 `review_chunk` 划分完整章节的阅读单元，默认 3、范围 1～5；同一次独立审稿运行连续处理这些单元与最终综合判断，分块不再等于逐块派新 agent。按完整阅读需要调整，不缩减覆盖范围或替代逐章综合审稿。
- 局部缺陷优先定点补丁，改动后照常复审；不靠切换更小模型、缩短正文或减少审稿项提速。

用 `config --project "$PROJECT" --key review_chunk --value 3` 调整粒度；恢复时沿用已有设置。usage 观察重复成本，真实 token/费用以宿主为准。

## 章节批次执笔与整批独立审稿

`chapter_batch_size` 默认 3、范围 1～5，设为 1 沿用逐章审稿。批次越小连坐返修范围越小、派工次数越多，默认 3 平衡两者。每批从现有计划取连续章节，遇卷末、终章、十章复核点、计划缺失或新人物准备边界提前结束；试章与历史修订保留逐章流程。`writer_batch` 仍是执笔会话最多处理 1～10 个不同章节的上限，也约束本批长度。已有 pending 与旧材料 bundle 按原协议完成后，新批次才采用此模式。

1. 主控将带 `chapter_session.role=writer` 的信封派给空历史执笔者。每次仍写一章，提交正文、实际摘要、带证据的 `candidate_events` 与非空具体 `self_check`。程序核验候选事件前态、证据、资源、时钟和到期承诺；候选状态仅服务下一章承接，不表示审稿通过。真实事实、导出、HTML 与审计只读取正式账本。
2. `commands.submit` 带 `--continue-chapter-batch`，`action=continue_chapter_batch` 时直接处理响应 task，同一次执行内逐章推进；不是主控逐章 resume。批次完成、暂停、范围/依赖变化或上下文不足即停止，返回最后已提交 ID。不能 next/advance 或跨角色。公共资料在 `shared_materials` 的完整 JSON 文件中，只在同一 Worker 已读且版本未变时复用；新 Worker 须重新读。
3. 主控核实执笔者结束后 advance，派一个新的独立审稿者。审稿信封 `first_read_files` 只交付本批全部正文；首读模板的 `first_reads` 逐章绑定正文哈希与四项体验，顶层 `first_read` 与当前章一致。整批首读持久冻结后才解锁约定和账本。
4. 同一审稿者在同次执行内逐章完成 facts/reader、作者约定与证据核验；后续任务绑定此前真实冻结的本章首读，直接交付正式模板。按顺序入库，不用一份总 pass 代替逐章检查，也不把五章复核称为五个独立读者。
5. 明确的数字字面笔误可由有能力标记的独立审稿者定点修正，并在同一回执完整核验合成稿；见 [局部修订](repairs.md)。其他 fix 交回执笔者，保留正文和失败次数。局部 fix 暂存后文候选稿与事件，返修后程序比较真实结算并连续复算；前态未变且后文成立时直接派新的独立审稿批次，不重写后文、不沿用 pass。结算改变、复算失败、整章 fix 或约定/计划/人物/设计版本改变时，后文候选事件失效，执笔者核对承接并重提事件，可用补丁或 reuse_original 保留仍成立的正文。两轮返修及一次重设计的上限不重置。

已明确终止章号时，主控启动用 `advance --through N`，引擎将新写批次截到 N，已写范围的到期十章/卷末复审与候选修订复审、合并仍须完成，再返回 `target_reached`；抵达终章时须通过全书验收才能返回 `completed`。不会把“当前五章”当作完本目标。恢复沿用原范围，新授权用相同入口更新，`--through 0` 清除章范围。不得在 pending 中改变范围，也不能把已有候选批次截成未处理的残稿。

长材料可能需额外 Worker：整批首读尚未冻结时，审稿资料准备由单独材料者处理，完成后再派新的独立审稿者；不能让未来审稿者先提炼设定再声称盲首读。首读已冻结后，审稿者可按返回任务继续本批疑点准备。正常资料齐备时，五章仅需主控两次派工；模型仍逐章创作与核验，真实耗时和费用以宿主为准。

中断恢复须先核实旧 Worker 已结束，再 advance 取得唯一 pending；已保存候选前章不重写。新执笔者读完整必要资料与候选前态，新审稿者保留实际已冻结首读并补读正文，不伪造新的首读体验。记录会话与章号仍用 `checkpoint --writer-session ID --handled-chapter N`，不猜测宿主会话是否存活。

## 主控交接与单会话降级

主控接到作者目标时执行 `checkpoint --project "$PROJECT" --goal "作者已授权目标"`；程序不推断授权。`advance`、`next` 和首读交付会自动刷新 PROJECT/work/controller.json，CLI 提交、批准、暂停和修订也刷新。快照保存 ROOT/PROJECT、批准状态、目标、进度、pending ID/hash、执笔会话/批次和下一步。新会话先读快照，再用 advance 恢复，不复制旧聊天或正文；清变量或结束 Worker 不会清空主控历史。

确无可用空历史入口或调用明确不支持时，说明缺失能力后降级为串行“同会话审稿”，不得声称独立审稿。工具改名应先查等价入口；权限、暂时或执行失败按真实错误处理，不静默降级。无法自动交接时保存恢复点并报告。

## 暂停、恢复与进度判读

```bash
python3 "$ROOT/scripts/novel.py" pause --project "$PROJECT" --reason "作者暂停"
python3 "$ROOT/scripts/novel.py" audit --project "$PROJECT"
python3 "$ROOT/scripts/novel.py" resume --project "$PROJECT"
```

resume 先审计、释放旧租约，保留同一 pending 输入。丢弃无效任务须显式 pause --cancel-task，旧回执失效、失败次数保留。完整性失败不自动修库，旧进程或权限无法核实时停止。

取消批次首个任务且尚无候选稿时，可执行 replan 重做未来计划；旧批次同时归档并清理，下一次 advance 从规划阶段恢复。已有候选稿仍通过审稿返修，不删除计划留下未处理正文。advance/next 完成修订合并后刷新正式 book.html。

`status`（含 brief）、`advance` 与 controller 快照均带 `recovery`：`work` 是工作分支已验收正文的卷次/章号，`published` 是正式展示分支的位置；`target` 是 pending、未来计划或待复核的 `revision_boundary`，不能当作已完成正文。修订写满传播范围后，状态停留在修订边界，不指向下一卷计划。没有派发任务时 `stage=null`，不得推测下一阶段；实际派工以 advance 返回为准。预算默认属于工作分支，book.html 默认属于正式分支，两者不能混成一份进度。暂停与 resume 不切卷、不切分支，也不自动合并修订。

存在候选修订时，`revision_context.chain` 从父到子保留全部未完成修订原因与传播范围，新执笔、设计和审稿输入均携带它。后加文风目标不会自动替代父级长篇结构目标。修订整体复核的 `revision_intent` 须逐项对照正文举证；旧稿的 through 与 volume_end 仅说明历史安排，不能证明结构扩充已完成。超出传播范围的长期展开目标应在有效设计和后续计划中继续承接，不要求本次历史修订写完未来全卷。原因文本不自动转换成程序硬配额，仍按作者约定与实际展开作语义判断。

## 连续独立复审

新建 batch/volume/revision/book 复审任务携带 `review_session`，绑定当前工作分支、正式正文尾部和作者约定/声线/计划/档案/设计版本。主控派一个新的独立审稿者，Worker 用生成的 `submit --continue-review` 提交；`action=continue_review` 时直接处理响应 task，可接续分块、整体综合判断及必要材料准备。相同正式版本下，卷审、修订审或全书审可以处于同次运行，各自检查项、范围、证据、memory 和 pass/fix 仍分别提交。

公共资料按完整 JSON 版本引用共享，历史事件按 selected.material、候选前缀事件按 candidate_ledger.prefix.events_material 分别引用完整记录；章节窗口滑动不改变未变事件的引用。正文保留完整逐章文件和哈希；只复用同一执行自己已读且哈希未变的资料，不能自动复制其他 scope 或其他人的 pass。整体判断仍核对跨块因果与范围目标，疑点补读原文。换 Worker 或上下文不足时读取必要全文，不把其他执行的覆盖回执称为自己通读。

发现 fix、作者暂停、正文/约定/档案/计划变化、分支改变或目标变化时停止。`review_done` 后返回最后提交 ID，主控用 `advance --after` 处理返修、合并、作者批准、完本或下一阶段；复审 Worker 不启动执笔批次。必要材料带同一 review_session 时仍由当前复审者处理；没有此标记的独立材料 Worker 保留材料批次边界。旧 pending 输入与提交命令原样恢复，完成后新任务才启用连续复审。

review_session 是引擎依赖绑定，不是宿主存活或独立性证明。主控仍核实新 Worker 独立于执笔者；恢复先确认旧执行结束，再派一个 Worker，不重复派工。复审回执不改变正式正文，因此不重绘 book.html；实际入库或合并仍按原入口更新展示。
