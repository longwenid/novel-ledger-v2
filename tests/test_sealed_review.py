"""Exercise the delivered CLI contract, without the legacy review fixture helpers."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from support import connect, contract, dump, envelope, ev, get, initialize, put, respond
import novel
from novel_core import engine, reviewflow, taskio, dependencies
from novel_core.store import Error, digest, head


class SealedReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        c=contract();c['style']={'voice':'克制','traits':['动作表现选择','人物对白有区别']}
        initialize(self.path,c);self.db=connect(self.path)
        self.prose='周行交出收据。宋禾拒绝签字，让他先找来证人。'
        respond(self.db,self.path,engine.next_task(self.db,self.path))
        respond(self.db,self.path,engine.next_task(self.db,self.path),prose=self.prose)
        self.task=engine.next_task(self.db,self.path)

    def tearDown(self):self.db.close();self.tmp.cleanup()
    def path_for(self,key):return self.path/self.task[key]
    def first(self):
        receipt=json.loads(self.path_for('first_read_template_path').read_text(encoding='utf-8'))
        receipt['first_read']={'desire':'交收据','obstacle':'宋禾拒签','change':'先找证人','read_on_reason':'证人是否愿意来'}
        return receipt
    def seal(self):
        reviewflow.seal(self.db,self.path,self.task['task_id'],self.first())
        self.task=engine.next_task(self.db,self.path)
    def receipt(self):
        r=json.loads(self.path_for('template_path').read_text(encoding='utf-8'));e=ev(self.prose)
        for category in ('facts','reader'):
            r['data'][category]['verdict']='pass'
            for row in r['data'][category]['checks']:
                row.update(result='pass',reason='合成工程夹具，用于验证回执结构与事务。',evidence=[copy.deepcopy(e)])
                if 'challenge' in row:
                    row['challenge']={'risk':'工程夹具的可疑交收顺序。','disposition':'satisfied',
                                      'reason':'仅验证结构，不代表文学判断。','evidence':copy.deepcopy(e)}
                if 'elapsed_days' in row:row['elapsed_days']=0
            for row in r['data'][category].get('obligations',[]):
                row.update(result='pass',reason='合成正文没有引入与此约定矛盾的记录。',evidence=[copy.deepcopy(e)],
                           challenge={'disposition':'satisfied','reason':'合成工程夹具的约束判断。','evidence':copy.deepcopy(e)})
        r['data']['facts']['summary']='宋禾拒签，周行需要找证人。'
        return r
    def cli(self,*args):
        return novel.dispatch(novel.parser().parse_args([args[0],'--project',str(self.path),*args[1:]]))
    def assert_error(self,code,call):
        with self.assertRaises(Error) as caught:call()
        self.assertEqual(caught.exception.code,code)

    def test_first_delivery_has_only_current_prose_and_scoped_read_record(self):
        self.assertEqual(self.task['delivery_phase'],'first_read')
        self.assertFalse(self.path_for('input_path').exists())
        self.assertNotIn('template_path',self.task)
        self.assertEqual(self.path_for('first_read_path').read_text(encoding='utf-8'),self.prose)
        self.assertEqual(self.task['prose_files'],[self.task['first_read_path']])
        initial=dependencies.read_set(self.db,self.task['task_id'])['entries']
        self.assertEqual(len(initial),1)
        minimal={'branch':'main','chapter':1,'stage':'chapter_review',
                 'payload':{'prose_path':self.task['first_read_path'],'prose_hash':self.task['first_read_hash']}}
        self.assertEqual(initial[0]['value_hash'],digest(minimal))
        for command in (['recall','--contract'],['recall','--state'],['design'],['character','--entity','hero'],['config']):
            self.assert_error('first_read_required',lambda:self.cli(*command))
        self.assertEqual(self.cli('evidence','--task',self.task['task_id'],'--quote','宋禾拒绝签字'),ev(self.prose,quote='宋禾拒绝签字'))

    def test_cli_seal_unlocks_template_and_is_immutable_across_restart(self):
        receipt=self.first();path=self.path_for('first_read_result_path');path.write_text(dump(receipt), encoding='utf-8')
        accepted=self.cli('first-read','--task',self.task['task_id'],'--result',str(path))
        self.assertTrue(accepted['accepted']);self.assertTrue(self.path_for('input_path').exists())
        self.task=engine.next_task(self.db,self.path)
        work=envelope(self.task)
        self.assertEqual([r['id'] for r in work['review_obligations']],['language','world:0','style:0','style:1'])
        self.assertEqual(self.receipt()['data']['reader']['first_read_ref'],accepted['first_read_hash'])
        self.assertEqual(len(dependencies.read_set(self.db,self.task['task_id'])['entries']),2)
        self.assertEqual(self.cli('first-read','--task',self.task['task_id'],'--result',str(path)),accepted)
        self.db.close();self.db=connect(self.path)
        receipt['first_read']['change']='补读后改写'
        self.assert_error('first_read_conflict',lambda:reviewflow.seal(self.db,self.path,self.task['task_id'],receipt))
        self.assertEqual(get(self.db,reviewflow.key(self.task['task_id']))['hash'],accepted['first_read_hash'])

    def test_bad_or_stale_seal_never_unlocks(self):
        for field,value in (('input_hash','wrong'),('prose_hash','wrong'),('first_read',{'desire':'空缺'})):
            bad=self.first();bad[field]=value
            with self.assertRaises(Error):reviewflow.seal(self.db,self.path,self.task['task_id'],bad)
            self.assertFalse(self.path_for('input_path').exists())
            self.assertIsNone(get(self.db,reviewflow.key(self.task['task_id'])))
        c=get(self.db,'contract');c['reader_promise']='改变阅读承诺';put(self.db,'contract',c)
        self.assert_error('review_context_stale',lambda:reviewflow.seal(self.db,self.path,self.task['task_id'],self.first()))
        new=engine.next_task(self.db,self.path)
        self.assertNotEqual(new['task_id'],self.task['task_id']);self.assertEqual(new['delivery_phase'],'first_read')

    def test_submit_requires_seal_not_just_four_self_reported_fields(self):
        self.seal();receipt=self.receipt();put(self.db,reviewflow.key(self.task['task_id']),None)
        self.assert_error('first_read_required',lambda:engine.submit(self.db,receipt))
        self.assertEqual(head(self.db,'main'),0)

    def test_corrupted_seal_never_authorizes_material_delivery(self):
        self.seal();saved=get(self.db,reviewflow.key(self.task['task_id']))
        saved['receipt']['first_read']['change']='损坏记录';put(self.db,reviewflow.key(self.task['task_id']),saved)
        self.assert_error('first_read_integrity',lambda:engine.next_task(self.db,self.path))
        self.assert_error('first_read_integrity',lambda:self.cli('recall','--contract'))

    def test_ambiguous_evidence_requires_context(self):
        self.assert_error('evidence',lambda:taskio.locate(self.db,self.task['task_id'],'不存在'))
        # Build an explicit duplicate candidate without changing the real pending.
        text='重复。重复。';work=json.loads(self.db.execute('SELECT input FROM tasks WHERE id=?',(self.task['task_id'],)).fetchone()[0])
        put(self.db,'candidate_text:'+digest(text),text);work['payload']['prose_hash']=digest(text)
        self.db.execute('UPDATE tasks SET input=? WHERE id=?',(dump(work),self.task['task_id']))
        self.assert_error('evidence',lambda:taskio.locate(self.db,self.task['task_id'],'重复'))
        self.assertEqual(taskio.locate(self.db,self.task['task_id'],text),{'chapter':1,'start':0,'quote':text})

    def test_coverage_forgery_or_reinterpreted_first_read_rolls_back(self):
        self.seal();original=self.receipt()
        for change,code in (
            (lambda r:r['data']['reader']['obligations'].pop(),'review_obligations'),
            (lambda r:r['data']['facts']['obligations'][0].update(id='world:unknown'),'review_obligations'),
            (lambda r:r['data']['reader']['obligations'][0]['challenge']['evidence'].update(quote='不存在'),'evidence'),
            (lambda r:r['data']['reader'].update(first_read={'change':'补读后合理化'}),'first_read_conflict'),
            (lambda r:r['data']['reader']['obligations'][0]['challenge'].update(disposition='violated'),'review_challenge')):
            r=copy.deepcopy(original);change(r)
            self.assert_error(code,lambda:engine.submit(self.db,r))
            self.assertEqual(head(self.db,'main'),0);self.assertEqual(engine.next_task(self.db,self.path)['task_id'],self.task['task_id'])

    def test_obligation_defects_across_rows_are_reported_together(self):
        self.seal();r=self.receipt()
        r['data']['facts']['obligations'][0]['evidence']=[]
        r['data']['reader']['obligations'][0]['challenge']['reason']=''
        r['data']['reader']['obligations'][2]['evidence'][0]['quote']='不存在的原文'
        with self.assertRaises(Error) as caught:engine.submit(self.db,r)
        details=caught.exception.details
        self.assertEqual(caught.exception.code,'review_obligations')
        self.assertEqual(details['count'],3)
        self.assertEqual({i['obligation_id'] for i in details['issues']},{'world:0','language','style:1'})
        self.assertEqual({i['code'] for i in details['issues']},{'review_obligations','review_challenge','evidence'})
        self.assertEqual(head(self.db,'main'),0)

    def test_violation_must_link_to_check_and_repair_finding(self):
        self.seal();r=self.receipt();reader=r['data']['reader'];reader['verdict']='fix'
        row=reader['obligations'][0];row['result']='fix';row['challenge']['disposition']='violated'
        self.assert_error('review_obligations',lambda:engine.submit(self.db,r))
        check=next(c for c in reader['checks'] if c['id']=='language')
        check['result']='fix';check['challenge']['disposition']='violated'
        self.assert_error('review_obligations',lambda:engine.submit(self.db,r))
        reader['findings']=[{'obligation_id':'language','severity':'quality','impact':'工程测试的明确违约路径',
                            'goal':'修改所指语段','evidence':ev(self.prose)}]
        reader['verdict']='pass'
        self.assert_error('review_obligations',lambda:engine.submit(self.db,r))
        reader['verdict']='fix'
        engine.submit(self.db,r)
        self.assertEqual(head(self.db,'main'),0);self.assertEqual(engine.next_task(self.db,self.path)['stage'],'repair')

    def test_compact_submit_updates_controller_without_heavy_status(self):
        self.seal();taskio.checkpoint(self.db,self.path,goal='只写首章')
        r=self.receipt();self.path_for('output_path').write_text(dump(r), encoding='utf-8')
        with patch('novel_core.budget.overview',side_effect=AssertionError('heavy status')):
            out=self.cli('submit','--result',str(self.path_for('output_path')),'--brief')
        self.assertTrue(out['accepted']);self.assertNotIn('usage',out);self.assertNotIn('volume_budget',out)
        point=json.loads((self.path/'work/controller.json').read_text(encoding='utf-8'))
        self.assertEqual(point['committed'],1);self.assertIsNone(point['task']);self.assertEqual(point['authorized_goal'],'只写首章')
        self.assertEqual(point['root'],str(Path(engine.__file__).resolve().parents[2]))
        same=self.cli('submit','--result',str(self.path_for('output_path')),'--brief');self.assertTrue(same['idempotent'])

    def test_checkpoint_does_not_infer_authorization_and_resets_writer(self):
        point=taskio.checkpoint(self.db,self.path);self.assertFalse(point['authorization_recorded'])
        self.assertEqual(point['task']['input_hash'],self.task['input_hash'])
        taskio.checkpoint(self.db,self.path,goal='写十章')
        with self.assertRaises(Error):taskio.checkpoint(self.db,self.path,writer_session='a',handled=[1])
        put(self.db,'controller_intent',{'authorized_goal':'写十章','writer_session_id':'old-writer','handled_chapters':[1]})
        point=taskio.checkpoint(self.db,self.path);self.assertNotIn('writer_session_id',point)
        self.assertEqual(point['authorized_goal'],'写十章')

    def test_frozen_old_combined_task_remains_submit_compatible(self):
        first=self.first()['first_read']
        row=self.db.execute('SELECT * FROM tasks WHERE id=?',(self.task['task_id'],)).fetchone();work=json.loads(row['input'])
        work.pop('review_protocol');work.pop('review_obligations')
        self.db.execute('UPDATE tasks SET input=?,input_hash=? WHERE id=?',(dump(work),digest(work),row['id']))
        self.task=engine.next_task(self.db,self.path)
        self.assertEqual(self.task['delivery_phase'],'task');self.assertTrue(self.path_for('input_path').exists())
        r=self.receipt();r['data']['reader']['first_read']=first
        engine.submit(self.db,r);self.assertEqual(head(self.db,'main'),1)


if __name__=='__main__':unittest.main()
