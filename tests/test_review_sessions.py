"""Continuous independent review keeps separate judgments and stops at role boundaries."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from support import *
from novel_core import engine, reviewrun, taskio, contextflow
from novel_core.store import Error,head,digest,branch_hash
from novel_core.ledger import replay
import novel


class ReviewSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        c=contract();c['review_chunk']=3;c['volumes'].append({'id':'v2','purpose':'继续展开'})
        initialize(self.path,c);self.db=connect(self.path)

    def tearDown(self):self.db.close();self.tmp.cleanup()
    def cli(self,*args):return novel.dispatch(novel.parser().parse_args([args[0],'--project',str(self.path),*args[1:]]))

    def prepare(self,count=6,revision=True,ending=True,start=2):
        # Canonical sources pass the regular chapter pipeline before long review.
        while head(self.db,'main')<count:
            t=engine.next_task(self.db,self.path)
            if t.get('stage')=='await_author':engine.approve(self.db)
            else:respond(self.db,self.path,t)
        if not get(self.db,'pilot_approved'):engine.approve(self.db)
        branch='main'
        if revision:
            branch=engine.begin_revision(self.db,start,'核对人物选择与第一卷展开')['branch']
            # Branch fixture carries exactly the same accepted suffix: no model rewrite.
            self.db.execute('INSERT INTO chapters SELECT ?,number,prose,data,hash FROM chapters WHERE branch=? AND number>=?',(branch,'main',start))
        if ending:
            p=json.loads(self.db.execute('SELECT data FROM plans WHERE branch=? AND number=?',(branch,count)).fetchone()[0]);p['volume_end']=True
            self.db.execute('UPDATE plans SET data=? WHERE branch=? AND number=?',(dump(p),branch,count))
        return branch

    def receipt(self,t):
        w=json.loads((self.path/t['input_path']).read_text());p=w['payload']
        if t['stage'] in ('context_scan','context_reduce'):return result(t,{'cannot_compact':True,'reason':'工程夹具逐字保留全部来源。'})
        n=p['first'];prose=self.db.execute('SELECT prose FROM chapters WHERE branch=? AND number=?',(t['branch'],n)).fetchone()[0]
        data=review(p['checks'],prose,n)
        if not p['scope'].endswith('_chunk'):data['memory']={'text':'工程夹具核对跨块因果与范围目标。','sources':[ev(prose,n)]}
        return result(t,data)

    def submit(self,t,receipt=None):
        f=self.path/t['output_path'];f.write_text(dump(receipt or self.receipt(t)))
        return self.cli('submit','--result',str(f),'--brief','--continue-review')

    def stream(self,t):
        trace=[]
        for _ in range(80):
            w=json.loads((self.path/t['input_path']).read_text());trace.append((t,w.get('payload',{}).get('scope')))
            reply=self.submit(t)
            if reply['action']!='continue_review':return reply,trace
            t=reply['task']
        self.fail('review did not finish')

    def test_volume_chunks_and_revision_judgments_run_in_one_session_without_promoting(self):
        b=self.prepare();before=(head(self.db,b),branch_hash(self.db,b),digest(replay(self.db,b)))
        t=self.cli('advance')['task'];self.assertIn('--continue-review',t['commands']['submit'])
        with patch('novel._auto_render_html',side_effect=AssertionError('unchanged prose rendered')):
            reply,trace=self.stream(t)
        self.assertEqual([s for _,s in trace],['volume_chunk','volume_chunk','volume','revision_chunk','revision_chunk','revision'])
        self.assertEqual(len({x['review_session']['id'] for x,_ in trace}),1)
        self.assertEqual(reply['action'],'review_done');self.assertEqual(get(self.db,'active_branch'),'main')
        self.assertEqual(before,(head(self.db,b),branch_hash(self.db,b),digest(replay(self.db,b))))
        self.assertFalse(self.db.execute("SELECT 1 FROM tasks WHERE state='pending'").fetchone())
        self.assertEqual(self.cli('advance','--after',reply['task_id'])['action'],'revision_promoted')

    def test_normal_volume_review_stops_before_creating_writer_batch(self):
        self.prepare(revision=False);put(self.db,'chapter_batch_size',5)
        for n in (7,8):self.db.execute('INSERT INTO plans VALUES (?,?,?)',('main',n,dump(plan(n))))
        reply,trace=self.stream(self.cli('advance')['task'])
        self.assertEqual(reply['action'],'review_done')
        self.assertIsNone(get(self.db,'chapter_batch:main'))
        self.assertFalse(self.db.execute("SELECT 1 FROM tasks WHERE state='pending'").fetchone())
        self.assertEqual(self.cli('advance','--after',reply['task_id'])['task']['stage'],'draft')

    def test_replay_returns_same_pending_and_obsolete_callback_cannot_advance(self):
        self.prepare();t=self.cli('advance')['task'];receipt=self.receipt(t)
        a=self.submit(t,receipt);b=self.submit(t,receipt)
        self.assertEqual(a['task']['task_id'],b['task']['task_id'])
        self.assertEqual(a['task']['input_hash'],b['task']['input_hash'])
        current=a['task'];self.submit(current)
        count=self.db.execute('SELECT count(*) FROM tasks').fetchone()[0]
        old=self.submit(t,receipt)
        self.assertEqual(old['action'],'resync')
        self.assertEqual(self.db.execute('SELECT count(*) FROM tasks').fetchone()[0],count)

    def test_pause_resume_keeps_same_pending_and_new_worker_must_read_sources(self):
        self.prepare();t=self.cli('advance')['task'];receipt=self.receipt(t)
        engine.submit(self.db,receipt);row=self.db.execute('SELECT * FROM tasks WHERE id=?',(t['task_id'],)).fetchone()
        self.cli('pause','--reason','作者暂停')
        self.assertEqual(reviewrun.continue_session(self.db,self.path,row)['action'],'paused')
        self.cli('resume');following=reviewrun.continue_session(self.db,self.path,row)['task']
        self.cli('pause','--reason','作者暂停');self.cli('resume')
        restored=self.cli('advance')['task']
        self.assertEqual(restored['task_id'],following['task_id'])
        self.assertEqual(restored['input_hash'],following['input_hash'])
        self.assertIn('新 Worker',restored['worker_prompt'])

    def test_dependency_change_rejects_submission_then_controller_refreshes_session(self):
        self.prepare();t=self.cli('advance')['task'];r=self.receipt(t)
        c=get(self.db,'contract');c['world_rules'].append('不得借复核省略人物代价');put(self.db,'contract',c)
        with self.assertRaises(Error) as caught:self.submit(t,r)
        self.assertEqual(caught.exception.code,'review_session_stale')
        fresh=self.cli('advance')['task']
        self.assertNotEqual(fresh['task_id'],t['task_id'])
        self.assertNotEqual(fresh['review_session']['id'],t['review_session']['id'])

    def test_change_outside_chunk_also_refreshes_pending_not_trapping_it(self):
        self.prepare();t=self.cli('advance')['task']
        p=json.loads(self.db.execute('SELECT data FROM plans WHERE branch=? AND number=6',(t['branch'],)).fetchone()[0])
        p['change']='工程模拟：后文约定发生变化'
        self.db.execute('UPDATE plans SET data=? WHERE branch=? AND number=6',(dump(p),t['branch']))
        fresh=self.cli('advance')['task']
        self.assertNotEqual(fresh['task_id'],t['task_id'])
        self.assertNotEqual(fresh['review_session'],t['review_session'])

    def test_fix_returns_to_controller_and_never_dispatches_repair(self):
        self.prepare();t=self.cli('advance')['task'];r=self.receipt(t);d=r['data']
        d['verdict']='fix';d['checks'][0]['result']='fix'
        e=d['checks'][0]['evidence'][0]
        d['findings']=[{'severity':'quality','impact':'跨章选择缺乏承接','goal':'补充前因与代价','evidence':e}]
        reply=self.submit(t,r)
        self.assertEqual(reply['action'],'resync')
        self.assertNotEqual(get(self.db,'work_branch'),t['branch'])
        self.assertFalse(self.db.execute("SELECT 1 FROM tasks WHERE state='pending'").fetchone())

    def test_each_scope_requires_its_own_real_checks(self):
        self.prepare();t=self.cli('advance')['task']
        bad=self.receipt(t);bad['data']['checks']=[]
        with self.assertRaises(Error):self.submit(t,bad)
        self.assertEqual(self.db.execute('SELECT state FROM tasks WHERE id=?',(t['task_id'],)).fetchone()[0],'pending')
        self.assertFalse(self.db.execute('SELECT 1 FROM reviews WHERE branch=?',(t['branch'],)).fetchone())

    def test_legacy_frozen_review_keeps_hash_and_does_not_adopt_streaming(self):
        self.prepare()
        with patch('novel_core.reviewrun.session',return_value=None):t=self.cli('advance')['task']
        original=self.db.execute('SELECT input FROM tasks WHERE id=?',(t['task_id'],)).fetchone()[0]
        restored=self.cli('advance')['task']
        self.assertEqual(restored['input_hash'],t['input_hash']);self.assertNotIn('review_session',restored)
        self.assertNotIn('--continue-review',restored['commands']['submit'])
        with self.assertRaises(Error):self.submit(restored)
        self.assertEqual(self.db.execute('SELECT input FROM tasks WHERE id=?',(t['task_id'],)).fetchone()[0],original)
        r=self.receipt(restored);engine.submit(self.db,r)
        self.assertIn('review_session',self.cli('advance')['task'])

    def test_flags_cannot_be_combined_or_used_by_writer(self):
        t=self.cli('advance')['task'];f=self.path/t['output_path'];f.write_text(dump(result(t,{'chapters':[plan(1)]})))
        with self.assertRaises(Error):self.cli('submit','--result',str(f),'--continue-review')
        self.prepare();t=self.cli('advance')['task'];f=self.path/t['output_path'];f.write_text(dump(self.receipt(t)))
        with self.assertRaises(Error):self.cli('submit','--result',str(f),'--continue-review','--continue-preparation')

    def test_shared_materials_are_exact_and_missing_files_are_rebuilt(self):
        self.prepare();t=self.cli('advance')['task'];v=json.loads((self.path/t['input_path']).read_text())
        row=self.db.execute('SELECT input FROM tasks WHERE id=?',(t['task_id'],)).fetchone();full=json.loads(row[0])
        self.assertIn('contract',v['shared_materials'])
        for k,ref in v['shared_materials'].items():
            self.assertEqual(json.loads((self.path/ref['path']).read_text()),full[k])
            self.assertTrue(digest(full[k]).startswith(ref['hash']))
        ref=v['shared_materials']['contract'];(self.path/ref['path']).write_bytes(b'\xff')
        same=self.cli('advance')['task']
        self.assertEqual(same['input_hash'],t['input_hash'])
        self.assertEqual(json.loads((self.path/ref['path']).read_text()),full['contract'])

    def test_long_material_preparation_stays_inside_review_and_preserves_sources(self):
        from novel_core.ledger import append_events
        b=self.prepare();put(self.db,'preparation_policy','semantic');put(self.db,'context_mode','bounded')
        prose=self.db.execute('SELECT prose FROM chapters WHERE branch=? AND number=6',(b,)).fetchone()[0]
        events=[{'op':'knowledge','entity':'hero','topic':'topic'+str(i),'before':None,
                 'claim':'工程夹具中的完整认知'+str(i),'stance':'knows','source':'本章凭证','evidence':ev(prose,6)} for i in range(120)]
        state=replay(self.db,b);append_events(self.db,b,6,prose,events,state)
        data=json.loads(self.db.execute('SELECT data FROM chapters WHERE branch=? AND number=6',(b,)).fetchone()[0])
        data['events']=events;self.db.execute('UPDATE chapters SET data=? WHERE branch=? AND number=6',(dump(data),b))
        t=self.cli('advance')['task'];token=t['review_session']
        reply=self.submit(t,result(t,{'context_more':[{'entity':'hero'}],'reason':'核对周行此前全部认知来源和跨章选择。'}))
        self.assertEqual(reply['action'],'continue_review');t=reply['task']
        self.assertEqual(t['stage'],'context_scan')
        reply,trace=self.stream(t)
        self.assertTrue(any(x['stage']=='context_scan' for x,_ in trace))
        self.assertTrue(any(scope=='volume' for _,scope in trace))
        self.assertTrue(any(scope=='revision' for _,scope in trace))
        self.assertTrue(all(x['review_session']==token for x,_ in trace))
        self.assertEqual(reply['action'],'review_done')
        self.assertEqual(engine.audit(self.db,b)['ok'],True)

    def test_final_book_review_returns_before_completion(self):
        c=get(self.db,'contract');c['volumes']=c['volumes'][:1];put(self.db,'contract',c)
        self.prepare(revision=False)
        p=json.loads(self.db.execute("SELECT data FROM plans WHERE branch='main' AND number=6").fetchone()[0])
        p['book_end']=True;self.db.execute("UPDATE plans SET data=? WHERE branch='main' AND number=6",(dump(p),))
        reply,trace=self.stream(self.cli('advance')['task'])
        self.assertTrue(any(scope=='book' for _,scope in trace))
        self.assertEqual(reply['action'],'review_done');self.assertEqual(get(self.db,'status'),'ready')
        self.assertFalse(self.db.execute("SELECT 1 FROM tasks WHERE state='pending'").fetchone())
        self.assertEqual(self.cli('advance','--after',reply['task_id'])['action'],'completed')

    def test_author_range_change_stops_old_callback_without_dispatch(self):
        self.prepare();t=self.cli('advance')['task'];engine.submit(self.db,self.receipt(t))
        row=self.db.execute('SELECT * FROM tasks WHERE id=?',(t['task_id'],)).fetchone()
        put(self.db,'batch_through',6)
        reply=reviewrun.continue_session(self.db,self.path,row)
        self.assertEqual(reply['action'],'review_done')
        self.assertFalse(self.db.execute("SELECT 1 FROM tasks WHERE state='pending'").fetchone())
