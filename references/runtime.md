# 运行、能力与恢复

## 最小运行条件

Python 3.9+、可读写项目目录、支持工具调用的模型宿主。所有脚本只有标准库依赖。以 `ROOT` 指向本 skill，`PROJECT` 指向书的绝对路径：

```bash
python3 "$ROOT/scripts/novel.py" init --project "$PROJECT" --contract /absolute/contract.json
python3 "$ROOT/scripts/novel.py" doctor --project "$PROJECT"
python3 "$ROOT/scripts/novel.py" status --project "$PROJECT"
python3 "$ROOT/scripts/novel.py" next --project "$PROJECT"
```

doctor 只验证实际能观察的 Python/SQLite 与配置；它不能探知所有模型平台能力。宿主需如实声明子代理委派能力、上下文大小及是否可报告用量。不绑定模型品牌；用户不必使用 Codex。

不同宿主的计费、配额与上下文上限由宿主控制；未知用量保持未知。独立性由委派实现：worker 一次一任务、工作包互不重叠，不依赖平台提供空会话。

## 会话内连续运行

新模板启用分层设计，先运行 world_plan 和 volume_plan，再进入章节任务。规划 worker 按 [分层设计](design-and-brief.md) 返回提案；写作 worker 完整读取 input_path，使用 writer_brief 明确场景与表达空间；brief_path 是替代视图，不重复加载。启用 profiled 的书还会按需派发 character_plan，见 [人物指南](characters.md)。固定只识别 plan/draft 的旧宿主接线须先适配新增阶段。

宿主主会话循环 next → 将当前任务委派给独立 worker → submit → next。同书串行；写作、事实核验、读者审稿分别委派，独立审稿不继承执笔聊天；不向子代理发“接管整本书”的无界任务。

v1.5 默认启用 [有界上下文](context-runtime.md)：next 可能先派发 context_scan/context_reduce，全部通过后才进入原阶段。主控只保留短状态；窗口接近上限时在新宿主会话恢复，任务与账本全部在数据库。没有自动调度，不承诺聊天关闭后持续执行。

## 暂停、恢复、接管

```bash
python3 "$ROOT/scripts/novel.py" pause --project "$PROJECT" --reason "作者暂停"
python3 "$ROOT/scripts/novel.py" status --project "$PROJECT"
python3 "$ROOT/scripts/novel.py" audit --project "$PROJECT"
python3 "$ROOT/scripts/novel.py" resume --project "$PROJECT"
```

正常停止会释放任务租约，可直接继续手工会话。

resume 通过严格审计后释放旧任务租约并保留同一 pending 输入。需要丢弃一个已知无效任务时显式 `pause --cancel-task`，旧结果永远失效，再 resume 后重新取任务。质量耗尽不能用频繁取消任务绕过，失败轮次仍保存在候选稿中。

数据完整性失败保留库和证据，不自动改历史。无法核实的旧进程、权限、账户或核心创作约定问题均如实停止。共享文件不是安全沙箱，恶意直接写数据库不在保障范围。
