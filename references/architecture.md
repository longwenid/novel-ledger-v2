# 架构契约

何时读：修改引擎、新增 CLI 子命令、调整守卫或判定逻辑之前。本文是**判据**，不是教程；实现细节见 [protocol](protocol.md) 与各阶段指南。

## 一、系统形状

**唯一真源**：`novel.sqlite3`。正文、事件、计划、设计、人物、任务、回执、用量全部入库。
**派生视图**：`work/`（任务目录与内容寻址正文）、`book.html`、`export/`。崩溃可重建，任何时刻不得被当成真源。
**唯一写入通道**：`submit`。除 `initialize` 建库外，没有任何命令可以绕过它改正文或事件。
**跨进程边界**：主控（宿主会话）与 Worker（被委派的模型）只通过「任务信封 → 结果回执」交换数据，不共享记忆。

```
作者 / 主控 ──advance──▶ 任务信封(work/…) ──委派──▶ Worker ──submit──▶ 账本
                    ▲                                                  │
                    └──────────────── 下一任务 ◀────────────────────────┘
```

## 二、五条架构原则

每条都给出**违反它的真实事故**，便于判断新代码是否越线。

### P1 单一权威（Single Writer）

每个可变状态只有一个地方决定它的值。谁都可以读，只有权威能写。

- **权威归属**：`draft` → `engine`（作者/审稿流程）；`batch` → `batchflow`；`branch` → `engine.begin_revision` / `promote` / `discard`；`task` → `engine`（除节流的 lease/state 字段）；`plans` → `validate_plan` / `design.save` / `replan`。
- **反例**：`draft['stage']` 曾被 `engine.repair`（语义阶梯）与 `engine.develop_short_draft`（篇幅阶梯）各自写一次，后写者覆盖前者 —— 已升级到 `redesign` 的章被短稿降级回 `repair`，返修上限被突破。
- **判据**：如果两处代码都能让同一字段取不同值，必须合并为一个转换函数。

### P2 默认拒绝（Deny by Default）

列举"禁止什么"必然漏项；列举"允许什么"漏项时是安全的。

- **反例**：首读隔离用黑名单拦住 `recall/map/design/...`，`html`/`export`/`audit` 未登记，于是审稿者可在盲读前用一条 `html` 拿到全书视图（含账本与设计）。
- **判据**：新增任何读取能力时，必须先在分级表中登记；未登记即拒绝。守护测试要求 CLI 子命令与分级表一一对应。

### P3 能力诚实（Honest Capability）

程序能机械证明的才叫保证；近似判定必须显式标注为近似，并保留人工复核通道。

- **反例**：`carriage` 用「依赖差分 + 字面扫荡」两个结构手段去证明「这章不受修订影响」这一**语义**命题，但注释与文档写成「三重核验全部通过即自动携带原审结论」。实测：账本值 `30→20`、正文用「三十五文」指代且不写出归属人时，该章被自动携带并入库，`audit` 仍报 `ok`。
- **判据**：任何自动免审、自动携带、自动通过的路径，必须能回答"它在什么情况下会漏"，并把漏出的情况交给独立的复核者。

### P4 失败分级（Failure Taxonomy）

失败必须归入三类之一，不允许第四种。

| 类别 | 语义 | 处理 | 示例 |
|---|---|---|---|
| **拒绝** | 输入不合法 | 事务回滚，返回 `Error(code, message, details)`，`details` 必须含可直接照做的修正出口 | `Batch` 一次性列出全部问题行 |
| **降级** | 非关键环节失败，主流程可继续 | 继续执行，但**必须留痕**：写 `log` 且在返回值里体现 | 派生视图渲染失败 |
| **致命** | 状态可能不一致 | 回滚并抛出领域错误；不得以 `TypeError`/`AttributeError` 形式逃逸 | 事件链校验失败 |

- **反例**：`_auto_render_html` 用裸 `except Exception: pass` 吞掉渲染失败，`submit` 仍报 `accepted`，`book.html` 静默过期。
- **反例**：`carriage.fast_forward` 的 `replay` 在 try 之外，完整性错误以硬异常逃出 `next_task`，而不是降级为正常派发。
- **反例**：`--brief` / `--compact` 本意是轻量交接，但提交路径仍触发一次全量审计＋全书画页；这个成本被上面的裸 `except` 掩盖，测试因此假绿。
- **判据**：`except ...: pass` 一律违规。捕获可以宽（派生视图的任何失败都不得改变已成功的主流程），但必须**留痕并把失败交给调用方**。全文唯一允许的静默点是「留痕本身失败且账本不可写」，已在代码注释登记。

### 派生视图的刷新归属

`book.html` 是派生视图，刷新它是**主控在章节边界**的职责，不是每个 Worker 回执的职责：

- Worker 用 `--brief --compact` 提交 → 只更新 controller 快照，不渲染（`--brief`/`--compact` 描述的是**响应**，不是副作用）。
- 主控 `advance` 检测到有章节落地 → 渲染一次。
- 作者随时可显式 `html` 刷新。

这样每章渲染一次而不是每任务一次，且渲染成本不再藏在一个被吞掉的异常里。


### P5 机械事实单源（Single Source for Mechanical Facts）

版本号、协议串、检查项清单、命令清单、上下限 —— 只能有一处定义，其余从它派生或由测试锁死。

- **反例**：`protocol.md` 的"核心项"列 18 项，`engine.validate_review` 的 `mandatory` 是 21 项；`README` 版本号落后于 `store.VERSION`。
- **判据**：任何"文档里的数字/清单"，要么由代码生成，要么在守护测试里断言相等。

## 三、不变量清单

每条带 ID，可在测试中断言。违反即为缺陷，与是否"看起来能跑"无关。

| ID | 不变量 | 守卫位置 |
|---|---|---|
| INV-1 | 正式章节只能由通过两类审稿的 `chapter_review` 写入 | `engine.commit_draft` 是唯一写入口 |
| INV-2 | 每条任务在任一时点至多一条 `pending` | `store` 的 `one_pending` 唯一索引 |
| INV-3 | **已提交章必有章计划**；有草稿的章计划不得被删 | `atoms.plan(required=True)`；`design.save` 的草稿守卫 |
| INV-4 | `draft.stage` 由阶梯合成函数唯一决定 | `transitions.resolve_stage` |
| INV-5 | `batch.constraints` 必须与当前 `[first,last]` 区间一致 | `batchflow.rebind_batch` 唯一写入口 |
| INV-6 | 携带（carry）章必须保留父章已通过的两类审稿，并在 `status`/`book.html` 可见 | `carriage._carry_one`；`engine.status` |
| INV-7 | 事件链哈希连续；任何一条被改动都必须被 `audit` 发现 | `ledger.append_events` / `audit` |
| INV-8 | 首读一旦冻结不可改写；冻结前不得读取约定、账本或他人产物 | `reviewflow.seal` / `access.guard` |
| INV-9 | 每个 CLI 子命令都在访问分级表与文档索引中登记 | 守护测试 |
| INV-10 | 模块级 import 只能指向同层或更低层 | 守护测试（分层见第四节） |

## 四、分层与依赖方向

```
L4  scripts/novel.py          CLI 薄壳：参数解析、分级守卫、JSON 输出
L3  engine                   编辑状态机：choose / submit / commit / revision
    carriage numeric bookpage
L2  batchflow settlement memory reviewrun
L1  access atoms wire ledger patches budget spatial sources projections
    dependencies recovery reviewflow characters design contextflow taskio
L0  store transitions
```

规则：**模块级 import 只能同层或向下**。层号与登记表由 `tests/test_architecture_layers.py` 锁定：新增引擎模块必须登记层号，否则测试失败。

需要向上调用时只能使用**函数内 import** —— 那是显式的延迟绑定，表示"此处存在设计上的回环"。回环必须登记，未登记者视为架构漂移：

| 模块 | 向上调用 | 原因 |
|---|---|---|
| `store` | `budget` `dependencies` `ledger` `projections` `sources` | `initialize` / `ensure_derived_schema` 的建库编排，一次性动作 |
| `settlement` | `engine` | `evaluate` 需要时间词表与 hook 期限校验 |
| `design` | `engine` | `save` 需要 `load_draft` 判断"有草稿的章计划不得删" |
| `reviewflow` | `engine` `batchflow` | `seal` 物化信封并读取批次正文 |
| `taskio` | `engine` | 模板与信封生成需要状态机常量 |
| `batchflow` | `engine` | 候选入账复用 `draft_job` / `save_draft` |
| `contextflow` | `memory` | 归并页需要记忆视图 |
| `reviewrun` | `engine` | 会话续接需要 `next_task` |

## 五、状态机

### 任务（`tasks.state`）

```
pending ──submit──▶ done
   │                  ▲
   ├──cancel──────────┴──▶ cancelled     （信封物化失败 / 暂停 / 重规划）
   └──pause──────────▶ cancelled
```
`done` 的任务重提同一内容 → 幂等；内容不同 → `replay_conflict`。已 `done` 不可覆盖。

### 草稿（`drafts.stage`，唯一权威 `transitions.resolve_stage`）

```
draft ──审稿 fix──▶ repair ──审稿 fix──▶ repair ──审稿 fix──▶ redesign ──再 fix──▶ 暂停
                        ▲                                    
                        └──── 篇幅不足（length 阶梯，与上面取更严者）
```
阶梯合成规则：**语义阶梯与篇幅阶梯各自计数，结果取更严的一侧**，禁止向宽的方向覆盖。

### 分支（`branches.status`）

```
active ──revise──▶ candidate ──promote──▶ active（旧的转 archived）
                      │
                      └──discard──▶ discarded（回到父候选或 active）
```

## 六、隔离分级

`access.COMMAND_LEVEL` 是唯一登记处。未登记 → 拒绝。

| 级别 | 含义 | 命令 |
|---|---|---|
| `public` | 不接触正文、账本内容或他人产物 | `init` `status` `next` `advance` `submit` `preflight` `measure` `pause` `resume` `complete` `replan` `doctor` `tidy` `checkpoint` `approve` `pilot` `first-read` |
| `sealed` | 接触正文/账本/设计/他人产物；**存在未冻结首读的待审稿任务时禁止** | `recall` `map` `design` `character` `candidate` `material` `impact` `patch` `evidence` `dependencies` `config`(读) `audit` `html` `export` `revise` |

`sealed` 的判定理由：隔离的目的是保证审稿者的**首读体验**不受约定与账本污染。任何能读到这些内容的命令都属该级，不论它叫 recall 还是 html。

## 七、修改检查表

改引擎前逐条自问：

1. 我写的是哪个状态的权威？（P1）
2. 这个新能力在分级表里登记了吗？漏登记会怎样？（P2）
3. 我的判定是机械可证明的，还是近似的？近似的话，漏出的情况交给谁？（P3）
4. 失败属于拒绝/降级/致命哪一类？降级留痕了吗？（P4）
5. 我写进文档的数字/清单，代码里是不是也有一份？会不会漂移？（P5）
6. 新增的模块级 import 是否只向下？函数内 import 是否需要在第四节登记？
7. 违反哪条不变量？测试里断言了吗？
