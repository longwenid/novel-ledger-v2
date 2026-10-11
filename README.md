# novel-ledger-v2

跨模型连续创作长篇网络小说的 Skill：以 **SQLite 账本为唯一真源**、按需检索与独立审稿支撑百万字工程，支持分批续接、历史修订与全书视图。当前版本 **v1.40.0**。

## 核心理念

1. **SQLite 为唯一真源**——正文、任务、恢复点、用量全部入库，只通过 `submit` 更新，不手改数据库或伪造回执；`work/` 目录只是派生视图，崩溃可重建。
2. **执笔与审稿隔离**——`chapter_review` 由独立于执笔者的审稿 Worker 完成事实与读者两类检查，逐项阅读、举证，通过后才入库；审稿者不读取执笔自评。
3. **上下文按结构规则控量**——正文与资料按内容寻址平文本文件交付（`work/prose/<hash16>.txt`），包内只留路径与哈希；同一 Worker 只复用已读且哈希未变的资料，新 Worker 重新读取。不给输入设字符上限，靠任务拆分、实体/话题定向检索与版本补丁防爆炸。
4. **文风锚点**——`style_anchor` 随执笔/审稿包下发，reader 用 `language` 核验；所有文风删除非必要形容词，用动作、事实、对白和后果表达（"人味写作"正面框架）。

## 派工模型

- **`advance` 主控交接**：一次返回 `action`、短状态、任务信封与恢复点；Worker 结束后 `advance --after 最后已提交任务ID` 核实真实提交并接续。信封自带程序生成的 `worker_prompt`，主控只收短状态，不自行读写正文。
- **章节批次连写**：`chapter_session` 默认 3 章、最多 5 章给单一执笔角色，同会话逐章提交并自查；程序在提交时校验合成全文的实际篇幅。
- **整批密封审稿**：每批完成后派一个新的独立审稿者，先只读整批正文、`first-read` 逐章持久冻结首读，再一次提交事实+读者两类检查。
- **连续会话**：跨章复审（`review_session`）由同一独立审稿者连续处理分块与综合判断；材料准备（`preparation_session`）由同一 Worker 连续处理同批材料任务。
- **定点修订**：审稿冻结稿（批次候选或单章/修订稿）的简单数字笔误可在一次独立审稿中用版本补丁修正；普通局部返修在结算未变且后文仍可复算时保留后文候选。完整两类审稿与账本核验继续执行，历史章仍走修订分支。
- **确定性定位和结算**：`patch` 返回精确补丁位置，`preflight` 回滚执行完整提交校验，`submit` 返回实际资源/时钟结算；历史与候选事件分别按完整版本引用交付。
- **输入输出去重**：人物、设计及较大的账本与连续性资料按完整记录分片，同一 Worker 复用已读且未变的版本；主控用 `advance --compact`，生成的提交命令返回简洁交接，完整信封从 `task_path` 读取。重复原文证据可在 `evidence_catalog` 定义一次、各检查按编号引用，展开后仍执行全部核验。正式回执用本任务冻结首读哈希引用原体验，恢复时有完整首读文件，不重复抄写。

流程图见 [references/workflow.md](references/workflow.md)，协议细节见 [references/protocol.md](references/protocol.md)。

## 使用

作为 Skill 使用：在 ZCode 中调用 `novel-ledger-v2`，按 SKILL.md 的核心操作流走——开书（creation + hatch-ideas）、续写/无人值守/恢复（runtime）、局部返修（repairs）、历史大修（continuity）、验收与完本（editorial）。小说账本与正文只存项目目录（`PROJECT`），不写入 Skill 目录。

命令入口（Windows 用 `python`，其余 `python3`）：

```bash
python scripts/novel.py --project "$PROJECT" <command>
```

常用子命令：`init`（开书）、`advance`/`next`/`submit`（派工与提交）、`measure`（篇幅）、`preflight`（完整预检）、`patch`（定点定位）、`approve`（首章定调）、`recall`（定向检索）、`revise`（修订分支）、`audit`（全书审计）、`export`/`html`（导出与书刊视图）、`doctor`（环境诊断）、`status`/`pause`/`resume`/`complete`（运行管理），以及 `design`/`character`/`map`/`material`/`impact`/`pilot`/`first-read`/`evidence`/`checkpoint`/`dependencies`/`tidy` 等视图与治理命令。

依赖：Python 3.9+，仅标准库，无第三方包。

## 仓库结构

```
SKILL.md              Skill 入口与核心规则
agents/               宿主 agent 展示配置
assets/               契约与实体示例 JSON
references/           阶段指南（creation/runtime/writing/protocol/voices… 16 篇）
scripts/novel.py      CLI 入口
scripts/novel_core/   引擎（engine/store/memory/ledger/batchflow/reviewflow… ）
tests/                引擎与行为边界测试（pytest）
validation/           端到端验证脚本与基准（chapter_batch / review_session / material_efficiency）
```

## 开发

```bash
python -m pytest tests/ -q          # 全量测试
python validation/run_story.py      # 端到端故事验证
```

版本沿用 `scripts/novel_core/store.py` 的 `VERSION`，升级就地生效：旧任务按库内存储输入恢复，账本快照版本不符时走慢路径重放，无需迁移。
