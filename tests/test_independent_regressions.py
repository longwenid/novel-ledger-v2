"""Behavior regressions from isolated forward testing; no model or network calls.

Run directly: python3 tests/test_independent_regressions.py
"""
import json
from pathlib import Path
import tempfile
import unittest

from support import (
    connect,
    contract,
    drive,
    engine,
    envelope,
    ev,
    get,
    initialize,
    pack_prose,
    plan,
    respond,
    result,
    review,
    rpath,
    submit_review,
    transaction,
)
from novel import dispatch, parser
from novel_core.ledger import replay, resource_key
from novel_core.store import Error, chapters, head


class IndependentRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.book_number = 0

    def book(self, config=None):
        self.book_number += 1
        project = self.root / str(self.book_number)
        initialize(project, config or contract())
        db = connect(project)
        self.addCleanup(db.close)
        return project, db

    def test_unrepresentable_resource_result_rolls_back_and_task_can_recover(self):
        config = contract()
        config['initial_resources'][0]['quantity'] = '1e120'
        project, db = self.book(config)
        respond(db, project, engine.next_task(db, project))
        respond(db, project, engine.next_task(db, project),
                prose='周行划去那笔一文的收入，将原有银钱全部交出。')
        task = engine.next_task(db, project)
        prose = pack_prose(project, envelope(task))
        data = review(engine.FACT_CHECKS, prose)
        data.update(summary='周行交出原有银钱。', events=[{
            'op': 'resource', 'asset': 'cash', 'unit': '文',
            'quantity': '1', 'from': None, 'to': 'hero',
            'to_before': '1e120', 'reason': '收入', 'evidence': ev(prose),
        }])
        before = replay(db, 'main')
        with self.assertRaises(Error) as rejected:
            submit_review(db, task, data)
        self.assertEqual(rejected.exception.code, 'amount')
        self.assertEqual(replay(db, 'main'), before)
        self.assertEqual(head(db, 'main'), 0)
        self.assertEqual(engine.next_task(db, project)['task_id'], task['task_id'])
        data['events'] = [{
            'op': 'resource', 'asset': 'cash', 'unit': '文',
            'quantity': '1e120', 'from': 'hero', 'to': None,
            'from_before': '1e120', 'reason': '交出原有银钱', 'evidence': ev(prose),
        }]
        submit_review(db, task, data)
        self.assertEqual(head(db, 'main'), 1)
        from decimal import Decimal
        self.assertEqual(Decimal(replay(db, 'main')['resources'][
            resource_key('hero', 'cash', '文')]), Decimal(0))
        self.assertTrue(engine.audit(db)['ok'])

    def test_revision_rejects_early_ending_and_can_finish_original_suffix(self):
        project, db = self.book()
        self.assertEqual(drive(db, project, 3)['status'], 'completed')
        original = [(row['number'], row['hash']) for row in chapters(db, 'main')]
        with transaction(db):
            engine.begin_revision(db, 1, '收紧结局并复核后文')
        dispatch(parser().parse_args([
            'replan', '--project', str(project), '--reason', '调整结局节奏',
        ]))
        task = engine.next_task(db, project)
        self.assertEqual(task['stage'], 'plan')
        with self.assertRaises(Error) as rejected:
            engine.submit(db, result(task, {'chapters': [plan(1, ending=True)]}))
        self.assertEqual(rejected.exception.code, 'revision')
        self.assertEqual(engine.next_task(db, project)['task_id'], task['task_id'])
        self.assertEqual(head(db, get(db, 'work_branch')), 0)
        self.assertEqual(drive(db, project, 3)['status'], 'completed')
        self.assertEqual(head(db, get(db, 'active_branch')), 3)
        self.assertNotEqual(get(db, 'active_branch'), 'main')
        self.assertEqual([(row['number'], row['hash'])
                          for row in chapters(db, 'main')], original)

    def test_three_independent_batch_repairs_each_get_a_revision_budget(self):
        project, db = self.book()
        requested = set()
        promotions = 0
        for _ in range(1500):
            task = engine.next_task(db, project)
            stage = task.get('stage')
            if stage == 'await_author':
                engine.approve(db)
                continue
            if stage == 'revision_promoted':
                promotions += 1
                continue
            if not task.get('task_id'):
                self.assertEqual(task.get('status'), 'completed', task)
                break
            if (stage == 'batch_review' and task['chapter'] not in requested
                    and task['branch'] == get(db, 'active_branch')):
                requested.add(task['chapter'])
                payload = envelope(task)['payload']
                first = payload['first']
                prose = db.execute(
                    'SELECT prose FROM chapters WHERE branch=? AND number=?',
                    (task['branch'], first),
                ).fetchone()[0]
                data = review(payload['checks'], prose, first)
                data['verdict'] = 'fix'
                data['checks'][0]['result'] = 'fix'
                data['findings'] = [{
                    'severity': 'quality', 'impact': '本批人物选择尚不清晰',
                    'goal': '补上人物主动选择', 'evidence': ev(prose, first),
                }]
                submit_review(db, task, data)
            else:
                respond(db, project, task, ending=task['chapter'] == 30)
        else:
            self.fail('State machine failed to finish within its bounded test budget')
        self.assertEqual(requested, {10, 20, 30})
        self.assertEqual(promotions, 3)
        self.assertEqual(head(db, get(db, 'active_branch')), 30)
        self.assertTrue(engine.audit(db)['ok'])


if __name__ == '__main__':
    unittest.main()
