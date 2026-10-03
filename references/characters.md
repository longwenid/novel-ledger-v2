# 人物小传、表达习惯与场景状态

## 同一件事为什么说不同的话

区别首先来自人物关注什么、知道什么、在和谁说话、想获得什么以及愿意承担什么，再体现在措辞与节奏。不要仅给每人一种口头禅、方言或固定句长。同一个人面对上级、亲人和敌人会调整表达；重大打击、疲劳和成长也会改变反应。不是每句话都必须独特，合作的人也可以自然地说同一句普通话。

例如得知“原件丢了”：负责还原经过的人先问最后谁签收；担心同伴的人先问有没有受伤或被跟踪；害怕承担责任的人先说明交出去时文件完整。写作要让这些差异来自人物经历、关系与当下处境，避免给职业、出身或身份套统一性格。

## 三层人物资料

| 层次 | 内容 | 如何使用 |
|---|---|---|
| 人物小传 biography | 社会身份、自身诉求、关键经历；主要人物另有恐惧、价值排序、底线、内在矛盾 | 回答“为什么这样选择”；新编经历标 design，已有经历须引用有效正文 |
| 表达习惯 voice | 语域、提问/拒绝/回避等策略，句式节奏，压力下变化，对不同关系的表达方式 | 提供方向，不是口癖配额；可引用本书代表性对白作为校准 |
| 本场景状态 dialogue_intents | 此刻想从对方获得什么、不愿透露什么、压力、表达策略、需核对的认知话题 | 结合实际 knowledge、关系、伤势与近期经历使用；未来意图不能变成已发生事实 |

人物分 major/supporting/walk_on。主要人物准备完整动机、矛盾、表达与成长方向；配角和一次性人物可用简档，但仍有自己的诉求，不能只当主角传声筒。投入程度随角色实际承担的剧情变化，不在开书时为所有路人写千字履历。

小传和表达档案属于 `interpretation_and_design_not_canon`：包含创作设想与带来源的解读，不覆盖作者约定或正式事实。人物确实有某个生日、伤疤、亲属或职业时，继续使用既有事实/anchor 管理；只在小传写一句不能建立客观事实。性格解释也不是医学诊断或永久行为定律。

## 自动准备和输入隔离

新模板包含 `character_mode: "profiled"`。章计划通过后，next 从实际涉及的 person/character 实体中检查档案；缺失、成长记录变化或来源失效时，先派发 `character_plan`，再派发 draft。无需每章重写全体小传。

档案使用稳定实体 ID，改名或化名沿用同一人物。计划的 entities、character_choices、场景 present/speakers 和对话对象用于选择相关资料；正文临时出现的新人物先在 facts 登记实体与实际变化，后续章计划再为其准备档案，不能为了补档而给所有人加戏。

- 写作：`character_context` 包含相关小传、声线与成长记录；`writer_brief.scene_proposals` 提供逐人场景意图；`state.knowledge` 保留相关人物已登记认知，不因关键词不匹配而漏掉误信。
- 事实审稿：读取相关档案及正文来源，但把设计/解读与客观事实分开。检查角色是否突然知道秘密，行动是否违背已确立的经历与条件，转变是否有因果。
- 读者首读：不提供人物档案和作者意图。先判断正文能否呈现人物差异，完成 first_read 后再查相关档案和来源；不能因为设定写了“谨慎”，就替莽撞且无依据的正文辩护。
- 十章、卷末、修订和全书复核：观察声线与行为随处境和经历的变化，检查是否突然重置、所有配角逐渐同腔，或作者为了固定人设阻止合理成长。

无独立会话时仍为 serial，材料隔离不等于模型已忘记前文。人物资料同样受工作包容量锚点约束，相关必需档案放不下会明确报错；成长材料保留所有尚未纳入档案的记录及最近记录，较早已吸收记录以 omitted_developments 告知省略数量并可检索；提炼档案并保留证据，不静默删除影响当前选择的经历。

## character_plan 回执

沿用任务信封。data 仅为 `{"profiles":[...]}`，必须恰好覆盖本次 `payload.entities`；不能顺带改其他人的档案。可运行样例见 [characters.example.json](../assets/characters.example.json)。

每个人物包含：

- `entity`：已登记的 person/character ID。
- `tier`：major/supporting/walk_on。
- `biography`：至少 `public_role/desire`；major 还需 `fear/values/boundary/contradiction`，values 为有先后顺序的字符串数组。可选 `experiences`。
- `voice`：至少 `register/tactics`，tactics 是非空字符串数组；major 还需 `rhythm/stress_shift`。可选 `relationship_modes:[{"audience":"对方ID或关系类型","approach":"如何表达"}]`、`avoid` 字符串数组。avoid 是语境提醒，不是机械禁词表。
- `arc`：major 必需，包含 `direction/not_yet_earned`，后者为数组，说明哪些转变尚缺经历支撑；不是预先宣布成长完成。
- `examples`：可选代表性对白数组，每项 `speaker` 必须是本人 ID，`note` 解释对话对象与语境，`evidence` 引用已有有效正文。没有正文时保持空，不用新编台词冒充历史。
- `canon_refs`：可选 `[{"entity":"hero","key":"birth_date","value":"1990-04-02"}]`；与当前事实核对，变化后档案需重审。
- `change_reason`：更新已有档案时必填，说明何种新证据、作者调整或解释修正导致变化。

经历格式：

```json
{"text":"曾因过快判断而误解同事，尚待故事选择是否采用","status":"design"}
```

已确立经历用 `status: "established"` 并加 `evidence:{chapter,start,quote}`。只能引用本任务之前已提交的有效正文；工具核对引文和版本，不能证明引文语义真的支持整段小传，也不能自动辨认引文是谁说的。来源真实性、说话人和推论力度须由模型核验。

档案保存在同一 SQLite，记录版本、任务 ID、来源正文哈希与成长状态哈希。旧版本保留在任务回执中。不能直接编辑导出的小传文件替代提交。

## 场景意图不是预写台词

profiled 模式的每个场景可指定 `speakers`。省略时默认 present 中的人物会发言；纯动作、无对白场景明确 `speakers: []`。每个拟发言者恰有一个 dialogue_intents 项：

```json
{
  "entity":"hero",
  "to":"ally",
  "seeks":"核对最后一次交接经过",
  "withholds":"暂不公开证人身份",
  "pressure":"问得太急会使对方停止合作",
  "tactic":"先问可确认的细节，再解释下一步用途",
  "knowledge_topics":["original_document"]
}
```

seeks/withholds/pressure/tactic 必填，但可以明确“无额外隐瞒”“当前无额外压力”，不强迫所有人勾心斗角。to 可省略；knowledge_topics 是要核实的话题，不表示角色已经知道。世界设计里的秘密和作者预定答案不能因此进入角色内心。

执笔者可以改变具体台词、小动作和场景顺序；同一信息通过不同人物的关注点、问法、回避与行动呈现。不要让每个人都负责补齐一项设定说明，也不要为了区分声线让普通对话处处像机锋。

## 成长、反常与修订

短暂情绪记在实际状态/正文中，不因一次吼叫就重写性格。只有影响后续行为或表达的重要变化才提取 `character_development`：

```json
{
  "op":"character_development","entity":"hero","before":null,
  "change":"开始尊重同伴自行选择承担风险",
  "cause":"同伴拒绝被保护在外，并明确要求共同签字",
  "evidence":{"chapter":3,"start":0,"quote":"本章实际变化的连续原文"},
  "cause_evidence":{"chapter":3,"start":0,"quote":"本章实际原因的连续原文"}
}
```

before 首次为 null，之后为上一条成长记录的 hash；`character --history` 可查。两份引文必须来自本章，既有原因可在正文回忆或对话中呈现，再关联相关历史来源。它记录有正文依据的变化解释，不自动覆盖生日、关系或其他客观事实，也不证明人物永远不会反复。

重要成长入账后，下次相关出场自动重新准备档案；新档案必须解释改变并保留依然有效的习惯。重大冲击可能暂时压住原有表达，也可能逐渐改变它，需要人物行为与后果支撑。人物可以反常，但读者应有机会理解其原因。

既有正文 revise 时，档案来源按新分支核验，被删改的经历与成长不能继续当作当前事实；正式版本在候选晋升前保持可查。只调整未写部分的设计，可在没有 pending 任务和草稿时请求重新准备：

```bash
python3 "$ROOT/scripts/novel.py" character --project "$PROJECT" --entity "周行"
python3 "$ROOT/scripts/novel.py" character --project "$PROJECT" --entity hero --history
python3 "$ROOT/scripts/novel.py" character --project "$PROJECT" --entity hero --refresh --reason "让尚未使用的关系表达更贴合人物经历"
```

refresh 只使当前工作分支档案失效，下次相关写作前更新；不修改正文或清空修订次数。已有草稿走审稿返工，完本后修改走 revise。旧版未声明 character_mode 的书不自动插入新阶段；原有审稿回执按其任务输入验证。

## 审稿与质量边界

facts 新增 `character_consistency`：引用具体段落，检查人物经历、认知、动机和变化依据；不能用“性格如此”解释所有行为。reader 新增 `dialogue_identity`：尝试交换说话人，看关注点、关系立场和表达是否仍毫无区别，再判断是否影响本场景；无对白时给具体 not_applicable 理由，不为了过审添加对白。

整体复核新增 `voice_evolution`，看成长是否在后续持续起作用，差异是否被单一作者腔吞没。实质缺陷用 quality/fix，并明确段落、读者影响和修订目标；这会进入现有 repair/redesign 流程。

不以专属词出现次数、口癖覆盖率、人物性格打分或强制每句不同证明成功。程序保证资料版本、来源、派发和检查项，人物可信度及对白自然程度仍需要实际阅读判断。

## 长历史档案读取

人物小传与全部认知仍完整保存。相关档案过大时经 [材料准备](context-runtime.md) 分页阅读，当前任务读取带来源的 context_notes，具体疑点回查 material/character。保留本场景诉求、声线、关系模式与认知边界；不能把误信提炼成客观真相，不能因未选入便给人物凭空补知识。
