import copy
import json
import tempfile
import unittest
import uuid
import subprocess
import sys
from pathlib import Path
from support import *
from novel_core import contextflow, characters
from novel_core.store import Error, digest, head, write_json
from novel_core.ledger import seed, append_events, replay

MARKER='她误信钥匙在东门'


def preparation(task,work,verbose=False):
    p=work['payload']
    if task['stage']=='context_scan':
        notes=[];text=p['fragment']
        if MARKER in text:
            notes=[{'text':'保留误信；不能替人物自动知道真相。','evidence':[{'document':p['document'],'start':p['start']+text.index(MARKER),'quote':MARKER}]}]
        elif verbose:
            notes=[{'text':'合成工程测试的来源覆盖说明。'*85,'evidence':[{'document':p['document'],'start':p['start'],'quote':text[:20]}]}]
        if verbose:
            for note in notes:note['text']+='工程审读附注。'*80
        return {'coverage':{k:p[k] for k in ('document','document_hash','start','end')},'notes':notes,
                'omission_reason':'合成测试已知这些记录不是本次钥匙认知约束；不是生产语义审稿。'}
    all_notes=[note for node in p['inputs'] for note in node['notes']]
    important=[note for note in all_notes if '误信' in note['text']]
    notes=important[:1] or all_notes[:1]
    outgoing={dump(e) for note in notes for e in note['evidence']}
    accounting=[]
    for node in p['inputs']:
        disposition='irrelevant' if not node['notes'] else ('kept' if all(any(dump(e) in outgoing for e in note['evidence']) for note in node['notes']) else 'duplicate')
        accounting.append({'input_id':node['id'],'disposition':disposition,'duplicate_of':0,'reason':'合成测试已知这些页是重复背景与相同认知，保留一个代表来源。'})
    return {'notes':notes,'omission_reason':'所有输入均为合成样本的重复背景。','accounting':accounting}


class ContextFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        c=contract();c['context_mode']='bounded';c['character_mode']='profiled'
        initialize(self.path,c);self.db=connect(self.path)
        put(self.db,'session_mode','fresh');put(self.db,'pilot_approved',True)

    def tearDown(self):self.db.close();self.tmp.cleanup()

    def submit(self,t,data):
        return engine.submit(self.db,result(t,data,session={'id':uuid.uuid4().hex,'origin':'empty'}))

    def grow(self,count=500):
        text=MARKER+'。周行逐项核对档案。'
        state=seed(get(self.db,'contract'));events=[]
        for i in range(count):
            claim=MARKER if i==0 else '无关的档案记录'+str(i)
            events.append({'op':'knowledge','entity':'hero','topic':'key' if i==0 else 'archive'+str(i),
                           'before':None,'stance':'believes' if i==0 else 'knows','claim':claim,
                           'source':'合成工程夹具','evidence':ev(text)})
        data={'volume':'v1','summary':'合成状态','events':events,'prose_hash':digest(text),
              'facts_review':{'verdict':'pass'},'reader_review':{'verdict':'pass'}}
        with transaction(self.db):
            self.db.execute('INSERT INTO chapters VALUES (?,?,?,?,?)',('main',1,text,dump(data),digest(text)))
            self.db.execute('INSERT INTO plans VALUES (?,?,?)',('main',1,dump(plan(1))))
            self.db.execute('INSERT INTO plans VALUES (?,?,?)',('main',2,dump(plan(2))))
            append_events(self.db,'main',1,text,events,state)
            profiles=json.loads((Path(__file__).resolve().parents[1]/'assets/characters.example.json').read_text())
            characters.save(self.db,'main',2,profiles,['hero','ally'],'fixture')
        return digest(replay(self.db,'main'))

    def ready(self,verbose=False):
        seen=[]
        for _ in range(200):
            task=engine.next_task(self.db,self.path);work=json.loads(rpath(task, 'input_path').read_text())
            self.assertLessEqual(len(rpath(task, 'input_path').read_text()),contextflow.limits(self.db)['input'])
            if task['stage'] not in ('context_scan','context_reduce'):return task,work,seen
            seen.append(task['stage']);self.submit(task,preparation(task,work,verbose))
        self.fail('Preparation did not converge')

    def test_growth_prepares_then_writes_without_changing_canon(self):
        before=self.grow();task,work,seen=self.ready()
        self.assertEqual(task['stage'],'draft');self.assertIn('context_scan',seen)
        self.assertIn(MARKER,dump(work['context_notes']))
        self.assertEqual(digest(replay(self.db,'main')),before)
        self.submit(task,{'prose':'周行记得，她误信钥匙在东门。'})
        next_task,next_work,_=self.ready()
        self.assertEqual(next_task['stage'],'facts')
        self.assertEqual(head(self.db,'main'),1)

    def test_reduction_coverage_and_source_chain(self):
        self.grow(1500);task,work,seen=self.ready(verbose=True)
        self.assertIn('context_reduce',seen);self.assertIn(MARKER,dump(work['context_notes']))
        self.assertEqual(task['stage'],'draft')

    def test_restart_duplicate_and_bad_coverage_are_atomic(self):
        before=self.grow();task=engine.next_task(self.db,self.path);work=json.loads(rpath(task, 'input_path').read_text())
        data=preparation(task,work);bad=copy.deepcopy(data);bad['coverage']['end']-=1
        with self.assertRaises(Error):self.submit(task,bad)
        self.db.close();self.db=connect(self.path)
        same=engine.next_task(self.db,self.path);self.assertEqual(task['input_hash'],same['input_hash'])
        receipt=result(task,data,session={'id':uuid.uuid4().hex,'origin':'empty'})
        engine.submit(self.db,receipt);self.assertTrue(engine.submit(self.db,receipt)['idempotent'])
        self.assertEqual(digest(replay(self.db,'main')),before)

    def test_forged_note_evidence_is_rejected(self):
        self.grow();task=engine.next_task(self.db,self.path);work=json.loads(rpath(task, 'input_path').read_text())
        data=preparation(task,work);data['notes']=[{'text':'假的来源','evidence':[{'document':work['payload']['document'],'start':0,'quote':'不存在的内容'}]}]
        with self.assertRaises(Error):self.submit(task,data)
        self.assertEqual(engine.next_task(self.db,self.path)['task_id'],task['task_id'])

    def test_cannot_compact_stops_with_persistent_reason(self):
        self.grow();task=engine.next_task(self.db,self.path)
        self.submit(task,{'cannot_compact':True,'reason':'当前任务确实需要全部原始条件同时可见，无法在预算内表达。'})
        self.assertEqual(get(self.db,'status'),'paused')
        self.assertEqual(get(self.db,'pause_reason')['code'],'context_unresolved')
        engine.resume(self.db)
        with self.assertRaises(Error) as exc:engine.next_task(self.db,self.path)
        self.assertEqual(exc.exception.code,'context_unresolved')

    def test_serial_checkpoint_requires_new_session_id(self):
        put(self.db,'session_mode','serial');put(self.db,'context_session_used',48000)
        task=engine.next_task(self.db,self.path);self.assertEqual(task['stage'],'await_session')
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM tasks WHERE state='pending'").fetchone()[0],0)
        with transaction(self.db):contextflow.start_session(self.db,'new-host-session')
        task=engine.next_task(self.db,self.path);self.assertEqual(task['stage'],'plan')
        with self.assertRaises(Error):contextflow.start_session(self.db,'new-host-session')

    def test_retrieval_is_bounded_and_cumulative(self):
        task=engine.next_task(self.db,self.path)
        package=len(rpath(task, 'input_path').read_text())
        with transaction(self.db):
            one=contextflow.bounded_read(self.db,{'state':'很长的内容'*6000},task['task_id'])
        self.assertLessEqual(len(contextflow.wire(one)),contextflow.limits(self.db)['retrieval'])
        self.assertIsNotNone(one['next_start'])
        used=get(self.db,'context_reads:'+task['task_id']);self.assertGreater(used,0)
        # Cumulative raw reading is bounded by the package it serves, not by a fixed quota.
        put(self.db,'context_reads:'+task['task_id'],max(2048,package))
        with self.assertRaises(Error):contextflow.bounded_read(self.db,{},task['task_id'])

    def test_read_allowance_is_proportional_to_its_package(self):
        task=engine.next_task(self.db,self.path)
        with transaction(self.db):
            self.db.execute("UPDATE tasks SET input=? WHERE id=?",(dump({'pad':'x'*20000}),task['task_id']))
            put(self.db,'context_reads:'+task['task_id'],10000)
            one=contextflow.bounded_read(self.db,{'state':'内容'},task['task_id'])
        # 10000 chars already read would exhaust a fixed 1/6 quota; the proportional rule still
        # serves small targeted reads because the package itself is large.
        self.assertNotIn('format',one)

    def test_context_more_keeps_same_creative_stage_and_no_fact_mutation(self):
        before=self.grow();task,work,_=self.ready()
        self.submit(task,{'context_more':[{'chapter':1}],'reason':'补核她误信的原文语境'})
        again,new_work,_=self.ready()
        self.assertEqual(again['stage'],'draft');self.assertNotEqual(task['task_id'],again['task_id'])
        self.assertEqual(digest(replay(self.db,'main')),before)
        self.assertEqual(get(self.db,'context_extra:'+work['context_identity'])['rounds'],1)

    def test_oversized_irreducible_core_has_actionable_error(self):
        c=get(self.db,'contract');c['red_lines']=['约束'*20000];put(self.db,'contract',c)
        with self.assertRaises(Error) as exc:engine.next_task(self.db,self.path)
        self.assertEqual(exc.exception.code,'context_capacity')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],0)

    def test_actual_input_bytes_match_character_meter(self):
        task=engine.next_task(self.db,self.path);raw=rpath(task, 'input_path').read_text();work=json.loads(raw)
        self.assertEqual(len(raw),work['chars']);self.assertEqual(raw,contextflow.wire(work))
        self.assertLessEqual(len(raw),work['context_capacity']['package_chars'])
        self.assertEqual(work['context_capacity']['capacity_chars'],48000)
        self.assertTrue(work['context_capacity']['rules'])

    def test_receipt_is_one_unit_that_fits_the_declared_window(self):
        task=engine.next_task(self.db,self.path)
        window=48000-len(rpath(task, 'input_path').read_text())-contextflow.limits(self.db)['overhead']
        with self.assertRaises(Error) as exc:self.submit(task,{'huge':'字'*window})
        self.assertEqual(exc.exception.code,'context_output')
        self.assertEqual(engine.next_task(self.db,self.path)['task_id'],task['task_id'])
        # A receipt larger than the old fixed quarter but inside the declared window is no
        # longer rejected by a quota gate; only unit-level validation may still refuse it.
        task=engine.next_task(self.db,self.path)
        with self.assertRaises(Error) as exc:self.submit(task,{'huge':'字'*13000})
        self.assertNotEqual(exc.exception.code,'context_output')

    def test_failed_receipt_consumes_serial_budget_without_advancing_task(self):
        put(self.db,'session_mode','serial')
        task=engine.next_task(self.db,self.path);used=get(self.db,'context_session_used')
        bad=result(task,{'wrong':'missing chapter plans'})
        with self.assertRaises(Error):engine.submit(self.db,bad)
        self.assertEqual(get(self.db,'context_session_used'),used+len(contextflow.wire(bad)))
        self.assertEqual(self.db.execute('SELECT state FROM tasks WHERE id=?',(task['task_id'],)).fetchone()[0],'pending')
        self.assertEqual(head(self.db,'main'),0)

    def test_failed_fresh_attempt_cannot_reuse_host_session(self):
        task=engine.next_task(self.db,self.path)
        bad=result(task,{},session={'id':'real-host-session','origin':'empty'})
        with self.assertRaises(Error):engine.submit(self.db,bad)
        with self.assertRaises(Error) as exc:engine.submit(self.db,bad)
        self.assertEqual(exc.exception.code,'session_reused')

    def test_cli_supplement_automatically_charges_pending_task(self):
        put(self.db,'session_mode','serial');task=engine.next_task(self.db,self.path)
        used=get(self.db,'context_session_used')
        cli=Path(__file__).resolve().parents[1]/'scripts/novel.py'
        call=subprocess.run([sys.executable,str(cli),'recall','--project',str(self.path),'--state'],capture_output=True,text=True)
        self.assertEqual(call.returncode,0,call.stderr)
        self.assertEqual(get(self.db,'context_reads:'+task['task_id']),len(call.stdout))
        self.assertEqual(get(self.db,'context_session_used'),used+len(call.stdout))
        # Historical task reads still consume this session's available context.
        self.db.execute("UPDATE tasks SET state='done' WHERE id=?",(task['task_id'],))
        before=get(self.db,'context_session_used')
        contextflow.bounded_read(self.db,{'old':'source'},task['task_id'])
        self.assertGreater(get(self.db,'context_session_used'),before)

    def test_reducer_cannot_claim_kept_without_retaining_source(self):
        before=self.grow(1500)
        for _ in range(100):
            task=engine.next_task(self.db,self.path);work=json.loads(rpath(task, 'input_path').read_text())
            if task['stage']=='context_reduce':break
            self.submit(task,preparation(task,work,verbose=True))
        self.assertEqual(task['stage'],'context_reduce')
        bad={'notes':[],'omission_reason':'伪称已经保留','accounting':[{'input_id':n['id'],'disposition':'kept','reason':'伪称保留'} for n in work['payload']['inputs']]}
        with self.assertRaises(Error) as exc:self.submit(task,bad)
        self.assertEqual(exc.exception.code,'context_coverage')
        self.assertEqual(engine.next_task(self.db,self.path)['task_id'],task['task_id'])
        self.assertEqual(digest(replay(self.db,'main')),before)

    def test_synthesis_receives_actual_segment_review_sources(self):
        from novel_core.memory import pack
        data={'first':1,'through':10,'verdict':'pass','checks':[{'finding':'蓝绳结显示人物信任发生变化','evidence':{'chapter':7,'start':0,'quote':'蓝绳结'}}]}
        self.db.execute('INSERT INTO reviews VALUES (?,?,?,?,?)',('main','book_chunk',10,'fixture-source-hash',dump(data)))
        work=pack(self.db,'main',1,'book_review',{'scope':'book','first':1,'through':10})
        material=work['synthesis_materials'][0]
        self.assertEqual((material['chapter'],material['input_hash']),(10,'fixture-source-hash'))
        # Provenance and verdicts survive; bulk evidence quotes are re-derivable via recall.
        slim=material['review']
        self.assertEqual((slim['first'],slim['through'],slim['verdict']),(1,10,'pass'))
        self.assertEqual(slim['checks'][0]['finding'],'蓝绳结显示人物信任发生变化')
        self.assertNotIn('quote',dump(slim))

    def test_oversized_legacy_pending_is_replanned_with_stale_output_rejected(self):
        self.grow();put(self.db,'context_mode','legacy');put(self.db,'context_chars',500000)
        old=engine.next_task(self.db,self.path)
        put(self.db,'context_mode','bounded');put(self.db,'context_chars',48000)
        new=engine.next_task(self.db,self.path)
        self.assertEqual(new['stage'],'context_scan');self.assertNotEqual(old['task_id'],new['task_id'])
        with self.assertRaises(Error) as exc:self.submit(old,{'prose':'过期输出'})
        self.assertEqual(exc.exception.code,'stale_task')
        self.assertEqual(head(self.db,'main'),1)

    def test_malformed_driver_output_and_retry_input_consume_window(self):
        from novel_core.runner import run
        put(self.db,'session_mode','serial')
        task=engine.next_task(self.db,self.path);before=get(self.db,'context_session_used')
        driver=self.path/'bad_driver.py'
        driver.write_text('import os\nfrom pathlib import Path\nPath(os.environ["NOVEL_RESULT_FILE"]).write_text("malformed JSON")\n')
        outcome=run(self.db,self.path,{'argv':[sys.executable,str(driver)],'session_mode':'serial','max_retries':5,'timeout':5},max_tasks=1)
        self.assertEqual(outcome.get('stage'),'await_session')
        self.assertGreaterEqual(get(self.db,'context_session_used'),before+contextflow.limits(self.db)['output'])
        self.assertEqual(head(self.db,'main'),0)
        self.assertEqual(self.db.execute('SELECT state FROM tasks WHERE id=?',(task['task_id'],)).fetchone()[0],'pending')

if __name__=='__main__':unittest.main()
