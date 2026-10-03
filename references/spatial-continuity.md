# 场所、场景调度与稳定事实

## 地图画到会影响故事的程度

每个场景明确地点、时间或先后关系、在场者和关键物件；不是每个场景都要完整平面图。按叙事需要细化：

| 场景用途 | 最少保存什么 |
|---|---|
| 一次性普通场景 | 稳定地点 ID、所在大区域、已写出的必要特征、在场人物；不编造米数 |
| 反复使用的家、办公室、据点 | 父场所、楼层/房号、关键房间或区域、出入口、影响行动的家具与遮挡 |
| 追逐、潜入、战斗、密室与旅途 | 上述信息加可通行连接、方向、障碍、必要的最短耗时、视线/声音连接、人物和关键物品站位 |

地图是当前有效正文与作者约定的派生视图。候选平面图只能作计划，不能抢先成为事实；没有登记的空间是未知，不代表不存在。无需画出所有抽屉，也不要为了账本让人物在正文里报坐标。地图默认是局部拓扑图，不按比例；仅有正文依据时登记距离或耗时。复杂建筑分楼层/区域读图。

## “局长办公室”不是一个会变地址的房间

拆成三层：`hq` 是市局大楼，`r4` 是四楼 101 这个物理房间，`office` 是“局长办公室”这一用途。作者约定或第一章明确写出后，固定 `r4.floor="4"`、`r4.room_number="101"`，用途绑定 `office.office_place="r4"`。不要按 101 推算成一楼。

后文再次写到时核对 ID、楼层、房号与用途绑定：

- **无搬迁，写成三楼 505**：事实错误，修正文；不能为迁就新稿覆盖旧设定。
- **办公室搬迁**：先演出可核实的搬迁原因和结果，以 `anchor_change` 将用途绑定到另一个已登记房间 `r3`；原四楼 101 仍在。
- **房号重编或建筑改造**：用 `place_change` 给同一物理 ID 记录前后属性、原因、变更正文证据。父建筑不可直接更换。
- **人物说错、回忆旧址或故意撒谎**：记录人物认知/误信，不能覆盖客观地址。
- **第一章本来写错**：走 `revise` 重审后文，不能伪造搬迁遮掩作者错误。

房间里的关键位置可以细化成子节点，例如门内、屏风后、窗边。桌上抽屉可登记为容器实体，让钥匙由抽屉持有；人物、容器、车辆与物品使用统一持有链。已有地图站位后，不再另用 `state.location` 保存第二套位置。

## 写作与事实审稿的动作

世界框架和分卷候选布局在 [分层设计流程](design-and-brief.md) 中准备；直到作者约定明确或正文实际确立，才成为地图事实。写作以结构化场景任务书输入，不预写全书所有场景。

规划时用可选 `scenes` 提供涉及的已知地点及人物，例如 `[{"place":"r4","present":["hero"]}]`。新地点尚未发生，用 `{"label":"尚未到过的修理铺","present":["hero"]}` 提案，正文发生后再登记 ID。场景数量自由，不要求固定结构。

执笔读取工作包 `continuity`：相关稳定事实、用途绑定、局部地图、路径条件和站位。关键空间首次出现，给读者足够定位；后续描述不必重复报门牌。安排动作之前检查入口、谁在门哪边、物品握在哪只手、是否看得见/听得到、移动是否来得及。计划之外新增的暗门、伤疤、手套、武器交接也要提取，不能只登记预定事项。

facts 从本次 `payload.checks` 取检查项。有稳定事实时增加 `stable_facts`，有地图或场景提案时增加 `spatial`，均须给正文证据。除地址外，逐场景核对：进入前位置 → 行动/转手/感知 → 离开后位置；同章多次移动按因果顺序登记。跨句指代如“他进了办公室。这屋在三楼”，也由审稿者主动回查，而不是等程序提示。

`address_signals` 仅提示部分同句地址冲突，不作语义裁决。每条提示都须在 `address_responses` 回应：

```json
{"id":"复制提示ID","status":"error","reason":"没有搬迁，三楼505与现有四楼101冲突","evidence":{"chapter":2,"start":0,"quote":"实际正文原句"}}
```

status 为 `not_conflict / error / uncertain`；只有全部 `not_conflict` 且给出理由/证据才可 pass。`error/uncertain` 必须给 fix 和具体 findings；合法搬迁、历史引述、否定句也可能触发提示，应核对后解释。无提示不等于无矛盾；空 events 不等于没发生变化。

每十章、卷末与全书复核，抽取反复出现的据点和关键动作链，比较首次设定、当前状态和中间变更证据。发现源头错误，修订从最早受影响章开始；不要只修最后一次提及。

## 事件接口（所有事件须带 evidence）

精确引文格式见 [结果协议](protocol.md)。样例约定见 [spatial.example.json](../assets/spatial.example.json)，其中分钟是该书自选的时钟单位。

| op | 字段与约束 |
|---|---|
| `anchor` | `entity,key,value,policy`；policy 默认 fixed，也可 eventful；固定出生日期/惯用手/房间属性，用途或职务可 eventful。重复同值可接受，改值拒绝 |
| `anchor_assert` | `entity,key,value`；核对再次明确出现的已锁定事实，错误值拒绝；不是把所有句子再抄一遍 |
| `anchor_change` | `entity,key,before,after,reason,change_evidence`；仅 eventful 可变，前值必须匹配 |
| `place` | `entity,parent,attributes`；实体先登记 kind=place，父节点先登记，attributes 如 `{"floor":"4","room_number":"101"}`，房号和楼层为字符串；同父区域同楼层不可重复房号 |
| `place_change` | `entity,before,after,reason,change_evidence`；before/after 为相同键集合的属性对象，不能更换 parent |
| `link` | `id,a,b,kind`；kind 为 path/sight/sound；可选 directed=false、open=true、label、min_ticks、requires。min_ticks 只用于路径，缺省为未知；requires 如 `{"door:open":true,"lamp:lit":true}` |
| `link_change` | `id,before,after,reason`；前后为连接 open 布尔值；门锁等实体条件仍用 state |
| `placement` | `entity,before,after`；位置恰为 `{"place":"r4"}` 或 `{"holder":"hero"}`，首次 before=null。交接须在同一已知地点；持有链不能成环 |
| `move` | `entity,from,to,route,links`；每段显式指定 path ID；可选 depart/arrive 必须同时给或同时未知，单位为 clock_unit。已知最短耗时仍约束整条路线；未知段不捏造数值 |
| `perceive` | `observer,target,channel,from,to`；channel=sight/sound；跨地点须给直接 link。通路不等于视线，视线不等于听觉，感知也不自动成为 knowledge |

搬迁示例（evidence 与 change_evidence 必须换成本次有效原文位置）：

```json
{
  "op":"anchor_change","entity":"office","key":"office_place",
  "before":"r4","after":"r3","reason":"原办公室整修，正式迁入三楼505",
  "evidence":{"chapter":8,"start":0,"quote":"本章实际搬迁原句"},
  "change_evidence":{"chapter":8,"start":0,"quote":"本章实际搬迁原句"}
}
```

作者约定可用 `anchors/places/links/placements` 初始化，字段与事件表相同但不带 op/evidence；初始化 placements 不填 before。来源标为 author_contract。书中后续首次出现的设定须通过章节事件提交，不能改约定补写过去。

有时间戳时，物品继承携带者的到达下限，转手或放下不能抹掉；接收者再次移动也不能早于所携物已确定的时间。位置未知时先建立有证据的站位，不能借“转交一个未定位的人”绕过路径。没有精确时间就省略成对时间字段，结果标为未验证，不硬填分钟。

## 其他同类漂移

| 易漂移内容 | 登记方式与审查重点 |
|---|---|
| 年龄、日期、昼夜、季节 | 出生日期用 fixed anchor；时间推进用 clock；年龄、星期与历法换算、倒叙和跨日由语义核验，不把动态年龄锁为永恒常数 |
| 左右手、伤疤侧别、外貌与身体状况 | 稳定特征用 anchor；新增伤势、换装和恢复用 state；核对受伤侧是否仍能完成动作、伤势是否无故消失 |
| 身份、官职、隶属、称谓、亲属关系 | 稳定人物 ID+alias；职务变迁用 eventful anchor+任免证据；事实亲属关系与人物误信分开，禁止一换名字就成新角色 |
| 独一物品、武器、文件、容器与车辆 | 每件重要物品稳定 ID+placement；核对取得/交出/丢失/毁坏，现金或弹药数量另用 resource；现有工具不从“毁坏”自动推断所有动作禁止，审稿须检查可用性 |
| 能力条件、冷却、伤势代价、资源单位 | 规则来自约定，动态状态与精确收支分开；语义核查触发条件、代价和换算，不能以新招式名抹掉旧限制 |
| 门窗、遮挡、光照、天气、声音与消息来源 | 动态 state+连接 requires；分别审查能通过、能看到、能听到和能理解，不能因为作者知道就让角色知道 |
| 关系、承诺、情绪余波 | state/knowledge/hook+来源；检查冲突后关系是否重置、旧秘密是否重复首揭、兑现是否真正改变人物处境 |

以上是按本书相关性选用的核对框架；程序严格验证已建模字段、事件和证据，不声称自动理解所有自然语言。属性名在一本书内保持统一，先检索再新增，避免 `birthday` 与 `birth_date` 成为两份矛盾事实。

## 查看当前及历史地图

```bash
python3 "$ROOT/scripts/novel.py" map --project "$PROJECT" --place "局长办公室"
python3 "$ROOT/scripts/novel.py" map --project "$PROJECT" --place hq --through 1 --depth 2 --format mermaid
```

返回 JSON；Mermaid 源码在 `diagram` 字段。`--through 0` 为作者约定初态，省略时为当前工作分支已验收章末，可指定 `--branch`。`depth` 为 0～4；大图分区查看。注册名称可保留历史房号便于检索，显示与写作使用 `display_name` 及当前 address。不要手改导出地图充当正文修订。

v1.1 是 v2 项目的增量协议扩展：未使用空间事件的项目保持原状态摘要，旧项目不会自动长出地图。增加空间记录后须使用 v1.1+ 工具；原 novel-ledger 数据库仍不在兼容范围。旧 location/holder 首次转为 placement 时会核对已有位置，矛盾须修订，不能覆盖。

地图、稳定锚和必要来源进入工作包的必需部分；放不下会明确报容量问题，不静默删掉门牌或出口。地图没有表达的障碍、同一节点内的遮挡、复杂视距、地形坡度、天气及生理条件仍需审稿；必要时细分节点与条件。词法提示漏掉跨句指代不构成通过依据。
