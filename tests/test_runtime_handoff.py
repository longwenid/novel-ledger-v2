"""Exercise the shorter controller loop without bypassing submission gates."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from support import contract, initialize, connect, get, put, respond, result, dump, ev
from novel_core import engine
from novel_core.store import Error, head
from test_chapter_length import developed_plan
import novel


class RuntimeHandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        initialize(self.path,contract());self.db=connect(self.path)

    def tearDown(self):self.db.close();self.tmp.cleanup()
    def cli(self,*args):
        return novel.dispatch(novel.parser().parse_args([args[0],'--project',str(self.path),*args[1:]]))
    def count(self):return self.db.execute('SELECT count(*) FROM tasks').fetchone()[0]
    def start_review(self):
        planning=self.cli('advance')['task'];respond(self.db,self.path,planning)
        draft=self.cli('advance','--after',planning['task_id'])['task'];respond(self.db,self.path,draft)
        return self.cli('advance','--after',draft['task_id'])['task']
    def formal_review(self,task):
        first=json.loads((self.path/task['first_read_template_path']).read_text())
        first['first_read']={'desire':'交付收据','obstacle':'尚未信任','change':'按下指印','read_on_reason':'选择的后果'}
        first_path=self.path/task['first_read_result_path'];first_path.write_text(dump(first))
        # Use the returned envelope directly: no second next call or task.json reload.
        formal=self.cli('first-read','--task',task['task_id'],'--result',str(first_path))['task']
        receipt=json.loads((self.path/formal['template_path']).read_text())
        prose=(self.path/formal['first_read_path']).read_text();evidence=ev(prose)
        for category in ('facts','reader'):
            data=receipt['data'][category];data['verdict']='pass'
            for row in data['checks']:
                row.update(result='pass',reason='工程夹具验证提交与交接，不评价文学质量。',evidence=[copy.deepcopy(evidence)])
            for row in data['obligations']:
                row.update(result='pass',reason='工程夹具验证约定完整覆盖。',evidence=[copy.deepcopy(evidence)],
                           challenge={'disposition':'satisfied','reason':'工程夹具的结构判断。','evidence':copy.deepcopy(evidence)})
        receipt['data']['facts']['summary']='周行交付收据，宋禾按下指印。'
        return formal,receipt
    def submit_file(self,task,receipt):
        path=self.path/task['output_path'];path.write_text(dump(receipt))
        return self.cli('submit','--result',str(path),'--brief')

    def test_start_and_retry_restore_one_frozen_task_with_short_status_and_checkpoint(self):
        started=self.cli('advance')
        with patch('novel_core.budget.overview',side_effect=AssertionError('heavy status')):
            again=self.cli('advance')
        self.assertEqual(started['action'],'task_ready');self.assertEqual(self.count(),1)
        self.assertEqual(started['task']['task_id'],again['task']['task_id'])
        self.assertEqual(started['task']['input_hash'],again['task']['input_hash'])
        self.assertNotIn('usage',started['status']);self.assertNotIn('volume_budget',started['status'])
        point=json.loads((self.path/'work/controller.json').read_text())
        self.assertFalse(point['authorization_recorded'])
        self.assertEqual(point['task']['input_hash'],started['task']['input_hash'])

    def test_worker_completion_claim_and_failed_receipt_cannot_advance(self):
        task=self.cli('advance')['task'];bad=result(task,{'chapters':[]})
        with self.assertRaises(Error):self.submit_file(task,bad)
        waited=self.cli('advance','--after',task['task_id'])
        self.assertEqual(waited['action'],'wait_for_submit');self.assertNotIn('task',waited)
        self.assertEqual(waited['status']['task']['id'],task['task_id']);self.assertEqual(self.count(),1)
        with self.assertRaises(Error) as caught:self.cli('advance','--after','unknown')
        self.assertEqual(caught.exception.code,'handoff_task');self.assertEqual(self.count(),1)

    def test_accepted_handoff_is_idempotent_and_obsolete_reply_cannot_create_review(self):
        planning=self.cli('advance')['task'];respond(self.db,self.path,planning)
        first=self.cli('advance','--after',planning['task_id']);second=self.cli('advance','--after',planning['task_id'])
        self.assertEqual(first['task']['stage'],'draft')
        self.assertEqual(first['task']['task_id'],second['task']['task_id']);self.assertEqual(self.count(),2)
        respond(self.db,self.path,first['task'])
        stale=self.cli('advance','--after',planning['task_id'])
        self.assertEqual(stale['action'],'resync');self.assertNotIn('task',stale);self.assertEqual(self.count(),2)
        reviewed=self.cli('advance','--after',first['task']['task_id'])['task']
        self.assertEqual(reviewed['stage'],'chapter_review');self.assertEqual(reviewed['delivery_phase'],'first_read')
        self.assertFalse((self.path/reviewed['input_path']).exists())

    def test_direct_first_read_response_supports_submission_and_preserves_author_gate(self):
        task=self.start_review();formal,receipt=self.formal_review(task)
        self.assertEqual(formal['delivery_phase'],'review')
        self.assertTrue(self.submit_file(formal,receipt)['accepted'])
        handoff=self.cli('advance','--after',task['task_id'])
        self.assertEqual(handoff['action'],'await_author');self.assertNotIn('task',handoff)
        self.assertEqual(head(self.db,'main'),1);self.assertFalse(get(self.db,'pilot_approved'))
        self.assertEqual(self.cli('advance')['action'],'await_author')

    def test_invalid_review_evidence_does_not_unlock_handoff(self):
        task=self.start_review();formal,receipt=self.formal_review(task)
        receipt['data']['reader']['obligations'][0]['challenge']['evidence']['quote']='不存在的证据'
        with self.assertRaises(Error):self.submit_file(formal,receipt)
        self.assertEqual(self.cli('advance','--after',task['task_id'])['action'],'wait_for_submit')
        self.assertEqual(head(self.db,'main'),0)

    def test_quality_finding_still_routes_to_repair(self):
        task=self.start_review();formal,receipt=self.formal_review(task)
        reader=receipt['data']['reader'];reader['verdict']='fix'
        obligation=reader['obligations'][0];obligation['result']='fix';obligation['challenge']['disposition']='violated'
        next(row for row in reader['checks'] if row['id']=='language')['result']='fix'
        reader['findings']=[{'obligation_id':'language','severity':'quality','impact':'明确的工程违约路径',
                            'goal':'修改证据所指语段','evidence':copy.deepcopy(obligation['evidence'][0])}]
        self.submit_file(formal,receipt)
        self.assertEqual(self.cli('advance','--after',task['task_id'])['task']['stage'],'repair')
        self.assertEqual(head(self.db,'main'),0)

    def test_direct_draft_submit_still_checks_full_length_and_preserves_target(self):
        c=get(self.db,'contract');c['chapter_target_han']=90;put(self.db,'contract',c)
        planning=self.cli('advance')['task'];self.submit_file(planning,result(planning,{'chapters':[developed_plan()]}))
        draft=self.cli('advance','--after',planning['task_id'])['task']
        self.submit_file(draft,result(draft,{'prose':'宋禾没有立刻按手印。','han_characters':9000}))
        repair=self.cli('advance','--after',draft['task_id'])['task']
        self.assertEqual(repair['stage'],'repair');self.assertEqual(head(self.db,'main'),0)
        saved=engine.load_draft(self.db,'main',1)
        self.assertEqual(saved['prose'],'宋禾没有立刻按手印。')
        self.assertEqual(saved['chapter_length']['target_han'],90)
        self.assertGreater(saved['chapter_length']['shortfall_han'],0)

    def test_short_draft_handoffs_keep_the_finite_repair_limit(self):
        c=get(self.db,'contract');c['chapter_target_han']=90;put(self.db,'contract',c)
        planning=self.cli('advance')['task'];self.submit_file(planning,result(planning,{'chapters':[developed_plan()]}))
        task=self.cli('advance','--after',planning['task_id'])['task']
        texts=['宋禾没有签字。','宋禾拒绝签字。','宋禾把纸推回。','宋禾先找证人。']
        expected=['repair','repair','redesign','paused']
        for text,next_stage in zip(texts,expected):
            data={'prose':text,'repair_note':'补实人物选择','design_change':'改变作证的前置选择'}
            self.submit_file(task,result(task,data))
            following=self.cli('advance','--after',task['task_id'])
            if next_stage=='paused':
                self.assertEqual(following['action'],'paused');self.assertNotIn('task',following)
                self.assertEqual(following['status']['reason']['code'],'chapter_development_exhausted')
            else:
                task=following['task'];self.assertEqual(task['stage'],next_stage)
        self.assertEqual(head(self.db,'main'),0)
        self.assertEqual(get(self.db,'contract')['chapter_target_han'],90)

    def test_handoff_loop_completes_only_after_all_final_reviews(self):
        following=self.cli('advance');scopes=[];approved=False
        for _ in range(30):
            if following['action']=='await_author':
                # Simulate an explicit author decision; advance never supplies it.
                self.cli('approve');approved=True;following=self.cli('advance');continue
            if following['action']=='completed':break
            self.assertEqual(following['action'],'task_ready')
            task=following['task'];scopes.append(task['stage'])
            respond(self.db,self.path,task,ending=task['chapter']==1)
            following=self.cli('advance','--after',task['task_id'])
        else:self.fail('handoff loop did not stop at completion')
        self.assertTrue(approved);self.assertIn('chapter_review',scopes)
        self.assertIn('volume_review',scopes);self.assertIn('book_review',scopes)
        for scope in ('volume_chunk','book_chunk'):
            self.assertEqual(self.db.execute('SELECT count(*) FROM reviews WHERE scope=?',(scope,)).fetchone()[0],1)
        self.assertTrue(engine.audit(self.db)['ok'])
        self.assertEqual(self.cli('advance')['action'],'completed')

    def test_pause_or_branch_change_cannot_dispatch_from_old_callback(self):
        task=self.cli('advance')['task'];respond(self.db,self.path,task)
        self.cli('pause','--reason','作者暂停')
        self.assertEqual(self.cli('advance','--after',task['task_id'])['action'],'paused');self.assertEqual(self.count(),1)
        self.cli('resume')
        # A foreign callback is rejected before querying or creating branch work.
        put(self.db,'work_branch','other')
        out=self.cli('advance','--after',task['task_id'])
        self.assertEqual(out['action'],'resync');self.assertEqual(out['transition']['handoff_reason'],'branch_changed')
        self.assertEqual(self.count(),1)


if __name__=='__main__':unittest.main()
