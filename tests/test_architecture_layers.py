"""Architecture contracts (references/architecture.md) as executable guards.

One test per principle or invariant, so a change that violates the contract
fails here rather than surfacing as a production incident. These are structure
tests: they never assert literary behaviour.
"""
import ast
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / 'scripts' / 'novel_core'
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from novel_core import access, atoms, batchflow, engine, transitions
from novel_core.store import Error, VERSION, connect, initialize
from support import contract

# 架构契约第四节的分层。编号越小越底层；模块级 import 只能指向 <= 自己的层。
LAYERS = {
    0: ('store', 'transitions'),
    1: ('access', 'atoms', 'wire', 'ledger', 'patches', 'budget', 'spatial',
        'sources', 'projections', 'dependencies', 'recovery', 'reviewflow',
        'characters', 'design', 'contextflow', 'taskio'),
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
    'taskio': ('engine',),
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
