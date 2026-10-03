"""Behavior regressions from isolated forward testing; no model or network calls.

Run directly: python3 tests/test_independent_regressions.py
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import textwrap
import unittest

from support import (
    contract, drive, engine, ev, initialize, connect, get, plan, respond,
    result, review, rpath, transaction,
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

    @unittest.skipUnless(os.name == 'posix', 'Process-group regression is POSIX-specific')
    def test_exited_driver_does_not_leave_workers_on_retry_or_success(self):
        cli = Path(__file__).resolve().parents[1] / 'scripts' / 'novel.py'
        for mode in ('fail', 'pass'):
            with self.subTest(mode=mode):
                project, db = self.book()
                child = project / 'child.py'
                child.write_text(textwrap.dedent('''\
                    import json
                    import os
                    from pathlib import Path
                    import time
                    Path('child-' + str(os.getpid()) + '.json').write_text(
                        json.dumps({'pid': os.getpid(), 'pgid': os.getpgrp()}))
                    time.sleep(60)
                '''), encoding='utf-8')
                driver = project / 'driver.py'
                driver.write_text(textwrap.dedent('''\
                    import json
                    import os
                    from pathlib import Path
                    import subprocess
                    import sys
                    import time
                    child = subprocess.Popen([sys.executable, 'child.py'])
                    marker = Path('child-' + str(child.pid) + '.json')
                    deadline = time.monotonic() + 5
                    while not marker.exists():
                        if time.monotonic() > deadline:
                            raise SystemExit(3)
                        time.sleep(0.01)
                    if sys.argv[1] == 'fail':
                        raise SystemExit(1)
                    task = json.loads(Path(os.environ['NOVEL_TASK_FILE']).read_text())
                    data = json.loads(Path('plan.json').read_text())
                    Path(os.environ['NOVEL_RESULT_FILE']).write_text(json.dumps({
                        'schema': 'novel-ledger-v2.result/1',
                        'task_id': task['task_id'],
                        'input_hash': task['input_hash'],
                        'lease_owner': task['lease_owner'],
                        'data': data,
                    }))
                '''), encoding='utf-8')
                (project / 'plan.json').write_text(
                    json.dumps({'chapters': [plan(1)]}), encoding='utf-8')
                config = project / 'driver.json'
                config.write_text(json.dumps({
                    'argv': [sys.executable, str(driver), mode],
                    'timeout': 10,
                    'max_retries': 1,
                }), encoding='utf-8')
                try:
                    completed = subprocess.run([
                        sys.executable, str(cli), 'run', '--project', str(project),
                        '--driver', str(config), '--max-tasks', '1',
                    ], capture_output=True, text=True, timeout=30)
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    outcome = json.loads(completed.stdout)
                    markers = list(project.glob('child-*.json'))
                    self.assertEqual(len(markers), 2 if mode == 'fail' else 1)
                    for marker in markers:
                        pid = json.loads(marker.read_text())['pid']
                        # Observe independently of the runner's own liveness helper.
                        observed = subprocess.run(
                            ['ps', '-p', str(pid), '-o', 'stat='],
                            capture_output=True, text=True, check=False,
                        ).stdout.strip()
                        self.assertTrue(not observed or observed.startswith('Z'),
                                        f'Driver descendant {pid} still running: {observed}')
                    self.assertIsNone(get(db, 'runner'))
                    if mode == 'fail':
                        self.assertEqual(outcome['status'], 'paused')
                        self.assertEqual(outcome['reason']['code'], 'driver_exhausted')
                    else:
                        self.assertEqual(outcome['tasks_completed'], 1)
                        self.assertEqual(engine.next_task(db, project)['stage'], 'draft')
                finally:
                    # Also clean up when running this test against the broken version.
                    for marker in project.glob('child-*.json'):
                        group = json.loads(marker.read_text())['pgid']
                        try:
                            os.killpg(group, signal.SIGKILL)
                        except ProcessLookupError:
                            pass

    def test_unrepresentable_resource_result_rolls_back_and_task_can_recover(self):
        config = contract()
        config['initial_resources'][0]['quantity'] = '1e120'
        project, db = self.book(config)
        respond(db, project, engine.next_task(db, project))
        respond(db, project, engine.next_task(db, project),
                prose='周行划去那笔一文的收入，将原有银钱全部交出。')
        task = engine.next_task(db, project)
        prose = json.loads(rpath(task, 'input_path').read_text())['payload']['prose']
        data = review(engine.FACT_CHECKS, prose)
        data.update(summary='周行交出原有银钱。', events=[{
            'op': 'resource', 'asset': 'cash', 'unit': '文',
            'quantity': '1', 'from': None, 'to': 'hero',
            'to_before': '1e120', 'reason': '收入', 'evidence': ev(prose),
        }])
        before = replay(db, 'main')
        with self.assertRaises(Error) as rejected:
            engine.submit(db, result(task, data))
        self.assertEqual(rejected.exception.code, 'amount')
        self.assertEqual(replay(db, 'main'), before)
        self.assertEqual(head(db, 'main'), 0)
        self.assertEqual(engine.next_task(db, project)['task_id'], task['task_id'])
        data['events'] = [{
            'op': 'resource', 'asset': 'cash', 'unit': '文',
            'quantity': '1e120', 'from': 'hero', 'to': None,
            'from_before': '1e120', 'reason': '交出原有银钱', 'evidence': ev(prose),
        }]
        engine.submit(db, result(task, data))
        respond(db, project, engine.next_task(db, project))
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
                payload = json.loads(rpath(task, 'input_path').read_text())['payload']
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
                engine.submit(db, result(task, data))
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
