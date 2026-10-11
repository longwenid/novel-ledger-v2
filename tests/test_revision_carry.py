"""v1.46.0 revision scope: carry provably unaffected chapters (carriage.py).

Three detectors must each be able to demote a chapter back to normal
dispatch, and a clean carry must reach the range review without any worker
touching the carried chapter.
"""
import json
import tempfile
import unittest
from pathlib import Path
from support import (contract, initialize, connect, respond, result, review,
                     plan, rpath, pack_prose, submit_review)
from novel_core import engine
from novel_core.ledger import replay
from novel_core.store import get, head

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

    def _chapter(self, prose, events, plan_entities=None, plan_extra=None):
        planned = self.task(); self.assertEqual(planned['stage'], 'plan')
        if plan_entities is not None or plan_extra:
            from support import result as _result
            work = json.loads(rpath(planned, 'input_path').read_text(encoding='utf-8'))
            chapter = dict(plan(planned['chapter']))
            if plan_entities is not None:
                chapter['entities'] = list(plan_entities)
            chapter.update(plan_extra or {})
            engine.submit(self.db, _result(planned, {'chapters': [chapter]}))
        else:
            respond(self.db, self.path, planned)
        respond(self.db, self.path, self.task(), prose=prose)     # draft
        task = self.task(); self.assertEqual(task['stage'], 'chapter_review')
        work = json.loads(rpath(task, 'input_path').read_text(encoding='utf-8'))
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
        work = json.loads(rpath(task, 'input_path').read_text(encoding='utf-8'))
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

    def test_empty_delta_never_carries_worker_path_stays_authoritative(self):
        # 表达类修订（无账本变化）：隐式自由文本读取不可分析（test_revision_scope 的
        # 既有裁决），自动携带不得触发，由 worker fast-commit 路径裁决。
        p3 = '第3天，周行翻出旧案卷宗，按自己的计划排好了顺序。'
        self._book_three_chapters(
            [self._state_event('hero', 'plan', None, '追索', '旧案', p3, 3)], p3)
        engine.begin_revision(self.db, 2, '文风统一，不涉及事实')
        branch = get(self.db, 'work_branch')
        task = self.task(); self.assertEqual(task['stage'], 'draft')   # ch2 仍派 worker
        p2 = '第2天，宋禾在灯下把半文旧账重新誊清了一遍。'
        respond(self.db, self.path, task, prose=p2)
        task = self.task(); self.assertEqual(task['stage'], 'chapter_review')
        work = json.loads(rpath(task, 'input_path').read_text(encoding='utf-8'))
        text = pack_prose(self.path, work)
        data = review(work['payload']['facts_checks'], text, 2)
        data.update(events=[self._state_event('ally', 'ledger_note', None, '半文旧账', '半文旧账', p2, 2)],
                    summary='仅文风调整，账本值不变。',
                    prior_dispositions=[{'index': 0, 'disposition': 'replaced', 'reason': '措辞变化，事件重锚，值不变'}])
        submit_review(self.db, task, data)
        self.assertEqual(head(self.db, branch), 2)
        following = self.task()
        self.assertEqual(following['stage'], 'draft')                  # ch3 回落正常派发
        self.assertEqual(following['chapter'], 3)


if __name__ == '__main__':
    unittest.main()
