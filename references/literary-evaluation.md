# 独立正文质量评测
何时读：离线盲测审稿门禁的样本、隔离与计分；不用于正式审稿。

对照评测检查审稿门禁发现问题的能力，不替代正式审稿。正文、变体、预期和回执存 PROJECT/evaluation。

## 样本与隔离

选完整目标章和必要依赖，标真实卷界。比较首稿、修复稿与定位变体：提前知情、动机突变、代价抹除、因果越界、承诺未兑和重复解释；用成立的静章/高潮作对照。

准备 manifest（UTF-8 JSON），chapters 包含 chapter/path，可选真实 volume；cases 包含内部 label、完整源章列表 chapters、目标章 targets、可选 edits 与 expectations。edit 包含 chapter/start/old/new/criterion，必须精确定位且不重叠。criterion 为 knowledge/motivation/cost/causality/progress/payoff/language/pacing。预期与修改理由只留私有文件，Worker 仅收到随机 ID 的 input.json，不知道是原稿还是变体。

```bash
python3 "$ROOT/scripts/quality_eval.py" prepare --manifest "$PROJECT/evaluation/manifest.json" --out "$PROJECT/evaluation/run-new"
python3 "$ROOT/scripts/quality_eval.py" score --directory "$PROJECT/evaluation/run-new"
```

prepare 不调用模型。每份样本派空历史新 Worker，只读本样本，不读 manifest/private/其他回执；先 first_read，再核对依赖正文。事实与读者可分别独立评测，不把同一人重读当新盲审；不适用须解释。

language 按 [声线规则](voices.md) 判断，修复稿同样执行，证据引文原样保留。

回执写本 case 的 review.json，包含 case_id/input_hash/first_read/checks/findings；八个 checks 各含 id/result/reason/evidence，result 为 pass/fix/not_applicable。finding 含 criterion/severity/reason/evidence，severity 为 blocking/major/minor。fix 必有对应 finding；证据为 `{chapter,start,quote}`，按本 case 完整 prose 坐标精确引用必要语境。input_hash 为规范 JSON（ensure_ascii=False/sort_keys=True/separators=(',',':')/allow_nan=False）的 SHA256。

## 计分与修复

score 验证冻结样本、精确证据和 checks/finding 一致性，统计植入缺陷的定位命中与漏检、原稿报警数、实际协议字符。定位只要求对应 criterion 的 finding 证据覆盖修改处，主控仍需独立检查其理由确实指向预期语义问题，不能让擦边引文冒充发现。

原稿不是金标准。误报/精确率须有独立裁决：expectations 中填写 criterion/expected（pass/fix）、adjudicator 与 reason；未裁决项计 pending，不强算真阴性或零误报。模型二次评价仍应注明来源，不能冒充人类作者/专家。实际费用未知就保留 billed_tokens=null。

保留原稿，定点修补真实缺陷；新评审读完整修复稿及依赖，重新首读，核对缺陷消失且无新增损失。跨卷验收须有实际卷界、前卷承诺和后卷兑现，区分覆盖、漏检、争议与未验证项。
