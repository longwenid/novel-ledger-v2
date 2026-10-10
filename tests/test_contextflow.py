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


def preparation(project,task,work,verbose=False):
    p=work['payload']
    if task['stage']=='context_scan' and 'pages' in p:
        receipts=[preparation(project,task,{'payload':page},verbose) for page in p['pages']]
        return {'coverage':[r['coverage'] for r in receipts], 'notes':[n for r in receipts for n in r['notes']],
                'omission_reason':'合成工程夹具已覆盖所有交付页。'}
    if task['stage']=='context_scan':
        notes=[];text=(Path(project)/p['prose_path']).read_text(encoding='utf-8')
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
    notes=copy.deepcopy(important[:1] or all_notes[:1])
    if notes:
        # Synthetic duplicate decisions must preserve every original source,
        # just as production reductions do; one representative quote is not
        # enough to retain a multi-source causal conclusion.
        by_source={dump(e):e for note in all_notes for e in note['evidence']}
        notes[0]['evidence']=list(by_source.values())
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
        put(self.db,'pilot_approved',True)

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
            profiles=json.loads((Path(__file__).resolve().parents[1]/'assets/characters.example.json').read_text(encoding='utf-8'))
            characters.save(self.db,'main',2,profiles,['hero','ally'],'fixture')
        return digest(replay(self.db,'main'))

    def ready(self,verbose=False):
        seen=[]
        for _ in range(200):
            task=engine.next_task(self.db,self.path);work=json.loads(rpath(task, 'input_path').read_text(encoding='utf-8'))
            if task['stage'] not in ('context_scan','context_reduce'):
                self.assertIn('[观测]',work['guide'])
                return task,work,seen
            seen.append(task['stage']);self.submit(task,preparation(self.path,task,work,verbose))
        self.fail('Preparation did not converge')

    def test_growth_prepares_then_writes_without_changing_canon(self):
        before=self.grow();task,work,seen=self.ready()
        self.assertEqual(task['stage'],'draft');self.assertIn('context_scan',seen)
        self.assertIn(MARKER,dump(work['context_notes']))
        self.assertEqual(digest(replay(self.db,'main')),before)
        self.submit(task,{'prose':'周行记得，她误信钥匙在东门。'})
        next_task,next_work,_=self.ready()
        self.assertEqual(next_task['stage'],'chapter_review')
        self.assertEqual(head(self.db,'main'),1)

    def test_reduction_coverage_and_source_chain(self):
        self.grow(1500);task,work,seen=self.ready(verbose=True)
        self.assertIn('context_reduce',seen);self.assertIn(MARKER,dump(work['context_notes']))
        self.assertEqual(task['stage'],'draft')

    def test_restart_duplicate_and_bad_coverage_are_atomic(self):
        before=self.grow();task=engine.next_task(self.db,self.path);work=json.loads(rpath(task, 'input_path').read_text(encoding='utf-8'))
        data=preparation(self.path,task,work);bad=copy.deepcopy(data);bad['coverage']['end']-=1
        with self.assertRaises(Error):self.submit(task,bad)
        self.db.close();self.db=connect(self.path)
        same=engine.next_task(self.db,self.path);self.assertEqual(task['input_hash'],same['input_hash'])
        receipt=result(task,data,session={'id':uuid.uuid4().hex,'origin':'empty'})
        engine.submit(self.db,receipt);self.assertTrue(engine.submit(self.db,receipt)['idempotent'])
        self.assertEqual(digest(replay(self.db,'main')),before)

    def test_forged_note_evidence_is_rejected(self):
        self.grow();task=engine.next_task(self.db,self.path);work=json.loads(rpath(task, 'input_path').read_text(encoding='utf-8'))
        data=preparation(self.path,task,work);data['notes']=[{'text':'假的来源','evidence':[{'document':work['payload']['document'],'start':0,'quote':'不存在的内容'}]}]
        with self.assertRaises(Error):self.submit(task,data)
        self.assertEqual(engine.next_task(self.db,self.path)['task_id'],task['task_id'])

    def test_cannot_compact_retains_complete_source_without_pausing(self):
        self.grow();task=engine.next_task(self.db,self.path)
        work=json.loads(rpath(task).read_text(encoding='utf-8'))
        self.submit(task,{'cannot_compact':True,'reason':'当前任务确实需要全部原始条件同时可见，无法在预算内表达。'})
        self.assertEqual(get(self.db,'status'),'ready')
        bundle=get(self.db,contextflow.view_key(work['payload']['bundle']))
        node=bundle['node_archive'][task['task_id']]
        self.assertEqual(node['notes'][0]['evidence'][0]['quote'],pack_prose(self.path,work))
        self.assertEqual(node['retained_source']['end'],work['payload']['end'])
        self.assertNotEqual(engine.next_task(self.db,self.path)['task_id'],task['task_id'])

    def test_failed_receipt_consumes_serial_budget_without_advancing_task(self):
        task=engine.next_task(self.db,self.path)
        bad=result(task,{'wrong':'missing chapter plans'})
        with self.assertRaises(Error):engine.submit(self.db,bad)
        self.assertEqual(self.db.execute('SELECT state FROM tasks WHERE id=?',(task['task_id'],)).fetchone()[0],'pending')
        self.assertEqual(head(self.db,'main'),0)

    def test_cli_supplement_automatically_charges_pending_task(self):
        task=engine.next_task(self.db,self.path)
        cli=Path(__file__).resolve().parents[1]/'scripts/novel.py'
        call=subprocess.run([sys.executable,str(cli),'recall','--project',str(self.path),'--state','--entity','hero'],capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(call.returncode,0,call.stderr)
        self.assertEqual(get(self.db,'context_reads:'+task['task_id']),len(call.stdout))

    def test_reducer_cannot_claim_kept_without_retaining_source(self):
        before=self.grow(1500)
        for _ in range(100):
            task=engine.next_task(self.db,self.path);work=json.loads(rpath(task, 'input_path').read_text(encoding='utf-8'))
            if task['stage']=='context_reduce':break
            self.submit(task,preparation(self.path,task,work,verbose=True))
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

    def test_large_legacy_pending_remains_valid_after_configuration_change(self):
        self.grow();put(self.db,'context_mode','legacy');put(self.db,'context_chars',500000)
        old=engine.next_task(self.db,self.path)
        put(self.db,'context_mode','bounded');put(self.db,'context_chars',48000)
        new=engine.next_task(self.db,self.path)
        self.assertEqual(new['task_id'],old['task_id'])
        self.submit(old,{'prose':'完整任务仍可提交。'})
        self.assertEqual(engine.next_task(self.db,self.path)['stage'],'context_scan')
        self.assertEqual(head(self.db,'main'),1)

if __name__=='__main__':unittest.main()
