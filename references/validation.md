# 验证方法
何时读：跑测试与规模基准；缓存与索引的实现边界。

## 自动化测试集

任选一种运行全量测试：
```bash
pytest
# 或
python -m pytest tests/ -q   # 部分测试文件不是 unittest.TestCase，unittest discover 收集不到
```

工程测试使用临时 SQLite，不调用外部模型。

## 覆盖要点

1. **事务与账本**：阶段原子性、资源守恒、时钟、空间路径、伏笔到期、引用和审稿覆盖。
2. **返修**：局部补丁与整章重写、短稿保留、篇幅计数、目标不被默降、有限返工及修订传播。
3. **材料保真**：完整记录直读与来源逐字段相等，分支/来源改变取新原记录；语义页还原冻结来源，坐标与哈希一致；归并继承全部证据，旧 pending 与 bundle 保留身份、策略和输入。
4. **缓存与索引**：来源/用途/读依赖变化失效；负查询、历史分支、unknown/core 依赖不遗漏；完整 audit 全验。
5. **视图与导出**：人物场景视图、book.html 与 export 保真。
6. **篇幅与卷末**：substance/pacing/volume_development 必须正文举证；合理短章、短卷和持续高潮允许；无预算与旧 pending 保持兼容。
7. **首读与派工协议**：首读前不交付正式输入；记录持久、版本绑定、幂等且不可补读后改写；逐条约定的违约结论与检查、返修发现一致。旧综合审稿 pending 沿用冻结协议。跨文档扫描完整覆盖才推进，页内与绝对证据坐标一致。短状态不计算全书观测，恢复点不推断授权或会话存活。
8. **合并交接**：advance 只接续真实提交；口头完成、无效回执、未知或过时任务 ID 均不派下一任务；有效重试保持 pending ID/hash。首读响应通过 task_path 恢复完整正式信封，仍保留证据门禁与作者定调；无需 measure 的直接提交仍按合成全文检查篇幅，短稿走有上限的返修。
9. **材料连续处理**：只有带标记的同批次准备任务可连续提交；无效覆盖不推进，重放恢复同一 pending，过时回调不派工；暂停、目标变化或批次完成均停止，不创建执笔或审稿。长批次归并继承全部引文，材料提交不重绘 HTML。正式审稿仍先冻结首读再取得完整资料。

10. **五章批次**：候选状态叠加只读正式前缀与连续草稿，执笔提交不入库或重绘 HTML；批次/角色 scope 不启动后续批次。整批首读覆盖、版本与不可改写，后续章绑定实际记录；每章独立登记事件并按顺序通过原证据门禁。事件差异/返修/元数据变化保留正文与次数、失效后续候选承接；暂停、重放、指定章范围、卷界和公共文件重建均覆盖。旧测试显式使用逐章模式，新批次使用独立夹具，工程回执不代表文学评测。

## 缓存与索引实现边界

人物/话题倒排索引须保持准确 total、Python casefold 子串匹配与原顺序，共享事件不重复；未定向全文子串仍可能遍历候选。前缀快照仅复用已验历史并核验纯追加后缀，早插、改删、替换、约定或原章变化时失效；完整 audit/历史 replay 仍全量核验。用量和卷预算采用可重建增量计数，预算以实际正文汉字计算。

## 规模与结构测试

```bash
python3 validation/context_endurance.py --out /tmp/results.json
python3 validation/context_endurance.py --out /tmp/knowledge.json --chapters 100 500 1000 --cases knowledge --unscoped-knowledge
python3 validation/structural_benchmark.py --out /tmp/structure.json
python3 validation/chapter_batch_benchmark.py --out /tmp/chapter-batch.json
```

五章对比使用相同正文与事件的工程夹具，检查最终正式正文/状态相同，记录主控派工次数、会话数与交付文本；公共文件按各会话首次读取计数。可选分词统计不含宿主提示、聊天历史、回执或缓存计费，不能换算实际费用或文学质量。

测试项目存临时目录。分别观察单包、累计输入、准备成本和本地耗时；协议字符与毫秒不换算模型 token 或费用。

已存在冻结准备记录的章节可离线比较直读路径：

```bash
python3 validation/material_efficiency.py --project "$PROJECT" --branch BRANCH --chapter N --out /tmp/material-efficiency.json
```

工具只读原项目，备份到临时账本后重放输入，逐字段核验来源保真。统计完整交付 JSON 与每任务引用的去重正文文件；有可用 tiktoken 时附 o200k_base 分词对比。这是统一比较尺度，不是 Gemini 计费；不含系统提示、聊天、回执和工具包装，不测宿主真实耗时或文学质量。较大的来源仍需连续准备，不能用此直读对比工具声称消除了全部准备。

宿主会话的真实 token 观测走独立流，不改故事账本：

```bash
python3 scripts/observe.py import-antigravity <transcript_full.jsonl> --tree --out <observations.jsonl>
python3 scripts/observe.py summary <observations.jsonl>
```

文学质量按 [独立正文评测](literary-evaluation.md) 使用实际正文、定位变体和新 Worker 验证，再复审修稿。合成回执只验证工程流转；误报率需要独立裁决，跨卷质量需要真实卷界与兑现证据。

恢复回归覆盖正式稿/候选稿分离、暂停保留 pending 或显式取消后同卷恢复、嵌套修订目标继承、旧输入哈希保持、首读前隐藏目标、修订范围结束不跳转新卷，以及继承目标未满足时阻止合并。

连续复审验证：同一正式版本下分块与卷/修订/全书综合检查逐项提交；pause、fix、旧回调、依赖/目标变化均停止，不能启动执笔批次、自动合并或完本。旧 pending 保留输入哈希与命令。公共 JSON 逐字可还原，损坏文件可重建；补读触发的长材料在同次复审内完整准备。使用 `validation/review_session_benchmark.py` 比较等价工程样本的派工、正文阅读版本和交付字符，不代替实际文学质量、耗时或费用评估。

输入输出基准：`python3 validation/context_io_benchmark.py --out /tmp/context-io.json`。比较完整分片、简洁交接、证据目录与冻结首读引用，验证相同逻辑值及真实提交预检；incremental_from_v1_38 单独列出本轮账本/连续性复用与首读引用的增量。合成五章与冻结首读样例不代表实际模型理解或费用，不能把各项降幅相加当作总会话节省。
