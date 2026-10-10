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
        c=contract();c['style']={'traits':['动作呈现选择'],'sample':'周行核对每一项证据。'}
        initialize(self.path,c);self.db=connect(self.path)
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
            t=self.task();self.assertEqual(t['stage'],'chapter_review')
            work=json.loads(rpath(t, 'input_path').read_text(encoding='utf-8'));prose=pack_prose(self.path, work)
            data=review(work['payload']['facts_checks'],prose,n)
            data.update(events=[{'op':'clock','before':n-1,'after':n,'evidence':ev(prose,n)}],summary=f'第{n}章。')
            submit_review(self.db,t,data)
            if n==1 and self.task().get('stage')=='await_author':engine.approve(self.db)
        self.assertEqual(head(self.db,'main'),2)

    def test_unchanged_chapters_fast_commit_without_re_review(self):
        self._two_chapters()
        engine.begin_revision(self.db,1,'定点复核')
        for n in (1,2):
            t=self.task();self.assertEqual(t['stage'],'draft')
            payload=json.loads(rpath(t, 'input_path').read_text(encoding='utf-8'))['payload']
            outcome=engine.submit(self.db,result(t,{'base_hash':payload['base_hash'],'reuse_original':True}))
            self.assertTrue(outcome['fast_committed'])
            self.assertEqual(head(self.db,get(self.db,'work_branch')),n)
            nxt=self.task()
            self.assertNotEqual(nxt.get('stage'),'chapter_review')          # no re-review dispatched
            if nxt.get('stage')=='revision_review':
                break
        self.assertEqual(head(self.db,'main'),2)                    # main untouched

    def test_patch_route_only_touches_cited_chapter(self):
        self._two_chapters()
        engine.begin_revision(self.db,1,'第2章收束段定点修改')
        t=self.task()                                               # ch1 draft
        payload=json.loads(rpath(t, 'input_path').read_text(encoding='utf-8'))['payload']
        engine.submit(self.db,result(t,{'base_hash':payload['base_hash'],'reuse_original':True}))
        self.assertEqual(head(self.db,get(self.db,'work_branch')),1)
        t=self.task();self.assertEqual(t['stage'],'draft')          # ch2 draft (patch target)
        payload=json.loads(rpath(t, 'input_path').read_text(encoding='utf-8'))['payload']
        from novel_core.patches import source
        base=source(self.db,{'stage':'draft','payload':payload})
        old='两人核对每一行。';new='两人在灯下把最后一行核完才歇。'
        data={'base_hash':payload['base_hash'],'repair_note':'收束段去同质化',
              'patches':[{'start':base.index(old),'old':old,'new':new}]}
        submit_review(self.db,t,data)
        t=self.task();self.assertEqual(t['stage'],'chapter_review')          # changed chapter re-registers
        work=json.loads(rpath(t, 'input_path').read_text(encoding='utf-8'))
        prior=work['payload']['prior_events']
        self.assertEqual(work['payload']['prior_events_source'],'revision')
        data=review(work['payload']['facts_checks'],pack_prose(self.path, work),2)
        dispositions=[{'index':i,'disposition':'kept'} for i in range(len(prior))]
        for d in dispositions:d['disposition']='kept'
        data.update(events=[],summary='仅收束段措辞变化。',prior_dispositions=dispositions)
        # kept 事件须原样出现——但引文整段断裂,自动续算不可用;改为逐条 replaced
        data['prior_dispositions']=[{'index':i,'disposition':'replaced','reason':'收束段措辞变化,事件内容不变但证据需重锚'} for i in range(len(prior))]
        data['events']=[dict(e) for e in prior]
        for e in data['events']:
            e['evidence']={'chapter':2,'start':0,'quote':pack_prose(self.path, work)}
        submit_review(self.db,t,data)
        self.assertEqual(head(self.db,get(self.db,'work_branch')),2)

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
            work=json.loads(rpath(t, 'input_path').read_text(encoding='utf-8'));prose=pack_prose(self.path, work)
            data=review(work['payload']['facts_checks'],prose,n)
            before=100 if n==1 else 70
            data.update(events=[{'op':'resource','asset':'cash','unit':'文','quantity':spend,
                                 'from':'hero','to':None,'from_before':str(before),
                                 'reason':'买纸墨','evidence':ev(prose,n)}],summary=f'第{n}天付{spend}文。')
            submit_review(self.db,t,data)
            if n==1:
                t=self.task()
                if t.get('stage')=='await_author':engine.approve(self.db)
        self.assertEqual(head(self.db,'main'),2)
        engine.begin_revision(self.db,1,'删去第1章的支出')
        t=self.task()                                               # ch1 draft: patch out the spend
        payload=json.loads(rpath(t, 'input_path').read_text(encoding='utf-8'))['payload']
        from novel_core.patches import source
        base=source(self.db,{'stage':'draft','payload':payload})
        old='周行付了30文买纸墨。'
        data={'base_hash':payload['base_hash'],'repair_note':'删去支出',
              'patches':[{'start':base.index(old),'old':old,'new':'周行把纸墨记在了赊账簿上。'}]}
        submit_review(self.db,t,data)
        t=self.task();self.assertEqual(t['stage'],'chapter_review')
        work=json.loads(rpath(t, 'input_path').read_text(encoding='utf-8'));prose=pack_prose(self.path, work)
        data=review(work['payload']['facts_checks'],prose,1)
        data.update(events=[{'op':'clock','before':0,'after':1,'evidence':ev(prose,1)}],summary='记赊账,无支出。',
                    prior_dispositions=[{'index':0,'disposition':'dropped','reason':'支出句已删,资源流水不再发生'}])
        submit_review(self.db,t,data)
        self.assertEqual(head(self.db,get(self.db,'work_branch')),1)
        t=self.task();self.assertEqual(t['stage'],'draft')          # ch2 reuse → 前置(90)已断裂
        payload=json.loads(rpath(t, 'input_path').read_text(encoding='utf-8'))['payload']
        outcome=engine.submit(self.db,result(t,{'base_hash':payload['base_hash'],'reuse_original':True}))
        self.assertNotIn('fast_committed',outcome)
        t=self.task();self.assertEqual(t['stage'],'chapter_review')          # fell back to a real facts pass

    def _historical_read_without_write(self, dependency=False, token_fact=False):
        """The second chapter reads history but writes no ledger events."""
        respond(self.db,self.path,self.task())
        first='周行得到玉佩，亲手挂在腰间。' if token_fact else '周行决定明日开庭，宋禾答应到场。'
        respond(self.db,self.path,self.task(),prose=first)
        events=[{'op':'state','entity':'hero','key':'has_token','before':None,'after':True,
                 'evidence':ev(first,1,'得到玉佩')}] if token_fact else []
        respond(self.db,self.path,self.task(),events=events)
        engine.approve(self.db)
        t=self.task();p=plan(2)
        if dependency:p['dependencies']=[1]
        engine.submit(self.db,result(t,{'chapters':[p]}))
        second='周行按住腰间的玉佩，松了一口气。' if token_fact else '宋禾按照昨日约定赴庭，等待周行。'
        respond(self.db,self.path,self.task(),prose=second)
        respond(self.db,self.path,self.task(),events=[])

    def _change_first_then_reuse_second(self, prose, events=None, dispositions=None):
        engine.begin_revision(self.db,1,'修正第一章历史内容')
        respond(self.db,self.path,self.task(),prose=prose)
        t=self.task();work=json.loads(rpath(t).read_text(encoding='utf-8'))
        data=review(work['payload']['facts_checks'],prose,1)
        data.update(events=events or [],summary='第一章历史内容已修正。')
        if dispositions is not None:data['prior_dispositions']=dispositions
        submit_review(self.db,t,data)
        t=self.task();payload=json.loads(rpath(t).read_text(encoding='utf-8'))['payload']
        return engine.submit(self.db,result(t,{'base_hash':payload['base_hash'],'reuse_original':True}))

    def test_upstream_fact_change_rechecks_chapter_with_no_write_events(self):
        self._historical_read_without_write(token_fact=True)
        prose='周行没有取得玉佩，腰间空空如也。'
        outcome=self._change_first_then_reuse_second(prose,
            [{'op':'state','entity':'hero','key':'has_token','before':None,'after':False,
              'evidence':ev(prose,1,'没有取得玉佩')}],
            [{'index':0,'disposition':'replaced','reason':'原文取得玉佩错误，改为未取得。'}])
        self.assertNotIn('fast_committed',outcome)
        self.assertEqual(self.task()['stage'],'chapter_review')

    def test_unregistered_historical_change_rechecks_implicit_read(self):
        self._historical_read_without_write()
        outcome=self._change_first_then_reuse_second('周行决定取消明日开庭，宋禾答应不再到场。')
        self.assertNotIn('fast_committed',outcome)
        self.assertEqual(self.task()['stage'],'chapter_review')

    def test_changed_explicit_dependency_rechecks_even_with_equal_state(self):
        self._historical_read_without_write(dependency=True)
        outcome=self._change_first_then_reuse_second('周行决定取消明日开庭，宋禾答应不再到场。')
        self.assertNotIn('fast_committed',outcome)
        self.assertEqual(self.task()['stage'],'chapter_review')

    def test_parent_reviews_must_actually_have_passed(self):
        self._two_chapters()
        row=self.db.execute("SELECT data FROM chapters WHERE branch='main' AND number=1").fetchone()
        data=json.loads(row[0]);data['reader_review']['verdict']='fix'
        self.db.execute("UPDATE chapters SET data=? WHERE branch='main' AND number=1",(dump(data),))
        engine.begin_revision(self.db,1,'核验不可信的旧审稿记录')
        t=self.task();payload=json.loads(rpath(t).read_text(encoding='utf-8'))['payload']
        outcome=engine.submit(self.db,result(t,{'base_hash':payload['base_hash'],'reuse_original':True}))
        self.assertNotIn('fast_committed',outcome)
        self.assertEqual(self.task()['stage'],'chapter_review')


if __name__=='__main__':unittest.main()
