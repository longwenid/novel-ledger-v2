"""v1.45.0 receipt recovery: rejections carry the ledger answer.

Sessions e354 (我在异界弹钢琴) and 53bc (仙道天骄前传) ran 64 submit attempts
with 55 rejections; the top classes were evidence transcription (18),
event_accounting kept bookkeeping (11) and time_schedule with no escape hint
(7, two workers exhausted). The validator holds the answer in each case, so
the rejection now ships it: closest fragment, derived kept, recovery paths.
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from support import (contract, initialize, connect, respond, result, review,
                     ev, plan, rpath, pack_prose, fixture_first_read, combined_review, submit_review)
from test_envelope_fixes import fill_skeleton
from novel_core import engine
from novel_core.ledger import evidence, replay
from novel_core.store import Error, get, head

EXAMPLE = json.loads((Path(__file__).resolve().parents[1] / 'assets/design.example.json').read_text(encoding='utf-8'))


class EvidenceAnswerTests(unittest.TestCase):
    PROSE = '周行把收据交给宋禾。她看了许久，终于按下指印。'

    def test_drifted_quote_gets_copyable_closest(self):
        with self.assertRaises(Error) as caught:
            evidence(self.PROSE, {'quote': '周行把密据交给宋禾', 'start': 0})
        self.assertEqual(caught.exception.code, 'evidence')
        details = caught.exception.details
        closest = details['closest']
        self.assertEqual(self.PROSE.index(closest['quote']), closest['start'])
        self.assertIn('收据', closest['quote'])
        self.assertIn('commands.evidence', details['recover'])

    def test_ambiguous_quote_lists_hits(self):
        prose = '灯亮了一次。灯亮了一次。夜深了。'
        with self.assertRaises(Error) as caught:
            evidence(prose, {'quote': '灯亮了一次'})
        details = caught.exception.details
        self.assertEqual(details['match_count'], 2)
        self.assertEqual([m['start'] for m in details['matches']], [0, 6])
        self.assertIn('加长', details['recover'])

    def test_unrelated_quote_still_explains_recovery(self):
        with self.assertRaises(Error) as caught:
            evidence(self.PROSE, {'quote': '完全无关的句子'})
        self.assertIn('recover', caught.exception.details)


class LocateAnswerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.path = Path(self.tmp.name)
        initialize(self.path, contract()); self.db = connect(self.path)
        respond(self.db, self.path, engine.next_task(self.db, self.path))          # plan
        respond(self.db, self.path, engine.next_task(self.db, self.path))          # draft
        self.task = engine.next_task(self.db, self.path)
        work = json.loads(rpath(self.task, 'input_path').read_text(encoding='utf-8'))
        self.prose = pack_prose(self.path, work)

    def tearDown(self):
        self.db.close(); self.tmp.cleanup()

    def test_locate_miss_returns_closest(self):
        from novel_core.taskio import locate
        with self.assertRaises(Error) as caught:
            locate(self.db, self.task['task_id'], self.prose.replace('收据', '密据')[:12])
        details = caught.exception.details
        self.assertIn('closest', details)
        self.assertIn(details['closest']['quote'], self.prose)

    def test_locate_hit_unchanged(self):
        from novel_core.taskio import locate
        quote = self.prose[:6]
        found = locate(self.db, self.task['task_id'], quote)
        self.assertEqual(found['start'], self.prose.index(quote))
        self.assertEqual(found['chapter'], self.task['chapter'])


class TimeScheduleEscapeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.path = Path(self.tmp.name)
        c = contract(); c['clock_unit'] = '天'
        initialize(self.path, c); self.db = connect(self.path)

    def tearDown(self):
        self.db.close(); self.tmp.cleanup()

    def task(self):
        return engine.next_task(self.db, self.path)

    def test_out_of_schedule_clock_carries_both_exits(self):
        respond(self.db, self.path, self.task())
        respond(self.db, self.path, self.task(), prose='次日清晨，周行在档案室核对记录。')
        task = self.task()
        work = json.loads(rpath(task, 'input_path').read_text(encoding='utf-8'))
        prose = pack_prose(self.path, work)
        data = review(work['payload']['facts_checks'], prose)
        data.update(events=[{'op': 'clock', 'before': 0, 'after': 1, 'evidence': ev(prose)}], summary='次日核对。')
        submit_review(self.db, task, data)
        if self.task().get('stage') == 'await_author':
            engine.approve(self.db)
        planned = plan(2)
        planned['scenes'] = [{'label': '货栈', 'present': ['hero'], 'time_hint': 'D+2 黄昏，先清点再碰头'}]
        engine.submit(self.db, result(self.task(), {'chapters': [planned]}))
        respond(self.db, self.path, self.task(), prose='两天后，周行走进货栈清点货物。')
        task = self.task()
        work = json.loads(rpath(task, 'input_path').read_text(encoding='utf-8'))
        prose = pack_prose(self.path, work)
        data = review(work['payload']['facts_checks'], prose, 2)
        data.update(events=[{'op': 'clock', 'before': 1, 'after': 2, 'evidence': ev(prose, 2)}], summary='进货栈。')
        with self.assertRaises(Error) as caught:
            submit_review(self.db, task, data)
        details = caught.exception.details
        self.assertEqual(details['expected_range'], [3, 4])
        self.assertIn('clock 事件', details['recover'])
        self.assertIn('replan', details['recover'])


class KeptDerivationTests(unittest.TestCase):
    """Re-reporting a prior event verbatim is kept; only drops need words."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.path = Path(self.tmp.name)
        c = contract(); c['chapter_batch_size'] = 1
        initialize(self.path, c); self.db = connect(self.path)

    def tearDown(self):
        self.db.close(); self.tmp.cleanup()

    def task(self):
        return engine.next_task(self.db, self.path)

    def _draft_with_short_quote_events(self):
        respond(self.db, self.path, self.task())                     # plan
        prose = '第1章，周行把收据交给宋禾。她看了许久，终于按下指印。'
        respond(self.db, self.path, self.task(), prose=prose)        # draft
        task = self.task()                                           # chapter_review
        work = json.loads(rpath(task, 'input_path').read_text(encoding='utf-8'))
        events = [
            {'op': 'state', 'entity': 'hero', 'key': 'mood', 'before': None, 'after': '坚定',
             'evidence': {'chapter': 1, 'quote': '收据', 'start': prose.index('收据')}},
            {'op': 'resource', 'asset': 'cash', 'unit': '文', 'quantity': '10', 'from': 'hero', 'to': None,
             'from_before': '100', 'reason': '购买灯油',
             'evidence': {'chapter': 1, 'quote': '指印', 'start': prose.index('指印')}}]
        receipt = combined_review(work, prose, 1, events=events, summary='登记两条事件。')
        bad = review(engine.READER_CHECKS, prose)
        bad['verdict'] = 'fix'; bad['checks'][0]['result'] = 'fix'
        bad['first_read'] = fixture_first_read()
        bad['findings'] = [{'severity': 'quality', 'impact': '平铺直叙', 'goal': '加强动作', 'evidence': ev(prose)}]
        receipt['reader'] = bad
        engine.submit(self.db, result(task, receipt))                # fix -> repair task
        return prose

    def _reach_facts_after_context_drift(self):
        prose = self._draft_with_short_quote_events()
        task = self.task()                                           # repair
        work = json.loads(rpath(task, 'input_path').read_text(encoding='utf-8'))
        payload = work['payload']
        base = get(self.db, 'candidate_text:' + payload['base_hash'])
        start = base.index('把')
        engine.submit(self.db, result(task, {'base_hash': payload['base_hash'], 'repair_note': '措辞微调',
                                             'patches': [{'start': start, 'old': '把', 'new': '将'}]}))
        task = self.task()
        self.assertEqual(task['stage'], 'chapter_review')
        return task, json.loads(rpath(task, 'input_path').read_text(encoding='utf-8'))

    def test_verbatim_resubmission_is_kept_without_dispositions(self):
        task, work = self._reach_facts_after_context_drift()
        self.assertEqual(work['payload']['auto_carry'], [1])          # '指印' quote context intact
        prior = work['payload']['prior_events']
        data = review(work['payload']['facts_checks'], pack_prose(self.path, work))
        data.update(summary='原样重报，无新增事件。', events=copy_events(prior[:1]))
        submit_review(self.db, task, data)                            # no prior_dispositions needed
        state = replay(self.db, 'main')
        self.assertEqual(state['facts']['hero:mood'], '坚定')
        self.assertEqual(state['resources']['["hero","cash","文"]'], '90')
        self.assertEqual(head(self.db, 'main'), 1)

    def test_silent_drop_still_rejected_with_indices(self):
        task, work = self._reach_facts_after_context_drift()
        data = review(work['payload']['facts_checks'], pack_prose(self.path, work))
        data.update(summary='漏交代。', events=[])
        with self.assertRaises(Error) as caught:
            submit_review(self.db, task, data)
        self.assertEqual(caught.exception.code, 'event_accounting')
        issue = caught.exception.details['issues'][0]
        self.assertEqual(issue['indices'], [0])
        self.assertIn('prior_dispositions', issue['recover'])


def copy_events(events):
    return json.loads(json.dumps(events, ensure_ascii=False))


class TemplateMirrorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.path = Path(self.tmp.name)
        c = contract(); c['planning_mode'] = 'layered'; c['volumes'].append({'id': 'v2', 'purpose': '公开结算'})
        initialize(self.path, c); self.db = connect(self.path)

    def tearDown(self):
        self.db.close(); self.tmp.cleanup()

    def task(self):
        return engine.next_task(self.db, self.path)

    def work_input(self, task):
        row = self.db.execute('SELECT * FROM tasks WHERE id=?', (task['task_id'],)).fetchone()
        return dict(row), json.loads(row['input'])

    def test_volume_plan_skeleton_passes_validation(self):
        world = self.task(); self.assertEqual(world['stage'], 'world_plan')
        submit_review(self.db, world, copy.deepcopy(EXAMPLE['world_plan']))
        task = self.task(); self.assertEqual(task['stage'], 'volume_plan')
        from novel_core.taskio import template
        row, work = self.work_input(task)
        skeleton = template(row, work, db=self.db)['data']
        self.assertEqual(skeleton['volume'], 'v1')
        filled = fill_skeleton(skeleton)
        engine.submit(self.db, result(task, filled))
        self.assertEqual(self.task()['stage'], 'plan')

    def test_layered_plan_skeleton_carries_rhythm_and_validates(self):
        submit_review(self.db, self.task(), copy.deepcopy(EXAMPLE['world_plan']))
        submit_review(self.db, self.task(), copy.deepcopy(EXAMPLE['volume_plan']))
        task = self.task(); self.assertEqual(task['stage'], 'plan')
        from novel_core.taskio import template
        row, work = self.work_input(task)
        skeleton = template(row, work, db=self.db)['data']['chapters'][0]
        self.assertEqual(set(skeleton['rhythm']),
                         {'chapter_role', 'pressure_curve', 'breathing_points', 'carries_in', 'handoff'})
        baseline = self.db.execute('SELECT count(*) FROM plans').fetchone()[0]
        filled = fill_skeleton(skeleton)
        filled['ending_shape'] = '闭环:测试收束'      # placeholder text carries no closing category
        engine.validate_plan(self.db, 'main', task['chapter'], {'chapters': [filled]})
        self.assertEqual(self.db.execute('SELECT count(*) FROM plans').fetchone()[0], baseline + 1)


if __name__ == '__main__':
    unittest.main()
