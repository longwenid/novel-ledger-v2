"""Batch sessions keep provisional state out of canon and preserve review gates."""
import copy
import json
import shlex
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from support import contract, initialize, connect, get, put, dump, ev, plan, result, respond, combined_review, fixture_first_read
from novel_core import engine, batchflow, reviewflow
from novel_core.ledger import replay, resource_key
from novel_core.store import digest, Error, head
import novel


class ChapterBatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        c=contract();c['chapter_batch_size']=5;initialize(self.path,c);self.db=connect(self.path)
        for _ in range(3):respond(self.db,self.path,engine.next_task(self.db,self.path))
        engine.approve(self.db)
        engine.validate_plan(self.db,'main',2,{'chapters':[plan(n) for n in range(2,11)]})
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def cli(self,*args):return novel.dispatch(novel.parser().parse_args([args[0],'--project',str(self.path),*args[1:]]))
    def work(self,t):return json.loads((self.path/t['input_path']).read_text())
    def write(self,t,events=None,prose=None):
        n=t['chapter'];text=prose or f'第{n}章，周行把收据交给宋禾。她在凭证上写下第{n}次交接的去向。'
        before=batchflow.candidate_state(self.db,'main',n-1)['facts'].get('hero:route')
        events=events if events is not None else [{'op':'state','entity':'hero','key':'route','before':before,'after':'step'+str(n),'evidence':ev(text,n)}]
        return {'prose':text,'summary':'核验凭证后改变去向。','self_check':'核对凭证、去向变化和上一章的交接前态。',
                'candidate_events':events,'repair_note':'按证据修复承接','design_change':'由核实凭证推动行动'}
    def submit(self,t,data):
        path=self.path/t['output_path'];path.write_text(dump(result(t,data)))
        return self.cli('submit','--result',str(path),'--brief','--continue-chapter-batch')
    def writer_batch(self,t=None):
        t=t or self.cli('advance')['task'];tasks=[]
        with patch('novel._auto_render_html',side_effect=AssertionError('writer rendered canon')):
            for _ in range(12):
                tasks.append(t);out=self.submit(t,self.write(t))
                if out['action']!='continue_chapter_batch':return out,tasks
                t=out['task']
        self.fail('writing did not stop at batch boundary')
    def seal(self,t):
        receipt={'task_id':t['task_id'],'input_hash':t['input_hash'],'prose_hash':t['first_read_hash'],'first_read':fixture_first_read(),
            'first_reads':[{'chapter':r['chapter'],'prose_hash':r['prose_hash'],'first_read':fixture_first_read()} for r in t['first_read_files']]}
        return reviewflow.seal(self.db,self.path,t['task_id'],receipt)['task']
    def review(self,t,events=None):
        work=self.work(t);draft=engine.load_draft(self.db,'main',t['chapter'])
        return combined_review(work,draft['prose'],t['chapter'],events=draft['candidate_events'] if events is None else events)

    def test_first_read_seal_derives_top_level_from_batch_entry(self):
        out,_=self.writer_batch();t=self.cli('advance','--after',out['task_id'])['task']
        delivered=json.loads((self.path/t['first_read_template_path']).read_text(encoding='utf-8'))
        self.assertNotIn('first_read',delivered);self.assertIn('first_reads',delivered)
        reads=[{'chapter':r['chapter'],'prose_hash':r['prose_hash'],'first_read':fixture_first_read()} for r in t['first_read_files']]
        divergent={'task_id':t['task_id'],'input_hash':t['input_hash'],'prose_hash':t['first_read_hash'],
                   'first_reads':reads,'first_read':{**fixture_first_read(),'change':'补读后另写'}}
        with self.assertRaises(Error) as caught:reviewflow.seal(self.db,self.path,t['task_id'],divergent)
        self.assertEqual(caught.exception.code,'first_read_conflict')
        self.assertEqual(caught.exception.details['chapter'],t['chapter'])
        self.assertIn('entry',caught.exception.details)
        self.assertIsNone(reviewflow.record(self.db,t['task_id']))
        receipt={'task_id':t['task_id'],'input_hash':t['input_hash'],'prose_hash':t['first_read_hash'],'first_reads':reads}
        reviewflow.seal(self.db,self.path,t['task_id'],receipt)
        self.assertEqual(reviewflow.record(self.db,t['task_id'])['receipt']['first_read'],fixture_first_read())

    def test_five_drafts_then_one_reviewer_session_without_canonical_writes(self):
        before=digest(replay(self.db,'main'));out,tasks=self.writer_batch()
        self.assertEqual([t['chapter'] for t in tasks],[2,3,4,5,6]);self.assertEqual(out['action'],'chapter_batch_done')
        self.assertEqual(head(self.db,'main'),1);self.assertEqual(digest(replay(self.db,'main')),before)
        self.assertEqual(len({t['chapter_session']['batch'] for t in tasks}),1)
        self.assertEqual(self.work(tasks[-1])['state']['facts']['hero:route'],'step5')
        self.assertIn('--continue-chapter-batch',shlex.split(tasks[0]['commands']['submit']))
        self.assertFalse(self.db.execute("SELECT 1 FROM tasks WHERE state='pending'").fetchone())
        reviewer=self.cli('advance','--after',out['task_id'])['task'];self.assertEqual(reviewer['chapter_session']['role'],'reviewer')
        self.assertEqual(len(reviewer['first_read_files']),5);self.assertFalse((self.path/reviewer['input_path']).exists())
        reviewer=self.seal(reviewer);batch=reviewer['chapter_session']['batch']
        for n in range(2,7):
            self.assertEqual(reviewer['chapter'],n);self.assertEqual(reviewer['chapter_session']['batch'],batch)
            self.assertNotIn('candidate_ledger',self.work(reviewer));self.assertNotIn('self_check',dump(self.work(reviewer)))
            report=self.submit(reviewer,self.review(reviewer))
            self.assertEqual(head(self.db,'main'),n)
            if n<6:
                reviewer=report['task'];self.assertEqual(reviewer['delivery_phase'],'review')
                self.assertIsNotNone(reviewflow.record(self.db,reviewer['task_id']))
        self.assertEqual(report['action'],'chapter_batch_done');self.assertIsNone(batchflow.active(self.db))
        self.assertEqual(replay(self.db,'main')['facts']['hero:route'],'step6')
        self.assertTrue(engine.audit(self.db)['ok'])

    def test_first_read_requires_all_five_and_cannot_be_reinterpreted(self):
        out,_=self.writer_batch();t=self.cli('advance','--after',out['task_id'])['task']
        receipt={'task_id':t['task_id'],'input_hash':t['input_hash'],'prose_hash':t['first_read_hash'],'first_read':fixture_first_read()}
        with self.assertRaises(Error):reviewflow.seal(self.db,self.path,t['task_id'],receipt)
        self.assertIsNone(reviewflow.record(self.db,t['task_id']))
        with self.assertRaises(Error):self.cli('recall','--task',t['task_id'],'--contract')
        t=self.seal(t);r=self.review(t);r['reader']['first_read']['change']='补读后改写'
        with self.assertRaises(Error):self.submit(t,r)
        self.assertEqual(head(self.db,'main'),1)

    def _write_batch(self,events_for):
        t=self.cli('advance')['task'];tasks=[]
        for _ in range(12):
            tasks.append(t);n=t['chapter']
            out=self.submit(t,self.write(t,events=events_for(n,t)))
            if out['action']!='continue_chapter_batch':return out,tasks
            t=out['task']
        self.fail('writing did not stop at batch boundary')

    def test_restructured_actual_events_rebind_suffix_without_new_batch(self):
        prose2=f'第2章，周行把收据交给宋禾。她在凭证上写下第2次交接的去向。'
        prose3=f'第3章，周行把收据交给宋禾。她在凭证上写下第3次交接的去向。'
        def events_for(n,_t):
            if n==2:return [{'op':'state','entity':'hero','key':'route','before':None,'after':'step2','evidence':ev(prose2,2)},
                            {'op':'knowledge','entity':'hero','topic':'去向','before':None,'claim':'宋禾记录了去向。',
                             'stance':'knows','source':'凭证','evidence':ev(prose2,2)}]
            if n==3:return [{'op':'state','entity':'hero','key':'route','before':'step2','after':'step3','evidence':ev(prose3,3)},
                            {'op':'knowledge','entity':'hero','topic':'去向','before':'宋禾记录了去向。','claim':'去向核对无误。',
                             'stance':'knows','source':'核对','evidence':ev(prose3,3)}]
            return None
        out,tasks=self._write_batch(events_for);batch=tasks[0]['chapter_session']['batch']
        t=self.seal(self.cli('advance','--after',out['task_id'])['task'])
        restructured=[
            {'op':'state','entity':'hero','key':'route','before':None,'after':'第二步','evidence':ev(prose2,2)},
            {'op':'knowledge','entity':'hero','topic':'交接认知','before':None,'claim':'宋禾按凭证记下两次去向并开始核对。',
             'stance':'knows','source':'凭证','evidence':ev(prose2,2)}]
        report=self.submit(t,self.review(t,events=restructured))
        self.assertEqual(report['action'],'continue_chapter_batch');self.assertEqual(head(self.db,'main'),2)
        self.assertEqual(report['task']['chapter_session']['batch'],batch)
        suffix=engine.load_draft(self.db,'main',3)
        self.assertEqual(suffix['stage'],'chapter_review')
        events={e['op']:e for e in suffix['candidate_events']}
        self.assertEqual(events['state']['before'],'第二步')
        self.assertIsNone(events['knowledge']['before'])
        self.assertEqual(suffix['candidate_rebound']['base_chapter'],2)
        self.assertTrue(self.db.execute("SELECT 1 FROM logs WHERE action='chapter_batch_rebound'").fetchone())
        following=self.submit(report['task'],self.review(report['task']))
        self.assertEqual(following['action'],'continue_chapter_batch');self.assertEqual(head(self.db,'main'),3)
        self.assertEqual(following['task']['chapter_session']['batch'],batch)
        self.assertTrue(engine.audit(self.db)['ok'])

    def test_hard_precondition_break_invalidates_only_the_broken_suffix(self):
        prose2=f'第2章，周行把收据交给宋禾。她在凭证上写下第2次交接的去向。'
        prose3=f'第3章，周行把收据交给宋禾。她在凭证上写下第3次交接的去向。'
        def events_for(n,_t):
            if n==2:return [{'op':'resource','asset':'cash','unit':'文','from':'hero','to':None,
                             'from_before':'100','quantity':'40','reason':'交付凭证的费用','evidence':ev(prose2,2)}]
            if n==3:return [{'op':'resource','asset':'cash','unit':'文','from':'hero','to':None,
                             'from_before':'60','quantity':'10','reason':'继续核对的开销','evidence':ev(prose3,3)}]
            return None
        out,tasks=self._write_batch(events_for)
        t=self.seal(self.cli('advance','--after',out['task_id'])['task'])
        settled_more=[{'op':'resource','asset':'cash','unit':'文','from':'hero','to':None,
                       'from_before':'100','quantity':'70','reason':'交付凭证的费用远超预期','evidence':ev(prose2,2)}]
        report=self.submit(t,self.review(t,events=settled_more))
        self.assertEqual(report['action'],'chapter_batch_done');self.assertEqual(head(self.db,'main'),2)
        self.assertEqual(replay(self.db,'main')['resources'][resource_key('hero','cash','文')],'30')
        suffix=engine.load_draft(self.db,'main',3)
        self.assertEqual(suffix['stage'],'draft');self.assertNotIn('candidate_events',suffix)
        next_task=self.cli('advance','--after',report['task_id'])['task']
        self.assertEqual(next_task['chapter'],3);self.assertEqual(next_task['chapter_session']['role'],'writer')
        self.assertNotEqual(next_task['chapter_session']['batch'],tasks[0]['chapter_session']['batch'])
        self.assertEqual(self.work(next_task)['state']['resources'][resource_key('hero','cash','文')],'30')

    def test_review_fix_keeps_attempt_limit_and_invalidates_unreviewed_suffix(self):
        out,_=self.writer_batch();t=self.seal(self.cli('advance','--after',out['task_id'])['task']);r=self.review(t)
        prose=engine.load_draft(self.db,'main',2)['prose'];finding={'severity':'quality','impact':'承接没有明确代价','goal':'写清交接代价','evidence':ev(prose,2)}
        r['reader']['verdict']='fix';r['reader']['checks'][0]['result']='fix';r['reader']['findings']=[finding]
        report=self.submit(t,r);self.assertEqual(report['action'],'chapter_batch_done');self.assertEqual(head(self.db,'main'),1)
        self.assertEqual(engine.load_draft(self.db,'main',2)['attempts'],1)
        self.assertEqual(engine.load_draft(self.db,'main',3)['stage'],'draft')
        repair=self.cli('advance','--after',report['task_id'])['task'];self.assertEqual(repair['stage'],'repair')
        with self.assertRaises(Error):self.submit(repair,{'reuse_original':True})
        self.assertEqual(engine.load_draft(self.db,'main',2)['attempts'],1)

    def test_writer_flag_rejects_legacy_and_missing_self_check(self):
        t=self.cli('advance')['task'];data=self.write(t);data.pop('self_check')
        with self.assertRaises(Error):self.submit(t,data)
        self.assertEqual(head(self.db,'main'),1);self.assertIsNone(engine.load_draft(self.db,'main',2))
        self.assertEqual(self.cli('advance')['task']['task_id'],t['task_id'])

    def test_retry_recovers_one_pending_and_obsolete_reply_does_not_advance(self):
        t=self.cli('advance')['task'];data=self.write(t);a=self.submit(t,data);b=self.submit(t,data)
        self.assertEqual(a['task']['task_id'],b['task']['task_id']);self.assertEqual(a['task']['input_hash'],b['task']['input_hash'])
        self.submit(a['task'],self.write(a['task']));count=self.db.execute('SELECT count(*) FROM tasks').fetchone()[0]
        self.assertEqual(self.submit(t,data)['action'],'resync');self.assertEqual(self.db.execute('SELECT count(*) FROM tasks').fetchone()[0],count)

    def test_pause_cancel_resume_recovers_provisional_state_without_rewriting_prefix(self):
        t=self.cli('advance')['task'];a=self.submit(t,self.write(t));next_id=a['task']['task_id']
        self.cli('pause','--reason','作者暂停');self.assertEqual(self.cli('advance')['action'],'paused')
        self.cli('resume');self.assertEqual(self.cli('advance')['task']['task_id'],next_id)
        self.assertEqual(head(self.db,'main'),1);self.assertEqual(engine.load_draft(self.db,'main',2)['stage'],'chapter_review')
        self.assertEqual(self.cli('recall','--task',next_id,'--state','--entity','hero')['facts']['hero:route'],'step2')
        self.assertNotIn('hero:route',self.cli('recall','--state','--entity','hero')['facts'])
        candidate=self.cli('recall','--task',next_id,'--chapter','2');self.assertTrue(candidate['candidate'])

    def test_author_target_and_volume_boundary_shorten_batches(self):
        t=self.cli('advance','--through','3')['task'];out,tasks=self.writer_batch(t)
        self.assertEqual([t['chapter'] for t in tasks],[2,3]);r=self.seal(self.cli('advance','--after',out['task_id'])['task'])
        a=self.submit(r,self.review(r));a=self.submit(a['task'],self.review(a['task']))
        self.assertEqual(self.cli('advance','--after',a['task_id'])['action'],'target_reached')
        self.assertEqual(head(self.db,'main'),3)

    def test_bad_candidate_events_leave_no_draft_or_progress(self):
        t=self.cli('advance')['task'];data=self.write(t);data['candidate_events'][0]['before']='invented'
        with self.assertRaises(Error):self.submit(t,data)
        self.assertIsNone(engine.load_draft(self.db,'main',2));self.assertEqual(self.cli('advance')['task']['task_id'],t['task_id'])

    def test_cancel_then_replan_retires_batch_before_removing_its_plans(self):
        t=self.cli('advance')['task'];old=batchflow.active(self.db)
        self.cli('pause','--reason','重新规划未来章节','--cancel-task')
        self.cli('replan','--reason','更换未来章节安排')
        self.assertIsNone(batchflow.active(self.db))
        self.assertEqual(get(self.db,'chapter_batch_archive:'+old['id'])['cancelled_by'],'replan')
        fresh=self.cli('advance')['task']
        self.assertEqual(fresh['stage'],'plan');self.assertEqual(fresh['chapter'],2)
        engine.submit(self.db,result(fresh,{'chapters':[plan(n) for n in range(2,5)]}))
        restarted=self.cli('advance','--after',fresh['task_id'])['task']
        self.assertEqual(restarted['stage'],'draft');self.assertEqual(restarted['chapter'],2)
        self.assertNotEqual(restarted['chapter_session']['batch'],old['id'])
        self.assertEqual(self.db.execute('SELECT state FROM tasks WHERE id=?',(t['task_id'],)).fetchone()[0],'cancelled')

    def test_raw_output_retry_and_changed_retry_stay_bound_to_original_task(self):
        t=self.cli('advance')['task'];data=self.write(t,events=[])
        path=self.path/t['output_path'];path.write_text(dump(data))
        first=self.cli('submit','--result',str(path),'--brief','--continue-chapter-batch')
        replayed=self.cli('submit','--result',str(path),'--brief','--continue-chapter-batch')
        self.assertEqual(first['task_id'],replayed['task_id'])
        self.assertEqual(first['task']['task_id'],replayed['task']['task_id'])
        self.assertIsNone(engine.load_draft(self.db,'main',3))
        changed={**data,'prose':data['prose']+'周行重新核对。'};path.write_text(dump(changed))
        with self.assertRaises(Error) as caught:
            self.cli('submit','--result',str(path),'--brief','--continue-chapter-batch')
        self.assertEqual(caught.exception.code,'replay_conflict')
        with self.assertRaises(Error):self.cli('preflight','--result',str(path))
        self.assertEqual(self.cli('advance')['task']['task_id'],first['task']['task_id'])
        self.assertIsNone(engine.load_draft(self.db,'main',3))

    def test_unscoped_raw_retry_keeps_binding_after_restart(self):
        t=self.cli('advance')['task'];data=self.write(t,events=[])
        engine.submit(self.db,data)
        following=self.cli('advance','--after',t['task_id'])['task']
        self.db.close();self.db=connect(self.path)
        self.assertTrue(engine.submit(self.db,data)['idempotent'])
        self.assertEqual(self.cli('advance')['task']['task_id'],following['task_id'])
        self.assertIsNone(engine.load_draft(self.db,'main',3))

    def test_cancelled_output_cannot_be_retargeted_when_raw_content_changes(self):
        t=self.cli('advance')['task'];data=self.write(t,events=[])
        self.cli('pause','--reason','取消旧执行','--cancel-task');self.cli('resume')
        fresh=self.cli('advance')['task']
        path=self.path/t['output_path'];path.write_text(dump(data))
        with self.assertRaises(Error) as caught:self.cli('submit','--result',str(path))
        self.assertEqual(caught.exception.code,'stale_task')
        self.assertEqual(self.cli('advance')['task']['task_id'],fresh['task_id'])
        self.assertIsNone(engine.load_draft(self.db,'main',2))

    def test_changed_plan_cancels_pending_and_rechecks_saved_prose(self):
        t=self.cli('advance')['task'];a=self.submit(t,self.write(t));pending=a['task']
        p=json.loads(self.db.execute('SELECT data FROM plans WHERE number=2').fetchone()[0]);p['goal']='核对新承诺'
        self.db.execute('UPDATE plans SET data=? WHERE number=2',(dump(p),))
        fresh=self.cli('advance')['task'];self.assertEqual(fresh['chapter'],2);self.assertNotEqual(fresh['task_id'],pending['task_id'])
        self.assertEqual(engine.load_draft(self.db,'main',2)['stage'],'draft');self.assertEqual(head(self.db,'main'),1)

    def test_shared_files_are_exact_versioned_and_repaired_on_restore(self):
        t=self.cli('advance')['task'];view=self.work(t)
        self.assertNotIn('contract',view);self.assertIn('contract',view['shared_materials'])
        ref=view['shared_materials']['contract'];path=self.path/ref['path']
        self.assertEqual(json.loads(path.read_text()),get(self.db,'contract'))
        full=digest(json.loads(path.read_text()));self.assertTrue(full.startswith(ref['hash']));self.assertTrue(ref['path'].endswith(full[:16]+'.json'))
        a=self.submit(t,self.write(t));self.assertEqual(self.work(a['task'])['shared_materials']['contract'],ref)
        path.write_bytes(b'\xff\xfe');self.cli('advance')
        self.assertTrue(digest(json.loads(path.read_text())).startswith(ref['hash']))
        self.assertIn('同一 Worker',a['task']['worker_prompt'])

    def test_volume_end_closes_batch_before_next_volume_or_long_review(self):
        p=json.loads(self.db.execute('SELECT data FROM plans WHERE number=4').fetchone()[0]);p['volume_end']=True
        self.db.execute('UPDATE plans SET data=? WHERE number=4',(dump(p),))
        out,tasks=self.writer_batch();self.assertEqual([t['chapter'] for t in tasks],[2,3,4])
        t=self.seal(self.cli('advance','--after',out['task_id'])['task'])
        for _ in range(3):
            r=self.submit(t,self.review(t))
            if r['action']=='continue_chapter_batch':t=r['task']
        next_job=self.cli('advance','--after',r['task_id'])['task']
        self.assertEqual(next_job['stage'],'volume_review');self.assertNotIn('chapter_session',next_job)

    def test_first_read_batch_replay_cannot_change_other_chapter_impression(self):
        out,_=self.writer_batch();t=self.cli('advance','--after',out['task_id'])['task'];sealed=self.seal(t)
        saved=copy.deepcopy(get(self.db,reviewflow.batch_key(t['chapter_session']['batch'])))
        receipt={'task_id':t['task_id'],'input_hash':t['input_hash'],'prose_hash':t['first_read_hash'],
                 'first_read':fixture_first_read(),'first_reads':saved['reads']}
        receipt['first_reads'][-1]['first_read']['change']='读约定后重新解释'
        with self.assertRaises(Error):reviewflow.seal(self.db,self.path,t['task_id'],receipt)
        self.assertEqual(get(self.db,reviewflow.batch_key(t['chapter_session']['batch']))['hash'],saved['hash'])

    def test_unsealed_review_preparation_requires_a_separate_material_worker(self):
        from novel_core import contextflow
        base={'schema':'novel-ledger-v2.pack/1','branch':'main','chapter':2,'stage':'chapter_review',
              'contract':contract(),'payload':{},'chapter_session':{'protocol':batchflow.PROTOCOL,'batch':'fixture','role':'reviewer','first':2,'last':6},
              'state':{'knowledge':{str(i):{'claim':'完整认知'+str(i)} for i in range(64)}}}
        work=contextflow.fit(self.db,base);self.assertEqual(work['stage'],'context_scan')
        self.assertNotIn('chapter_session',work)
        base['batch_first_read_sealed']=True;work=contextflow.fit(self.db,base)
        self.assertEqual(work['chapter_session']['role'],'reviewer')

    def test_review_without_protocol_evidence_cannot_commit_any_candidate(self):
        out,_=self.writer_batch();t=self.seal(self.cli('advance','--after',out['task_id'])['task']);r=self.review(t)
        r['facts']['checks'][0]['evidence']=[{'chapter':2,'start':0,'quote':'并不存在的原文'}]
        with self.assertRaises(Error):self.submit(t,r)
        self.assertEqual(head(self.db,'main'),1);self.assertEqual(self.cli('advance')['task']['task_id'],t['task_id'])

    def test_existing_unmarked_task_finishes_before_new_batch_policy(self):
        put(self.db,'chapter_batch_size',1);old=self.cli('advance')['task'];before=self.db.execute('SELECT input,input_hash FROM tasks WHERE id=?',(old['task_id'],)).fetchone()
        put(self.db,'chapter_batch_size',5);restored=self.cli('advance')['task']
        self.assertEqual(restored['task_id'],old['task_id']);self.assertNotIn('chapter_session',restored)
        self.assertEqual(tuple(before),tuple(self.db.execute('SELECT input,input_hash FROM tasks WHERE id=?',(old['task_id'],)).fetchone()))
        respond(self.db,self.path,restored);r=self.cli('advance')['task'];self.assertNotIn('chapter_session',r)
        respond(self.db,self.path,r);new=self.cli('advance')['task'];self.assertEqual(new['chapter'],3)
        self.assertEqual(new['chapter_session']['role'],'writer')

    def test_repeated_batch_repairs_do_not_reset_the_quality_limit(self):
        task=None
        for attempt in range(1,5):
            if task is None:task=self.cli('advance')['task']
            for _ in range(10):
                text=f'第{task["chapter"]}章，周行把收据交给宋禾。第{attempt}轮核对后，她要求留下交接凭证。'
                out=self.submit(task,self.write(task,prose=text))
                if out['action']!='continue_chapter_batch':break
                task=out['task']
            t=self.seal(self.cli('advance','--after',out['task_id'])['task']);r=self.review(t)
            prose=engine.load_draft(self.db,'main',2)['prose']
            r['facts']['verdict']='fix';r['facts']['checks'][0]['result']='fix'
            r['facts']['findings']=[{'severity':'quality','impact':'凭证来源仍未写清','goal':'明确凭证来源','evidence':ev(prose,2)}]
            reply=self.submit(t,r);self.assertEqual(engine.load_draft(self.db,'main',2)['attempts'],attempt)
            self.assertEqual(head(self.db,'main'),1)
            if attempt==4:
                self.assertEqual(reply['action'],'paused');self.assertEqual(get(self.db,'pause_reason')['code'],'quality_exhausted')
            else:
                task=self.cli('advance','--after',reply['task_id'])['task']
                self.assertEqual(task['stage'],'repair' if attempt<=2 else 'redesign')

    def test_second_batch_stops_at_ten_chapter_review_boundary(self):
        for expected in ([2,3,4,5,6],[7,8,9,10]):
            out,tasks=self.writer_batch();self.assertEqual([t['chapter'] for t in tasks],expected)
            t=self.seal(self.cli('advance','--after',out['task_id'])['task'])
            for _ in expected:
                a=self.submit(t,self.review(t))
                if a['action']=='continue_chapter_batch':t=a['task']
        long_review=self.cli('advance','--after',a['task_id'])['task']
        self.assertEqual(long_review['stage'],'batch_review');self.assertNotIn('chapter_session',long_review)
        self.assertEqual(head(self.db,'main'),10);self.assertTrue(engine.audit(self.db)['ok'])

    def test_large_materials_preserve_batch_roles_and_first_read_order(self):
        from novel_core.ledger import append_events
        put(self.db,'context_mode','bounded')
        row=self.db.execute('SELECT prose,data FROM chapters WHERE number=1').fetchone();data=json.loads(row['data'])
        extra=[{'op':'knowledge','entity':'hero','topic':'k'+str(i),'before':None,'claim':'完整条件'+str(i),
                'stance':'believes','source':'旧证词','evidence':ev(row['prose'],1)} for i in range(64)]
        append_events(self.db,'main',1,row['prose'],extra,replay(self.db,'main'))
        data['events']=extra;self.db.execute('UPDATE chapters SET data=? WHERE number=1',(dump(data),))
        task=self.cli('advance')['task'];writer_pages=0
        for _ in range(150):
            self.assertEqual(task['chapter_session']['role'],'writer')
            if task['stage'] in ('context_scan','context_reduce'):
                writer_pages+=1;receipt={'cannot_compact':True,'reason':'工程夹具保留全部冻结认知及来源。'}
            else:receipt=self.write(task)
            out=self.submit(task,receipt)
            if out['action']!='continue_chapter_batch':break
            task=out['task']
        else:self.fail('writer did not reach batch boundary')
        self.assertGreater(writer_pages,0);self.assertEqual(head(self.db,'main'),1)
        task=self.cli('advance','--after',out['task_id'])['task'];self.assertNotIn('chapter_session',task)
        for _ in range(30):
            receipt=result(task,{'cannot_compact':True,'reason':'首读前由材料者保留原始资料，不签审稿。'})
            path=self.path/task['output_path'];path.write_text(dump(receipt))
            out=self.cli('submit','--result',str(path),'--brief','--continue-preparation')
            if out['action']!='continue_preparation':break
            task=out['task']
        else:self.fail('material preparation did not stop')
        task=self.cli('advance','--after',out['task_id'])['task'];self.assertEqual(task['stage'],'chapter_review')
        self.assertEqual(len(task['first_read_files']),5);self.assertFalse((self.path/task['input_path']).exists())
        task=self.seal(task);reader_pages=0
        for _ in range(150):
            self.assertEqual(task['chapter_session']['role'],'reviewer')
            if task['stage'] in ('context_scan','context_reduce'):
                reader_pages+=1;receipt={'cannot_compact':True,'reason':'整批首读已冻结，继续保留疑点来源。'}
            else:receipt=self.review(task)
            out=self.submit(task,receipt)
            if out['action']!='continue_chapter_batch':break
            task=out['task']
        else:self.fail('reviewer did not finish batch')
        self.assertGreater(reader_pages,0);self.assertEqual(head(self.db,'main'),6)
        self.assertTrue(engine.audit(self.db)['ok'])

    def test_profile_boundary_does_not_skip_preparation_for_preserved_suffix(self):
        self.writer_batch();batch=batchflow.active(self.db)
        batchflow.invalidate(self.db,batch,2,'人物准备范围改变，保留正文重核')
        def job(db,branch,n,plan):
            return ('character_plan',n,{'entities':['hero']}) if n==3 else None
        with patch('novel_core.characters.next_job',side_effect=job):
            t=self.cli('advance')['task'];self.assertEqual(t['chapter'],2)
            out=self.submit(t,self.write(t));self.assertEqual(out['action'],'chapter_batch_done')
            r=self.seal(self.cli('advance','--after',out['task_id'])['task'])
            reply=self.submit(r,self.review(r));self.assertEqual(head(self.db,'main'),2)
            profile=self.cli('advance','--after',reply['task_id'])['task']
            self.assertEqual(profile['stage'],'character_plan');self.assertEqual(profile['chapter'],3)
            self.assertEqual(engine.load_draft(self.db,'main',3)['stage'],'draft')


if __name__=='__main__':unittest.main()
