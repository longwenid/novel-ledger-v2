import copy
import json
import tempfile
import unittest
from pathlib import Path
from support import *
from novel_core.store import Error, digest, head, chapters
from novel_core.ledger import replay, apply, seed, resource_key, append_events
from novel_core.memory import recall, pack

class BookTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        initialize(self.path,contract());self.db=connect(self.path)
    def tearDown(self): self.db.close();self.temp.cleanup()
    def task(self): return engine.next_task(self.db,self.path)
    def chapter(self,ending=False):
        while head(self.db,get(self.db,'work_branch'))==0:
            t=self.task();respond(self.db,self.path,t,ending=ending)
    def test_idempotent_task_and_receipt(self):
        a=self.task();self.assertEqual(a,self.task())
        r=result(a,{'chapters':[plan(1)]})
        engine.submit(self.db,r);self.assertTrue(engine.submit(self.db,r)['idempotent'])
        r['data']['chapters'][0]['goal']='另一目标'
        with self.assertRaises(Error):engine.submit(self.db,r)
    def test_pipeline_completion_full_review(self):
        out=drive(self.db,self.path,2)
        self.assertEqual(out['status'],'completed')
        self.assertTrue(engine.audit(self.db)['ok'])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM reviews WHERE scope='book_chunk'").fetchone()[0],2)
    def test_no_premature_complete(self):
        with self.assertRaises(Error):
            with transaction(self.db):engine.complete_inner(self.db)
    def test_fresh_session_reuse(self):
        with transaction(self.db):put(self.db,'session_mode','fresh')
        t=self.task();engine.submit(self.db,result(t,{'chapters':[plan(1)]},session={'id':'same','origin':'empty'}))
        t=self.task()
        with self.assertRaises(Error):engine.submit(self.db,result(t,{'prose':'周行交出收据。'},session={'id':'same','origin':'empty'}))
        self.assertEqual(self.task()['task_id'],t['task_id'])
    def test_quality_failure_repair_and_no_canon_pollution(self):
        t=self.task();respond(self.db,self.path,t)
        t=self.task();respond(self.db,self.path,t)
        t=self.task();respond(self.db,self.path,t)
        t=self.task();p=json.loads(rpath(t, 'input_path').read_text(encoding='utf-8'))['payload']['prose']
        bad=review(engine.READER_CHECKS,p);bad['verdict']='fix';bad['checks'][0]['result']='fix'
        bad['first_read']={'desire':'目标','obstacle':'阻力','change':'变化','read_on_reason':'不想读'}
        bad['findings']=[{'severity':'quality','impact':'人物没有选择，场面像汇报','goal':'呈现犹豫与选择','evidence':ev(p)}]
        engine.submit(self.db,result(t,bad))
        self.assertEqual(head(self.db,'main'),0);self.assertEqual(self.task()['stage'],'repair')
    def test_bad_evidence_transaction_rolls_back(self):
        respond(self.db,self.path,self.task());respond(self.db,self.path,self.task())
        t=self.task();p=json.loads(rpath(t, 'input_path').read_text(encoding='utf-8'))['payload']['prose']
        data=review(engine.FACT_CHECKS,p);data.update(summary='摘要',events=[{'op':'state','entity':'hero','key':'alive','before':None,'after':False,'evidence':{'chapter':1,'start':0,'quote':'不存在'}}])
        with self.assertRaises(Error):engine.submit(self.db,result(t,data))
        self.assertEqual(self.task()['task_id'],t['task_id']);self.assertEqual(head(self.db,'main'),0)
    def test_resource_conservation_knowledge_alias_and_time(self):
        s=seed(contract());p='周行给宋禾三十文，听说收据是假的。他明日再来。'
        common={'evidence':ev(p)}
        apply(s,dict(common,op='resource',asset='cash',unit='文',quantity='30',**{'from':'hero','to':'ally','from_before':'100','to_before':'0'},reason='转交'),p,1)
        self.assertEqual(s['resources'][resource_key('hero','cash','文')],'70')
        self.assertEqual(s['resources'][resource_key('ally','cash','文')],'30')
        apply(s,dict(common,op='knowledge',entity='hero',topic='receipt',before=None,claim='收据是假',stance='believes',source='传言'),p,1)
        self.assertFalse(s['facts']);self.assertEqual(s['knowledge']['hero:receipt']['stance'],'believes')
        with self.assertRaises(Error):apply(s,dict(common,op='entity',entity={'id':'hero2','name':'老周','kind':'person'}),p,1)
        with self.assertRaises(Error):apply(s,dict(common,op='clock',before=0,after=-1),p,1)
    def test_expired_lease_rejected(self):
        t=self.task()
        with transaction(self.db):self.db.execute('UPDATE tasks SET lease_owner=?,lease_until=0 WHERE id=?',('old',t['task_id']))
        with self.assertRaises(Error):engine.submit(self.db,result(t,{'chapters':[plan(1)]},lease_owner='old'))
    def test_cancelled_result_rejected(self):
        t=self.task();engine.pause(self.db,'取消测试',True);engine.resume(self.db)
        with self.assertRaises(Error):engine.submit(self.db,result(t,{'chapters':[plan(1)]}))
        self.assertNotEqual(t['task_id'],self.task()['task_id'])
    def test_revision_keeps_original_and_replays_suffix(self):
        drive(self.db,self.path,2);old=[r['hash'] for r in chapters(self.db,'main')]
        with transaction(self.db): rev=engine.begin_revision(self.db,1,'补充人物选择')
        t=self.task();respond(self.db,self.path,t,prose='周行不肯让宋禾独自作证，先在收据上签了自己的名字。')
        self.assertEqual([r['hash'] for r in chapters(self.db,'main')],old)
        out=drive(self.db,self.path,2)
        self.assertEqual(out['status'],'completed');self.assertNotEqual(get(self.db,'active_branch'),'main')
        self.assertEqual([r['hash'] for r in chapters(self.db,'main')],old)
    def test_published_revision_refused(self):
        drive(self.db,self.path,2)
        with transaction(self.db):put(self.db,'published_through',1)
        with self.assertRaises(Error):
            with transaction(self.db):engine.begin_revision(self.db,1,'不能改已发布稿')
    def test_context_capacity_and_absence_semantics(self):
        with transaction(self.db):put(self.db,'context_chars',4096)
        p=pack(self.db,'main',1,'draft',{})
        self.assertLessEqual(len(dump(p)),4096);self.assertTrue(p['retrieval']['absence_is_not_proof'])
        with self.assertRaises(Error):pack(self.db,'main',1,'draft',{'prose':'字'*5000})
    def test_file_tampering_does_not_change_database(self):
        t=self.task();rpath(t, 'input_path').write_text('{}', encoding='utf-8')
        t=self.task();self.assertIn('contract',json.loads(rpath(t, 'input_path').read_text(encoding='utf-8')))
    def test_length_not_self_reported(self):
        c=get(self.db,'contract');c['word_range']=[10000,20000]
        with transaction(self.db):put(self.db,'contract',c)
        with self.assertRaises(Error):drive(self.db,self.path,2)
        self.assertNotEqual(get(self.db,'status'),'completed')

if __name__=='__main__':unittest.main()

class BoundaryTests(unittest.TestCase):
    def test_empty_entities_rejected_before_creation(self):
        with tempfile.TemporaryDirectory() as temp:
            c=contract();c.pop('entities');c.pop('initial_resources')
            with self.assertRaises(Error):initialize(temp,c)
            self.assertFalse((Path(temp)/'novel.sqlite3').exists())
    def test_large_decimal_transfer_exact(self):
        c=contract();c['initial_resources'][0]['quantity']='100000000000000000000000000000'
        s=seed(c);p='周行给了宋禾一文钱。'
        apply(s,{'op':'resource','asset':'cash','unit':'文','quantity':'1','from':'hero','to':'ally','from_before':c['initial_resources'][0]['quantity'],'to_before':'0','reason':'转交','evidence':ev(p)},p,1)
        self.assertEqual(s['resources'][resource_key('hero','cash','文')],'99999999999999999999999999999')
