# novel-ledger-v2

**懒人长篇写作**——作者只做关键决策，其余交给引擎。跨模型连续创作长篇网络小说的 Skill：以 **SQLite 账本为唯一真源**、按需检索与独立审稿支撑百万字工程，支持分批续接、历史修订与全书视图。当前版本 **v1.55.0**。

## 懒人长篇写作是什么

写百万字通常败在两件事：写着写着忘了自己埋的设定，以及逐章盯稿的精力耗尽。这个 Skill 把长篇做成一条**作者只出选择题、引擎出体力**的流水线：

作者做的事（全是决策，没有体力活）：

- **开书选门**：每轮 2～3 个候选，回 `A/B/C`，或 `换一组` / `自定义`（见下节）。
- **试章定调**：首章过两类检查后交作者，认可 `approve`；只给一句文风意见（形容词密度、章末处理）用 `pilot` 驳回重写，设计原样保留；要改情节走向才 `revise`。
- **授权与暂停**：告诉主控写到哪（若干章/某卷/完本），随时 `pause` / `resume`。
- **看书**：`book.html` 全书视图在章节边界自动刷新，汇报带文件链接。

引擎做的事（无人值守）：

- **`advance` 循环**：取任务 → 派工 → 核实真实提交 → 接续，直到目标完成或真阻碍；主控只收短状态，不自行读写正文。
- **单章写审**：每个执笔会话写 1 章，再派一个新的独立审稿者完成读者与事实核验，通过后写下一章。
- **独立审稿**：每章先只读正文冻结首读，再提交事实+读者两类检查；定期跨章复审由新审稿者连续处理分块和综合判断。缺陷自动返修（最多两轮，再重设计重写一次），数字笔误定点补丁。
- **滚动规划**：未来 5～6 章的章计划随进度细化，伏笔、时钟、资源账本随章结算——书越写越厚，设定不会丢。
- **断点恢复**：所有状态在账本里，会话崩了、隔天再来、换一个模型，`advance` 从上次提交接续；`work/` 目录只是派生视图，崩溃可重建。

流程图见 [references/workflow.md](references/workflow.md)。

## 开书：概念设计

开书把「想写一本什么书」拆成八道门，每道门都是一次选择题，选完即映射进约定（contract）入库：

| 门 | 决策内容 | 映射约定字段 |
|---|---|---|
| 1 读者承诺 | 题材定位、核心看点、一句话高概念 | `reader_promise` |
| 2 推进引擎 | 核心驱动力与全书各卷职责 | `arcs` |
| 3 主角设定 | 行动逻辑、欲望与底线 | `protagonist_drive` |
| 4 核心关系 | 驱动故事的人际或对抗关系（可为无） | 人物线与 `arcs` |
| 5 世界对抗 | 世界秩序、资源限制与核心压力 | `world_rules` |
| 6 机制设定 | 特殊机制原理、代价与失效条件（可为无） | `world_rules` |
| 7 终局节点 | 终局事实与 4～8 个核心因果节点 | `ending` 与 `arcs` 脊柱 |
| 8 体量文风 | 分卷字数预算、章篇幅与文风锚点 | `volumes[].word_budget`、`style` |

- **候选不是换名字**：每轮候选必须因果不同，写清收益、代价与风险，且至少一个可支撑长篇的高概念脑爆项（`wild`）；同质化时按 [创意工具箱](references/hatch-ideas.md) 的八大设定生成方法切换透镜（高概念混搭、倒置公理、不可逆代价、限制驱动、认知差张力、规则推演极限、对抗绑定、终局因果闭环）。
- **先定终局再动笔**：第 7 门把终局事实与因果脊柱先入库，后续各卷向它收拢，避免百万字写到一半没了方向。
- **人物命名**：契合阶层、出身与行当；定名前 `recall` 查重，外号经 `alias` 登记。
- **分层展开**：门后按 全书世界设计（`world_plan`）→ 分卷设计（`volume_plan`：本卷要改变的局面、代价与交接压力）→ 章节计划（`plan`：未来 5～6 章滚动细化）流转；各卷 `word_budget` 合计落入全书 `word_range`，后卷按实际偏差自适应，防终局草草收尾。
- **一条命令落地**：约定确定后 `init --project "$PROJECT" --contract contract.json`，试写首章、定调，进入上面的写作循环。

细节见 [references/creation.md](references/creation.md) 与 [references/hatch-ideas.md](references/hatch-ideas.md)。

## 核心理念

1. **SQLite 为唯一真源**——正文、任务、恢复点、用量全部入库，只通过 `submit` 更新，不手改数据库或伪造回执；`work/` 目录只是派生视图，崩溃可重建。
2. **执笔与审稿隔离**——`chapter_review` 由独立于执笔者的审稿 Worker 完成事实与读者两类检查，逐项阅读、举证，通过后才入库；审稿者不读取执笔自评。
3. **上下文按结构规则控量**——正文与资料按内容寻址平文本文件交付（`work/prose/<hash16>.txt`），包内只留路径与哈希；`input.json` 是索引，字段按类型分片到 `input/<字段>.json`，`must_read` 标出本任务必读、`on_demand` 留给按需补读；同一 Worker 只复用已读且哈希未变的资料，新 Worker 重新读取。不给输入设字符上限，靠任务拆分、实体/话题定向检索与版本补丁防爆炸。
4. **文风锚点**——`style_anchor` 随执笔/审稿包下发，reader 用 `language` 核验；所有文风删除非必要形容词，用动作、事实、对白和后果表达（"人味写作"正面框架）。

## 交互架构改造

新任务可用 `commands.read` 一次读取全部必读记录，与 `reading_view.pages` 或 JSON 索引及必读分片三选一；`task-view` 可重建视图。首读未冻结时不开放账本资料。证据规则由同一契约生成：目录和内联证据都可只交 `chapter/quote`，程序批量补齐唯一坐标，歧义汇总报告，原始回执的幂等语义保留。

新规划硬日程通过 `time/timeline` 明确 `batch_anchor`、`chapter_start` 或 `absolute_clock`，不能只交 `D+N`。旧冻结任务和已有 `time_hint`/`day_span` 沿用原章首推进协议。新审稿的时间与文风检查须核对反证，正文推算天数须与事件结算一致；这些守卫证明协议一致，不证明文学与时间语义正确，冲突仍由独立审稿判 fix 并返修。

宿主派工规格保存于 `host-dispatch.json`，它只描述任务，不启动模型或保证权限。无人值守唯一模式为单章写审：一章一执笔、一章一独立综合审稿。旧 v2 项目首次打开自动备份并升级；当前旧任务先按冻结协议完成，随后停止批内续接，保留候选正文逐章重审。doctor 可查看升级状态及备份路径，不提供旧模式切换。真实 token 用独立工具导入，不改故事账本：

```bash
python3 scripts/observe.py import-antigravity <transcript_full.jsonl> --tree --out <observations.jsonl>
python3 scripts/observe.py summary <observations.jsonl>
```

详见 [交互架构](references/interaction-architecture-design.md) 与 [协议](references/protocol.md)。

## 派工模型

- **`advance` 主控交接**：一次返回 `action`、短状态、任务信封与恢复点；Worker 结束后 `advance --after 最后已提交任务ID` 核实真实提交并接续。信封自带程序生成的 `worker_prompt`，主控只收短状态，不自行读写正文。
- **执行与规划分开**：`execution_mode=single_chapter` 固定逐章写审；滚动章纲、十章与卷末复核照常。旧批次升级后归档，候选稿保留逐章验收。
- **单章密封审稿**：每章写完派新的独立审稿者，先只读本章正文，`first-read` 冻结首读后由同一审稿者完成事实与读者两类核验。
- **连续会话**：跨章复审（`review_session`）由同一独立审稿者连续处理分块与综合判断；材料准备（`preparation_session`）由同一 Worker 连续处理同批材料任务。
- **定点修订**：审稿冻结稿的简单数字笔误可在一次独立审稿中用版本补丁修正；普通局部返修在结算未变且后文仍可复算时保留后文候选。完整两类审稿与账本核验继续执行，历史章仍走修订分支。修订范围按依赖推导：后缀章经依赖差分、表面形扫荡与事件重放三重核验后自动携带原审结论，不受影响不再重写重审；分块与整体复核仍是安全网。
- **确定性定位和结算**：`patch` 返回精确补丁位置，`preflight` 回滚执行完整提交校验，`submit` 返回实际资源/时钟结算；历史与候选事件分别按完整版本引用交付。校验拒绝自带答案：引文错误附最接近的原文片段或多处命中位置，前序事件 kept 由重报内容自动推导，日程冲突给出两条修正出口。
- **输入输出去重**：人物、设计及较大的账本与连续性资料按完整记录分片，同一 Worker 复用已读且未变的版本；主控用 `advance --compact`，生成的提交命令返回简洁交接，完整信封从 `task_path` 读取。重复原文证据可在 `evidence_catalog` 定义一次、各检查按编号引用，展开后仍执行全部核验。正式回执用本任务冻结首读哈希引用原体验，恢复时有完整首读文件，不重复抄写。

协议细节见 [references/protocol.md](references/protocol.md)。

## 使用

作为 Skill 使用：在 ZCode 中调用 `novel-ledger-v2`，按 SKILL.md 的核心操作流走——开书（creation + hatch-ideas）、续写/无人值守/恢复（runtime）、局部返修（repairs）、历史大修（continuity）、验收与完本（editorial）。小说账本与正文只存项目目录（`PROJECT`），不写入 Skill 目录。

命令入口（Windows 用 `python`，其余 `python3`）：

```bash
python scripts/novel.py <command> --project "$PROJECT"
```

常用子命令：`init`（开书）、`advance`/`next`/`submit`（派工与提交）、`approve`（首章定调）、`pilot`（文风驳回）、`measure`（篇幅）、`preflight`（完整预检）、`patch`（定点定位）、`recall`（定向检索）、`revise`（修订分支）、`audit`（全书审计）、`export`/`html`（导出与书刊视图）、`doctor`（环境诊断）、`status`/`pause`/`resume`/`complete`（运行管理），以及 `config`/`replan`/`candidate`/`design`/`character`/`map`/`material`/`impact`/`first-read`/`evidence`/`checkpoint`/`dependencies`/`tidy`/`task-view` 等视图与治理命令。

依赖：Python 3.9+，仅标准库，无第三方包。

## 仓库结构

```
SKILL.md              Skill 入口与核心规则
agents/               宿主 agent 展示配置
assets/               契约与实体示例 JSON
references/           阶段指南与架构契约（creation/runtime/writing/protocol/architecture… 17 篇）
scripts/novel.py      CLI 入口（含默认拒绝的访问分级守卫）
scripts/novel_core/   引擎（engine/store/ledger/batchflow/reviewflow/atoms/transitions… ）
tests/                引擎与行为边界测试（pytest；含架构守护）
validation/           端到端验证脚本与测量工具（delivery_probe / run_story /
                      session_cost / chapter_batch / review_session / material_efficiency）
```

## 架构

改引擎前先读 [references/architecture.md](references/architecture.md)：五条设计原则（单一权威 / 默认拒绝 / 能力诚实 / 失败分级 / 机械事实单源）、十五条不变量、模块分层与延迟绑定登记表、修改检查表。这些契约由 `tests/test_architecture_layers.py` 以测试形式锁定——分层违规、未登记的命令分级、未登记的回环、文档与代码的机械事实漂移，都会在合并前失败。

## 开发

```bash
python validation/run_tests.py                        # 全量测试（单行进度条 + 预计剩余时间）
python -m pytest tests/ -q                            # 等价的直接调用
python validation/run_story.py --project <新项目目录>   # 端到端故事验证
python validation/delivery_probe.py --keep            # 量每个阶段的交付成本（索引/分片/必读/派工数）
python validation/session_cost.py <会话导出目录>        # 量一次真实运行的 token 与读入量
python validation/stage_cost.py <会话导出目录>          # 把成本按阶段归因（占了总成本的百分之几）
python validation/worker_trace.py <会话目录> <子会话ID> # 追一个 Worker 的每一轮：读了什么、多大
```

交付成本改动必须用 `delivery_probe.py` 给出前后数字，并用 `EnvelopeSlicingTests` 锁住判据；只缩小索引而让必读量变大不算降耗。任何「为了省 token 而多派一次工」的改动，都要先出示 `characters.worth_indexing` 那条算式：省下的字节必须大于这次派工的成本。

版本沿用 `scripts/novel_core/store.py` 的 `VERSION`，升级就地生效：旧任务按库内存储输入恢复，账本快照版本不符时走慢路径重放，无需迁移。
