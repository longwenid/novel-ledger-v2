"""v1.44.0 envelope fixes: plan preflight/aggregation/template, stray DB sweep."""
import json
import tempfile
import unittest
from pathlib import Path
from support import contract, initialize, connect, respond, plan, result
from novel_core import engine
from novel_core.store import Error, dump
from novel_core.taskio import template
import novel


def fill_skeleton(value, key=None):
    """Replace template placeholders with minimal real content."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k == 'time_hint':
                continue  # optional; a placeholder would pin a bogus day schedule
            out[k] = 'hero' if k in ('entity',) and isinstance(v, str) and v.startswith('<') else fill_skeleton(v, k)
        return out
    if isinstance(value, list):
        if key in ('entities', 'present', 'speakers'):
            return ['hero']
        return [fill_skeleton(v, key) for v in value]
    if isinstance(value, str) and (value.startswith('<') or not value.strip()):
        return '示例内容：经核对后写成的实际字段。'
    return value


class EnvelopeFixTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.path = Path(self.tmp.name)
        c = contract(); c['chapter_batch_size'] = 1; initialize(self.path, c); self.db = connect(self.path)
        for _ in range(3):
            respond(self.db, self.path, engine.next_task(self.db, self.path))
        engine.approve(self.db)

    def tearDown(self):
        self.db.close(); self.tmp.cleanup()

    def cli(self, *args):
        return novel.dispatch(novel.parser().parse_args([args[0], '--project', str(self.path), *args[1:]]))

    def plan_task(self):
        task = self.cli('advance')['task']
        self.assertEqual(task['stage'], 'plan')
        return task

    def plans_count(self):
        return self.db.execute('SELECT count(*) FROM plans').fetchone()[0]

    def test_preflight_validates_plan_without_writing(self):
        task = self.plan_task()
        baseline = self.plans_count()
        data = {'chapters': [plan(2), plan(3)]}
        path = self.path / task['output_path']
        path.write_text(dump(result(task, data)))
        out = self.cli('preflight', '--result', str(path))
        self.assertTrue(out['checked']); self.assertFalse(out['submitted'])
        self.assertEqual(self.plans_count(), baseline)
        row = self.db.execute('SELECT state FROM tasks WHERE id=?', (task['task_id'],)).fetchone()
        self.assertEqual(row['state'], 'pending')
        self.assertTrue(self.cli('submit', '--result', str(path))['accepted'])
        self.assertEqual(self.plans_count(), baseline + 2)

    def test_plan_rejection_lists_every_chapter_and_field(self):
        task = self.plan_task()
        baseline = self.plans_count()
        data = {'chapters': [
            {'number': 2, 'goal': '', 'conflict': '阻力', 'change': '变化', 'volume': 'v1',
             'entities': ['ghost'], 'scenes': [{'label': '角落', 'present': ['ghost']}]},
            {'number': 3, 'goal': '目标', 'conflict': '阻力', 'change': '变化', 'volume': 'v9',
             'entities': [], 'scenes': []},
        ]}
        path = self.path / task['output_path']
        path.write_text(dump(result(task, data)))
        with self.assertRaises(Error) as caught:
            self.cli('submit', '--result', str(path))
        issues = caught.exception.details['issues']
        chapters = {i.get('chapter') for i in issues}
        self.assertEqual(chapters, {2, 3})
        self.assertGreaterEqual(len(issues), 4)
        self.assertEqual(self.plans_count(), baseline)

    def _skeleton_for(self, extra=None):
        task = {'id': 'fixture-task', 'input_hash': '0' * 64, 'stage': 'plan', 'chapter': 2, 'branch': 'main'}
        return template(task, {}, db=self.db)['data']['chapters'][0]

    def test_plan_template_skeleton_passes_validation_default_mode(self):
        skeleton = self._skeleton_for()
        self.assertEqual(skeleton['volume'], 'v1')
        self.assertNotIn('speakers', skeleton['scenes'][0])
        baseline = self.plans_count()
        engine.validate_plan(self.db, 'main', 2, {'chapters': [fill_skeleton(skeleton)]})
        self.assertEqual(self.plans_count(), baseline + 1)

    def test_plan_template_skeleton_passes_validation_profiled_mode(self):
        self.db.close(); self.tmp.cleanup()
        self.tmp = tempfile.TemporaryDirectory(); self.path = Path(self.tmp.name)
        c = contract(); c['character_mode'] = 'profiled'
        initialize(self.path, c); self.db = connect(self.path)
        skeleton = self._skeleton_for()
        scene = skeleton['scenes'][0]
        self.assertIn('speakers', scene); self.assertIn('dialogue_intents', scene)
        engine.validate_plan(self.db, 'main', 2, {'chapters': [fill_skeleton(skeleton)]})
        self.assertEqual(self.plans_count(), 1)

    def test_doctor_reports_and_tidy_relocates_stray_ledger_files(self):
        (self.path / 'ledger.sqlite').write_bytes(b'legacy-data')
        report = self.cli('doctor')
        stray = report.get('stray_ledger_files', [])
        self.assertTrue(any(r['path'] == 'ledger.sqlite' for r in stray))
        self.assertTrue((self.path / 'ledger.sqlite').exists())
        self.cli('tidy')
        backup = self.path / 'work' / 'backup-legacy-db' / 'ledger.sqlite'
        self.assertEqual(backup.read_bytes(), b'legacy-data')
        self.assertFalse((self.path / 'ledger.sqlite').exists())
        (self.path / 'ledger.sqlite').write_bytes(b'legacy-data-2')
        self.cli('tidy')
        self.assertTrue((self.path / 'work' / 'backup-legacy-db' / 'ledger.sqlite.1').exists())


if __name__ == '__main__':
    unittest.main()
