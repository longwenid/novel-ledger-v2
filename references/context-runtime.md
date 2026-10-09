# 长历史材料组织

context_mode=bounded 表示按任务和来源组织材料。context_chars/context_output_chars/context_retrieval_chars/context_overhead_chars 不再限制内容；字符统计只观察重复读取。

## 三层结构

1. **持久原始记录**：SQLite 保留正文、事件、人物、地图、计划与审稿版本。只有有效正文与作者约定能确立事实，摘要和设计不能覆盖账本。
2. **材料准备层**：相关历史冻结为带哈希的来源文档，按完整结构化对象或列表项分页；context_scan 提取当前任务所需的约束与疑点，context_reduce 合并重复解释，覆盖、取舍、游标和来源持久化。
3. **当前任务层**：作者约定、当前正文与必要任务完整保留；相关认知由 topics 和对白 knowledge_topics 共同选取，依赖原文由 dependencies 明确。其他材料保留索引与定向检索通路，缺项不是不存在。

按完整语义单元分页，不截断长段、事实对象或因果说明。无法安全归并就保留原节点与来源；事实冲突交 facts/修订处理。

准备任务只处理材料，不替目标任务审稿、不消耗文学返修次数；完成后主控 next 取正式阶段。Worker 分工见 [runtime](runtime.md)。

## 结构上的消耗控制

- **定向取材**：按实体、话题、场景和依赖章获取材料，拒绝无目标全历史倾倒；近期摘要和最新事件是导航视图，不代表完整历史。
- **完整单元分页**：依赖原文按完整章节记录、账本按对象/事件组、人物按档案与成长、设计按卷/节拍分读。candidate 支持按自然段余文或明确原稿范围读取；recall --chapter 当前返回整章，不声称已有章内段落分页。所有页绑定来源版本与游标；已读范围不能混用不同版本。
- **文件交付**：正文写文件，返修用补丁，未变正文用 reuse_original，不在聊天复述全文。
- **语义去重**：重复约束可以归并，误信、动机、因果、资源前态、地点和承诺的必要差异必须保留。缓存若复用必须绑定来源哈希、用途及失效依赖，不能把上次任务结论无条件当本次事实。
- **会话交接**：宿主窗口不足时按完整场景或问题拆任务，按 runtime 续接。

## 准备回执与来源

信封沿用 [protocol](protocol.md)。payload 含 fragment、document/hash、start/end、目标阶段、用途、疑点与核心来源索引。用 target_materials 的 core:FIELD 补读完整子节，正式任务保留完整核心；start 是冻结文档字符坐标，不是页内或字节位置。

context_scan 的 data：

```json
{
  "coverage":{"document":"d0","document_hash":"照抄本页哈希","start":0,"end":1200},
  "notes":[{"text":"保留当前行动需要核实的因果或认知条件。","evidence":[{"document":"d0","start":123,"quote":"精确材料原文及必要语境"}]}]
}
```

通读 fragment，coverage 四项与本页一致；notes 每项绑定原始来源。无相关结论时 notes=[] 并给 omission_reason，不能因复杂而省略。

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

task_reads 保存消费任务、目标任务、返回值哈希、来源与查询范围；分页仅记实际交付页。空查询也保存实体、话题、范围和域版本，新增历史会使其失效。旧无记录任务标 unknown_legacy，不补造交付记录、不改 pending ID/hash/input。

```bash
python3 "$ROOT/scripts/novel.py" dependencies --project "$PROJECT" --task TASK_ID
```

coverage=delivered_only 仅证明交付；versions_unchanged 不授权跨 scope 免审。复审仍受 review_context_hash、旧卷补核和全部上游正文相等约束。

## 本地查询与模型成本

人物/话题倒排索引计算准确 total 和分页，查询保留 Python casefold 子串与原顺序，共享事件不会重复。未定向全文子串仍可能遍历候选。冷启动状态使用已验前缀快照并核验纯追加后缀；早插、改删、替换、契约或原章变化失效。完整 audit/历史 replay 仍全量核验。用量和卷预算采用可重建增量计数，预算以实际正文汉字计算，不用元数据代替正文。

本地耗时、协议字符与宿主 token/费用分别计量。

## 验证边界

无 topics 的认知、适用范围未知的经历和未决承诺仍需完整准备，不为缓存命中删内容。结构分页不保证固定窗口或调用量；文学质量按 [独立评测](literary-evaluation.md) 验证。
