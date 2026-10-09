import json
import tempfile
import unittest
from pathlib import Path
from support import *
from novel_core import dependencies
from novel_core.store import digest,Error
import novel


class DependencyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        initialize(self.path,contract());self.db=connect(self.path)
        self.task=engine.next_task(self.db,self.path)
    def tearDown(self):self.db.close();self.temp.cleanup()
    def cli(self,*argv):
        return novel.dispatch(novel.parser().parse_args([argv[0],'--project',str(self.path),*argv[1:]]))
    def test_initial_sidecar_does_not_rewrite_frozen_task_or_claim_comprehension(self):
        before=dict(self.db.execute('SELECT * FROM tasks WHERE id=?',(self.task['task_id'],)).fetchone())
        reads=dependencies.read_set(self.db,self.task['task_id'])
        self.assertEqual(reads['coverage'],'delivered_only');self.assertFalse(reads['complete_semantic_read_set']);self.assertFalse(reads['reuse_authority'])
        resumed=engine.next_task(self.db,self.path)
        self.assertEqual(resumed['task_id'],self.task['task_id'])
        self.assertEqual(dict(self.db.execute('SELECT * FROM tasks WHERE id=?',(self.task['task_id'],)).fetchone()),before)
        self.assertEqual(dependencies.read_set(self.db,self.task['task_id'])['signature'],reads['signature'])
    def test_negative_query_scope_is_recorded_and_domain_mutation_invalidates_witness(self):
        value=self.cli('recall','--topic','missing')
        self.assertEqual(value['total'],0)
        reads=self.cli('dependencies','--task',self.task['task_id'])
        entry=reads['entries'][-1];self.assertEqual(entry['query']['topic'],'missing');self.assertTrue(entry['versions_unchanged'])
        self.db.execute('INSERT INTO events VALUES (?,?,?,?,?,?)',('main',1,0,dump({'type':'hook','id':'missing'}),'','test'))
        reads=self.cli('dependencies','--task',self.task['task_id'])
        self.assertFalse(reads['versions_unchanged']);self.assertFalse(reads['reuse_authority'])
    def test_current_payer_is_distinct_from_historical_candidate_target(self):
        old_work={'payload':{'original_prose':'完整旧稿'},'branch':'main','chapter':1,'stage':'draft'}
        self.db.execute("INSERT INTO tasks(id,branch,stage,chapter,input_hash,input,state) VALUES ('old','main','draft',1,?,?,'done')",(digest(old_work),dump(old_work)))
        self.cli('candidate','--task','old')
        entry=dependencies.read_set(self.db,self.task['task_id'])['entries'][-1]
        self.assertEqual(entry['target_task'],'old');self.assertEqual(entry['query']['command'],'candidate')
        self.assertEqual(dependencies.read_set(self.db,'old')['coverage'],'unknown_legacy')
    def test_only_delivered_complete_current_chapter_can_support_profile_scope(self):
        prose='完整原章来源与充分语境。';h=digest(prose)
        self.db.execute('INSERT INTO chapters VALUES (?,?,?,?,?)',('main',1,prose,dump({'summary':'摘要'}),h))
        dependencies.record_read(self.db,self.task['task_id'],{'command':'recall','chapter':1,'branch':'main'},{'summary':'摘要'})
        self.assertEqual(dependencies.read_chapters(self.db,self.task['task_id'],'main'),{})
        self.cli('recall','--chapter','1')
        self.assertEqual(dependencies.read_chapters(self.db,self.task['task_id'],'main'),{'1':h})
        self.assertEqual(dependencies.read_chapters(self.db,self.task['task_id'],'other'),{})
        self.db.execute('UPDATE chapters SET prose=? WHERE branch=? AND number=1',('改变的来源','main'))
        self.assertEqual(dependencies.read_chapters(self.db,self.task['task_id'],'main'),{})
    def test_dependency_inspection_does_not_write_or_retrofit_old_coverage(self):
        before=list(self.db.iterdump());self.cli('dependencies','--task',self.task['task_id'])
        self.assertEqual(list(self.db.iterdump()),before)
        with self.assertRaises(Error):dependencies.read_set(self.db,'missing')


if __name__=='__main__':unittest.main()
