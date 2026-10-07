"""Architecture-level regressions: receipt normalization, evidence recalibration,
prior-event accounting, patch span repair, session handoff self-containment."""
import json
import tempfile
import unittest
from pathlib import Path
from support import *
from novel_core.store import Error, digest, head, put, transaction, get
from novel_core.ledger import evidence, replay
from novel_core import contextflow, engine


def logs(db, action):
    return [json.loads(r[0]) for r in db.execute('SELECT data FROM logs WHERE action=? ORDER BY id', (action,))]


class ArchitectureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        initialize(self.path,contract());self.db=connect(self.path)
    def tearDown(self): self.db.close();self.temp.cleanup()
    def task(self): return engine.next_task(self.db,self.path)

    def test_evidence_unique_drift_is_recalibrated(self):
        prose='周行把收据交给宋禾。她终于按下指印。'
        item={'chapter':1,'quote':'她终于按下指印','start':0}
        self.assertTrue(evidence(prose,item,1))
        self.assertEqual(item['start'],prose.index('她终于按下指印'))
        missing={'chapter':1,'quote':'周行把收据','start':None}
        self.assertTrue(evidence(prose,missing,1))
        self.assertEqual(missing['start'],0)

    def test_evidence_ambiguity_and_absence_stay_errors(self):
        prose='他点头。他又点头。'
        with self.assertRaises(Error) as exc: evidence(prose,{'quote':'点头','start':0},None)
        self.assertEqual(exc.exception.code,'evidence')
        with self.assertRaises(Error) as exc: evidence(prose,{'quote':'完全不存在的句子','start':0},None)
        self.assertEqual(exc.exception.code,'evidence')

    def test_top_level_worker_receipt_is_normalized(self):
        t=self.task()
        engine.submit(self.db,{'chapters':[plan(1)]})
        repaired=logs(self.db,'receipt_normalized')
        self.assertEqual(len(repaired),1);self.assertEqual(repaired[0]['task'],t['task_id'])
        self.assertEqual(self.task()['stage'],'draft')

    def test_mistyped_task_id_retargets_to_single_pending(self):
        t=self.task()
        raw={'schema':'novel-ledger-v2.result/1','task_id':t['task_id'][:-1]+('0' if t['task_id'][-1]!='0' else '1'),
             'data':{'chapters':[plan(1)]}}
        engine.submit(self.db,raw)
        self.assertEqual(logs(self.db,'receipt_normalized')[0]['fixed']['task_id'],True)
        self.assertEqual(self.task()['stage'],'draft')

    def test_well_formed_receipt_is_not_logged(self):
        respond(self.db,self.path,self.task())
        self.assertEqual(logs(self.db,'receipt_normalized'),[])

    def test_normalization_keeps_version_and_replay_protection(self):
        t=self.task()
        with self.assertRaises(Error) as exc:
            engine.submit(self.db,{'chapters':[plan(1)],'input_hash':'deadbeef'})
        self.assertEqual(exc.exception.code,'input_version')
        engine.submit(self.db,{'chapters':[plan(1)]})
        self.assertTrue(engine.submit(self.db,{'chapters':[plan(1)]})['idempotent'])
        with self.assertRaises(Error) as exc:
            engine.submit(self.db,{'chapters':[plan(2)]})
        self.assertEqual(exc.exception.code,'replay_conflict')

    def test_unknown_task_without_pending_still_errors(self):
        respond(self.db,self.path,self.task())
        with self.assertRaises(Error) as exc:
            engine.submit(self.db,{'schema':'novel-ledger-v2.result/1','task_id':'missing','data':{'chapters':[plan(2)]}})
        self.assertEqual(exc.exception.code,'task')

    def _draft_with_events(self):
        respond(self.db,self.path,self.task())                     # plan
        t=self.task();respond(self.db,self.path,t)                 # draft
        t=self.task()                                              # facts
        pack=json.loads(rpath(t, 'input_path').read_text(encoding='utf-8'));prose=pack['payload']['prose']
        data=review(pack['payload']['checks'],prose)
        data.update(summary='登记两条事件。',events=[
            {'op':'state','entity':'hero','key':'mood','before':None,'after':'坚定','evidence':ev(prose)},
            {'op':'resource','asset':'cash','unit':'文','quantity':'10','from':'hero','to':None,
             'from_before':'100','reason':'购买灯油','evidence':ev(prose)}])
        engine.submit(self.db,result(t,data))
        return t,prose

    def test_repair_patch_span_form_auto_converted(self):
        t,prose=self._draft_with_events()
        t=self.task()                                              # reader
        bad=review(engine.READER_CHECKS,prose);bad['verdict']='fix';bad['checks'][0]['result']='fix'
        bad['first_read']={'desire':'目标','obstacle':'阻力','change':'变化','read_on_reason':'悬念'}
        bad['findings']=[{'severity':'quality','impact':'平铺直叙','goal':'加强动作','evidence':ev(prose)}]
        engine.submit(self.db,result(t,bad))
        t=self.task();self.assertEqual(t['stage'],'repair')
        pack=json.loads(rpath(t, 'input_path').read_text(encoding='utf-8'));payload=pack['payload']
        base=payload['base_hash'];full=get(self.db,'candidate_text:'+base)
        start=full.index('收据');end=start+len('收据')
        engine.submit(self.db,result(t,{'base_hash':base,'repair_note':'替换关键道具词',
                                        'patches':[{'start':start,'end':end,'text':'密信'}]}))
        t=self.task();self.assertEqual(t['stage'],'facts')
        self.assertIn('密信',json.loads(rpath(t, 'input_path').read_text(encoding='utf-8'))['payload']['prose'])

    def test_repair_patch_invalid_span_not_converted(self):
        t,prose=self._draft_with_events()
        t=self.task()
        bad=review(engine.READER_CHECKS,prose);bad['verdict']='fix';bad['checks'][0]['result']='fix'
        bad['first_read']={'desire':'目标','obstacle':'阻力','change':'变化','read_on_reason':'悬念'}
        bad['findings']=[{'severity':'quality','impact':'平铺直叙','goal':'加强动作','evidence':ev(prose)}]
        engine.submit(self.db,result(t,bad))
        t=self.task();pack=json.loads(rpath(t, 'input_path').read_text(encoding='utf-8'));payload=pack['payload']
        with self.assertRaises(Error) as exc:
            engine.submit(self.db,result(t,{'base_hash':payload['base_hash'],'repair_note':'越界',
                                            'patches':[{'start':0,'end':10**9,'text':'越界'}]}))
        self.assertEqual(exc.exception.code,'patch')

    def _reach_post_repair_facts(self):
        t,prose=self._draft_with_events()
        t=self.task()
        bad=review(engine.READER_CHECKS,prose);bad['verdict']='fix';bad['checks'][0]['result']='fix'
        bad['first_read']={'desire':'目标','obstacle':'阻力','change':'变化','read_on_reason':'悬念'}
        bad['findings']=[{'severity':'quality','impact':'平铺直叙','goal':'加强动作','evidence':ev(prose)}]
        engine.submit(self.db,result(t,bad))
        t=self.task();pack=json.loads(rpath(t, 'input_path').read_text(encoding='utf-8'));payload=pack['payload']
        full=get(self.db,'candidate_text:'+payload['base_hash'])
        engine.submit(self.db,result(t,{'prose':full+'宋禾低声补了一句。','repair_note':'补一个动作'}))
        t=self.task();self.assertEqual(t['stage'],'facts')
        return t,json.loads(rpath(t, 'input_path').read_text(encoding='utf-8')),full

    def test_post_repair_auto_carry_needs_no_dispositions(self):
        t,work,full=self._reach_post_repair_facts()
        prior=work['payload']['prior_events']
        self.assertEqual(len(prior),2);self.assertEqual(work['payload']['prior_events_source'],'repair')
        self.assertEqual(work['payload']['auto_carry'],[0,1])
        self.assertIn('prior_dispositions',work['payload']['prior_events_note'])
        data=review(work['payload']['checks'],work['payload']['prose'])
        data.update(events=[],summary='引擎自动续算，无新增事件。')
        engine.submit(self.db,result(t,data))
        t=self.task();self.assertEqual(t['stage'],'reader')
        respond(self.db,self.path,t)
        state=replay(self.db,'main')
        self.assertEqual(state['facts']['hero:mood'],'坚定')
        self.assertEqual(state['resources']['["hero","cash","文"]'],'90')

    def test_post_repair_override_drop_honored(self):
        t,work,full=self._reach_post_repair_facts()
        data=review(work['payload']['checks'],work['payload']['prose'])
        data.update(events=[],summary='声明丢弃资源流。',
                    prior_dispositions=[{'index':1,'disposition':'dropped','reason':'补句后该购买不再发生'}])
        engine.submit(self.db,result(t,data))
        t=self.task();self.assertEqual(t['stage'],'reader')
        respond(self.db,self.path,t)
        state=replay(self.db,'main')
        self.assertEqual(state['facts']['hero:mood'],'坚定')
        self.assertEqual(state['resources']['["hero","cash","文"]'],'100')

    def test_post_repair_duplicate_of_carried_rejected(self):
        t,work,full=self._reach_post_repair_facts()
        prior=work['payload']['prior_events']
        data=review(work['payload']['checks'],work['payload']['prose'])
        data.update(events=[prior[0]],summary='重复提交已续算事件。')
        with self.assertRaises(Error) as exc: engine.submit(self.db,result(t,data))
        self.assertEqual(exc.exception.code,'event_accounting')

    def _reach_patched_facts(self):
        """Repair that rewrites a word: every prior evidence quote breaks."""
        t,prose=self._draft_with_events()
        t=self.task()
        bad=review(engine.READER_CHECKS,prose);bad['verdict']='fix';bad['checks'][0]['result']='fix'
        bad['first_read']={'desire':'目标','obstacle':'阻力','change':'变化','read_on_reason':'悬念'}
        bad['findings']=[{'severity':'quality','impact':'道具不实','goal':'换核心道具','evidence':ev(prose)}]
        engine.submit(self.db,result(t,bad))
        t=self.task();payload=json.loads(rpath(t, 'input_path').read_text(encoding='utf-8'))['payload']
        base=get(self.db,'candidate_text:'+payload['base_hash'])
        start=base.index('收据')
        engine.submit(self.db,result(t,{'base_hash':payload['base_hash'],'repair_note':'换核心道具',
                                        'patches':[{'start':start,'old':'收据','new':'密信'}]}))
        t=self.task();self.assertEqual(t['stage'],'facts')
        return t,json.loads(rpath(t, 'input_path').read_text(encoding='utf-8'))

    def test_post_repair_broken_evidence_requires_disposition(self):
        t,work=self._reach_patched_facts()
        self.assertEqual(work['payload']['auto_carry'],[])
        data=review(work['payload']['checks'],work['payload']['prose'])
        data.update(events=[],summary='漏交代。')
        with self.assertRaises(Error) as exc: engine.submit(self.db,result(t,data))
        self.assertEqual(exc.exception.code,'event_accounting')
        self.assertEqual(self.task()['task_id'],t['task_id'])
        data.update(prior_dispositions=[{'index':0,'disposition':'dropped','reason':'道具改为密信,原心情状态前提消失'},
                                        {'index':1,'disposition':'dropped','reason':'购买灯油的资源流不再发生'}])
        engine.submit(self.db,result(t,data))
        self.assertEqual(self.task()['stage'],'reader')

    def test_post_repair_kept_on_broken_requires_event(self):
        t,work=self._reach_patched_facts()
        data=review(work['payload']['checks'],work['payload']['prose'])
        data.update(events=[],summary='声称保留却未重报。',
                    prior_dispositions=[{'index':0,'disposition':'kept'},{'index':1,'disposition':'dropped','reason':'资源流移除'}])
        with self.assertRaises(Error) as exc: engine.submit(self.db,result(t,data))
        self.assertEqual(exc.exception.code,'event_accounting')

    def test_post_repair_dropped_without_reason_rejected(self):
        t,work,full=self._reach_post_repair_facts()
        prose=work['payload']['prose']
        data=review(work['payload']['checks'],prose)
        data.update(events=[],summary='无理由丢弃。',
                    prior_dispositions=[{'index':0,'disposition':'dropped'},{'index':1,'disposition':'dropped'}])
        with self.assertRaises(Error) as exc: engine.submit(self.db,result(t,data))
        self.assertEqual(exc.exception.code,'event_accounting')

    def test_revision_branch_inherits_ancestor_prior_events(self):
        t,prose=self._draft_with_events()
        respond(self.db,self.path,self.task())                     # reader pass -> chapter 1 committed
        self.assertEqual(head(self.db,'main'),1)
        engine.begin_revision(self.db,1,'复核传播')
        t=self.task();self.assertEqual(t['stage'],'draft')
        work=json.loads(rpath(t, 'input_path').read_text(encoding='utf-8'));payload=work['payload']
        from novel_core.patches import source
        base=source(self.db,{'stage':'draft','payload':payload})
        engine.submit(self.db,result(t,{'base_hash':payload['base_hash'],'repair_note':'微调措辞',
                                        'patches':[{'start':0,'old':prose[:4],'new':prose[:3]+'二'}]}))
        t=self.task();self.assertEqual(t['stage'],'facts')
        work=json.loads(rpath(t, 'input_path').read_text(encoding='utf-8'))
        self.assertEqual(work['payload']['prior_events_source'],'revision')
        self.assertEqual(len(work['payload']['prior_events']),2)

    def test_synthesis_memory_text_form_accepted(self):
        while True:
            t=self.task()
            if t.get('stage')=='await_author': engine.approve(self.db);continue
            if t.get('stage')=='revision_promoted':continue
            if not t.get('task_id'):break
            pack=json.loads(rpath(t, 'input_path').read_text(encoding='utf-8'))
            if t['stage'].endswith('_review') and pack['payload'].get('scope')=='book':
                source=db_prose=self.db.execute('SELECT prose FROM chapters WHERE branch=? AND number=?',
                                                (t['branch'],pack['payload']['first'])).fetchone()[0]
                data=review(pack['payload']['checks'],source,pack['payload']['first'])
                data['memory']='全书主线兑现，人物承担代价。'
                engine.submit(self.db,result(t,data));break
            respond(self.db,self.path,t,ending=True)
        row=self.db.execute("SELECT data FROM memories WHERE scope='book'").fetchone()
        stored=json.loads(row[0])
        self.assertEqual(stored['text'],'全书主线兑现，人物承担代价。');self.assertEqual(stored['sources'],[])
