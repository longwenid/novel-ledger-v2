"""Lossless transport, original receipt billing and independent review gates."""
import copy
import json
import os
import subprocess
import shlex
import sys
import unittest
from pathlib import Path
import test_targeted_repairs as fixtures
from novel_core import engine,reviewflow
from novel_core.store import dump,digest,get,Error,head
from novel_core.wire import compact_response,expand_evidence,expand_receipt
from novel_core.taskio import shared_delivery


def catalog_receipt(data):
    out=copy.deepcopy(data);catalog={};names={}
    def visit(node):
        if isinstance(node,dict):
            if set(node)=={'chapter','start','quote'}:
                signature=dump(node)
                if signature not in names:
                    name='e'+str(len(names)+1);names[signature]=name;catalog[name]=copy.deepcopy(node)
                return {'ref':names[signature]}
            return {k:visit(v) for k,v in node.items()}
        if isinstance(node,list):return [visit(v) for v in node]
        return node
    out=visit(out);out['evidence_catalog']=catalog
    return out


def rebuild_parts(project,ref):
    if 'parts' not in ref:                      # whole-field file is the equivalent read
        return json.loads((Path(project)/ref['path']).read_text(encoding='utf-8'))
    result={}
    for part in ref['parts']:
        value=json.loads((Path(project)/part['path']).read_text(encoding='utf-8'))
        if not part['pointer']:result=value;continue
        cursor=result
        for key in part['pointer'][:-1]:cursor=cursor.setdefault(key,{})
        cursor[part['pointer'][-1]]=value
    return result


class ContextIOTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.TargetedRepairTests(methodName='runTest');self.f.setUp()
        self.path=self.f.path;self.db=self.f.db
    def tearDown(self):self.f.tearDown()

    def test_catalog_preserves_review_and_numeric_correction_exactly(self):
        t,base,corrected,data=self.f.numeric_task();wire=catalog_receipt(data)
        self.assertEqual(expand_evidence(wire,t['stage']),data)
        self.assertLess(len(dump(wire)),len(dump(data)))
        before=self.f.snapshot();proof=copy.deepcopy(reviewflow.record(self.db,t['task_id']))
        self.f.cli('preflight','--result',self.f.save(t,wire),'--compact')
        self.assertEqual(self.f.snapshot(),before)
        out=self.f.submit(t,wire)
        stored=json.loads(self.db.execute('SELECT receipt FROM tasks WHERE id=?',(t['task_id'],)).fetchone()[0])
        self.assertEqual(stored['data'],data)
        self.assertEqual(get(self.db,'wire_receipt:'+t['task_id']),self.f.receipt(t,wire))
        self.assertEqual(reviewflow.record(self.db,t['task_id']),proof)
        self.assertTrue(engine.submit(self.db,self.f.receipt(t,wire))['idempotent'])
        self.assertEqual(head(self.db,'main'),2);self.assertTrue(engine.audit(self.db)['ok'])

    def test_catalog_rejects_unknown_or_mixed_refs_and_invalid_real_evidence(self):
        t,base,corrected,data=self.f.numeric_task();before=self.f.snapshot()
        for mode in ('missing','mixed','fake','number'):
            wire=catalog_receipt(data);name=next(iter(wire['evidence_catalog']))
            if mode=='missing':wire['facts']['checks'][0]['evidence']=[{'ref':'missing'}]
            if mode=='mixed':wire['facts']['checks'][0]['evidence']=[{'ref':name,'quote':'override'}]
            if mode=='fake':wire['evidence_catalog'][name]['quote']='正文中没有这段文字'
            if mode=='number':wire['evidence_catalog'][name]['start']='0'
            with self.subTest(mode=mode),self.assertRaises(Error):engine.submit(self.db,self.f.receipt(t,wire),preview=True)
            self.assertEqual(self.f.snapshot(),before)

    def test_catalog_never_replaces_arbitrary_ledger_values(self):
        proof={'chapter':2,'start':0,'quote':'正文'}
        data={'evidence_catalog':{'e':proof},'candidate_events':[{'op':'state','before':None,
              'after':{'evidence':{'ref':'e'}},'evidence':{'ref':'e'}}]}
        expanded=expand_evidence(data,'draft')
        self.assertEqual(expanded['candidate_events'][0]['after'],data['candidate_events'][0]['after'])
        self.assertEqual(expanded['candidate_events'][0]['evidence'],proof)

    def test_compact_handoff_is_recoverable_and_keeps_first_read_sealed(self):
        raw=self.f.cli('advance');short=compact_response(self.path,raw)
        self.assertLess(len(dump(short)),len(dump(raw)))
        t=short['task'];full=json.loads((self.path/t['task_path']).read_text(encoding='utf-8'))
        self.assertEqual(t['task_id'],full['task_id']);self.assertEqual(t['input_hash'],full['input_hash'])
        self.assertIn('--compact',full['commands']['submit'])
        out=self.f.writer_batch(full)
        short=self.f.cli('advance','--after',out['task_id'],'--compact');t=short['task']
        full=json.loads((self.path/t['task_path']).read_text(encoding='utf-8'))
        self.assertEqual(full['delivery_phase'],'first_read');self.assertFalse((self.path/full['input_path']).exists())
        self.assertNotIn('numeric_correction',full);self.assertNotIn('template_path',full)
        with self.assertRaises(Error):self.f.cli('recall','--task',t['task_id'],'--contract')

    def test_real_cli_compact_flag_and_full_status_preserve_progress(self):
        script=Path(__file__).resolve().parents[1]/'scripts'/'novel.py'
        proc=subprocess.run([sys.executable,str(script),'advance','--project',str(self.path),'--compact'],
                            capture_output=True,text=True,encoding='utf-8',check=True)
        short=json.loads(proc.stdout);full=self.f.cli('advance')
        self.assertEqual(short['task']['task_id'],full['task']['task_id'])
        self.assertEqual(short['status']['work_committed'],full['status']['work_committed'])
        self.assertLess(len(proc.stdout),len(dump(full)))
        self.assertIn('recovery',self.f.cli('status'));self.assertNotIn('recovery',self.f.cli('status','--compact'))

    def test_generated_compact_submit_can_continue_using_full_task_file(self):
        full=self.f.cli('advance')['task'];self.f.save(full,self.f.draft(full))
        argv=shlex.split(full['commands']['submit'])
        if os.name=='nt':argv[0]=sys.executable  # generated envelopes say python3; Windows hosts lack it
        proc=subprocess.run(argv,capture_output=True,text=True,encoding='utf-8',check=True)
        out=json.loads(proc.stdout);self.assertEqual(out['action'],'chapter_written')
        self.assertEqual(out['response_view'],'compact/1');self.assertNotIn('task',out)
        following=self.f.cli('advance','--after',out['task_id'])['task']
        full_next=json.loads((self.path/following['task_path']).read_text(encoding='utf-8'))
        self.assertEqual(full_next['stage'],'chapter_review');self.assertNotIn('chapter_session',full_next)
        self.assertIn('--compact',full_next['commands']['submit'])
        self.assertEqual(head(self.db,'main'),1)

    def test_compact_settlement_preserves_all_balances_without_repeated_steps(self):
        t,base,corrected,data=self.f.numeric_task();file=self.f.save(t,data)
        before=self.f.snapshot();raw=self.f.cli('preflight','--result',file)
        short=self.f.cli('preflight','--result',file,'--compact')
        for key in ('resources','clock','clock_threads','before_state_hash','after_state_hash'):
            self.assertEqual(short['settlement'][key],raw['settlement'][key])
        self.assertEqual(short['settlement']['step_count'],len(raw['settlement']['steps']))
        self.assertEqual(self.f.snapshot(),before)

    def test_profile_parts_reconstruct_exact_values_and_reuse_unchanged_profiles(self):
        profile={'hash':'profile-v1','data':{'name':'宋禾','story':'完整经历。'*300}}
        full={'chapter_session':{'role':'writer'},'character_context':{'status':'design',
              'profiles':{'hero':profile,'guard':{'name':'门房','story':'短档案。'}},'developments':{'hero':[]},'validity':{'hero':True}},
              'design_context':{'status':'proposal_not_canon','world':{'data':'世界规则。'*300},'volume':{'data':'卷任务'}}}
        view=shared_delivery(self.path,full)
        import re
        refs=[view['shared_materials'][f] for f in ('character_context','design_context')]
        refs=refs+[p for r in refs for p in r.get('parts',[])]
        for ref in refs:self.assertRegex(ref['path'],r'work/shared/[0-9a-f]{16}\.json$')
        for field in ('character_context','design_context'):
            self.assertEqual(rebuild_parts(self.path,view['shared_materials'][field]),full[field])
        pointers=[tuple(p['pointer']) for p in view['shared_materials']['character_context']['parts']]
        self.assertIn(('profiles','hero'),pointers)
        self.assertNotIn(('profiles','guard'),pointers)  # debris-sized entries stay in the base part
        before=view['shared_materials']['character_context'];changed=copy.deepcopy(full)
        changed['character_context']['validity']['hero']=False;changed['character_context']['developments']['hero']=[{'change':'见证选择'}]
        after=shared_delivery(self.path,changed)['shared_materials']['character_context']
        a=next(p for p in before['parts'] if p['pointer']==['profiles','hero'])
        b=next(p for p in after['parts'] if p['pointer']==['profiles','hero'])
        self.assertEqual(a,b);self.assertNotEqual(before['hash'],after['hash'])
        (self.path/a['path']).write_bytes(b'broken');restored=shared_delivery(self.path,full)
        self.assertEqual(rebuild_parts(self.path,restored['shared_materials']['character_context']),full['character_context'])

    def test_shared_prefix_name_heals_foreign_bytes_in_place(self):
        from novel_core.store import digest
        full={'chapter_session':{'role':'writer'},'contract':{'title':'自愈用契约'}}
        prefix=digest(full['contract'])[:16]
        occupied=self.path/'work/shared';occupied.mkdir(parents=True,exist_ok=True)
        (occupied/(prefix+'.json')).write_text('{"别的":"过期或损坏字节"}',encoding='utf-8')
        view=shared_delivery(self.path,full)
        ref=view['shared_materials']['contract']
        self.assertEqual(ref['path'],'work/shared/'+prefix+'.json')
        self.assertEqual(json.loads((self.path/ref['path']).read_text(encoding='utf-8')),full['contract'])

    def test_frozen_first_read_reference_roundtrips_and_commits_numeric_patch(self):
        t,base,corrected,data=self.f.numeric_task();wire=catalog_receipt(data)
        sealed=copy.deepcopy(reviewflow.record(self.db,t['task_id']))
        wire['reader'].pop('first_read');wire['reader']['first_read_ref']=sealed['hash']
        row=self.db.execute('SELECT * FROM tasks WHERE id=?',(t['task_id'],)).fetchone()
        self.assertEqual(expand_receipt(self.db,row,json.loads(row['input']),wire),data)
        template=json.loads((self.path/t['template_path']).read_text(encoding='utf-8'))
        self.assertEqual(template['data']['reader']['first_read_ref'],sealed['hash'])
        self.assertEqual(json.loads((self.path/t['first_read_receipt_path']).read_text(encoding='utf-8')),sealed['receipt'])
        (self.path/t['first_read_receipt_path']).write_text('corrupt', encoding='utf-8')
        restored=engine.task_files(self.db,self.path,dict(row))
        self.assertEqual(json.loads((self.path/restored['first_read_receipt_path']).read_text(encoding='utf-8')),sealed['receipt'])
        before=self.f.snapshot();self.f.cli('preflight','--result',self.f.save(t,wire),'--compact')
        self.assertEqual(self.f.snapshot(),before)
        self.f.submit(t,wire)
        stored=json.loads(self.db.execute('SELECT receipt FROM tasks WHERE id=?',(t['task_id'],)).fetchone()[0])
        self.assertEqual(stored['data'],data)
        self.assertEqual(reviewflow.record(self.db,t['task_id']),sealed)
        self.assertEqual(get(self.db,'wire_receipt:'+t['task_id']),self.f.receipt(t,wire))
        self.assertTrue(engine.submit(self.db,self.f.receipt(t,wire))['idempotent'])
        self.assertTrue(engine.audit(self.db)['ok'])

    def test_first_read_reference_never_fills_judgments_or_overrides_seal(self):
        t,base,corrected,data=self.f.numeric_task();sealed=reviewflow.record(self.db,t['task_id'])
        before=self.f.snapshot()
        for mode in ('unknown','other_task','mixed','missing_check','missing_obligation'):
            wire=copy.deepcopy(data);wire['reader'].pop('first_read');wire['reader']['first_read_ref']=sealed['hash']
            if mode=='unknown':wire['reader']['first_read_ref']='missing'
            if mode=='other_task':wire['reader']['first_read_ref']=digest({**sealed['receipt'],'task_id':'another'})
            if mode=='mixed':wire['reader']['first_read']=sealed['receipt']['first_read']
            if mode=='missing_check':wire['reader']['checks'].pop()
            if mode=='missing_obligation':wire['reader']['obligations'].pop()
            with self.subTest(mode=mode),self.assertRaises(Error):engine.submit(self.db,self.f.receipt(t,wire),preview=True)
            self.assertEqual(self.f.snapshot(),before)
        # Missing/corrupt persisted records cannot authorize a hash shortcut.
        from novel_core.store import put
        wrong_input=copy.deepcopy(sealed['receipt']);wrong_input['input_hash']='stale'
        wrong_prose=copy.deepcopy(sealed['receipt']);wrong_prose['prose_hash']='stale'
        for value,code in ((None,'first_read_required'),({'receipt':sealed['receipt'],'hash':'bad'},'first_read_integrity'),
                           ({'receipt':wrong_input,'hash':digest(wrong_input)},'first_read_version'),
                           ({'receipt':wrong_prose,'hash':digest(wrong_prose)},'first_read_version')):
            put(self.db,reviewflow.key(t['task_id']),value);changed=self.f.snapshot()
            wire=copy.deepcopy(data);wire['reader'].pop('first_read')
            wire['reader']['first_read_ref']=value['hash'] if isinstance(value,dict) else sealed['hash']
            with self.subTest(code=code),self.assertRaises(Error) as cm:engine.submit(self.db,self.f.receipt(t,wire),preview=True)
            self.assertEqual(cm.exception.code,code);self.assertEqual(self.f.snapshot(),changed)
        put(self.db,reviewflow.key(t['task_id']),sealed)

    def test_state_and_continuity_reuse_complete_collections_without_losing_fact_values(self):
        full={'chapter_session':{'role':'writer'},'state':{'clock':2,'entities':{'hero':{'name':'周行'}},
            'facts':{'hero:memory':{'evidence':{'ref':'actual fact value'},'description':'原始事实。'*600}},
            'resources':{'cash':'100'},'knowledge':{'hero:rule':{'claim':'完整信息。'*500}}},
            'continuity':{'places':[{'id':'room','address':'完整地图原文。'*500}],
                          'links':{'door':{'requires':{'hero:key':'持有钥匙'}}},'absence_is_not_proof':True}}
        old=shared_delivery(self.path,full)
        for key in ('state','continuity'):
            self.assertNotIn(key,old);self.assertEqual(rebuild_parts(self.path,old['shared_materials'][key]),full[key])
        changed=copy.deepcopy(full);changed['state']['clock']=3;changed['state']['resources']['cash']='99'
        new=shared_delivery(self.path,changed)
        for key in ('facts','knowledge'):
            a=next(p for p in old['shared_materials']['state']['parts'] if p['pointer']==[key])
            b=next(p for p in new['shared_materials']['state']['parts'] if p['pointer']==[key])
            self.assertEqual(a,b)
            (self.path/a['path']).write_text('broken', encoding='utf-8')
        restored=shared_delivery(self.path,changed)
        self.assertEqual(rebuild_parts(self.path,restored['shared_materials']['state']),changed['state'])
        self.assertEqual(new['shared_materials']['continuity'],old['shared_materials']['continuity'])
        base=next(p for p in new['shared_materials']['state']['parts'] if p['pointer']==[])
        self.assertIn('entities',json.loads((self.path/base['path']).read_text(encoding='utf-8')))  # debris stays with the base
        small={'chapter_session':{'role':'writer'},'state':{'clock':0,'facts':{}}}
        self.assertEqual(shared_delivery(self.path,small)['state'],small['state'])
