# 运行、能力与恢复

## 最小运行条件

Python 3.9+、可读写项目目录、支持工具调用的模型宿主。所有脚本只有标准库依赖。以 `ROOT` 指向本 skill，`PROJECT` 指向书的绝对路径：

```bash
python3 "$ROOT/scripts/novel.py" init --project "$PROJECT" --contract /absolute/contract.json
python3 "$ROOT/scripts/novel.py" doctor --project "$PROJECT"
python3 "$ROOT/scripts/novel.py" status --project "$PROJECT"
python3 "$ROOT/scripts/novel.py" next --project "$PROJECT"
```

doctor 只验证实际能观察的 Python/SQLite 与配置；它不能探知所有模型平台能力。宿主需如实声明是否可新建无历史会话、是否可持续调度、上下文大小及是否可报告用量。默认 serial，不绑定模型品牌；用户不必使用 Codex。

有独立会话能力时设置 `config --key session_mode --value fresh`；由宿主提供真实会话 ID。fresh 回执是宿主承诺，数据库检查 ID 不复用，但不能从字符串证明平台确实清空了历史。不同宿主的计费、配额与上下文上限由宿主控制；未知用量保持未知。

## 会话内连续运行

新模板启用分层设计，先运行 world_plan 和 volume_plan，再进入章节任务。规划 worker 按 [分层设计](design-and-brief.md) 返回提案；写作 worker 完整读取 input_path，使用 writer_brief 明确场景与表达空间；brief_path 是替代视图，不重复加载。启用 profiled 的书还会按需派发 character_plan，见 [人物指南](characters.md)。固定只识别 plan/draft 的旧 driver 须先适配新增阶段。

宿主循环 next → 读取本次输入 → 完成一个角色任务 → 写 result → submit → next。同书串行；独立审稿不继承执笔聊天。有子代理能力的宿主可按当前授权分别委派角色，不向子代理发“接管整本书”的无界任务。

v1.5 默认启用 [有界上下文](context-runtime.md)：next 可能先派发 context_scan/context_reduce，全部通过后才进入原阶段。serial 会话累计接近声明容量时返回 await_session，保留状态；调度器必须在真实新会话执行 session-start 后续跑，不能在原聊天清零。主控也只保留短状态并交接。没有自动调度，不承诺聊天关闭后持续执行。

## 外部通用 driver

`assets/driver.example.json` 只给协议形状，不是已安装的服务。用户提供能调用自己模型通道的命令。先 check-only，再实际启动：

```bash
python3 "$ROOT/scripts/novel.py" run --project "$PROJECT" --driver /absolute/driver.json --check-only
python3 "$ROOT/scripts/novel.py" run --project "$PROJECT" --driver /absolute/driver.json
```

`argv` 是参数数组，绝不通过 shell；只支持 `{task_file}/{result_file}/{project}/{feedback_file}`。同时提供环境变量 `NOVEL_TASK_FILE/NOVEL_RESULT_FILE/NOVEL_FEEDBACK_FILE/NOVEL_LEASE_OWNER`。

repair 可能只提供 excerpts；写作 driver 应支持 patches 回执与 candidate 分段读取，而不是假定每个写作任务都有整章 prose。局部修订需要扩大上下文时走受控 expand_context；详见 [局部修订](repairs.md)。

driver 要读取 task_file 和其中 input_path，按协议调用模型，写 result_file，退出码 0。它只产候选，不直接 submit；控制器提交并更新状态。fresh 驱动每次创建空会话，必须把实际 session 回执放进结果。没有驱动时也可以用原生宿主工具完成同一任务。

driver 样例默认 fresh，仅在真正支持每次新建空上下文时采用。失败尝试的会话 ID 同样不能复用。serial 累计失败输出与补读，对无法解析/缺失回执保守扣除完整输出预留，重试再次输入另计。适配器须处理 context_scan/context_reduce、context_more 与 await_session，详见 [准备与交接协议](context-runtime.md)。

timeout 默认 1800 秒；max_retries 默认 2。每次尝试独立输入输出目录及 token；重试收到前一错误反馈。旧结果不能复用于新租约。临时失败可重试相同任务；语义错误必须根据证据修正。配置 `--max-tasks N` 可做短程接线验证，默认 0 表示持续至停止条件。

SQLite 租约保证同书只有一个控制器，10 秒心跳、60 秒租期；进程启动门确保真实模型驱动启动前已持久化其 PID。超时先终止模型进程后重试；POSIX 使用进程组，其他平台不能宣称具备同等级孙进程回收。

## 暂停、恢复、接管

```bash
python3 "$ROOT/scripts/novel.py" pause --project "$PROJECT" --reason "作者暂停"
python3 "$ROOT/scripts/novel.py" status --project "$PROJECT"
python3 "$ROOT/scripts/novel.py" audit --project "$PROJECT"
python3 "$ROOT/scripts/novel.py" resume --project "$PROJECT"
```

正常停止会终止活动 driver 并释放任务租约，可继续使用 driver 或手工会话。父控制器被强制杀死时，driver 可能仍存活：新控制器会检查保存的子进程 PID，拒绝重复派发，待其退出或确认终止后才接管。不能仅凭心跳过期当作旧写手不存在。

resume 确认旧控制器和 worker 均退出后，释放旧任务租约并保留同一 pending 输入。需要丢弃一个已知无效任务时显式 `pause --cancel-task`，旧结果永远失效，再 resume 后重新取任务。质量耗尽不能用频繁取消任务绕过，失败轮次仍保存在候选稿中。

数据完整性失败保留库和证据，不自动改历史。无法核实的旧进程、权限、账户或核心创作约定问题均如实停止。运行器与共享文件不是安全沙箱，恶意直接写数据库不在保障范围。
