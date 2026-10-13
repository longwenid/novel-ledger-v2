"""v1.46.0 revision scope: carry provably unaffected chapters (carriage.py).

Every refusal source must be able to demote a chapter back to normal
dispatch, and a clean carry must reach the range review without any worker
touching the carried chapter — including an expression-level fix whose
ledger delta is empty (v1.58.0: the old refusal handed the suffix to worker
re-drafts, whose drift fed the next revision chain).
"""
import json
import tempfile
import unittest
from pathlib import Path
from support import (
    connect,
    contract,
    envelope,
    initialize,
    pack_prose,
    plan,
    respond,
    result,
    review,
    rpath,
    submit_review,
)
from novel_core import engine
from novel_core.ledger import replay
from novel_core.store import Error, get, head

CH2_OLD = '半文旧账'
CH2_NEW = '一文清账'


class RevisionCarryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.path = Path(self.tmp.name)
        c = contract(); c['chapter_batch_size'] = 1
        initialize(self.path, c); self.db = connect(self.path)

    def tearDown(self):
        self.db.close(); self.tmp.cleanup()

    def task(self):
        return engine.next_task(self.db, self.path)

    def _chapter(self, prose, events, plan_entities=None, plan_extra=None, draft_extra=None):
        planned = self.task(); self.assertEqual(planned['stage'], 'plan')
        if plan_entities is not None or plan_extra:
            from support import result as _result
            work = envelope(planned)
            chapter = dict(plan(planned['chapter']))
            if plan_entities is not None:
                chapter['entities'] = list(plan_entities)
            chapter.update(plan_extra or {})
            engine.submit(self.db, _result(planned, {'chapters': [chapter]}))
        else:
            respond(self.db, self.path, planned)
        draft_run = self.task()
        if draft_extra:
            engine.submit(self.db, result(draft_run, {'prose': prose, **draft_extra}))
        else:
            respond(self.db, self.path, draft_run, prose=prose)
        task = self.task(); self.assertEqual(task['stage'], 'chapter_review')
        work = envelope(task)
        text = pack_prose(self.path, work)
        data = review(work['payload']['facts_checks'], text, task['chapter'])
        data.update(events=events, summary='登记事件。')
        submit_review(self.db, task, data)

    @staticmethod
    def _state_event(entity, key, before, after, quote, prose, chapter):
        return {'op': 'state', 'entity': entity, 'key': key, 'before': before, 'after': after,
                'evidence': {'chapter': chapter, 'quote': quote, 'start': prose.index(quote)}}

    def _book_three_chapters(self, third_events, third_prose, third_plan=None):
        p1 = '第1天，周行在铺子把收据交给宋禾。'
        self._chapter(p1, [self._state_event('hero', 'mood', None, '警觉', '收据', p1, 1)])
        if self.task().get('stage') == 'await_author':
            engine.approve(self.db)
        p2 = '第2天，宋禾在柜台记下' + CH2_OLD + '，压低了声音。'
        self._chapter(p2, [self._state_event('ally', 'ledger_note', None, CH2_OLD, CH2_OLD, p2, 2)])
        self._chapter(third_prose, third_events, plan_entities=['hero'], plan_extra=third_plan)
        self.assertEqual(head(self.db, 'main'), 3)

    def _revise_ch2(self, old=CH2_OLD, new=CH2_NEW):
        engine.begin_revision(self.db, 2, json.dumps([
            {'severity': 'quality', 'impact': '口径不一', 'goal': '统一第2章记账口径',
             'evidence': {'chapter': 2, 'quote': old, 'start': 0}}], ensure_ascii=False))
        branch = get(self.db, 'work_branch')
        task = self.task(); self.assertEqual(task['stage'], 'draft')
        p2 = '第2天，宋禾在柜台记下' + new + '，声音很轻。'
        respond(self.db, self.path, task, prose=p2)
        task = self.task(); self.assertEqual(task['stage'], 'chapter_review')
        work = envelope(task)
        text = pack_prose(self.path, work)
        data = review(work['payload']['facts_checks'], text, 2)
        data.update(events=[self._state_event('ally', 'ledger_note', None, new, new, p2, 2)],
                    summary='统一口径。',
                    prior_dispositions=[{'index': 0, 'disposition': 'replaced', 'reason': '口径由旧账统一为新账'}])
        submit_review(self.db, task, data)
        self.assertEqual(head(self.db, branch), 2)
        return branch

    def test_unaffected_chapter_carries_without_dispatch(self):
        p3 = '第3天，周行翻出旧案卷宗，按自己的计划排好了顺序。'
        self._book_three_chapters(
            [self._state_event('hero', 'plan', None, '追索', '旧案', p3, 3)], p3)
        branch = self._revise_ch2()
        following = self.task()
        self.assertEqual(following['stage'], 'revision_review')
        row = self.db.execute('SELECT data FROM chapters WHERE branch=? AND number=3', (branch,)).fetchone()
        self.assertEqual(json.loads(row['data'])['carried_from'], 'main')
        state = replay(self.db, branch)
        self.assertEqual(state['facts']['ally:ledger_note'], CH2_NEW)
        self.assertEqual(state['facts']['hero:plan'], '追索')

    def test_status_surfaces_carried_chapters(self):
        """INV-6: a carried chapter reached canon without per-chapter independent
        review. That exception must be visible to the controller and the author,
        not only to the revision reviewer."""
        p3 = '第3天，周行翻出旧案卷宗，按自己的计划排好了顺序。'
        self._book_three_chapters(
            [self._state_event('hero', 'plan', None, '追索', '旧案', p3, 3)], p3)
        self._revise_ch2()
        following = self.task()          # carries are lazy: they land on dispatch
        self.assertEqual(following['stage'], 'revision_review')
        self.assertEqual(engine.status(self.db).get('carried_chapters'), [3])

    def test_book_page_marks_carried_chapters(self):
        """INV-6's other half: the author reads book.html, so a chapter that
        skipped per-chapter independent review must be marked there too."""
        p3 = '第3天，周行翻出旧案卷宗，按自己的计划排好了顺序。'
        self._book_three_chapters(
            [self._state_event('hero', 'plan', None, '追索', '旧案', p3, 3)], p3)
        branch = self._revise_ch2()
        self.task()                      # carries are lazy: they land on dispatch
        from novel_core.bookpage import render
        page = render(self.db, branch)
        self.assertIn('未逐章重审', page)
        self.assertIn('第3章', page)

    def test_declared_read_blocks_carry_through_a_description(self):
        """The blind spot `reads` closes. Chapter 3 depends on the ledger value
        but refers to it by description — it names neither the owning entity nor
        the figure — so the entity read-set and the literal sweep are both blind
        to it. Only the writer's own declaration can express this dependency."""
        p1 = '第1天，周行在铺子把收据交给宋禾。'
        self._chapter(p1, [self._state_event('hero', 'mood', None, '警觉', '收据', p1, 1)])
        if self.task().get('stage') == 'await_author':
            engine.approve(self.db)
        p2 = '第2天，宋禾在柜台记下' + CH2_OLD + '，压低了声音。'
        self._chapter(p2, [self._state_event('ally', 'ledger_note', None, CH2_OLD, CH2_OLD, p2, 2)])
        p3 = '第3天，周行翻出旧案卷宗，按自己的计划排好了顺序。'
        self._chapter(p3, [self._state_event('hero', 'plan', None, '追索', '旧案', p3, 3)],
                      plan_entities=['hero'],
                      draft_extra={'reads': ['ally:ledger_note']})
        row = self.db.execute('SELECT data FROM chapters WHERE branch=? AND number=3',
                              ('main',)).fetchone()
        self.assertEqual(json.loads(row['data'])['reads'], ['ally:ledger_note'])
        branch = self._revise_ch2()
        following = self.task()
        self.assertEqual(following['stage'], 'draft')
        self.assertEqual(following['chapter'], 3)
        self.assertIsNone(self.db.execute(
            'SELECT data FROM chapters WHERE branch=? AND number=3', (branch,)).fetchone())

    def test_malformed_reads_is_refused(self):
        planned = self.task()
        respond(self.db, self.path, planned)
        with self.assertRaises(Error) as caught:
            engine.submit(self.db, result(self.task(), {'prose': '第1天，周行收下收据。',
                                                        'reads': 'ally:ledger_note'}))
        self.assertEqual(caught.exception.code, 'reads')

    def test_downstream_precondition_blocks_carry(self):
        p3 = '第3天，宋禾把' + CH2_OLD + '逐笔核对了一遍。'
        self._book_three_chapters(
            [self._state_event('ally', 'ledger_note', CH2_OLD, CH2_OLD + '已核', '核对', p3, 3)], p3)
        branch = self._revise_ch2()
        following = self.task()
        self.assertEqual(following['stage'], 'draft')
        self.assertEqual(following['chapter'], 3)
        self.assertIsNone(self.db.execute(
            'SELECT data FROM chapters WHERE branch=? AND number=3', (branch,)).fetchone())

    def test_surface_sweep_blocks_carry(self):
        p3 = '第3天，周行想起那笔' + CH2_OLD + '，翻出卷宗排好顺序。'
        self._book_three_chapters(
            [self._state_event('hero', 'plan', None, '追索', '卷宗', p3, 3)], p3)
        branch = self._revise_ch2()
        following = self.task()
        self.assertEqual(following['stage'], 'draft')
        self.assertEqual(following['chapter'], 3)

    def test_carried_revision_reaches_review_with_net_payload(self):
        p3 = '第3天，周行翻出旧案卷宗，按自己的计划排好了顺序。'
        self._book_three_chapters(
            [self._state_event('hero', 'plan', None, '追索', '旧案', p3, 3)], p3)
        self._revise_ch2()
        task = self.task()
        self.assertEqual(task['stage'], 'revision_review')
        while json.loads(self.db.execute('SELECT input FROM tasks WHERE id=?',
                                         (task['task_id'],)).fetchone()[0])['payload']['scope'].endswith('_chunk'):
            respond(db=self.db, project=self.path, task=task)     # 分块复核仍是安全网
            task = self.task()
        work = json.loads(self.db.execute(
            'SELECT input FROM tasks WHERE id=?', (task['task_id'],)).fetchone()[0])
        self.assertEqual(work['payload'].get('carried_chapters'), [3])
        self.assertIn('carried_chapters', work['payload']['instruction'])


    def test_declared_dependency_blocks_carry(self):
        """`plan['dependencies']` is the kernel's only explicit read
        declaration, and the dependency delta never consulted it: a chapter
        whose declared source had been rewritten upstream could still be
        carried on the strength of an entity read-set that does not encode
        prose dependencies at all."""
        p3 = '第3天，周行翻出旧案卷宗，按自己的计划排好了顺序。'
        self._book_three_chapters(
            [self._state_event('hero', 'plan', None, '追索', '旧案', p3, 3)], p3,
            third_plan={'dependencies': [2]})
        branch = self._revise_ch2()
        following = self.task()
        self.assertEqual(following['stage'], 'draft')
        self.assertEqual(following['chapter'], 3)
        self.assertIsNone(self.db.execute(
            'SELECT data FROM chapters WHERE branch=? AND number=3', (branch,)).fetchone())

    def test_chinese_numeral_surface_form_blocks_carry(self):
        """Measured escape: the ledger stored 30, the revision changed it to 20,
        and chapter 3 wrote 三十五文 without ever naming the owner. The old
        `len(text) >= 2` literal sweep matched neither '30' nor a Chinese form,
        so the chapter carried and `audit` still reported ok. `_value_forms`
        now generates the Chinese spelling, and the carry is refused."""
        p1 = '第1天，周行在铺子把收据交给宋禾。'
        self._chapter(p1, [self._state_event('hero', 'mood', None, '警觉', '收据', p1, 1)])
        if self.task().get('stage') == 'await_author':
            engine.approve(self.db)
        p2 = '第2天，宋禾在柜台记下30，压低了声音。'
        self._chapter(p2, [self._state_event('ally', 'ledger_note', None, '30', '30', p2, 2)])
        p3 = '第3天，周行盘算三十五文，觉得还够盘下西街的铺面。'
        self._chapter(p3, [self._state_event('hero', 'plan', None, '盘下铺面', '盘下', p3, 3)],
                      plan_entities=['hero'])
        branch = self._revise_ch2(old='30', new='20')
        following = self.task()
        self.assertEqual(following['stage'], 'draft')
        self.assertEqual(following['chapter'], 3)
        self.assertIsNone(self.db.execute(
            'SELECT data FROM chapters WHERE branch=? AND number=3', (branch,)).fetchone())

    def test_expression_level_revision_carries_the_suffix(self):
        # 表达类修订（账本零变化）：后缀章由引擎原文结转，不再派 Worker 重写。
        # 旧裁决（worker fast-commit 路径裁决）实测代价是创造性重写引入新缺陷，
        # 反喂下一条修订链（会话 14006c71）；结转章保持逐字不变，不可能漂移，
        # 未经逐章重审由整体复核的 carried_chapters 抽查兜底。
        p3 = '第3天，周行翻出旧案卷宗，按自己的计划排好了顺序。'
        self._book_three_chapters(
            [self._state_event('hero', 'plan', None, '追索', '旧案', p3, 3)], p3)
        branch = self._revise_ch2(new=CH2_OLD)          # 措辞变化，账本值不变 → 空 delta
        following = self.task()
        self.assertEqual(following['stage'], 'revision_review')       # ch3 引擎结转，不再派 draft
        row = self.db.execute('SELECT data FROM chapters WHERE branch=? AND number=3', (branch,)).fetchone()
        self.assertEqual(json.loads(row['data'])['carried_from'], 'main')

    def test_wording_echo_blocks_carry(self):
        """会话 14006c71 的实测漏网形态：一个外文残留词同时出现在多章。
        修订删掉了第2章的 bell，第3章正文还写着 bell——空 delta 的值级探测
        全部失明，回声扫描按"上游被删措辞的残留"拒绝结转，回落正常派发定点修。"""
        p1 = '第1天，周行在铺子把收据交给宋禾。'
        self._chapter(p1, [self._state_event('hero', 'mood', None, '警觉', '收据', p1, 1)])
        if self.task().get('stage') == 'await_author':
            engine.approve(self.db)
        p2 = '第2天，宋禾在柜台记下半文旧账，敲了敲那口 bell。'
        self._chapter(p2, [self._state_event('ally', 'ledger_note', None, CH2_OLD, '半文旧账', p2, 2)])
        p3 = '第3天，周行翻出旧案卷宗，又听见那口 bell 响了一声。'
        self._chapter(p3, [self._state_event('hero', 'plan', None, '追索', '旧案', p3, 3)],
                      plan_entities=['hero'])
        engine.begin_revision(self.db, 2, json.dumps([
            {'severity': 'quality', 'impact': '正文残留外文词，读者出戏',
             'goal': '删除第2章的 bell 改为中文', 'target_chapter': 2,
             'evidence': {'chapter': 2, 'quote': 'bell', 'start': p2.index('bell')}}], ensure_ascii=False))
        branch = get(self.db, 'work_branch')
        task = self.task(); self.assertEqual(task['stage'], 'draft')
        fixed = '第2天，宋禾在柜台记下半文旧账，敲了敲那口铜铃。'
        respond(self.db, self.path, task, prose=fixed)
        task = self.task(); self.assertEqual(task['stage'], 'chapter_review')
        work = envelope(task)
        text = pack_prose(self.path, work)
        data = review(work['payload']['facts_checks'], text, 2)
        data.update(events=[self._state_event('ally', 'ledger_note', None, CH2_OLD, '半文旧账', fixed, 2)],
                    summary='仅删外文残留，账本值不变。',
                    prior_dispositions=[{'index': 0, 'disposition': 'replaced', 'reason': '措辞变化，事件重锚，值不变'}])
        submit_review(self.db, task, data)
        self.assertEqual(head(self.db, branch), 2)
        following = self.task()
        self.assertEqual(following['stage'], 'draft')                  # ch3 残留 bell，拒绝结转
        self.assertEqual(following['chapter'], 3)
        self.assertIsNone(self.db.execute(
            'SELECT data FROM chapters WHERE branch=? AND number=3', (branch,)).fetchone())

    def test_implicated_chapter_is_never_carried(self):
        """修订自己的 findings 点名了第4章：即使账本零变化、回声干净，第4章也
        必须重新执行——结转它会原样保下本次修订要修的缺陷。中间未点名的第3章照常结转。"""
        p3 = '第3天，周行翻出旧案卷宗，按自己的计划排好了顺序。'
        self._book_three_chapters(
            [self._state_event('hero', 'plan', None, '追索', '旧案', p3, 3)], p3)
        self._chapter('第4天，周行把卷宗按口岸重新分了堆。', [], plan_entities=['hero'])
        self.assertEqual(head(self.db, 'main'), 4)
        engine.begin_revision(self.db, 2, json.dumps([
            {'severity': 'quality', 'impact': '口径不一', 'goal': '统一第2章记账口径',
             'target_chapter': 2, 'evidence': {'chapter': 2, 'quote': CH2_OLD, 'start': 0}},
            {'severity': 'quality', 'impact': '第4章分堆依据沿用旧口径', 'goal': '第4章对齐新口径',
             'target_chapter': 4, 'evidence': {'chapter': 4, 'quote': '分了堆', 'start': 12}}], ensure_ascii=False))
        branch = get(self.db, 'work_branch')
        task = self.task(); self.assertEqual(task['stage'], 'draft')   # ch2 起点章
        p2 = '第2天，宋禾在柜台记下' + CH2_NEW + '，声音很轻。'
        respond(self.db, self.path, task, prose=p2)
        task = self.task(); self.assertEqual(task['stage'], 'chapter_review')
        work = envelope(task)
        text = pack_prose(self.path, work)
        data = review(work['payload']['facts_checks'], text, 2)
        data.update(events=[self._state_event('ally', 'ledger_note', None, CH2_NEW, CH2_NEW, p2, 2)],
                    summary='统一口径。',
                    prior_dispositions=[{'index': 0, 'disposition': 'replaced', 'reason': '口径由旧账统一为新账'}])
        submit_review(self.db, task, data)
        self.assertEqual(head(self.db, branch), 2)
        self.task()                                                    # 引擎结转第3章（未点名）
        row = self.db.execute('SELECT data FROM chapters WHERE branch=? AND number=3', (branch,)).fetchone()
        self.assertEqual(json.loads(row['data'])['carried_from'], 'main')
        following = self.task()
        self.assertEqual(following['stage'], 'draft')                  # 第4章被点名：重新执行
        self.assertEqual(following['chapter'], 4)
        self.assertIsNone(self.db.execute(
            'SELECT data FROM chapters WHERE branch=? AND number=4', (branch,)).fetchone())


if __name__ == '__main__':
    unittest.main()
