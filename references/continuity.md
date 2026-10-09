# 连续性与修订

## 数据边界

全书与分卷设计也保存在同一 SQLite，但始终属于未来计划。使用 [分层设计](design-and-brief.md) 的版本和失效机制管理，不把场景任务书中的站位提案当成已发生动作。

novel.sqlite3 保存约定、计划、正文版本、事件、回执和任务状态。work 为任务视图，export 为阅读导出；修改正式正文须走 revise。

entity 注册稳定 ID，alias 登记化名/称号；合并已有实体前审查历史，不静默合并。伤势、生死、能力、动机和关系用 state，关系建议 relation:<对方ID>。人物位置和独一物归属用 placement/move，计数物用 resource；旧位置属性不能与地图维护两套事实。

反复使用的场所及稳定特征见 [空间与稳定事实](spatial-continuity.md)：固定房间、办公室用途、楼层房号、出口和站位分开保存；搬迁须有变更事件，生日等固定事实不能当动态状态覆盖。

作者约定可初始化人物/资源；后续每项事件绑定章号、精确引文和字符位置。角色的 `knowledge` 单独记录 topic、claim、stance、source；错误信念不是世界事实。

人物小传与表达习惯是带版本的设计/解读，不是新事实。重要成长使用 character_development 保存变化与原因的正文证据，并触发相关档案重审；来源被历史修订移除时重新准备，见 [人物档案](characters.md)。

## 资源

所有数量写十进制字符串。`resource` 记录资产、单位、来源、去向、数量、两侧期初和理由。转移必须同单位守恒；外部获得或消费可有一端 null，但仍需要正文证明来源或用途。不能借 null 绕过资源不足。

单位换算须有世界约定，用同一正文证据下的“消耗旧单位＋获得新单位”两条事件表达；facts 核对汇率与余数。

不可量化的“所剩无几”“疲倦”保持叙事状态，不擅自填数。复活与不可逆伤势改变需要规则、来源和代价；`alive:false→true` 必须写 `exception_reason`，审稿仍需核实。

## 伏笔与承诺

hook 初始 `open`，保存首次证据、预计最迟回收章、重要性；后续可兑现 `paid` 或在正文交代取消 `closed`。延期必须给理由，同一钩子最多两次，之后应重审设计。facts 与最终入库检查 due≤当前章的 open 承诺；同章先埋后兑现合法，未交代则保留 pending、拒绝入库。程序能验证状态和引用，不能证明读者获得满意兑现。

重要性在埋设时明确；不能临近完本把重要伏笔改成不重要来绕过验收。未回收但刻意留下的开放问题应一开始就归为非核心，并符合约定；核心悬念不能靠声明“留白”逃避。

## 检索与工作包

`recall --entity <ID/姓名/别名>`；`--topic <topic/hook/asset/key>`；`--query <文字>`；用 `--start/--end/--offset/--limit` 控制范围。`--chapter N` 查询完整有效正文，超大结果分页；`--contract` 返回创作约定；bounded 模式 `--state` 必须带 `--entity`，可加 `--topic` 查当前前态；无目标历史检索被拒绝。

dependencies 指定必要历史全文，entities/topics/hooks 选择材料；omitted 列可选省略项。摘要作导航，疑点用 material/recall 或 context_more 核查；未选入不等于不存在。

分页、扫描归并和补读见 [context-runtime](context-runtime.md)，会话交接见 [runtime](runtime.md)。

## 修订和恢复

`revise --from-chapter N --reason ...` 从最早受影响章建立候选分支。系统复制未改前缀，按新稿重建其后事件、摘要、审稿和记忆；后续计划在重新写作前要核对因果，必要时在正文重设计任务中调整。修订完成并复核后，单一事务切换正式分支。

原分支始终留存；失败可 `revise --discard` 丢弃候选，恢复正式分支。`config --key published_through --value N` 冻结已经对外发布的正文，边界只前进；导出不等于发布，系统不自动上传小说。

候选可用 [版本补丁](repairs.md) 或 reuse_original，不免除必要复审。candidate 读取不可变基稿快照，不是事实来源。

恢复先 status、audit。完整性损坏时保留数据库、用可信备份重建，不猜测修库。普通中断恢复同一 pending；过期或取消结果拒绝。
