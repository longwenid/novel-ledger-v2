"""Architecture contracts (references/architecture.md) as executable guards.

One test per principle or invariant, so a change that violates the contract
fails here rather than surfacing as a production incident. These are structure
tests: they never assert literary behaviour.
"""
import ast
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / 'scripts' / 'novel_core'
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from novel_core import access, atoms, batchflow, engine, taskio, transitions
from novel_core.store import Error, VERSION, connect, initialize, put
from support import contract, envelope

# 架构契约第四节的分层。编号越小越底层；模块级 import 只能指向 <= 自己的层。
LAYERS = {
    0: ('store', 'transitions', 'execution'),
    1: ('access', 'atoms', 'wire', 'ledger', 'patches', 'budget', 'spatial',
        'sources', 'projections', 'dependencies', 'recovery', 'reviewflow',
        'characters', 'design', 'contextflow', 'taskio', 'taskview', 'schedule', 'hostflow', 'observability'),
    2: ('batchflow', 'settlement', 'memory', 'reviewrun'),
    3: ('bookpage', 'carriage', 'numeric', 'engine'),
}
LAYER_OF = {name: level for level, names in LAYERS.items() for name in names}

# 架构契约第四节登记的延迟绑定。模块级 import 只能向下，需要向上调用时用
# 函数内 import —— 那是显式的回环，必须已知且被复核过。新增一条而未登记，
# 守护测试即失败，架构漂移在合并前暴露。
LATE_BINDING = {
    'batchflow': ('engine',),
    'contextflow': ('memory',),
    'design': ('engine',),
    'reviewflow': ('batchflow', 'engine'),
    'reviewrun': ('engine',),
    'settlement': ('engine',),
    'store': ('budget', 'dependencies', 'ledger', 'projections', 'sources'),
    'taskio': ('batchflow', 'engine'),
}


# 跨角色/跨进程边界上被依赖的协议标识。账本 schema 用完整形式；其余用协议
# 标识本身，因为代码里既有 `sealed-review/1` 这样的比较常量，也有
# `novel-ledger-v2.result/1` 这样的回执 schema —— 标识是二者的共同子串。
# 内部形状标识（status/pack/settlement/review-context/impact/character-view/
# character-index/writer-brief 等只用于标记来源或划分哈希域）不在此列，
# 这个划分是显式决定，并由下面的测试锁定。
WIRE_PROTOCOLS = (
    'novel-ledger-v2/2',
    'task/1',
    'result/1',
    'handoff/1',
    'controller/1',
    'preflight/1',
    'preparation/1',
    'chapter-batch/1',
    'sealed-review/1',
    'review-run/1',
    'same-bundle/1',
    'numeric-patch/1',
    'quote_optional_start/1',
    'task-reading/1',
    'chapter-timeline/1',
    'host-dispatch/1',
    'host-observation/1',
    'chapter-execution/1',
    'explicit-time-input/1',
    'review-scrutiny/1',
    'task-reading-bundle/1',
)


def package_imports(node, sink):
    """Collect intra-package imports from one AST node.

    `from . import x` carries module=None, so reading only `node.module` misses
    it — the earlier scanner did exactly that and under-reported the graph.
    """
    if isinstance(node, ast.ImportFrom) and node.level == 1:
        if node.module:
            sink.add(node.module.split('.')[0])
        else:
            for alias in node.names:
                sink.add(alias.name.split('.')[0])
    elif isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name.startswith('novel_core.'):
                sink.add(alias.name.split('.')[1])


def module_imports():
    """Module-level intra-package imports per module (function-level ones are
    deliberate late binding and are checked separately)."""
    graph = {}
    for path in sorted(CORE.glob('*.py')):
        if path.stem == '__init__':
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'))
        deps = set()
        for node in tree.body:
            package_imports(node, deps)
        graph[path.stem] = deps - {path.stem}
    return graph


def late_binding_imports():
    """Upward imports performed inside functions, i.e. registered cycles."""
    graph = {}
    for path in sorted(CORE.glob('*.py')):
        if path.stem == '__init__':
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'))
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for sub in ast.walk(node):
                    package_imports(sub, found)
        upward = tuple(sorted(dep for dep in found
                              if dep in LAYER_OF and LAYER_OF[dep] > LAYER_OF[path.stem]))
        if upward:
            graph[path.stem] = upward
    return graph


class LayeringTests(unittest.TestCase):
    def setUp(self):
        self.graph = module_imports()

    def test_every_module_is_registered(self):
        """A new engine module must declare its layer before it can be imported
        from, so the dependency direction stays reviewable."""
        unregistered = sorted(set(self.graph) - set(LAYER_OF))
        self.assertEqual(unregistered, [], f'未登记层号的模块：{unregistered}')

    def test_module_imports_point_downward(self):
        """INV-10: module-level imports never point at a higher layer. Upward
        calls must be function-level imports, which are visible in review."""
        violations = []
        for name, deps in self.graph.items():
            for dep in deps:
                if LAYER_OF.get(dep, -1) > LAYER_OF[name]:
                    violations.append(f'{name}(L{LAYER_OF[name]}) -> {dep}(L{LAYER_OF[dep]})')
        self.assertEqual(violations, [], '模块级 import 指向了更高层：' + '; '.join(violations))

    def test_module_graph_is_acyclic(self):
        """A cycle at module level makes import order significant and hides
        coupling; the engine uses function-level imports instead."""
        state, cycles = {}, []

        def visit(node, trail):
            if state.get(node) == 'open':
                cycles.append(' -> '.join(trail + [node]))
                return
            if state.get(node) == 'done':
                return
            state[node] = 'open'
            for dep in sorted(self.graph.get(node, ())):
                if dep in self.graph:
                    visit(dep, trail + [node])
            state[node] = 'done'

        for name in sorted(self.graph):
            visit(name, [])
        self.assertEqual(cycles, [], '模块级依赖出现环：' + '; '.join(cycles))

    def test_late_bindings_are_registered(self):
        """需要向上调用时只能用函数内 import，且必须在架构契约第四节登记。
        新增一条未登记的回环即失败，避免架构在无人察觉时漂移。"""
        self.assertEqual(late_binding_imports(), LATE_BINDING,
                         '函数内向上调用与登记表不一致：新增回环须先在 references/architecture.md 登记')


class AccessTierTests(unittest.TestCase):
    """P2 默认拒绝 + INV-9。"""

    def subcommands(self):
        from novel import parser
        group = parser()._subparsers._group_actions[0]
        return set(group.choices)

    def test_registry_covers_every_subcommand(self):
        declared, registered = self.subcommands(), set(access.COMMAND_LEVEL)
        self.assertEqual(sorted(declared - registered), [],
                         '这些子命令没有登记访问级别，会被默认拒绝')
        self.assertEqual(sorted(registered - declared), [],
                         '分级表登记了不存在的子命令')

    def test_unregistered_command_is_refused(self):
        with self.assertRaises(Error) as caught:
            access.level_of('not-a-command')
        self.assertEqual(caught.exception.code, 'access')
        self.assertIn('recover', caught.exception.details)

    def test_content_readers_are_sealed(self):
        """Regression lock: html/export/audit expose the contract, the ledger
        and every committed chapter, so they belong to the sealed tier. They
        shipped unguarded once and leaked the whole book before a first read."""
        for command in ('recall', 'map', 'design', 'character', 'candidate',
                        'material', 'impact', 'patch', 'audit', 'html', 'export'):
            self.assertEqual(access.level_of(command), access.SEALED, command)

    def test_config_tier_depends_on_read_or_write(self):
        class Args:
            value = None
        self.assertEqual(access.level_of('config', Args()), access.SEALED)
        Args.value = '3'
        self.assertEqual(access.level_of('config', Args()), access.PUBLIC)


class DraftLadderTests(unittest.TestCase):
    """INV-4 + P1：draft['stage'] 由阶梯合成唯一决定。"""

    def test_single_ladder_matches_the_documented_budget(self):
        self.assertEqual(transitions.resolve_stage(quality_rounds=1)[0], 'repair')
        self.assertEqual(transitions.resolve_stage(quality_rounds=2)[0], 'repair')
        self.assertEqual(transitions.resolve_stage(quality_rounds=3)[0], 'redesign')
        self.assertEqual(transitions.resolve_stage(quality_rounds=4)[0], 'redesign')
        self.assertEqual(transitions.resolve_stage(quality_rounds=4)[1], ['quality'])
        self.assertEqual(transitions.resolve_stage(length_rounds=4)[1], ['chapter_development'])

    def test_the_stricter_ladder_wins(self):
        """The regression: a length shortfall used to demote a chapter the
        semantic ladder had already escalated to redesign."""
        self.assertEqual(transitions.resolve_stage(quality_rounds=3, length_rounds=1)[0], 'redesign')
        self.assertEqual(transitions.resolve_stage(quality_rounds=1, length_rounds=3)[0], 'redesign')
        self.assertEqual(transitions.resolve_stage(quality_rounds=2, length_rounds=2)[0], 'repair')

    def test_exhaustion_is_reported_per_ladder(self):
        self.assertEqual(transitions.resolve_stage(quality_rounds=4, length_rounds=1)[1], ['quality'])
        self.assertEqual(transitions.resolve_stage(quality_rounds=1, length_rounds=4)[1],
                         ['chapter_development'])
        self.assertEqual(transitions.resolve_stage(quality_rounds=4, length_rounds=4)[1],
                         ['quality', 'chapter_development'])


class CharacterCoverageTests(unittest.TestCase):
    """INV-13：已落地章节的拟出场人物必须有可用档案。

    真实症状：章节写了很多，角色表却没有对应条目。原因是调度只检查「即将规划的
    那一章」，重建的后缀、就地改过的计划或携带章都能绕过它。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        c = contract()
        c['character_mode'] = 'profiled'
        initialize(self.path, c)
        self.db = connect(self.path)
        self.addCleanup(self.db.close)
        people = {'profiles': [{'entity': 'hero'}, {'entity': 'ally'}]}
        with self.db:
            self.db.execute('INSERT INTO plans VALUES (?,?,?)',
                            ('main', 1, json.dumps({'number': 1, 'volume': 'v1',
                                                    'entities': ['hero', 'ally'],
                                                    'goal': 'g', 'conflict': 'c', 'change': 'x'})))
            self.db.execute('INSERT INTO chapters VALUES (?,?,?,?,?)',
                            ('main', 1, '正文', json.dumps({'events': [], 'summary': 's'}), 'h'))

    def test_a_participant_without_a_profile_is_a_gap(self):
        from novel_core import characters
        report = characters.coverage(self.db, 'main')
        self.assertEqual([gap['entities'] for gap in report['gaps']], [['ally', 'hero']])
        self.assertFalse(report['complete'])

    def test_first_gap_stops_at_the_first_chapter(self):
        from novel_core import characters
        gaps = characters.coverage(self.db, 'main', first_gap=True)['gaps']
        self.assertEqual([gap['chapter'] for gap in gaps], [1])

    def test_unwritten_chapters_are_never_judged(self):
        """未落地章的 replay 状态为空，不能据此判定档案失效。"""
        from novel_core import characters
        with self.db:
            self.db.execute('INSERT INTO plans VALUES (?,?,?)',
                            ('main', 2, json.dumps({'number': 2, 'volume': 'v1',
                                                    'entities': ['hero'], 'goal': 'g',
                                                    'conflict': 'c', 'change': 'x'})))
        self.assertEqual([gap['chapter'] for gap in characters.coverage(self.db, 'main')['gaps']], [1])

    def test_coverage_is_not_enabled_for_legacy_projects(self):
        from novel_core import characters
        from novel_core.store import put
        with self.db:
            put(self.db, 'contract', {**contract(), 'character_mode': 'legacy'})
        report = characters.coverage(self.db, 'main')
        self.assertFalse(report['enabled'])
        self.assertEqual(report['gaps'], [])


class RevisionStartTests(unittest.TestCase):
    """INV-11：修订起点是修复指令，不是举证位置。"""

    @staticmethod
    def finding(**extra):
        base = {'severity': 'quality', 'evidence': {'chapter': 2}}
        base.update(extra)
        return base

    def test_undeclared_fix_stays_in_the_newest_chapter(self):
        """回溯重写未被点名的章代价高且可能改坏已正确的正文；默认就地修。"""
        self.assertEqual(transitions.revision_start([self.finding()], through=7), 7)

    def test_notes_never_route(self):
        self.assertEqual(
            transitions.revision_start([{'severity': 'note', 'target_chapter': 1}], through=7), 7)

    def test_target_chapter_outranks_a_declared_route(self):
        self.assertEqual(transitions.revision_start(
            [self.finding(target_chapter=3),
             self.finding(repair_route='revise', evidence={'chapter': 1})], through=7), 3)

    def test_a_declared_route_routes_to_its_own_chapter(self):
        for route in transitions.REPAIR_ROUTES:
            with self.subTest(route=route):
                self.assertEqual(transitions.revision_start(
                    [self.finding(repair_route=route)], through=7), 2)

    def test_the_earliest_declared_target_wins(self):
        self.assertEqual(transitions.revision_start(
            [self.finding(target_chapter=5), self.finding(target_chapter=4)], through=7), 4)

    def test_malformed_entries_never_route(self):
        self.assertEqual(transitions.revision_start(
            [None, 'x', 7,
             {'severity': 'quality', 'target_chapter': '1'},
             {'severity': 'quality', 'repair_route': 'revise', 'evidence': 'nope'}],
            through=7), 7)

    def test_findings_before_the_revision_are_out_of_scope(self):
        """修订分支只回答「这次改动是否传播正确」。范围之前的发现是复审路过
        时读到的既有缺陷——为它们建链会把已完成的修订整条拉回（实测：未修订
        章里的一个异体字重做了九章的活，而真正的修订范围一次没被复核）。"""
        self.assertTrue(transitions.outside_revision_scope(
            [self.finding(severity='quality', evidence={'chapter': 2})], revision_start=5))
        self.assertTrue(transitions.outside_revision_scope(
            [self.finding(severity='blocker', evidence={'chapter': 1}),
             self.finding(severity='quality', evidence={'chapter': 4})], revision_start=5))

    def test_a_finding_inside_the_revision_is_in_scope(self):
        self.assertFalse(transitions.outside_revision_scope(
            [self.finding(severity='quality', evidence={'chapter': 5})], revision_start=5))
        self.assertFalse(transitions.outside_revision_scope(
            [self.finding(severity='quality', evidence={'chapter': 2}),
             self.finding(severity='quality', evidence={'chapter': 7})], revision_start=5))

    def test_notes_and_malformed_entries_are_never_out_of_scope(self):
        self.assertFalse(transitions.outside_revision_scope(
            [self.finding(severity='note', evidence={'chapter': 1})], revision_start=5))
        self.assertFalse(transitions.outside_revision_scope(
            [self.finding(severity='quality', evidence='nope')], revision_start=5))
        self.assertFalse(transitions.outside_revision_scope([], revision_start=5))


class ReviewSeverityTests(unittest.TestCase):
    """轻微缺陷必须有非返工的出口。

    真实事故：批次复审在未修订的第 2 章发现一个异体字（執→执）。判 note 被
    「返工须提供可执行发现」拒绝，而文档又写着「pass 的 findings=[]」，报告者
    只能把它升格为 quality —— 于是一个字触发了从第 2 章重做整条修订链。出口
    只有一个：note 随 pass。
    """

    QUOTE = '原文'

    def receipt(self, severity, verdict='pass'):
        ref = {'chapter': 1, 'quote': self.QUOTE, 'start': 0}
        return {'verdict': verdict,
                'checks': [{'id': 'language', 'result': 'pass', 'reason': '夹具。', 'evidence': [dict(ref)]}],
                'findings': [{'severity': severity, 'impact': '读者影响', 'goal': '修订目标',
                              'evidence': dict(ref)}]}

    def validate(self, data):
        return engine.validate_review(data, ['language'], {1: self.QUOTE})

    def test_a_note_rides_along_with_pass(self):
        """可记录、不返工——这是轻微缺陷唯一不需要触发链条的表达方式。"""
        self.validate(self.receipt('note'))

    def test_a_quality_finding_requires_fix(self):
        with self.assertRaises(Error) as caught:
            self.validate(self.receipt('quality'))
        self.assertEqual(caught.exception.code, 'review')

    def test_fix_still_needs_a_non_note_finding(self):
        """反方向不变：不能拿 note 级观察当返工依据。"""
        with self.assertRaises(Error) as caught:
            self.validate(self.receipt('note', verdict='fix'))
        self.assertEqual(caught.exception.code, 'review')


class BatchGenerationTests(unittest.TestCase):
    """INV-5 + P1：batch 的换代只有一个权威。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        initialize(self.path, contract())
        self.db = connect(self.path)
        self.addCleanup(self.db.close)

    def test_new_generation_refreshes_constraints_and_drops_the_hold(self):
        batch = {'id': 'b1', 'branch': 'main', 'first': 1, 'last': 3, 'phase': 'writing',
                 'constraints': 'stale-digest', 'local_repair': {'chapter': 1}}
        batchflow.new_generation(self.db, batch)
        self.assertEqual(batch['constraints'],
                         batchflow.constraints(self.db, 'main', batch['first'], batch['last']))
        self.assertNotIn('local_repair', batch)
        self.assertNotEqual(batch['id'], 'b1')

    def test_invalidate_also_drops_the_hold(self):
        batch = {'id': 'b1', 'branch': 'main', 'first': 1, 'last': 3, 'phase': 'writing',
                 'constraints': 'stale-digest', 'local_repair': {'chapter': 2}}
        batchflow.invalidate(self.db, batch, 1, 'test')
        self.assertNotIn('local_repair', batch)


class DomainReadTests(unittest.TestCase):
    """INV-3 + P4：必需行缺失是领域错误，不是 TypeError。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        initialize(self.path, contract())
        self.db = connect(self.path)
        self.addCleanup(self.db.close)

    def test_missing_plan_is_a_domain_error_with_a_route(self):
        with self.assertRaises(Error) as caught:
            atoms.plan(self.db, 'main', 1)
        self.assertEqual(caught.exception.code, 'plan')
        self.assertIn('recover', caught.exception.details)
        self.assertEqual(caught.exception.details['chapter'], 1)

    def test_optional_read_returns_none(self):
        self.assertIsNone(atoms.plan(self.db, 'main', 1, required=False))
        self.assertIsNone(atoms.draft(self.db, 'main', 1))

    def test_missing_branch_is_a_domain_error(self):
        with self.assertRaises(Error) as caught:
            atoms.branch(self.db, 'rev-nope')
        self.assertEqual(caught.exception.code, 'branch')
        self.assertIsNone(atoms.branch(self.db, 'rev-nope', required=False))

    def test_ancestor_walk_terminates_at_the_root(self):
        self.assertIsNone(atoms.parent_of(self.db, 'main'))
        self.assertIsNone(atoms.ancestor_chapter_prose(self.db, 'main', 99))


class DeliveryCostTests(unittest.TestCase):
    """交付成本契约：同一批信息不得付两遍，大块材料不得强制内联。

    实测来源：一份真实会话的 16 个 Worker，one-shot 信封 60–72K，超过宿主单次
    读取上限，Worker 分页读完且每页被后续每一轮重发；同一个文件被反复读入
    412K 字符（占全部 read 的 28%）。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        initialize(self.path, contract())
        self.db = connect(self.path)
        self.addCleanup(self.db.close)

    @staticmethod
    def blob(count, tag):
        line = '北岸旧城的水道决定运送证据的路线，不能在推进剧情时凭空消失。'
        return {f'{tag}{i}': line * 22 for i in range(count)}

    def envelope(self, stage, **extra):
        work = {'branch': 'main', 'stage': stage, 'chapter': 9,
                'payload': {'prose_path': 'work/prose/x.txt', 'prose_hash': 'x'},
                'contract': self.blob(4, 'c'), 'style_anchor': self.blob(1, 's')}
        work.update(extra)
        return work

    def test_a_oneshot_envelope_addresses_its_block_materials(self):
        """design_context / character_context 超过阈值时必须外置。

        它们合占实测信封的 44–51%，内联就把信封顶到单次读取上限之上。
        """
        work = self.envelope('draft', design_context=self.blob(24, 'd'),
                             character_context={'profiles': self.blob(10, 'p')})
        view = taskio.shared_delivery(str(self.path), work)
        self.assertNotIn('design_context', view, '大块设计材料不得内联')
        self.assertNotIn('character_context', view, '大块人物档案不得内联')
        addressed = view['shared_materials']
        self.assertIn('design_context', addressed)
        self.assertIn('character_context', addressed)
        self.assertTrue((self.path / addressed['design_context']['path']).is_file())

    def test_required_readings_stay_inline(self):
        """契约、声线锚点与前态是 Worker 不能漏看且不大的读数，必须留在信封里。"""
        work = self.envelope('draft', state={'facts': self.blob(6, 'f')},
                             design_context=self.blob(24, 'd'))
        view = taskio.shared_delivery(str(self.path), work)
        for field in ('contract', 'style_anchor', 'state'):
            self.assertIn(field, view, f'{field} 应保持内联')

    def test_an_untouched_envelope_is_unchanged(self):
        """没有外置发生时不得添加空清单，否则每个冻结任务都会多出字段。"""
        work = self.envelope('draft', state={'facts': {'hero:mood': '警觉'}})
        view = taskio.shared_delivery(str(self.path), work)
        self.assertNotIn('shared_materials', view)
        self.assertNotIn('shared_read_rule', view)

    def test_brief_declares_itself_a_substitute_view(self):
        """brief.md 的内容全部派生自 input.json；文件本身要说明这一点，
        否则 Worker 会把同一批信息付两遍（实测两处各 27.3K）。"""
        from novel_core.design import render_brief
        work = self.envelope('draft', writer_brief={
            'schema': 'novel-ledger-v2.writer-brief/2', 'chapter': 9,
            'dramatic_task': {'reader_experience': 'x', 'goal': 'g', 'conflict': 'c',
                              'necessary_change': 'n', 'ending_shape': 'e', 'character_choices': []},
            'scene_proposals': [], 'expression_space': [], 'instruction': 'i'})
        rendered = render_brief(work)
        self.assertIn('替代视图', rendered)
        self.assertIn('不要读本文件', rendered)

    def test_handoff_keeps_a_batch_writer_alive(self):
        """批次进行中时，交接边界必须要求复用同一执笔者，而不是等它结束。"""
        branch = 'main'
        put(self.db, 'chapter_batch:' + branch,
            {'id': 'b' * 32, 'branch': branch, 'first': 1, 'last': 3, 'phase': 'writing'})
        boundary = taskio._handoff_boundary(self.db, branch)
        self.assertIn('同一执笔者', boundary)
        self.assertIn('不要新建', boundary)

    def test_handoff_without_a_batch_keeps_the_wait_wording(self):
        boundary = taskio._handoff_boundary(self.db, 'main')
        self.assertIn('等待宿主 Worker 结束', boundary)


class EnvelopeSlicingTests(unittest.TestCase):
    """信封按类型切片：一个随全书增长的对象必然要被分页读入。

    实测来源：真实会话里 input.json 达到 27–36K 字符，Worker 把同一个文件读成
    2–3 页（某修订章 27.3K + 26.9K + 27.4K），每一页都留在上下文里被后续每一轮
    重发。切片要证明的是「一次读取装得下」，不是「内容变少了」。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        initialize(self.path, contract())
        self.db = connect(self.path)
        self.addCleanup(self.db.close)

    @staticmethod
    def blob(count, tag):
        line = '北岸旧城的水道决定运送证据的路线，不能在推进剧情时凭空消失。'
        return {f'{tag}{i}': line * 22 for i in range(count)}

    @staticmethod
    def selected(count):
        line = '北岸旧城的水道决定运送证据的路线，不能在推进剧情时凭空消失。'
        return [{'kind': 'history', 'chapter': i, 'index': 0, 'event_hash': 'h' * 8, 'text': line * 22}
                for i in range(1, count + 1)]

    @staticmethod
    def state(count):
        return {'clock': 3, 'facts': {f'hero:f{i}': '值' * 300 for i in range(count)}}

    def task(self, **extra):
        work = {'branch': 'main', 'stage': 'draft', 'chapter': 9,
                'payload': {'prose_path': 'work/prose/x.txt', 'prose_hash': 'x'},
                'contract': self.blob(4, 'c'), 'style_anchor': self.blob(1, 's'),
                'plan': {'entities': ['hero'], 'goal': 'g'}}
        work.update(extra)
        return work

    def sliced(self, work):
        from novel_core.taskio import envelope, ENVELOPE_TARGET_BYTES
        return envelope(str(self.path), work, self.path), ENVELOPE_TARGET_BYTES

    def delivered_bytes(self, pointer):
        """A delivered slice is UTF-8; CJK content is not one byte per character."""
        return len((self.path / pointer['path']).read_bytes())

    def inline_bytes(self, value):
        return len((json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))

    def test_the_index_stays_inside_one_read(self):
        """索引必须能被一次读入；同一批字段切片后总量不得变大。"""
        work = self.task(design_context=self.blob(24, 'd'),
                         character_context={'profiles': self.blob(10, 'p')},
                         guide='提交约定：' * 900, selected=self.selected(6))
        view, target = self.sliced(work)
        index = len(json.dumps(view, ensure_ascii=False, indent=2).encode('utf-8'))
        self.assertLess(index, target, '索引自身超过了单次读取上限')
        self.assertTrue(view['input_files'], '超过阈值的字段必须外置')
        before = taskio.shared_delivery(str(self.path), work)
        sliced_bytes = sum(self.delivered_bytes(pointer) for pointer in view['input_files'].values())
        whole_bytes = sum(self.inline_bytes(before[field]) for field in view['input_files'])
        self.assertLessEqual(sliced_bytes, whole_bytes, '同字段切片后不得比留在索引里更大')
        for material in view.get('shared_materials', {}).values():
            for part in material.get('parts') or [material]:
                self.assertLess(self.delivered_bytes(part), 64000, '分片本身仍需可一次读入')

    def test_a_realistic_envelope_needs_less_reading_than_one_monolith(self):
        """判据是「读完必须读的那部分」的字节数，不是文件数。

        实测：同一份 27–36K 的 input.json 被读成 2–3 页，每页都留在上下文里；
        切片后必读集合 = 索引 + needed=true 的分片，其余按需。
        """
        from novel_core.taskio import envelope as build
        work = self.task(design_context=self.blob(24, 'd'),
                         character_context={'profiles': self.blob(10, 'p')},
                         guide='提交约定：' * 900, selected=self.selected(6))
        view = build(str(self.path), work, self.path)
        monolith = self.inline_bytes(taskio.shared_delivery(str(self.path), work))
        index = len(json.dumps(view, ensure_ascii=False, indent=2).encode('utf-8'))
        self.assertLess(index, monolith, '索引必须小于切片前的信封')
        required = index
        for entry in view.get('must_read', []):
            required += self.delivered_bytes(entry)
        self.assertLess(required, monolith, '切片后必读量反而变大')

    def test_every_sliced_field_comes_back_byte_identical(self):
        """切片是交付路由，不是内容裁剪：字段必须能原样还原。"""
        work = self.task(design_context=self.blob(6, 'd'),
                         character_context={'profiles': self.blob(4, 'p')},
                         state=self.state(3), guide='提交约定：' * 900)
        view, _ = self.sliced(work)
        self.assertTrue(view['input_files'])
        before = taskio.shared_delivery(str(self.path), work)
        for field, pointer in view['input_files'].items():
            restored = json.loads((self.path / pointer['path']).read_text(encoding='utf-8'))
            self.assertEqual(restored, work.get(field, before.get(field)),
                             f'{field} 还原后与原文不一致')
            self.assertEqual(pointer['bytes'], self.delivered_bytes(pointer))

    def test_required_and_optional_shards_are_marked_not_removed(self):
        """needed=false 只表示不必读，不表示可以少交付。"""
        line = '她在旧案里反复核对过交接顺序，知道每一步该留下什么凭证。'
        profiles = {'profiles': {'hero': {'voice': line * 40},
                                 'p01': {'voice': line * 40},
                                 'p02': {'voice': line * 40}}}
        work = self.task(character_context=profiles, plan={'entities': ['hero'], 'goal': 'g'})
        view, _ = self.sliced(work)
        delivered = json.loads((self.path / view['input_files']['character_context']['path'])
                               .read_text(encoding='utf-8'))
        marked = {key: record['needed'] for key, record in delivered['profiles'].items()}
        self.assertTrue(marked['hero'], '本章参与者必须标为必读')
        self.assertFalse(marked['p01'], '未参与者不应被标成必读')
        self.assertFalse(marked['p02'])
        self.assertEqual(len(delivered['profiles']), 3, '标记不得删掉任何记录')
        names = {row['field'] for row in view['must_read']} | {row['field'] for row in view['on_demand']}
        records = {'character_context/profiles/hero',
                   'character_context/profiles/p01',
                   'character_context/profiles/p02'}
        self.assertTrue(records <= names, '每个交付记录都要在索引里有一条摘要行')
        for row in view['must_read'] + view['on_demand']:
            if row['field'] in records:
                self.assertEqual(row['path'], view['input_files']['character_context']['path'],
                                 '整批交付时，摘要行必须指向该字段的文件地址')

    def test_a_material_that_ships_whole_also_marks_its_records(self):
        """外置成一份文件时，记录级的必读标记同样要落到交付文件里。"""
        line = '她在旧案里反复核对过交接顺序，知道每一步该留下什么凭证。'
        profiles = {'profiles': {'hero': {'voice': line * 200},
                                 'p01': {'voice': line * 200},
                                 'p02': {'voice': line * 200}}}
        work = self.task(character_context=profiles, plan={'entities': ['hero'], 'goal': 'g'})
        view, _ = self.sliced(work)
        self.assertIn('shared_materials', view, '大块材料必须外置')
        material = view['shared_materials']['character_context']
        entries = material.get('parts') or [material]
        flags = {tuple(part['pointer']): (part['needed'], part['path']) for part in entries
                 if part.get('pointer')}
        self.assertTrue(flags[('profiles', 'hero')][0], '本章参与者必须标为必读')
        self.assertFalse(flags[('profiles', 'p01')][0], '未参与者不应被标成必读')
        for part in entries:
            self.assertTrue((self.path / part['path']).is_file(), '被标为可选的分片仍须交付')

    def test_ledger_projections_are_delivered_without_routing_keys(self):
        """账本投影（state/continuity）不得被注入交付标记。

        `needed` 是给 Worker 的阅读路由。写进 `state.entities` 这类账本对象里，
        就等于改了交付内容：任何把账本对象按字节比较的消费者都会看到账本没有
        的字段。标记只允许出现在人物/设计这类「解读」材料里。
        """
        work = self.task(state={'clock': 3,
                                'entities': {'hero': {'id': 'hero', 'name': '周行'}},
                                'facts': {'hero:mood': '警觉'}},
                         continuity={'anchors': {'hero:mood': '警觉'}},
                         character_context={'profiles': {'hero': {'voice': 'a' * 2000},
                                                         'p01': {'voice': 'b' * 2000}}})
        view, _ = self.sliced(work)
        state = view.get('state') or {}
        for name, record in (state.get('entities') or {}).items():
            self.assertNotIn('needed', record, f'state.entities[{name}] 被注入了交付标记')
        self.assertEqual(state, work['state'], 'state 必须与账本投影逐字节一致')
        self.assertEqual(view.get('continuity'), work['continuity'],
                         'continuity 必须与账本投影逐字节一致')
        # The routing information still exists — it lives in the index summary.
        names = {row['field'] for row in view.get('must_read', [])} | \
                {row['field'] for row in view.get('on_demand', [])}
        self.assertIn('state/entities/hero', names, '路由信息必须留在索引摘要里')

    def test_interpretation_material_may_carry_the_marking(self):
        """人物档案是解读材料，可以带 needed；它不与账本逐字节比较。"""
        line = '她在旧案里反复核对过交接顺序，知道每一步该留下什么凭证。'
        work = self.task(character_context={'profiles': {'hero': {'voice': line * 40},
                                                         'p01': {'voice': line * 40}}})
        view, _ = self.sliced(work)
        delivered = json.loads((self.path / view['input_files']['character_context']['path'])
                               .read_text(encoding='utf-8'))
        self.assertTrue(delivered['profiles']['hero']['needed'])
        self.assertFalse(delivered['profiles']['p01']['needed'])

    def test_an_unsliced_envelope_keeps_the_old_shape(self):
        """没有超过阈值的内容时不得凭空添加路由字段。"""
        work = {'branch': 'main', 'stage': 'plan', 'chapter': 1,
                'payload': {'window': [1, 2]}, 'plan': {'entities': ['hero']}}
        view, _ = self.sliced(work)
        self.assertNotIn('input_files', view)
        self.assertNotIn('must_read', view)
        self.assertNotIn('on_demand', view)

    def test_always_sliced_documents_leave_a_stable_address(self):
        """约定与 guide 是版本稳定的整份文档：地址稳定，未变则读一次即可。"""
        work = self.task(guide='提交约定：' * 900)
        first, _ = self.sliced(work)
        self.assertIn('guide', first['input_files'])
        self.assertIn('contract', first['input_files'])
        again, _ = self.sliced(work)
        self.assertEqual(first['input_files'], again['input_files'],
                         '同一版本必须得到同一地址与同一哈希')
        self.assertIn('payload', first, '身份与执行字段留在索引里')


class DegradationTests(unittest.TestCase):
    """P4：降级必须留痕并把失败交给调用方，不得静默。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        initialize(self.path, contract())
        self.db = connect(self.path)
        self.addCleanup(self.db.close)

    def test_render_failure_is_reported_and_logged(self):
        from unittest.mock import patch
        from novel import _auto_render_html
        with patch('novel_core.bookpage.render', side_effect=RuntimeError('boom')):
            failed = _auto_render_html(str(self.path), self.db)
        self.assertIsNotNone(failed, '渲染失败必须回传调用方，不能静默吞掉')
        self.assertEqual(failed['code'], 'RuntimeError')
        logged = self.db.execute(
            "SELECT COUNT(*) FROM logs WHERE action='html_render_failed'").fetchone()[0]
        self.assertEqual(logged, 1, '降级必须留痕')

    def test_successful_render_reports_nothing(self):
        from novel import _auto_render_html
        self.assertIsNone(_auto_render_html(str(self.path), self.db))
        self.assertTrue((self.path / 'book.html').is_file())


class MechanicalFactTests(unittest.TestCase):
    """P5：文档里的机械事实必须与代码一致，不能各写一份。"""

    def doc(self, name):
        return (ROOT / name).read_text(encoding='utf-8')

    def test_readme_version_matches_the_engine(self):
        match = re.search(r'当前版本 \*\*v([0-9][0-9.]*)\*\*', self.doc('README.md'))
        self.assertIsNotNone(match, 'README 未声明当前版本')
        self.assertEqual(match.group(1), VERSION)

    def test_protocol_mandatory_list_matches_the_validator(self):
        match = re.search(r'核心项\s+(\S+)\s+不能使用', self.doc('references/protocol.md'))
        self.assertIsNotNone(match, 'protocol.md 未列出核心审稿项')
        self.assertEqual(set(match.group(1).split('/')), set(engine.REVIEW_MANDATORY_CHECKS))

    def test_readme_names_every_subcommand(self):
        from novel import parser
        text = self.doc('README.md')
        missing = sorted(name for name in parser()._subparsers._group_actions[0].choices
                         if f'`{name}`' not in text)
        self.assertEqual(missing, [], f'README 未提及这些子命令：{missing}')

    def test_wire_protocols_are_documented_and_live(self):
        """P5 双向锁定：跨边界协议串既要在 protocol.md 登记，也要仍在代码里。"""
        documented = self.doc('references/protocol.md')
        code = '\n'.join([path.read_text(encoding='utf-8') for path in sorted(CORE.glob('*.py'))]
                         + [(ROOT / 'scripts' / 'novel.py').read_text(encoding='utf-8')])
        for token in WIRE_PROTOCOLS:
            with self.subTest(token=token):
                self.assertIn(token, documented, f'{token} 未在 protocol.md 的协议串清单中登记')
                self.assertIn(token, code, f'{token} 已不在代码中：协议串清单过期')

    def test_architecture_doc_links_resolve(self):
        text = self.doc('references/architecture.md')
        for target in re.findall(r'\]\(([^)]+)\)', text):
            if target.startswith('http'):
                continue
            self.assertTrue((ROOT / 'references' / target).is_file(), target)


if __name__ == '__main__':
    unittest.main()
