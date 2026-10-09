# 局部返修与文本补丁

遵守 [声线规则](voices.md)，只修改问题涉及的正文，证据引文原样保留。

## 小问题只写修改部分

能明确定位且不改变整章因果结构的问题，优先局部返修：错别字、地址误写、一处重复说明、一段对白同腔、遗漏的短动作或不通顺的衔接。审稿 fix 时增加 `repair_scope: "local"`，findings 仍须给精确正文位置、读者影响和修订目标。

全章动机、视角、结构或多处因果重排用 repair_scope=chapter；省略则用整章上下文，不把结构问题缩成局部。

局部输入含目标及相邻段落、绝对范围、基稿哈希、省略字符数和必要约束。按换行分段，不切碎长段；单段章节可能仍交付全章。

## 提交补丁

信封不变，data 例如：

```json
{
  "base_hash":"复制本次 payload.base_hash",
  "patches":[
    {"start":123,"old":"原设定描述错误文本。","new":"修正后的客观事实文本。"}
  ],
  "repair_note":"修正笔误与客观事实，其余段落保持不变"
}
```

start 是**完整基稿的 Python 字符位置**，不是片段内位置、UTF-8 字节位置或“第几段”。使用输入 excerpt.start 加片段内精确位置计算；引文 old 必须与该位置完全一致。所有补丁针对同一个基稿，不能把前一个补丁造成的位移计入后一个 start。

- `patches` 非空，每项恰为 start/old/new。old 非空；插入新句时连同邻近原文作为替换锚，不能对空字符串插入。写成 `{start,end,text}` 的区间替换形式会被自动换算为 start/old/new（old 即该区间原文），区间越界或为空不换算、按 `patch` 报错。
- 可以删除某段（new 为空），但不能清空整章。
- 不允许重叠补丁、原样替换、错误哈希或错位原文。重复出现的相同句子依靠 start 精确定位，只改目标位置。
- 局部任务只能改已提供 excerpt 范围内的文本。工具先验证全部补丁，再从后往前合成；任一失败则原任务、草稿和正式稿均保持不变。
- 一次提交在 prose/patches/reuse_original 中只能选择一种。局部 repair 不接受整章 prose。

补丁合成候选全文后重新 facts/reader，重核事件、摘要与证据；唯一引文可重定位，见 [protocol](protocol.md)。回执和基稿快照留存，重复 submit 幂等。

facts 复审须对账 prior_events。auto_carry 自动续算符合条件的事件，events 只报新增/变更；其余用 prior_dispositions 声明 dropped/replaced，也可覆盖自动续算。静默丢弃报 event_accounting；续算的语境条件见 protocol，审查仍覆盖全章。

## 需要更多上下文时

按任务 ID 分段读取原候选，避免把尚未验收的稿件误当成 recall 返回的正式正文：

```bash
python3 "$ROOT/scripts/novel.py" candidate --project "$PROJECT" --task TASK_ID --start 0 --end 2000
```

范围为 Python [start:end]。candidate 返回不可变基稿和哈希；省略 end 时读当前段落余文，显式范围按版本和坐标校验。任务完成后仍可读；补读不扩大可改范围。

若还需改动未提供的段落，局部 repair 可提交：

```json
{"expand_context":true,"reason":"该处修改涉及前文因果交代，需联动校准"}
```

expand_context 保留草稿和次数，派完整正文 repair；只允许一次。仍可交 patches，redesign 则须完整 prose 与 design_change，不能重置失败计数。

## 篇幅不足的定向补足

短稿作为基稿，按 chapter_length.shortfall_han 与 execution.expansion_units 用补丁补实未演出的阻力、选择、关系和后果。先 measure 合成全文，再 submit；达标仍完整两审，不以重复解释充数。

两轮仍不足转 redesign，可用 plan_update 调整展开与节奏，不降目标；再失败保留现场。作者变更目标可重评，但不抹去语义缺陷或返工次数。

## 已有正文修订

必须先走 revise 建立候选分支。该分支 draft 有原稿时，提供 base_hash；可提交同样的 patches/repair_note，不必重发整章。后续某章经核对无需改变，可提交：

```json
{"base_hash":"本任务原稿哈希","reuse_original":true}
```

这省去原样正文重传。只有本章及全部前文版本、持久审稿上下文签名均未变，原两审通过且事件可复算时才免逐章审稿；其余情况重新核验。旧章缺签名会保守复审。新写首稿没有基稿，不接受补丁或复用；已发布正文的冻结规则不变。候选全部验证前，正式分支不变。
