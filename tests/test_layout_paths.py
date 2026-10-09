"""Output consolidation, project-relative routing and voice naming."""
import json
import tempfile
import unittest
from pathlib import Path
from support import *
from novel_core.store import Error, put, transaction
from novel_core import engine
import novel


class LayoutPathTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        initialize(self.path,contract());self.db=connect(self.path)
    def tearDown(self): self.db.close();self.temp.cleanup()
    def task(self): return engine.next_task(self.db,self.path)

    def test_envelope_routes_are_project_relative(self):
        t=self.task()
        for key in ('input_path','output_path'):
            route=t[key]
            self.assertFalse(Path(route).is_absolute(),route)
            self.assertNotIn('\\',route)
            self.assertNotIn(':',route)
            self.assertTrue(route.startswith('work/ch0001/'),route)
        self.assertEqual(t['output_path'],f"work/ch0001/plan-{t['task_id'][:8]}/result.json")
        self.assertEqual(Path(t['project']),self.path.resolve())
        self.assertTrue((self.path/t['input_path']).exists())

    def test_directories_grouped_by_chapter(self):
        respond(self.db,self.path,self.task())                     # plan ch1
        t=self.task();self.assertEqual(t['stage'],'draft')
        self.assertTrue(str(t['input_path']).startswith('work/ch0001/draft-'))
        respond(self.db,self.path,t)
        t=self.task();self.assertEqual(t['stage'],'facts')
        self.assertTrue(str(t['input_path']).startswith('work/ch0001/facts-'))

    def test_finished_directories_are_pruned(self):
        drive(self.db,self.path,count=1)
        remaining=[p for p in (self.path/'work').rglob('task.json')
                   if 'attempt' not in json.loads(p.read_text(encoding='utf-8'))]
        # Prune keeps the 3 newest finished dirs; the task completing after the
        # final dispatch adds one more before the run stops.
        self.assertLessEqual(len(remaining),4)
        self.assertGreater(self.db.execute("SELECT COUNT(*) FROM tasks WHERE state='done'").fetchone()[0],5)
        for row in self.db.execute("SELECT receipt FROM tasks WHERE state='done' LIMIT 3"):
            self.assertIsNotNone(row[0])

    def test_tidy_removes_all_finished_and_legacy_dirs(self):
        drive(self.db,self.path,count=1)
        legacy=self.path/'work'/'legacy-uuid-dir';legacy.mkdir(parents=True,exist_ok=True)
        done=self.db.execute("SELECT id FROM tasks WHERE state='done' LIMIT 1").fetchone()[0]
        (legacy/'task.json').write_text(json.dumps({'task_id':done}),encoding='utf-8')
        outcome=novel.dispatch(novel.parser().parse_args(['tidy','--project',str(self.path)]))
        self.assertGreater(outcome['removed'],0)
        self.assertFalse(legacy.exists())
        self.assertEqual(len([p for p in (self.path/'work').rglob('task.json')]),0)

    def test_submit_resolves_relative_result_against_project(self):
        t=self.task()
        out=self.path/t['output_path']
        out.parent.mkdir(parents=True,exist_ok=True)
        out.write_text(json.dumps(result(t,{'chapters':[plan(1)]}),ensure_ascii=False),encoding='utf-8')
        outcome=novel.dispatch(novel.parser().parse_args(['submit','--project',str(self.path),'--result',t['output_path']]))
        self.assertTrue(outcome['accepted'])
        self.assertEqual(self.task()['stage'],'draft')

    def test_prune_never_touches_pending_or_unknown_dirs(self):
        t=self.task()                                              # pending plan task
        unknown=self.path/'work'/'ch0001'/'facts-deadbeef';unknown.mkdir(parents=True,exist_ok=True)
        (unknown/'task.json').write_text('not-json',encoding='utf-8')
        removed=engine.prune_work(self.db,self.path)
        self.assertTrue((self.path/t['input_path']).exists())
        self.assertTrue(unknown.exists())
        self.assertEqual(removed,[])

    def test_style_anchor_voice_round_trip(self):
        with transaction(self.db):
            put(self.db,'style_anchor',{'voice':'白描底+悬疑线索纪律','traits':['克制叙述,动作可追','事实与猜测分开写'],
                                        'sample':'刀出鞘时,雨还没停。','source':'author'})
        respond(self.db,self.path,self.task())
        t=self.task()
        work=json.loads(rpath(t).read_text(encoding='utf-8'))
        self.assertEqual(work['style_anchor']['voice'],'白描底+悬疑线索纪律')
        self.assertIn('克制叙述',work['style_anchor']['traits'][0])

    def test_invalid_voice_rejected(self):
        from novel_core.store import validate_style
        with self.assertRaises(Error): validate_style({'voice':['平实白描'],'sample':'雨停了。'})


if __name__=='__main__':unittest.main()
