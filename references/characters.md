# 人物档案、声线与成长
何时读：profiled 人物档案、character_plan/character_index 回执与人物审稿检查项。

## 三层人物数据结构

启用 `character_mode: "profiled"` 时，人物档案分为三层：

| 层次 | 核心内容 | 作用与使用边界 |
|---|---|---|
| **小传 biography** | 身份定位、诉求、主要矛盾、底线与关键经历 | 解释角色行动逻辑；创作设想标 `design`，已有经历需引用有效正文证据 |
| **声线 voice** | 语域、话语策略、句式节奏、压力下变化及不同对象表达模式 | 指导对白风格，非机械口癖；可附代表性正文台词校准 |
| **场景意图 dialogue_intents** | 当下诉求、隐瞒点、压力与对白策略 | 随场景任务书下发，指导具体交涉，不构成客观事实 |

档案属于设计解读（`interpretation_and_design_not_canon`），不覆盖客观事实账本。人物分 major / supporting / walk_on 三级分层管理。

人物声线与对白遵守 [voices](voices.md)，用关注对象、策略、句式和关系立场区分人物。

## character_plan 回执协议

`character_plan` 阶段返回 `{"profiles":[...]}`，覆盖 `payload.entities`：

- `entity`：已登记人物 ID。
- `tier`：`major` / `supporting` / `walk_on`。
- `biography`：必填 `public_role`、`desire`；major 需附 `fear`、`values`（排序数组）、`boundary`、`contradiction`；经历 `experiences` 中已发生项标 `status: "established"` 并附正文引文。
- `voice`：必填 `register`、`tactics`（策略数组）；major 需附 `rhythm`、`stress_shift`；可选 `relationship_modes` 与 `avoid`。
- `arc`：major 必填，包含 `direction` 与 `not_yet_earned`（未兑现成长）。
- `change_reason`：更新旧档案时必填，说明变更原因。

## 场景发言与意图规范

计划中指定 `speakers`（纯动作场景显式 `speakers: []`），拟发言者附 `dialogue_intents`：

```json
{
  "entity": "hero",
  "seeks": "想从对方探知的事实或目标",
  "withholds": "不愿透露或隐瞒的信息",
  "pressure": "当前面临的交涉压力与顾虑",
  "tactic": "交涉所采取的话语策略",
  "knowledge_topics": ["需核实的话题"]
}
```

## 人物成长事件（character_development）

重大性格或认知转变通过正文事件提取：

```json
{
  "op": "character_development",
  "entity": "hero",
  "before": null,
  "change": "转变内容与体现",
  "cause": "驱动转变的客观事件或他人行为",
  "evidence": {"chapter": 3, "start": 0, "quote": "本章正文引文"},
  "cause_evidence": {"chapter": 3, "start": 0, "quote": "本章原因引文"}
}
```

入库后下次相关任务自动更新档案。查询与重审命令：
```bash
python3 "$ROOT/scripts/novel.py" character --project "$PROJECT" --entity hero
python3 "$ROOT/scripts/novel.py" character --project "$PROJECT" --entity hero --history
python3 "$ROOT/scripts/novel.py" character --project "$PROJECT" --entity hero --refresh --reason "原因"
```

## 完整档案与场景视图

新模板 character_views=scoped，未设的旧项目为 full。无 pending 时可 `config --key character_views --value scoped`，相关审稿会失效；完整档案与历史版本保留。

新版本先派独立 character_index，读完整 character_context.profiles、核心动机/声线及 sources 原章；必要时 recall --chapter N。索引须有真实全文交付，不能以摘要、引文或准备笔记替代。

返回 `data={"indexes":[...]}`，恰好覆盖 payload.entities。每份索引为：

```json
{
  "entity":"hero",
  "profile_hash":"完整档案 record.hash",
  "core_hash":"character --section core 的 source_ref.value_hash",
  "core_depends_on":[],
  "coverage":"complete",
  "items":[{
    "section":"biography.experiences",
    "item":0,
    "value_hash":"该完整条目的 source_ref.value_hash",
    "classification":"unknown",
    "reason":"说明这一经历是否影响核心动机、声音或本场景；不确定时保留"
  }]
}
```

items 覆盖 biography.experiences/voice.relationship_modes/examples，item 从 0 起。value_hash 用规范 JSON SHA256，或 section/item 补读取得。

- classification 为 global/unknown/scoped。核心动机、声音和普遍应对条目必须 global。
- core_depends_on 必填 section/item 对象数组，列核心所依赖的经历、关系和例句；无关联可空，选择时保留传递依赖。
- scoped 须有 `scope:{"entities":["ID"],"topics":["TOPIC"],"places":["ID"]}`、scope_complete:true 和穷尽条件说明；只写真正适用维度，任一维命中即保留，判断维度缺失也保留。
- 条目依赖用 `depends_on:[{"section":"biography.experiences","item":0}]`，选择取传递闭包；不凭关键词判无关。

来源、核心、成长或档案变化使索引失效，未索引/失效时交付完整档案。unknown/global 和未吸收成长保留；writer 交付完整核心、命中经历与依赖，facts 读完整档案。适用范围仍需语义核验。

完整补读入口：

```bash
python3 "$ROOT/scripts/novel.py" character --project "$PROJECT" --entity ID --section core
python3 "$ROOT/scripts/novel.py" character --project "$PROJECT" --entity ID --section biography.experiences --item 0 --revision PROFILE_HASH
python3 "$ROOT/scripts/novel.py" character --project "$PROJECT" --entity ID --section biography.experiences --topic TOPIC
python3 "$ROOT/scripts/novel.py" character --project "$PROJECT" --entity ID --section voice.relationship_modes --related-entity ID
```

topic/related-entity/place 可重复，返回完整命中条目、核心和依赖；未知维度、失效索引或历史版本退回完整 section。requested_branch 为查询分支，source_branch/source_ref.branch 为版本来源分支，旧证据按来源分支与 sources 哈希核对。--index 可读完整索引，常规用定向入口。

## 审稿检查项

- **facts 子回执**：`character_consistency` 检查行为是否符合认知、经历与动机依据。
- **reader 子回执**：`dialogue_identity` 核验对白是否有独立辨识度与立场（无对白场景注明 `not_applicable`）。
- **整体复核**：`voice_evolution` 检查成长弧光是否持续生效。
