"""Exact records and same-batch streaming retain provenance and role boundaries."""
import copy
import json
import shlex
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from support import contract, initialize, connect, get, put, dump, ev, plan, result, respond
from novel_core import contextflow, engine, taskio
from novel_core.store import digest, Error, transaction, head
from novel_core.ledger import seed, append_events, replay
import novel


def distribution():
    return {'schema':'novel-ledger-v2.pack/1','branch':'main','chapter':14,'stage':'draft',
        'contract':contract(),'payload':{},'plan':{'goal':'核对证词','entities':['hero']},
        'state':{'knowledge':{str(i):{'claim':'完整认知'+str(i),'stance':'believes','source':'旧证词'} for i in range(22)}},
        'character_context':{'profiles':{str(i):{'voice':'人物完整声线'+str(i)} for i in range(10)}},
        'design_context':{'notes':[{'condition':'设计前提'+str(i)} for i in range(3)]},
        'selected':[{'kind':'history','text':'完整历史与因果'+str(i)} for i in range(23)]}


class MaterialEfficiencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        c=contract();c['context_mode']='bounded';initialize(self.path,c);self.db=connect(self.path)
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def cli(self,*args):return novel.dispatch(novel.parser().parse_args([args[0],'--project',str(self.path),*args[1:]]))
    def grow(self,count):
        text='她误信钥匙在东门。周行没有替她确认。';state=seed(get(self.db,'contract'))
        events=[{'op':'knowledge','entity':'hero','topic':'k'+str(i),'before':None,
                 'stance':'believes','claim':'她误信钥匙在东门' if i==0 else '完整条件'+str(i),
                 'source':'旧证词','evidence':ev(text)} for i in range(count)]
        data={'volume':'v1','summary':'周行仍须核验证词。','events':events,'prose_hash':digest(text),
              'facts_review':{'verdict':'pass'},'reader_review':{'verdict':'pass'}}
        with transaction(self.db):
            self.db.execute('INSERT INTO chapters VALUES (?,?,?,?,?)',('main',1,text,dump(data),digest(text)))
            for number in (1,2):self.db.execute('INSERT INTO plans VALUES (?,?,?)',('main',number,dump(plan(number))))
            append_events(self.db,'main',1,text,events,state);put(self.db,'pilot_approved',True)
    def receipt(self,task,preserve=True):
        if preserve:return result(task,{'cannot_compact':True,'reason':'本批每条认知和来源均须保留。'})
        work=json.loads((self.path/task['input_path']).read_text());p=work['payload']
        return result(task,{'coverage':{k:p[k] for k in ('document','document_hash','start','end')},
                            'notes':[],'omission_reason':'工程夹具的显式取舍。'})
    def submit(self,task,receipt):
        path=self.path/task['output_path'];path.write_text(dump(receipt))
        return self.cli('submit','--result',str(path),'--brief','--continue-preparation')
    def stream(self,task):
        stages=[]
        with patch('novel._auto_render_html',side_effect=AssertionError('preparation rendered book')):
            for _ in range(100):
                stages.append(task['stage']);out=self.submit(task,self.receipt(task))
                if out['action']!='continue_preparation':return out,stages
                self.assertEqual(out['bundle'],task['preparation_session']['bundle']);task=out['task']
        self.fail('preparation did not stop')

    def test_chapter_fourteen_distribution_delivers_every_record_without_extraction(self):
        base=distribution();out=contextflow.fit(self.db,base)
        self.assertEqual(out['stage'],'draft');self.assertNotIn('context_notes',out)
        self.assertEqual(out['context_delivery']['mode'],'exact-records/1')
        for field in ('state','character_context','design_context','selected'):
            self.assertEqual(out[field],base[field])
            source=next(s for s in out['context_delivery']['sources'] if s['field']==field)
            self.assertTrue(digest(dump(base[field])).startswith(source['hash']));self.assertNotIn('version',source)
        self.assertEqual(sum(s['units'] for s in out['context_delivery']['sources']),58)
        review=copy.deepcopy(base);review['stage']='chapter_review';review.pop('design_context');review['selected']=review['selected'][:20]
        out=contextflow.fit(self.db,review);self.assertEqual(out['stage'],'chapter_review')
        self.assertEqual(out['state'],base['state']);self.assertEqual(out['selected'],review['selected'])
        self.assertEqual(sum(s['units'] for s in out['context_delivery']['sources']),52)

    def test_source_changes_and_branch_changes_never_reuse_interpretations(self):
        base=distribution();first=contextflow.fit(self.db,base)
        changed=copy.deepcopy(base);changed['state']['knowledge']['0']['stance']='knows';changed['branch']='revision'
        second=contextflow.fit(self.db,changed)
        a=first['context_delivery']['sources'][0];b=second['context_delivery']['sources'][0]
        self.assertNotEqual(a['source_id'],b['source_id']);self.assertNotEqual(a['hash'],b['hash'])
        self.assertEqual(second['state'],changed['state']);self.assertNotIn('context_notes',second)

    def test_frozen_preparation_survives_policy_change(self):
        put(self.db,'preparation_policy','semantic');base=distribution();old=contextflow.fit(self.db,base)
        put(self.db,'preparation_policy','adaptive');same=contextflow.fit(self.db,base)
        self.assertEqual(old,same);self.assertEqual(same['stage'],'context_scan')

    def test_direct_review_still_withholds_context_until_first_read(self):
        self.grow(32);before=digest(replay(self.db,'main'))
        draft=self.cli('advance')['task'];self.assertEqual(draft['stage'],'draft')
        work=json.loads((self.path/draft['input_path']).read_text());self.assertIn('context_delivery',work)
        self.assertEqual(work['state']['knowledge']['hero:k0']['stance'],'believes')
        respond(self.db,self.path,draft)
        review=self.cli('advance','--after',draft['task_id'])['task']
        self.assertEqual(review['stage'],'chapter_review');self.assertEqual(review['delivery_phase'],'first_read')
        self.assertFalse((self.path/review['input_path']).exists())
        with self.assertRaises(Error):self.cli('recall','--task',review['task_id'],'--contract')
        self.assertEqual(digest(replay(self.db,'main')),before);self.assertEqual(head(self.db,'main'),1)
        respond(self.db,self.path,review);self.assertEqual(head(self.db,'main'),2)

    def test_large_batch_streams_without_rendering_and_stops_before_draft(self):
        self.grow(64);before=digest(replay(self.db,'main'));task=self.cli('advance')['task']
        self.assertEqual(task['stage'],'context_scan')
        self.assertIn('--continue-preparation',shlex.split(task['commands']['submit']))
        out,stages=self.stream(task)
        self.assertGreater(len(stages),1);self.assertEqual(out['action'],'preparation_done')
        self.assertFalse(self.db.execute("SELECT 1 FROM tasks WHERE state='pending'").fetchone())
        self.assertEqual(digest(replay(self.db,'main')),before)
        draft=self.cli('advance','--after',out['task_id'])['task'];self.assertEqual(draft['stage'],'draft')
        work=json.loads((self.path/draft['input_path']).read_text())
        self.assertIn('她误信钥匙在东门',dump(work['context_notes']))
        self.assertEqual(sum(len(n['notes']) for n in work['context_notes']),len(stages))

    def test_bad_coverage_does_not_submit_or_generate_a_next_page(self):
        self.grow(64);task=self.cli('advance')['task'];r=self.receipt(task,False);r['data']['coverage']['end']-=1
        with self.assertRaises(Error):self.submit(task,r)
        self.assertEqual(self.db.execute('SELECT count(*) FROM tasks').fetchone()[0],1)
        self.assertEqual(self.cli('advance')['task']['task_id'],task['task_id'])

    def test_bare_preparation_receipt_keeps_normalization_and_no_html(self):
        self.grow(64);task=self.cli('advance')['task'];data=self.receipt(task)['data']
        with patch('novel._auto_render_html',side_effect=AssertionError('preparation rendered book')):
            out=self.submit(task,data)
        self.assertEqual(out['action'],'continue_preparation')
        self.assertEqual(out['task_id'],task['task_id'])
        self.assertEqual(self.db.execute('SELECT state FROM tasks WHERE id=?',(task['task_id'],)).fetchone()[0],'done')

    def test_retry_restores_one_next_page_and_old_reply_cannot_advance(self):
        self.grow(64);task=self.cli('advance')['task'];r=self.receipt(task)
        first=self.submit(task,r);again=self.submit(task,r)
        self.assertEqual(first['task']['task_id'],again['task']['task_id'])
        self.assertEqual(first['task']['input_hash'],again['task']['input_hash'])
        next_task=first['task'];self.submit(next_task,self.receipt(next_task))
        before=self.db.execute('SELECT count(*) FROM tasks').fetchone()[0]
        old=self.submit(task,r);self.assertEqual(old['action'],'resync')
        self.assertEqual(self.db.execute('SELECT count(*) FROM tasks').fetchone()[0],before)

    def test_continuation_flag_cannot_submit_writer_or_legacy_task(self):
        task=self.cli('advance')['task'];r=result(task,{'chapters':[plan(1)]})
        with self.assertRaises(Error):self.submit(task,r)
        self.assertEqual(self.db.execute('SELECT state FROM tasks').fetchone()[0],'pending')
        # Reset the fixture to a large preparation and remove only its new marker.
        self.db.execute('DELETE FROM tasks');self.grow(64);task=self.cli('advance')['task']
        row=self.db.execute('SELECT input FROM tasks WHERE id=?',(task['task_id'],)).fetchone();work=json.loads(row[0])
        work.pop('preparation_session');self.db.execute('UPDATE tasks SET input=?,input_hash=? WHERE id=?',(dump(work),digest(work),task['task_id']))
        task=engine.next_task(self.db,self.path)
        with self.assertRaises(Error):self.submit(task,self.receipt(task))
        self.assertEqual(self.db.execute('SELECT state FROM tasks').fetchone()[0],'pending')

    def test_pause_and_changed_target_stop_material_worker(self):
        self.grow(64);task=self.cli('advance')['task'];r=self.receipt(task)
        engine.submit(self.db,r,brief=True)
        self.cli('pause','--reason','作者暂停');row=self.db.execute('SELECT * FROM tasks WHERE id=?',(task['task_id'],)).fetchone()
        self.assertEqual(taskio.continue_preparation(self.db,self.path,row)['action'],'paused')
        self.cli('resume')
        p=json.loads(self.db.execute('SELECT data FROM plans WHERE number=2').fetchone()[0]);p['goal']='核对另一条证词'
        self.db.execute('UPDATE plans SET data=? WHERE number=2',(dump(p),))
        changed=taskio.continue_preparation(self.db,self.path,row)
        self.assertEqual(changed['action'],'preparation_done')
        self.assertFalse(self.db.execute("SELECT 1 FROM tasks WHERE state='pending'").fetchone())

    def test_large_review_batch_stops_before_fresh_reviewer(self):
        self.grow(64);prepared,_=self.stream(self.cli('advance')['task'])
        draft=self.cli('advance','--after',prepared['task_id'])['task'];respond(self.db,self.path,draft)
        scan=self.cli('advance','--after',draft['task_id'])['task'];self.assertEqual(scan['stage'],'context_scan')
        finished,_=self.stream(scan);self.assertEqual(finished['action'],'preparation_done')
        self.assertFalse(self.db.execute("SELECT 1 FROM tasks WHERE state='pending'").fetchone())
        review=self.cli('advance','--after',finished['task_id'])['task']
        self.assertEqual(review['stage'],'chapter_review');self.assertEqual(review['delivery_phase'],'first_read')
        self.assertFalse((self.path/review['input_path']).exists());self.assertEqual(head(self.db,'main'),1)

    def test_many_small_blocks_still_prepare_and_never_drop_sources(self):
        base=distribution()
        for field in ('state','character_context','design_context'):
            base[field]={'records':[{'id':str(i),'text':'完整源记录'+str(i)} for i in range(40)]}
        out=contextflow.fit(self.db,base)
        self.assertEqual(out['stage'],'context_scan')
        bundle=get(self.db,contextflow.view_key(out['payload']['bundle']))
        self.assertEqual(sum(len(d['units']) for d in bundle['documents']),143)

    def test_streaming_reduction_inherits_every_scanned_source(self):
        self.grow(160);out,stages=self.stream(self.cli('advance')['task'])
        self.assertEqual(out['action'],'preparation_done');self.assertIn('context_reduce',stages)
        draft=self.cli('advance','--after',out['task_id'])['task'];work=json.loads((self.path/draft['input_path']).read_text())
        notes=[note for node in work['context_notes'] for note in node['notes']]
        self.assertEqual(len(notes),stages.count('context_scan'))
        self.assertIn('完整条件159',dump(notes));self.assertIn('她误信钥匙在东门',dump(notes))


if __name__=='__main__':unittest.main()
