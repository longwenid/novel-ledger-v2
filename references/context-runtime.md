# 长历史材料组织
何时读：材料准备任务（context_scan/reduce）、疑点补读与缓存失效边界。

context_mode=bounded 表示按任务和来源组织材料。context_chars/context_output_chars/context_retrieval_chars/context_overhead_chars 不再限制内容；字符统计只观察重复读取。

## 三层结构

1. **持久原始记录**：SQLite 保留正文、事件、人物、地图、计划与审稿版本。只有有效正文与作者约定能确立事实，摘要和设计不能覆盖账本。
2. **材料交付层**：相关历史冻结为带哈希的来源文档。适合直读的结构化资料逐字恢复到正式输入；长历史按完整对象或列表项分页，由同一材料 Worker 连续完成 context_scan/context_reduce，覆盖、取舍、游标和来源持久化。
3. **当前任务层**：作者约定、当前正文与必要任务完整保留；相关认知由 topics 和对白 knowledge_topics 共同选取，依赖原文由 dependencies 明确。其他材料保留索引与定向检索通路，缺项不是不存在。

按完整语义单元分页，不截断长段、事实对象或因果说明。无法安全归并就保留原节点与来源；事实冲突交 facts/修订处理。

准备任务只处理材料，不替目标任务审稿、不消耗文学返修次数；完成后主控 advance 取正式阶段。Worker 分工见 [runtime](runtime.md)。

## 默认交付策略

`preparation_policy=adaptive` 按来源分别选路：总计不超过 48 个语义单元时沿用完整输入；超出时，每个不超过 48 单元的来源仍完整直读，只有更大的来源需提炼。小来源合计超过 96 单元时整体走准备流程，避免大量小块绕过长历史处理。这些数值只选择路径，均不删内容或限制字符。显式疑点请求仍走定向准备；已经冻结的 bundle 保持原输入、布局和策略。

直读输入的 `context_delivery.mode=exact-records/1` 列出字段、来源版本与哈希；完整记录位于对应字段，不能把索引当作内容，也不能把来源复用当作审稿通过。执笔者和独立审稿者分别阅读本任务实际资料，不共享前者的理解结论。可用 `config --key preparation_policy --value semantic` 保留全量模型提炼用于对照；有 pending 时不改配置。

带 `chapter_session` 时优先按生成的批内连续提交命令执行，章节/角色边界见 [runtime](runtime.md)；审稿批次未冻结整批首读前，材料者不能复用为正式审稿者。其余新准备信封带 `preparation_session.protocol=same-bundle/1`。按生成的 `commands.submit` 提交即可；返回 `action=continue_preparation` 时按 task_path 恢复完整信封，阅读输入与模板，继续同一批次。其他 action 时结束，并报告最后已提交 task_id。不得自行 next/advance、进入正式阶段或跨批次。上下文不足时保留已提交页与 pending，由主控换新 Worker 恢复；无需重扫已提交页。旧信封没有此标记时仍单任务交接。

## 结构上的消耗控制

输入去重优先按 `shared_read_rule`：人物/设计及较大的账本/连续性字段有 parts 时读取全部完整记录分片，后续同一 Worker 只重读变化的版本；selected.material、候选前缀 events_material 仍须读取。首次接手或上下文不足时重新恢复必要全文，不把其他人的已读记录当作自己阅读。完整字段文件与全部分片是等价读法，选一即可，语义材料不截断。

输出去重用生成的简洁提交命令；主控 `advance --compact`，Worker 按返回 task_path 恢复完整信封。审稿和候选事件可在 evidence_catalog 登记重复引文，各检查仅引用编号，但判断、具体理由和所需证据覆盖逐项完成；程序展开后执行原有全部校验，精确定义见 [协议](protocol.md)。正式 reader.first_read_ref 只引用已冻结的原首读；恢复时如未保留体验，从 first_read_receipt_path 完整读取。以上只减少重复传输，不改变准备路径、审稿覆盖或首读门禁。

- **定向取材**：按实体、话题、场景和依赖章获取材料，拒绝无目标全历史倾倒；近期摘要和最新事件是导航视图，不代表完整历史。
- **完整单元分页**：依赖原文按完整章节记录、账本按对象/事件组、人物按档案与成长、设计按卷/节拍分读。candidate 支持按自然段余文或明确原稿范围读取；recall --chapter 当前返回整章，不声称已有章内段落分页。所有页绑定来源版本与游标；已读范围不能混用不同版本。
- **文件交付**：正文写文件，返修用补丁，历史修订未变正文用 reuse_original，不在聊天复述全文。章节批内公共约定、声线、档案、设计和 guide 按内容哈希写完整共享文件；首次接手读全所需资料，同一 Worker 内哈希未变且已读时不重复加载。
- **批内连续处理**：长材料逐页核验，但不逐页新建 Worker、重载指南或让主控转述资料；材料提交不重绘整本 HTML，正式入库照常更新。
- **语义去重**：重复约束可以归并，误信、动机、因果、资源前态、地点和承诺的必要差异必须保留。缓存若复用必须绑定来源哈希、用途及失效依赖，不能把上次任务结论无条件当本次事实。
- **会话交接**：宿主窗口不足时按完整场景或问题拆任务，按 runtime 续接。

## 准备回执与来源

信封沿用 [protocol](protocol.md)。单页 payload 含页文本文件（prose_path/prose_hash）、document/hash、start/end；跨文档批次放在 payload.pages，各项保持同样的页结构。新 bundle 每次最多组织 16 个完整语义单元，文档尾页不足时装入下一文档，保留所有来源，不设置字符上限；已有 bundle 保留原布局。包内 guide 与 result.template.json 给出本阶段契约、目标、用途和来源索引。用 target_materials 的 core:FIELD 补读完整子节，正式任务保留完整核心；start 是冻结文档字符坐标，不是页内或字节位置；页文本文件字节即文档 [start:end] 切片。

context_scan 的 data：

```json
{
  "coverage":{"document":"d0","document_hash":"照抄本页哈希","start":0,"end":1200},
  "notes":[{"text":"保留当前行动需要核实的因果或认知条件。","evidence":[{"document":"d0","start":123,"quote":"精确材料原文及必要语境"}]}]
}
```

按页文件通读全部语义单元；片段带原始 JSON 分隔符，未必是可独立解析的 JSON。units 的 local_start/local_end/local_semantic_start/local_semantic_end 对应页文件，原 start/end/semantic_start/semantic_end 对应完整文档。用 `commands.evidence` 获取绝对引文坐标。单页 coverage 四项与本页一致；payload.pages 存在时 coverage 为各页四项组成的数组，完整覆盖后才原子推进游标；notes 每项绑定原始来源。无相关结论时 notes=[] 并给 omission_reason，不能因复杂而省略。

context_reduce 的 data：

```json
{
  "notes":[{"text":"合并后的约束，保留差异和疑点。","evidence":[{"document":"d0","start":123,"quote":"继承的原始引文"}]}],
  "accounting":[
    {"input_id":"输入节点ID","disposition":"kept","reason":"保留其行动前提与来源"},
    {"input_id":"另一节点ID","disposition":"duplicate","duplicate_of":0,"reason":"说明具体重复内容"}
  ]
}
```

每个输入登记一次：kept 由一条输出整体继承全部来源；duplicate 指向有效输出 note 并继承全部来源；irrelevant 说明无关依据。不添加未读来源。cannot_compact:true 配原因时保留原节点；形式校验不替代语义取舍。

## 疑点补读

```bash
python3 "$ROOT/scripts/novel.py" recall --project "$PROJECT" --state --entity ID --topic TOPIC
python3 "$ROOT/scripts/novel.py" recall --project "$PROJECT" --chapter N
python3 "$ROOT/scripts/novel.py" material --project "$PROJECT" --task TASK --bundle BUNDLE --document d0 --start OFFSET
```

补读按实际继续游标取下一页，核对 content_hash/document_hash。recall --chapter N 返回正文与摘要，账目用定向 recall；未读完不能认定无其他信息。

需要新的相关材料时，正式任务可提交：

```json
{"context_more":[{"chapter":12},{"entity":"hero","topic":"key","start":1,"end":100}],"reason":"说明尚未解决的疑点与需要核实的前提，必要解释不限篇幅"}
```

context_more 结束本次尝试、派材料准备，不混交正文或审稿、不改文学阶段。补读须有新来源或推进疑点；重复请求改为缩小问题或处理冲突。

usage 字段见 [protocol](protocol.md)；重复 next 不新增任务输入。

## 来源索引、用途缓存与读依赖

来源按 branch/field 建 source_id，完整文档和语义单元按内容版本保存。相同版本复用目录，新增条目只登记新单元；变化文档仍需结构定位与完整保存。目录或派生值校验失败时重建或报错。

准备结论绑定目标（章号、阶段、场景、约定、正文）、完整来源 manifest、规则版本与实际读依赖。仅传输标识改变时可复用结论；新增来源或跨章/阶段只复用目录。无法安全归并时保留原节点与材料入口。额外读取 bundle 外材料或缺初始交付记录时禁用结论缓存。

task_reads 保存消费任务、目标任务、返回值哈希、来源与查询范围；分页仅记实际交付页。空查询也跟踪范围，新增相关历史会使缓存失效。缺记录时标 unknown_legacy，不补造交付记录或修改 pending 输入。

```bash
python3 "$ROOT/scripts/novel.py" dependencies --project "$PROJECT" --task TASK_ID
```

coverage=delivered_only 仅证明交付；versions_unchanged 不授权跨 scope 免审。复审仍受 review_context_hash、旧卷补核和全部上游正文相等约束。

## 验证边界

无 topics 的认知、适用范围未知的经历和未决承诺仍完整交付或准备，不为缓存命中删内容。结构分页不保证固定窗口或调用量；缓存与索引的实现边界见 [验证方法](validation.md)，文学质量按 [独立评测](literary-evaluation.md) 验证。

跨章复审的准备 frame 可继承 `review_session`。这时同一独立复审者按 `continue_review` 在准备和正式复审之间连续处理；未改变来源或正式版本才可接续。仅有 preparation_session 的材料者仍不跨入正式任务。逐章 chapter_review 首读门禁保持原规则，不能借连续跨章复审提前接触盲首读资料。
