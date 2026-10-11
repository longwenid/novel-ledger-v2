"""Observable repair cost, independent review and rollback boundaries."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from support import contract, initialize, connect, respond, plan, ev, combined_review, fixture_first_read, fixture_obligations
from novel_core import engine, batchflow, reviewflow
from novel_core.store import dump, digest, Error, head, get
from novel_core.ledger import replay
import novel


class TargetedRepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        c=contract();c['chapter_batch_size']=3;initialize(self.path,c);self.db=connect(self.path)
        for _ in range(3):respond(self.db,self.path,engine.next_task(self.db,self.path))
        engine.approve(self.db)
        engine.validate_plan(self.db,'main',2,{'chapters':[plan(n) for n in range(2,11)]})
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def cli(self,*args):return novel.dispatch(novel.parser().parse_args([args[0],'--project',str(self.path),*args[1:]]))
    def work(self,t):return json.loads((self.path/t['input_path']).read_text())
    def receipt(self,t,data):return {'schema':'novel-ledger-v2.result/1','task_id':t['task_id'],'input_hash':t['input_hash'],'data':data}
    def save(self,t,data):
        path=self.path/t['output_path'];path.write_text(dump(self.receipt(t,data)));return str(path)
    def submit(self,t,data):return self.cli('submit','--result',self.save(t,data),'--brief','--continue-chapter-batch')
    def draft(self,t,text=None,events=None):
        n=t['chapter'];text=text or f'第{n}章。\n周行交出凭证。\n收据记着一百枚。\n宋禾签名。'
        before=batchflow.candidate_state(self.db,'main',n-1)['facts'].get('hero:route')
        if events is None:events=[{'op':'state','entity':'hero','key':'route','before':before,'after':str(n),'evidence':ev(text,n,'周行交出凭证。')}]
        return {'prose':text,'summary':'交出凭证后签名。','self_check':'核对交接与前态。','candidate_events':events}
    def writer_batch(self,first=None):
        t=first or self.cli('advance')['task']
        for _ in range(3):
            a=self.submit(t,self.draft(t))
            if a['action']!='continue_chapter_batch':return a
            t=a['task']
        self.fail('writer did not end')
    def seal(self,t):
        receipt={'task_id':t['task_id'],'input_hash':t['input_hash'],'prose_hash':t['first_read_hash'],
                 'first_read':fixture_first_read(),'first_reads':[{'chapter':r['chapter'],'prose_hash':r['prose_hash'],
                 'first_read':fixture_first_read()} for r in t['first_read_files']]}
        return reviewflow.seal(self.db,self.path,t['task_id'],receipt)['task']
    def review(self,t,prose=None,events=None):
        d=engine.load_draft(self.db,'main',t['chapter']);text=prose or d['prose']
        work=self.work(t)
        chosen=copy.deepcopy(d['candidate_events'] if events is None else events)
        if events is None and work['payload'].get('auto_carry'):
            prior=work['payload']['prior_events']
            chosen=[e for e in chosen if e not in [prior[i] for i in work['payload']['auto_carry']]]
        r=combined_review(work,text,t['chapter'],chosen)
        for category in ('facts','reader'):r[category]['obligations']=fixture_obligations(self.work(t),category,text,t['chapter'])
        return r
    def local_fix(self,t):
        r=self.review(t);r['reader'].update(verdict='fix',repair_scope='local')
        r['reader']['checks'][0]['result']='fix'
        text=engine.load_draft(self.db,'main',t['chapter'])['prose']
        r['reader']['findings']=[{'severity':'quality','impact':'签名没有回应交接','goal':'让人物确认后签名',
                                'evidence':ev(text,t['chapter'],'宋禾签名。')}]
        return self.submit(t,r)
    def snapshot(self):return list(self.db.iterdump())

    def test_preflight_runs_real_validation_without_any_persistent_write(self):
        t=self.cli('advance')['task'];data=self.draft(t)
        before=self.snapshot();files={str(p):p.read_bytes() for p in (self.path/'work').rglob('*') if p.is_file()}
        checked=self.cli('preflight','--result',self.save(t,data))
        self.assertTrue(checked['checked']);self.assertFalse(checked['submitted']);self.assertFalse(checked['would_commit'])
        self.assertEqual(self.snapshot(),before);self.assertIsNone(engine.load_draft(self.db,'main',2))
        for p,value in files.items():self.assertEqual(Path(p).read_bytes(),value)
        data['candidate_events'][0]['before']='wrong';before=self.snapshot()
        with self.assertRaises(Error) as cm:self.cli('preflight','--result',self.save(t,data))
        self.assertEqual(cm.exception.details['event_index'],0);self.assertEqual(self.snapshot(),before)
        # Structural repair of a bare receipt must also stay read-only.
        checked=self.cli('preflight','--result',self.save(t,self.draft(t)))
        raw=self.path/t['output_path'];raw.write_text(dump(self.draft(t)))
        checked=self.cli('preflight','--result',str(raw));self.assertTrue(checked['checked'])
        self.assertEqual(self.snapshot(),before)

    def test_settlement_exposes_arithmetic_and_error_event(self):
        t=self.cli('advance')['task'];text='第二天收到两枚，支付三枚。'
        events=[{'op':'resource','asset':'cash','unit':'文','from':None,'to':'hero','quantity':'2','to_before':'100','reason':'日结','evidence':ev(text,2)},
                {'op':'resource','asset':'cash','unit':'文','from':'hero','to':None,'quantity':'3','from_before':'102','reason':'支出','evidence':ev(text,2)}]
        out=self.submit(t,self.draft(t,text,events));settled=out['settlement']
        self.assertEqual(settled['resources'][0]['before'],'100');self.assertEqual(settled['resources'][0]['after'],'99')
        self.assertEqual(settled['steps'][0]['resources'][0]['after'],'102')
        self.assertEqual(head(self.db,'main'),1);self.assertEqual(replay(self.db,'main')['resources']['["hero","cash","文"]'],'100')

    def test_parallel_clock_settlement_and_malformed_evidence_are_scoped(self):
        from novel_core.settlement import evaluate
        before=self.snapshot();text='第二天支线抵达。';current=replay(self.db,'main')['clock']
        event={'op':'clock','thread':'支线','before':current,'after':current+1,'evidence':ev(text,2)}
        state,report=evaluate(self.db,'main',2,text,[event])
        self.assertEqual(report['clock_threads'],[{'key':'支线','before':None,'after':current+1}])
        self.assertEqual(report['clock']['after'],current)
        event['evidence']='wrong'
        with self.assertRaises(Error) as cm:evaluate(self.db,'main',2,text,[event])
        self.assertEqual(cm.exception.code,'evidence');self.assertEqual(cm.exception.details['event_index'],0)
        self.assertEqual(self.snapshot(),before)

    def test_local_repair_retains_suffix_without_draft_rework_and_reaudits(self):
        out=self.writer_batch();t=self.seal(self.cli('advance','--after',out['task_id'])['task'])
        suffix=copy.deepcopy(engine.load_draft(self.db,'main',3));fix=self.local_fix(t)
        self.assertEqual(engine.load_draft(self.db,'main',3),suffix)
        repair=self.cli('advance','--after',fix['task_id'])['task'];w=self.work(repair)
        template=json.loads((self.path/repair['template_path']).read_text())['data']
        self.assertTrue(template['reuse_candidate_events']);self.assertNotIn('candidate_events',template)
        patch=self.cli('patch','--task',repair['task_id'],'--old','宋禾签名。','--new','宋禾核对封条后签名。')
        data={**template,'base_hash':patch['base_hash'],'patches':[patch['patch']],
              'repair_note':'补上确认动作','summary':'交接后核对封条。','self_check':'事件证据句未变，核对签名动作。'}
        a=self.submit(repair,data);self.assertEqual(a['action'],'chapter_batch_done');self.assertEqual(head(self.db,'main'),1)
        self.assertEqual(engine.load_draft(self.db,'main',3)['prose'],suffix['prose'])
        reviewer=self.seal(self.cli('advance','--after',a['task_id'])['task'])
        for n in range(2,5):
            self.assertEqual(reviewer['stage'],'chapter_review');self.assertEqual(reviewer['chapter'],n)
            a=self.submit(reviewer,self.review(reviewer))
            if n<4:reviewer=a['task']
        self.assertEqual(head(self.db,'main'),4);self.assertTrue(engine.audit(self.db)['ok'])

    def test_local_repair_state_change_falls_back_to_suffix_rebase(self):
        out=self.writer_batch();t=self.seal(self.cli('advance','--after',out['task_id'])['task']);fix=self.local_fix(t)
        repair=self.cli('advance','--after',fix['task_id'])['task'];w=self.work(repair)
        old='宋禾签名。';base=engine.load_draft(self.db,'main',2)['prose'];events=copy.deepcopy(w['payload']['candidate_events_snapshot'])
        events[0]['after']='changed'
        a=self.submit(repair,{'base_hash':w['payload']['base_hash'],'patches':[{'start':base.index(old),'old':old,'new':'宋禾核对后签名。'}],
                             'repair_note':'核对动作与去向','candidate_events':events,'summary':'去向改变。','self_check':'按新去向核对。'})
        next_=self.cli('advance','--after',a['task_id'])['task']
        self.assertEqual(next_['stage'],'draft');self.assertEqual(next_['chapter'],3)
        self.assertNotIn('candidate_events',engine.load_draft(self.db,'main',3))
        nw=self.work(next_);self.assertIn('base_hash',nw['payload'])
        data=self.draft(next_);data.pop('prose');data.update(base_hash=nw['payload']['base_hash'],reuse_original=True)
        self.assertTrue(self.submit(next_,data)['accepted'])

    def numeric_task(self):
        t=self.cli('advance')['task'];data=self.draft(t);base=data['prose']
        data['candidate_events']=[{'op':'resource','asset':'cash','unit':'文','from':'hero','to':None,'quantity':'1',
                                  'from_before':'100','reason':'支付凭证费用','evidence':ev(base,2,'周行交出凭证。')}]
        a=self.submit(t,data);a=self.writer_batch(a['task'])
        t=self.seal(self.cli('advance','--after',a['task_id'])['task'])
        corrected=base.replace('一百','九十九');r=self.review(t,corrected)
        r['numeric_correction']={'base_hash':digest(base),'patches':[{'start':base.index('一百'),'old':'一百','new':'九十九'}],
                                 'repair_note':'按原收入支出结算修正余额，事件与其他文字不变'}
        return t,base,corrected,r

    def test_numeric_patch_commits_in_same_review_and_keeps_original_first_read(self):
        t,base,corrected,r=self.numeric_task();before=self.snapshot();count=self.db.execute('SELECT count(*) FROM tasks').fetchone()[0]
        frozen=copy.deepcopy(reviewflow.record(self.db,t['task_id']))
        checked=self.cli('preflight','--result',self.save(t,r));self.assertTrue(checked['would_commit']);self.assertEqual(self.snapshot(),before)
        a=self.submit(t,r);self.assertEqual(a['task']['stage'],'chapter_review');self.assertEqual(a['task']['chapter'],3)
        self.assertEqual(self.db.execute('SELECT count(*) FROM tasks').fetchone()[0],count+1)
        stored=self.db.execute('SELECT prose,data FROM chapters WHERE branch=? AND number=2',('main',)).fetchone()
        self.assertEqual(stored['prose'],corrected);self.assertEqual(reviewflow.record(self.db,t['task_id']),frozen)
        proof=json.loads(stored['data'])['numeric_correction'];self.assertEqual(proof['original_prose_hash'],digest(base))
        self.assertEqual(proof['corrected_prose_hash'],digest(corrected));self.assertEqual(a['settlement']['resources'][0]['after'],'99')
        self.assertTrue(engine.audit(self.db)['ok'])
        self.assertTrue(engine.submit(self.db,self.receipt(t,r))['idempotent'])

    def test_numeric_patch_cannot_change_units_effects_or_reinterpret_first_read(self):
        t,base,corrected,r=self.numeric_task();before=self.snapshot()
        cases=[]
        change=copy.deepcopy(r);change['numeric_correction']['patches'][0].update(old='一百枚',new='九十九文');cases.append(change)
        change=copy.deepcopy(r);change['facts']['events'][0]['quantity']='2';cases.append(change)
        change=copy.deepcopy(r);change['reader']['first_read']['change']='补读后重新解释';cases.append(change)
        change=copy.deepcopy(r);change['numeric_correction']['base_hash']='stale';cases.append(change)
        change=copy.deepcopy(r);change['reader']['verdict']='fix';cases.append(change)
        for value in cases:
            with self.subTest(value=value),self.assertRaises(Error):engine.submit(self.db,self.receipt(t,value),preview=True)
            self.assertEqual(self.snapshot(),before);self.assertEqual(head(self.db,'main'),1)

    def test_numeric_patch_does_not_invent_capability_for_frozen_task(self):
        t,base,corrected,r=self.numeric_task()
        work=json.loads(self.db.execute('SELECT input FROM tasks WHERE id=?',(t['task_id'],)).fetchone()[0])
        work['payload'].pop('numeric_correction')
        self.db.execute('UPDATE tasks SET input=? WHERE id=?',(dump(work),t['task_id']))
        with self.assertRaises(Error) as cm:engine.submit(self.db,self.receipt(t,r))
        self.assertEqual(cm.exception.code,'numeric_correction');self.assertEqual(head(self.db,'main'),1)

    def test_numeric_literals_title_entity_and_partial_number_boundaries(self):
        from novel_core.numeric import capability,compose
        state=replay(self.db,'main')
        state['entities']['new']={'name':'张三','aliases':['三哥'],'id':'new','kind':'person'}
        for base,old,new in [('第2章。','2','3'),('第2章。\n张三来了。','三','四'),
                             ('第2章。\n三哥来了。','三','四'),('第2章。\n余额一百二十。','一百','九十'),
                             ('第2章。\n余额123.45。','123','124'),('## 第2章\n余额100。','2','3'),
                             (' 第2章\n余额100。','2','3'),('余额1,000。','1','2'),('余额1e3。','1','2')]:
            draft={'prose':base,'candidate_events':[]};work={'payload':{'numeric_correction':capability(draft)}}
            data={'facts':{'verdict':'pass'},'reader':{'verdict':'pass'},'numeric_correction':{
                'base_hash':digest(base),'patches':[{'start':base.index(old),'old':old,'new':new}],'repair_note':'数值笔误'}}
            with self.subTest(base=base),self.assertRaises(Error):compose(work,draft,data,state)
        base='第2章，周行收到账款100文。';draft={'prose':base,'candidate_events':[]}
        work={'payload':{'numeric_correction':capability(draft)}}
        data={'facts':{'verdict':'pass'},'reader':{'verdict':'pass'},'numeric_correction':{
            'base_hash':digest(base),'patches':[{'start':base.index('100'),'old':'100','new':'99'}],'repair_note':'核实收入'}}
        self.assertEqual(compose(work,draft,data,state)[0],base.replace('100','99'))

    def test_patch_locator_ambiguity_and_stale_offsets_return_real_positions(self):
        t,base,corrected,r=self.numeric_task();before=self.snapshot()
        got=self.cli('patch','--task',t['task_id'],'--old','一百','--new','九十九')
        self.assertEqual(got['patch'],r['numeric_correction']['patches'][0]);self.assertEqual(self.snapshot(),before)
        from novel_core.patches import apply_patch
        with self.assertRaises(Error) as cm:apply_patch(base,{'base_hash':digest(base),
            'patches':[{'start':999,'old':'一百','new':'九十九'}]})
        self.assertEqual(cm.exception.details['matches'],[base.index('一百')])
        with self.assertRaises(Error) as cm:self.cli('patch','--task',t['task_id'],'--old','。','--new','！')
        self.assertGreater(len(cm.exception.details['matches']),1)

    def test_semantic_comparison_keeps_nested_fact_values(self):
        from novel_core.settlement import state_hash
        old=replay(self.db,'main');new=copy.deepcopy(old)
        old['facts']['hero:data']={'seed':1,'evidence':{'chapter':2,'start':0,'quote':'一百'}}
        new['facts']['hero:data']={'seed':2,'evidence':{'chapter':2,'start':0,'quote':'九十九'}}
        self.assertNotEqual(state_hash(old),state_hash(new))
        new=copy.deepcopy(old);old['knowledge']['hero:cash']={'claim':'余额','evidence':ev('余额',2)}
        new['knowledge']['hero:cash']={'claim':'余额','evidence':ev('余额更新',2)}
        self.assertEqual(state_hash(old),state_hash(new))

    def test_shared_event_delivery_is_exact_per_record_and_restorable(self):
        from novel_core.taskio import shared_delivery
        full={'chapter_session':{'role':'writer'},'selected':[{'kind':'history','chapter':1,'index':0,
              'event_hash':'source','event':{'op':'knowledge','claim':'完整记录','evidence':ev('旧收据',1)}}],
              'candidate_ledger':{'prefix':[{'chapter':2,'events':[{'op':'state','after':'候选'}]}]}}
        view=shared_delivery(self.path,full);record=view['selected'][0]['material']
        prefix=view['candidate_ledger']['prefix'][0]['events_material']
        self.assertEqual(json.loads((self.path/record['path']).read_text()),full['selected'][0])
        self.assertEqual(json.loads((self.path/prefix['path']).read_text()),full['candidate_ledger']['prefix'][0]['events'])
        extended=copy.deepcopy(full);extended['selected'].append({'kind':'history','chapter':2,'index':0,'event_hash':'new','event':{}})
        self.assertEqual(shared_delivery(self.path,extended)['selected'][0]['material'],record)
        (self.path/record['path']).write_bytes(b'broken');restored=shared_delivery(self.path,full)
        self.assertEqual(restored,view);self.assertEqual(json.loads((self.path/record['path']).read_text()),full['selected'][0])


class StandaloneNumericTests(unittest.TestCase):
    """Reviews without a candidate ledger (first chapter, single-chapter or
    revision flow) baseline numeric patches against their own frozen prose."""

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        c=contract();initialize(self.path,c);self.db=connect(self.path)
        self.prose='周行交出收据，金额一百文。宋禾拒绝签字，让他先找来证人。'
        respond(self.db,self.path,engine.next_task(self.db,self.path))
        respond(self.db,self.path,engine.next_task(self.db,self.path),prose=self.prose)
        self.task=engine.next_task(self.db,self.path)
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def cli(self,*args):return novel.dispatch(novel.parser().parse_args([args[0],'--project',str(self.path),*args[1:]]))
    def work(self,t):return json.loads((self.path/t['input_path']).read_text())

    def unlock(self):
        receipt=json.loads((self.path/self.task['first_read_template_path']).read_text())
        receipt['first_read']=fixture_first_read()
        path=self.path/self.task['first_read_result_path'];path.write_text(dump(receipt))
        self.cli('first-read','--task',self.task['task_id'],'--result',str(path))
        return engine.next_task(self.db,self.path)

    def receipt(self,t,prose):
        work=self.work(t);r=combined_review(work,prose,t['chapter'])
        for category in ('facts','reader'):
            r[category]['obligations']=fixture_obligations(work,category,prose,t['chapter'])
        return r

    def envelope(self,t,data):
        path=self.path/t['output_path']
        payload={'schema':'novel-ledger-v2.result/1','task_id':t['task_id'],'input_hash':t['input_hash'],'data':data}
        path.write_text(dump(payload));return payload

    def test_standalone_review_numeric_patch_commits_without_rework(self):
        t=self.unlock();work=self.work(t)
        self.assertNotIn('chapter_session',work)
        self.assertEqual(work['payload']['numeric_correction']['protocol'],'numeric-patch/1')
        corrected=self.prose.replace('一百','九十九')
        r=self.receipt(t,corrected)
        r['numeric_correction']={'base_hash':digest(self.prose),
            'patches':[{'start':self.prose.index('一百'),'old':'一百','new':'九十九'}],
            'repair_note':'按账本口径修正金额'}
        payload=self.envelope(t,r);file=str(self.path/t['output_path'])
        checked=self.cli('preflight','--result',file,'--task',t['task_id'])
        self.assertTrue(checked['would_commit']);self.assertIn('settlement',checked)
        self.assertTrue(self.cli('submit','--result',file)['accepted'])
        self.assertEqual(head(self.db,'main'),1)
        stored=self.db.execute('SELECT prose,data FROM chapters WHERE branch=? AND number=1',('main',)).fetchone()
        corrected=self.prose.replace('一百','九十九')
        self.assertEqual(stored['prose'],corrected)
        proof=json.loads(stored['data'])['numeric_correction']
        self.assertEqual(proof['original_prose_hash'],digest(self.prose))
        self.assertEqual(proof['corrected_prose_hash'],digest(corrected))
        engine.approve(self.db)
        following=self.cli('advance')['task']
        self.assertEqual(following['chapter'],2);self.assertNotIn('repair',following['stage'])
        self.assertTrue(engine.submit(self.db,payload)['idempotent'])

    def test_standalone_numeric_patch_still_literal_only(self):
        t=self.unlock();r=self.receipt(t,self.prose)
        r['numeric_correction']={'base_hash':digest(self.prose),
            'patches':[{'start':self.prose.index('收据'),'old':'收据','new':'清单'}],'repair_note':'换词'}
        payload=self.envelope(t,r)
        with self.assertRaises(Error) as caught:engine.submit(self.db,payload)
        self.assertEqual(caught.exception.code,'numeric_correction')
        self.assertEqual(head(self.db,'main'),0)

    def test_preflight_task_flag_and_stdout_error_contract(self):
        t=self.cli('advance')['task']
        payload=self.envelope(t,{'verdict':'pass','checks':[],'findings':[]})
        file=str(self.path/t['output_path'])
        with self.assertRaises(Error) as caught:self.cli('preflight','--result',file,'--task','0'*32)
        self.assertEqual(caught.exception.code,'preflight')
        import io,contextlib
        buffer=io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code=novel.main(['preflight','--project',str(self.path),'--result',file,'--task','0'*32])
        self.assertEqual(code,2)
        body=json.loads(buffer.getvalue())
        self.assertFalse(body['ok']);self.assertEqual(body['code'],'preflight')
        self.assertIn('--task',body['message'])


if __name__=='__main__':unittest.main()
