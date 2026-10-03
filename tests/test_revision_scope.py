"""Revision propagation scoping: unchanged chapters skip re-verification."""
import json
import tempfile
import unittest
from pathlib import Path
from support import *
from novel_core import engine
from novel_core.ledger import seed, append_events
from novel_core.store import Error, dump, digest, head, put, transaction


class FastCommitTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        initialize(self.path,contract());self.db=connect(self.path)
    def tearDown(self): self.db.close();self.temp.cleanup()
    def task(self): return engine.next_task(self.db,self.path)

    def _two_chapters(self):
        respond(self.db,self.path,self.task())                     # plan ch1
        for n in (1,2):
            if n==2:
                t=self.task()                                      # plan ch2 (after pilot approve)
                if t.get('stage')=='plan':respond(self.db,self.path,t)
            t=self.task();self.assertEqual(t['stage'],'draft')
            respond(self.db,self.path,t,prose=f'第{n}天，周行把收据交给宋禾，两人核对每一行。')
            t=self.task();self.assertEqual(t['stage'],'facts')
            work=json.loads(rpath(t, 'input_path').read_text());prose=work['payload']['prose']
            data=review(work['payload']['checks'],prose,n)
            data.update(events=[{'op':'clock','before':n-1,'after':n,'evidence':ev(prose,n)}],summary=f'第{n}章。')
            engine.submit(self.db,result(t,data))
            respond(self.db,self.path,self.task())                 # reader
            if n==1 and self.task().get('stage')=='await_author':engine.approve(self.db)
        self.assertEqual(head(self.db,'main'),2)

    def test_unchanged_chapters_fast_commit_without_re_review(self):
        self._two_chapters()
        engine.begin_revision(self.db,1,'定点复核')
        for n in (1,2):
            t=self.task();self.assertEqual(t['stage'],'draft')
            payload=json.loads(rpath(t, 'input_path').read_text())['payload']
            outcome=engine.submit(self.db,result(t,{'base_hash':payload['base_hash'],'reuse_original':True}))
            self.assertTrue(outcome['fast_committed'])
            self.assertEqual(head(self.db,get(self.db,'work_branch')),n)
            nxt=self.task()
            self.assertNotEqual(nxt.get('stage'),'facts')          # no re-review dispatched
            if nxt.get('stage')=='revision_review':
                break
        self.assertEqual(head(self.db,'main'),2)                    # main untouched

    def test_patch_route_only_touches_cited_chapter(self):
        self._two_chapters()
        engine.begin_revision(self.db,1,'第2章收束段定点修改')
        t=self.task()                                               # ch1 draft
        payload=json.loads(rpath(t, 'input_path').read_text())['payload']
        engine.submit(self.db,result(t,{'base_hash':payload['base_hash'],'reuse_original':True}))
        self.assertEqual(head(self.db,get(self.db,'work_branch')),1)
        t=self.task();self.assertEqual(t['stage'],'draft')          # ch2 draft (patch target)
        payload=json.loads(rpath(t, 'input_path').read_text())['payload']
        from novel_core.patches import source
        base=source(self.db,{'stage':'draft','payload':payload})
        old='两人核对每一行。';new='两人在灯下把最后一行核完才歇。'
        data={'base_hash':payload['base_hash'],'repair_note':'收束段去同质化',
              'patches':[{'start':base.index(old),'old':old,'new':new}]}
        engine.submit(self.db,result(t,data))
        t=self.task();self.assertEqual(t['stage'],'facts')          # changed chapter re-registers
        work=json.loads(rpath(t, 'input_path').read_text())
        prior=work['payload']['prior_events']
        self.assertEqual(work['payload']['prior_events_source'],'revision')
        data=review(work['payload']['checks'],work['payload']['prose'],2)
        dispositions=[{'index':i,'disposition':'kept'} for i in range(len(prior))]
        for d in dispositions:d['disposition']='kept'
        data.update(events=[],summary='仅收束段措辞变化。',prior_dispositions=dispositions)
        # kept 事件须原样出现——但引文整段断裂,自动续算不可用;改为逐条 replaced
        data['prior_dispositions']=[{'index':i,'disposition':'replaced','reason':'收束段措辞变化,事件内容不变但证据需重锚'} for i in range(len(prior))]
        data['events']=[dict(e) for e in prior]
        for e in data['events']:
            e['evidence']={'chapter':2,'start':0,'quote':work['payload']['prose']}
        engine.submit(self.db,result(t,data))
        self.assertEqual(self.task()['stage'],'reader')

    def test_fast_path_falls_back_when_upstream_breaks_precondition(self):
        respond(self.db,self.path,self.task())                     # plan ch1
        for n,spend in ((1,'30'),(2,'20')):
            if n==2:
                t=self.task()
                if t.get('stage')=='plan':respond(self.db,self.path,t)
            t=self.task()
            if t.get('stage')=='await_author':engine.approve(self.db);t=self.task()
            respond(self.db,self.path,t,prose=f'第{n}天，周行付了{spend}文买纸墨。')
            t=self.task()
            work=json.loads(rpath(t, 'input_path').read_text());prose=work['payload']['prose']
            data=review(work['payload']['checks'],prose,n)
            before=100 if n==1 else 70
            data.update(events=[{'op':'resource','asset':'cash','unit':'文','quantity':spend,
                                 'from':'hero','to':None,'from_before':str(before),
                                 'reason':'买纸墨','evidence':ev(prose,n)}],summary=f'第{n}天付{spend}文。')
            engine.submit(self.db,result(t,data))
            respond(self.db,self.path,self.task())
            if n==1:
                t=self.task()
                if t.get('stage')=='await_author':engine.approve(self.db)
        self.assertEqual(head(self.db,'main'),2)
        engine.begin_revision(self.db,1,'删去第1章的支出')
        t=self.task()                                               # ch1 draft: patch out the spend
        payload=json.loads(rpath(t, 'input_path').read_text())['payload']
        from novel_core.patches import source
        base=source(self.db,{'stage':'draft','payload':payload})
        old='周行付了30文买纸墨。'
        data={'base_hash':payload['base_hash'],'repair_note':'删去支出',
              'patches':[{'start':base.index(old),'old':old,'new':'周行把纸墨记在了赊账簿上。'}]}
        engine.submit(self.db,result(t,data))
        t=self.task();self.assertEqual(t['stage'],'facts')
        work=json.loads(rpath(t, 'input_path').read_text());prose=work['payload']['prose']
        data=review(work['payload']['checks'],prose,1)
        data.update(events=[{'op':'clock','before':0,'after':1,'evidence':ev(prose,1)}],summary='记赊账,无支出。',
                    prior_dispositions=[{'index':0,'disposition':'dropped','reason':'支出句已删,资源流水不再发生'}])
        engine.submit(self.db,result(t,data))
        self.assertEqual(self.task()['stage'],'reader')
        respond(self.db,self.path,self.task())
        self.assertEqual(head(self.db,get(self.db,'work_branch')),1)
        t=self.task();self.assertEqual(t['stage'],'draft')          # ch2 reuse → 前置(90)已断裂
        payload=json.loads(rpath(t, 'input_path').read_text())['payload']
        outcome=engine.submit(self.db,result(t,{'base_hash':payload['base_hash'],'reuse_original':True}))
        self.assertNotIn('fast_committed',outcome)
        t=self.task();self.assertEqual(t['stage'],'facts')          # fell back to a real facts pass


if __name__=='__main__':unittest.main()
