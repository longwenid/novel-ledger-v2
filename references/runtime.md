# 运行、委派与恢复

需要 Python 3.9+ 和项目读写权限，脚本仅依赖标准库。ROOT、PROJECT 为绝对路径，任务路由相对 PROJECT。

```bash
python3 "$ROOT/scripts/novel.py" doctor --project "$PROJECT"
python3 "$ROOT/scripts/novel.py" status --project "$PROJECT"
python3 "$ROOT/scripts/novel.py" next --project "$PROJECT"
```

doctor 检查 Python/SQLite 和配置；宿主窗口、费用和历史隔离由宿主核实。阶段由 next 决定。

## 主控与单任务 Worker

主控负责目标、next、委派、提交确认和进度。一个项目只有一个 pending，不并行写连续章节；先完成 context_scan/context_reduce 等准备任务，再 next 取正式任务。

写到卷末须核对约定体量与剧情承载，派工批次不等于卷末。未来设计用 replan，已写结构问题走修订；不改库或取消有效任务来强制转阶段。

每项任务使用空历史新 Worker；事实与读者审稿不复用执笔者或看过执笔解释的 Worker。

### 宿主适配与真实派工

按当前工具描述和参数 schema 选择入口，模型名称不决定宿主工具。

| 宿主 | 常见委派入口 | 新 Worker 与历史隔离 | 等待完成 |
|---|---|---|---|
| Claude Code | `Agent`，旧版可能为 `Task` | `subagent_type: "general-purpose"` 或空历史自定义 Worker；不选 `fork`，不传旧 `resume` | 前台返回或后台完成通知/等待工具 |
| Zcode | `Agent` | 新建 `general-purpose` 或等价 Worker，角色字段按 schema 填写 | 前台返回或后台完成通知 |
| Codex | `spawn_agent`，如 `collaboration.spawn_agent` | 支持 `fork_turns` 时设为 `"none"`，核实历史隔离 | 当前等待工具或完成通知，如可用的 `collaboration.wait_agent` |
| Antigravity | `invoke_subagent` | `Subagents` 仅一个规格：`TypeName: "self"`、`Workspace: "inherit"`、`Model: "inherit"`；`Role` 标阶段，`Prompt` 带任务材料 | 子会话完成通知或状态工具 |

入口与隔离说明：[Claude Code](https://code.claude.com/docs/en/sub-agents)、[Zcode](https://zcode.z.ai/cn/docs/subagents)、[Antigravity](https://antigravity.google/docs/subagents)。Antigravity 的 Workspace 继承工作目录，不继承聊天历史。

Worker 须能读 ROOT、读写同一 PROJECT、执行 measure/submit；只读 Explore/Plan 不适用。独立审稿者不能带执笔记忆或旧稿解释。共享目录与历史隔离分别核验，不另建账本副本。

沿用当前模型和推理设置，省略模型覆盖或填工具支持的继承值；所有模型使用同一任务协议和审稿门禁。

其他入口须具备新建模型 Worker、隔离父历史、项目读写、命令执行和完成返回能力。隔离不明则报告未知，不杜撰工具或把工具调用当终端命令。

doctor/next 不启动 agent，主控须真实调用委派工具。有委派能力时，续写、无人值守和恢复均由 Worker 执行；主控只读信封与短状态。

保留 Worker ID 或同步返回结果，等待真实完成。启动成功、partial 或轮数耗尽不算提交成功。运行中不重复派工、不抢写 output、不 next；完成后用 status 核对 pending 和预期状态。仍是同一 pending 就处理提交错误，确认旧 Worker 退出后恢复，不轮询 next 代替等待。

委派消息仅带：

- ROOT、PROJECT、当前 task.json 的绝对路径；task.json 与信封 input_path 同目录。
- 只完成该任务，读 input 一次，遵守 discipline 与阶段指南；brief 为替代视图。
- 写 output_path 后真实 submit，按错误修正；成功后返回 task_id、stage、状态和短错误，退出，不 next、不复述正文或完整回执。

Worker 从 ROOT 读 Skill 与阶段指南，命令用绝对路径，不依赖父环境变量、不嵌套派工。结果须写成 `result/1` JSON 文件，再 measure/submit；聊天代码块不算文件。

正文、补丁或原文复用先 measure，再按 [writing](writing.md) 自查并 submit；纯 expand_context/context_more 请求无需测量。measure 只读，短稿提交后保留并派定向返修。

阶段指南：规划读 design-and-brief，人物与索引读 characters，执笔读 writing，审稿读 editorial，准备读 context-runtime；协议只查相关字段。character_index 由新 Worker 完整阅读档案和来源章，不能证明适用范围时标 unknown/global，保留原文。

可每 3～5 章汇报进度，Worker 仍一次一任务。usage 观察重复成本，真实 token/费用以宿主为准；计划明确 topics，review_chunk（1～5）按完整阅读需要选择。

达到授权目标前自动续接；首章 await_author 由作者定调。质量、事实冲突或未授权改约阻碍须保留现场并报告。聊天关闭后的继续运行需要外部调度。

## 主控交接与单会话降级

主控窗口不足时，在 PROJECT 保存 ROOT/PROJECT、约定与批准状态、目标、进度、pending ID/hash、阻碍和下一命令。新会话从 status/next 恢复，不复制旧聊天或正文；清变量或退出 Worker 不会清空主控历史。

确无可用空历史入口或调用明确不支持时，说明缺失能力后降级为串行“同会话审稿”，不得声称独立审稿。工具改名应先查等价入口；权限、暂时或执行失败按真实错误处理，不静默降级。无法自动交接时保存恢复点并报告。

```bash
python3 "$ROOT/scripts/novel.py" pause --project "$PROJECT" --reason "作者暂停"
python3 "$ROOT/scripts/novel.py" audit --project "$PROJECT"
python3 "$ROOT/scripts/novel.py" resume --project "$PROJECT"
```

resume 先审计、释放旧租约，保留同一 pending 输入。丢弃无效任务须显式 pause --cancel-task，旧回执失效、失败次数保留。完整性失败不自动修库，旧进程或权限无法核实时停止。
