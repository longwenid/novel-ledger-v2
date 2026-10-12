"""Chapter lifecycle, frozen policy, recovery and independent combined review."""
import copy
import json
import shlex
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
import support
import novel
from novel_core import engine, execution, reviewflow, taskio
from novel_core.store import Error, connect, initialize, get, put, head
from support import contract, plan, envelope, result, combined_review, fixture_first_read, ev, respond


class SingleChapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        c=contract();c.pop('chapter_batch_size');initialize(self.path,c);self.db=connect(self.path)
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def cli(self,*args):
        return novel.dispatch(novel.parser().parse_args([args[0],'--project',str(self.path),*args[1:]]))
    def write(self,t,text=None):
        return {'prose':text or f'第{t["chapter"]}章，周行交出收据，宋禾核对之后签名。',
                'summary':'交出收据，核对后签名。','self_check':'核对交接顺序、人物选择和签名后果。'}
    def submit(self,t,data,session=None):
        receipt=result(t,data,fixture_writer_defaults=False,**({'session':session} if session else {}))
        (self.path/t['output_path']).write_text(json.dumps(receipt,ensure_ascii=False),encoding='utf-8')
        args=shlex.split(t['commands']['submit'])[2:]
        return novel.dispatch(novel.parser().parse_args(args))
    def review(self,t):
        work=envelope(t);text=engine.load_draft(self.db,'main',t['chapter'])['prose']
        return combined_review(work,text,t['chapter'])
    def pilot(self):
        t=self.cli('advance')['task'];respond(self.db,self.path,t)
        t=self.cli('advance')['task'];self.submit(t,self.write(t))
        t=self.cli('advance')['task'];self.submit(t,self.review(t));engine.approve(self.db)
        engine.validate_plan(self.db,'main',2,{'chapters':[plan(n) for n in range(2,12)]})

    def test_default_and_explicit_legacy_configuration(self):
        self.assertEqual(execution.settings(self.db),{'execution_mode':'single_chapter',**execution.SINGLE_OPTIONS})
        self.assertEqual(self.cli('config')['execution_mode'],'single_chapter')
        self.assertEqual(self.cli('config')['chapter_batch_size'],1)
        self.assertEqual(execution.initial_mode(contract()),'single_chapter')
        self.db.execute("DELETE FROM meta WHERE key='execution_mode'")
        self.assertEqual(execution.settings(self.db)['execution_mode'],'chapter_batch')

    def test_one_writer_one_combined_reviewer_before_next_chapter(self):
        self.pilot();sequence=[]
        for n in (2,3,4):
            writer=self.cli('advance')['task'];sequence.append((writer['stage'],writer['chapter']))
            self.assertEqual(writer['chapter'],n);self.assertNotIn('chapter_session',writer)
            self.assertNotIn('--continue',writer['commands']['submit'])
            out=self.submit(writer,self.write(writer),f'w{n}')
            self.assertEqual(out['action'],'chapter_written');self.assertNotIn('task',out)
            self.assertEqual(head(self.db,'main'),n-1)
            reviewer=self.cli('advance','--after',writer['task_id'])['task'];sequence.append((reviewer['stage'],reviewer['chapter']))
            self.assertEqual(reviewer['delivery_phase'],'first_read');self.assertNotIn('reading_view',reviewer)
            self.assertEqual(self.cli('advance')['task']['task_id'],reviewer['task_id'])
            policy=reviewer['execution_policy'];self.assertEqual(policy['role'],'combined_reviewer')
            self.assertTrue(policy['new_worker_required']);self.assertEqual(policy['reviewer_session_policy'],'chapter')
            spec=json.loads((self.path/reviewer['host_dispatch_path']).read_text(encoding='utf-8'))
            self.assertTrue(spec['new_worker_required']);self.assertFalse(spec['launched'])
            sealed=reviewflow.seal(self.db,self.path,reviewer['task_id'],{'task_id':reviewer['task_id'],
                'input_hash':reviewer['input_hash'],'prose_hash':reviewer['first_read_hash'],
                'first_read':fixture_first_read(),'session':f'r{n}'})['task']
            self.assertEqual(sealed['task_id'],reviewer['task_id']);self.assertNotIn('writer_summary',envelope(sealed))
            out=self.submit(sealed,self.review(sealed),f'r{n}')
            self.assertEqual(out['action'],'chapter_reviewed');self.assertEqual(head(self.db,'main'),n)
        self.assertEqual(sequence,[('draft',2),('chapter_review',2),('draft',3),('chapter_review',3),('draft',4),('chapter_review',4)])
        self.assertTrue(engine.audit(self.db)['ok'])

    def test_writer_requires_concrete_self_check_without_candidate_ledger(self):
        respond(self.db,self.path,self.cli('advance')['task']);t=self.cli('advance')['task']
        for key in ('self_check','summary'):
            data=self.write(t);data.pop(key)
            with self.subTest(key=key),self.assertRaises(Error):self.submit(t,data)
        data=self.write(t);data['candidate_events']=[]
        with self.assertRaises(Error) as caught:self.submit(t,data)
        self.assertEqual(caught.exception.code,'writer_scope')
        self.submit(t,self.write(t));self.assertEqual(head(self.db,'main'),0)

    def test_worker_continuation_is_rejected_before_submission(self):
        respond(self.db,self.path,self.cli('advance')['task']);t=self.cli('advance')['task']
        file=self.path/t['output_path'];file.write_text(json.dumps(result(t,self.write(t))),encoding='utf-8')
        with self.assertRaises(Error) as caught:self.cli('submit','--result',str(file),'--continue-chapter-batch')
        self.assertEqual(caught.exception.code,'worker_boundary')
        self.assertEqual(self.db.execute('SELECT state FROM tasks WHERE id=?',(t['task_id'],)).fetchone()[0],'pending')

    def test_known_session_reuse_and_reviewer_change_are_rejected(self):
        self.pilot();t=self.cli('advance')['task'];self.submit(t,self.write(t),'writer')
        r=self.cli('advance')['task'];data=self.review(r)
        with self.assertRaises(Error) as caught:self.submit(r,data,'writer')
        self.assertEqual(caught.exception.code,'worker_session');self.submit(r,data,'reviewer')
        following=self.cli('advance')['task']
        with self.assertRaises(Error):self.submit(following,self.write(following),'writer')
        self.submit(following,self.write(following),'writer3');r=self.cli('advance')['task']
        reviewflow.seal(self.db,self.path,r['task_id'],{'task_id':r['task_id'],'input_hash':r['input_hash'],
            'prose_hash':r['first_read_hash'],'first_read':fixture_first_read(),'session':'reviewer3'})
        with self.assertRaises(Error):self.submit(r,self.review(r),'different-reviewer')
        data=self.review(r);out=self.submit(r,data,'reviewer3');self.assertEqual(out['action'],'chapter_reviewed')
        self.assertTrue(self.submit(r,data,'reviewer3')['idempotent'])

    def test_checkpoint_is_task_bound_and_preserves_author_goal(self):
        self.pilot();t=self.cli('advance')['task']
        snap=self.cli('checkpoint','--goal','写到第 4 章','--writer-session','w2','--handled-chapter','2')
        self.assertEqual(snap['writer_task_id'],t['task_id'])
        with self.assertRaises(Error):self.cli('checkpoint','--handled-chapter','3')
        self.submit(t,self.write(t),'w2');r=self.cli('advance')['task']
        snap=get(self.db,'controller_intent');self.assertEqual(snap['authorized_goal'],'写到第 4 章')
        self.assertNotIn('writer_session_id',snap);self.assertNotIn('handled_chapters',snap)
        with self.assertRaises(Error):self.cli('checkpoint','--writer-session','w2')
        self.assertIn('新 Worker',taskio._handoff_boundary(self.db,'main'))

    def test_retired_mode_cannot_be_reenabled_and_pending_hash_frozen(self):
        self.pilot();t=self.cli('advance')['task'];before=self.db.execute('SELECT input_hash FROM tasks WHERE id=?',(t['task_id'],)).fetchone()[0]
        with self.assertRaises(Error):self.cli('config','--key','execution_mode','--value','chapter_batch')
        self.assertEqual(self.db.execute('SELECT input_hash FROM tasks WHERE id=?',(t['task_id'],)).fetchone()[0],before)
        self.submit(t,self.write(t))
        with self.assertRaises(Error) as caught:self.cli('config','--key','execution_mode','--value','chapter_batch')
        self.assertEqual(caught.exception.code,'config')
        r=self.cli('advance')['task'];self.submit(r,self.review(r))
        with self.assertRaises(Error):self.cli('config','--key','execution_mode','--value','chapter_batch')
        self.assertEqual(self.cli('config')['chapter_batch_size'],1)
        self.assertNotIn('chapter_session',self.cli('advance')['task'])

    def test_local_repair_new_workers_re_review_and_attempt_budget(self):
        self.pilot();t=self.cli('advance')['task'];self.submit(t,self.write(t));r=self.cli('advance')['task']
        data=self.review(r);data['reader']['verdict']='fix';data['reader']['repair_scope']='local'
        data['reader']['checks'][0]['result']='fix'
        text=engine.load_draft(self.db,'main',2)['prose']
        data['reader']['findings']=[{'severity':'quality','impact':'签名缺少人物回应','goal':'加入人物回应',
                                   'evidence':ev(text,2)}]
        self.assertEqual(self.submit(r,data)['action'],'repair_required')
        repair=self.cli('advance')['task'];self.assertEqual(repair['stage'],'repair')
        self.assertEqual(repair['chapter'],2);self.assertNotEqual(repair['task_id'],t['task_id'])
        d=engine.load_draft(self.db,'main',2);self.assertEqual(d['attempts'],1)
        data={'base_hash':envelope(repair)['payload']['base_hash'],
              'patches':[{'start':text.index('宋禾'),'old':'宋禾','new':'宋禾点头后'}],
              'repair_note':'在签名前加入回应。','summary':'回应后签名。','self_check':'核对回应承接交收动作。'}
        self.submit(repair,data)
        review=self.cli('advance')['task'];self.assertEqual(review['stage'],'chapter_review')
        self.assertNotEqual(review['task_id'],r['task_id']);self.assertEqual(review['delivery_phase'],'first_read')
        self.assertEqual(engine.load_draft(self.db,'main',2)['attempts'],1)
        self.submit(review,self.review(review));self.assertEqual(head(self.db,'main'),2)

    def test_ten_chapter_review_and_author_gate_preserved(self):
        t=self.cli('advance')['task'];respond(self.db,self.path,t)
        t=self.cli('advance')['task'];self.submit(t,self.write(t));r=self.cli('advance')['task'];self.submit(r,self.review(r))
        self.assertEqual(self.cli('advance')['action'],'await_author')
        engine.approve(self.db);engine.validate_plan(self.db,'main',2,{'chapters':[plan(n) for n in range(2,12)]})
        for n in range(2,11):
            w=self.cli('advance')['task'];self.submit(w,self.write(w));r=self.cli('advance')['task'];self.submit(r,self.review(r))
        task=self.cli('advance')['task'];self.assertEqual(task['stage'],'batch_review')
        self.assertEqual(head(self.db,'main'),10)
        self.assertTrue(self.db.execute('SELECT 1 FROM plans WHERE number=11').fetchone())

    def test_new_workers_do_not_reset_failure_ladder(self):
        self.pilot();stages=[]
        for attempt in range(4):
            writer=self.cli('advance')['task'];stages.append(writer['stage'])
            data=self.write(writer,f'第2章，周行交出收据，宋禾核对之后签名。第{attempt}次回应。')
            data.update(repair_note='展开回应的动因。',design_change='调整交接与选择的设计。')
            self.submit(writer,data,f'w-attempt-{attempt}')
            reviewer=self.cli('advance')['task'];data=self.review(reviewer)
            data['reader']['verdict']='fix';data['reader']['checks'][0]['result']='fix'
            text=engine.load_draft(self.db,'main',2)['prose']
            data['reader']['findings']=[{'severity':'quality','impact':'回应没有人物动因',
                'goal':'落实人物动因','evidence':ev(text,2)}]
            out=self.submit(reviewer,data,f'r-attempt-{attempt}')
        self.assertEqual(stages,['draft','repair','repair','redesign'])
        self.assertEqual(out['action'],'paused');self.assertEqual(head(self.db,'main'),1)
        self.assertEqual(self.cli('advance')['action'],'paused')

    def test_independent_knobs_cannot_override_single_mode(self):
        for key,value in (('chapter_batch_size','3'),('writer_batch','5'),('writer_session_policy','batch')):
            with self.subTest(key=key),self.assertRaises(Error) as caught:
                self.cli('config','--key',key,'--value',value)
            self.assertEqual(caught.exception.code,'execution_mode')

    def test_existing_batch_is_automatically_upgraded_without_losing_drafts(self):
        from test_targeted_repairs import TargetedRepairTests
        f=TargetedRepairTests(methodName='runTest');f.setUp()
        try:
            task=f.cli('advance')['task'];f.submit(task,f.draft(task))
            pending=f.cli('advance')['task'];frozen=tuple(f.db.execute('SELECT input,input_hash FROM tasks WHERE id=?',(pending['task_id'],)).fetchone())
            support.enable_upgrade(f.path)
            report=f.cli('doctor');self.assertEqual(report['execution_mode'],'single_chapter')
            self.assertEqual(tuple(f.db.execute('SELECT input,input_hash FROM tasks WHERE id=?',(pending['task_id'],)).fetchone()),frozen)
            migration=get(f.db,'execution_upgrade');self.assertEqual(migration['state'],'draining')
            self.assertTrue(Path(migration['backup_path']).is_file())
            out=f.submit(pending,f.draft(pending));self.assertEqual(out['worker_boundary'],'finished')
            restored=f.cli('advance','--after',pending['task_id'])['task']
            self.assertEqual(restored['execution_policy']['mode'],'single_chapter')
            self.assertNotIn('chapter_session',restored);self.assertEqual(restored['stage'],'chapter_review')
            self.assertEqual(restored['chapter'],2)
            self.assertIsNotNone(engine.load_draft(f.db,'main',3));self.assertEqual(get(f.db,'execution_upgrade')['state'],'complete')
            backups=list((f.path/'work/migrations').glob('*.sqlite3'))
            f.cli('doctor');f.cli('doctor');self.assertEqual(list((f.path/'work/migrations').glob('*.sqlite3')),backups)
            import sqlite3
            backup=sqlite3.connect(migration['backup_path']);backup.row_factory=sqlite3.Row
            try:
                self.assertEqual(get(backup,'version'),'1.53.0')
                self.assertEqual(get(backup,'execution_mode'),'chapter_batch')
                self.assertEqual(tuple(backup.execute('SELECT input,input_hash FROM tasks WHERE id=?',(pending['task_id'],)).fetchone()),frozen)
            finally:backup.close()
        finally:f.tearDown()

    def test_upgrade_preserves_frozen_old_review_then_restarts_next_first_read(self):
        from test_targeted_repairs import TargetedRepairTests
        f=TargetedRepairTests(methodName='runTest');f.setUp()
        try:
            out=f.writer_batch();old=f.seal(f.cli('advance','--after',out['task_id'])['task'])
            first=copy.deepcopy(reviewflow.record(f.db,old['task_id']))
            frozen=tuple(f.db.execute('SELECT input,input_hash FROM tasks WHERE id=?',(old['task_id'],)).fetchone())
            put(f.db,'controller_intent',{'authorized_goal':'写到第4章','writer_session_id':'old-id','handled_chapters':[2,3,4]})
            support.enable_upgrade(f.path);f.cli('doctor')
            self.assertEqual(reviewflow.record(f.db,old['task_id']),first)
            self.assertEqual(tuple(f.db.execute('SELECT input,input_hash FROM tasks WHERE id=?',(old['task_id'],)).fetchone()),frozen)
            out=f.submit(old,f.review(old));self.assertEqual(out['worker_boundary'],'finished')
            new=f.cli('advance','--after',old['task_id'])['task']
            self.assertEqual(new['chapter'],3);self.assertEqual(new['delivery_phase'],'first_read')
            self.assertNotIn('chapter_session',new)
            self.assertEqual(get(f.db,'controller_intent')['authorized_goal'],'写到第4章')
            self.assertNotIn('writer_session_id',get(f.db,'controller_intent'))
            self.assertEqual(head(f.db,'main'),2)
        finally:f.tearDown()

    def test_upgrade_failure_is_atomic_and_future_version_cannot_downgrade(self):
        put(self.db,'version','1.53.0');put(self.db,'execution_mode','chapter_batch')
        before=list(self.db.iterdump())
        with patch('novel_core.execution.sqlite3.connect',side_effect=OSError('backup unavailable')):
            with self.assertRaises(OSError):execution.upgrade(self.db,self.path)
        self.assertEqual(list(self.db.iterdump()),before)
        put(self.db,'version','99.0.0');before=list(self.db.iterdump())
        with self.assertRaises(Error) as caught:execution.upgrade(self.db,self.path)
        self.assertEqual(caught.exception.code,'newer_version');self.assertEqual(list(self.db.iterdump()),before)


if __name__=='__main__':unittest.main()
