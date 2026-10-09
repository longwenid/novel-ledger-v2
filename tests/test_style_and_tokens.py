"""Style anchor governance and task-scoped historical navigation."""
import json
import tempfile
import unittest
from pathlib import Path
from support import *
from novel_core.store import Error, digest, head, put, transaction, get, dump
from novel_core.ledger import append_events, replay, seed
from novel_core import engine


class StyleAnchorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        initialize(self.path,contract());self.db=connect(self.path)
    def tearDown(self): self.db.close();self.temp.cleanup()
    def task(self): return engine.next_task(self.db,self.path)

    def test_contract_style_propagates_to_packs_and_checks(self):
        anchor={'traits':['冷峻短句，动作先于情绪'],'sample':'刀出鞘时，雨还没停。他没有看血，只看路。'}
        with transaction(self.db):put(self.db,'style_anchor',anchor|{'source':'author'})
        respond(self.db,self.path,self.task())                     # plan
        t=self.task();self.assertEqual(t['stage'],'draft')
        work=json.loads(rpath(t, 'input_path').read_text(encoding='utf-8'))
        self.assertEqual(work['style_anchor']['traits'],anchor['traits'])
        self.assertEqual(work['style_anchor']['sample'],anchor['sample'])
        respond(self.db,self.path,t)
        t=self.task();self.assertEqual(t['stage'],'facts')
        self.assertNotIn('style_anchor',json.loads(rpath(t, 'input_path').read_text(encoding='utf-8')))
        respond(self.db,self.path,t)
        t=self.task();self.assertEqual(t['stage'],'reader')
        work=json.loads(rpath(t, 'input_path').read_text(encoding='utf-8'))
        self.assertIn('voice_match',work['payload']['checks'])
        self.assertIn('style_anchor',work)

    def test_no_anchor_no_voice_check(self):
        respond(self.db,self.path,self.task());respond(self.db,self.path,self.task());respond(self.db,self.path,self.task())
        t=self.task();self.assertEqual(t['stage'],'reader')
        work=json.loads(rpath(t, 'input_path').read_text(encoding='utf-8'))
        self.assertNotIn('voice_match',work['payload']['checks'])

    def test_approve_snapshots_pilot_as_anchor(self):
        while head(self.db,'main')==0:
            t=self.task()
            if t.get('stage')=='await_author':break
            respond(self.db,self.path,t)
        self.assertIsNone(get(self.db,'style_anchor'))
        engine.approve(self.db)
        anchor=get(self.db,'style_anchor')
        self.assertEqual(anchor['source'],'pilot')
        self.assertTrue(anchor['sample'].strip())
        while True:
            t=self.task()
            if t.get('stage')=='reader':break
            self.assertTrue(t.get('task_id'))
            respond(self.db,self.path,t)
        work=json.loads(rpath(t, 'input_path').read_text(encoding='utf-8'))
        self.assertIn('voice_match',work['payload']['checks'])
        self.assertEqual(work['style_anchor']['sample'],anchor['sample'])

    def test_voice_match_requires_evidence(self):
        with transaction(self.db):put(self.db,'style_anchor',{'traits':['短句'],'sample':'雨未停。','source':'author'})
        respond(self.db,self.path,self.task());respond(self.db,self.path,self.task());respond(self.db,self.path,self.task())
        t=self.task();work=json.loads(rpath(t, 'input_path').read_text(encoding='utf-8'))
        data=review(work['payload']['checks'],pack_prose(self.path, work))
        row=next(r for r in data['checks'] if r['id']=='voice_match');row['evidence']=[]
        row['not_applicable']='跳过'
        data['first_read']={'desire':'d','obstacle':'o','change':'c','read_on_reason':'r'}
        with self.assertRaises(Error) as exc: engine.submit(self.db,result(t,data))
        self.assertEqual(exc.exception.code,'review')

    def test_invalid_style_shape_rejected(self):
        from novel_core.store import validate_style
        with self.assertRaises(Error): validate_style({'traits':'不是数组'})
        with self.assertRaises(Error): validate_style({'sample':{'text':'雨停了。'}})
        validate_style({'traits':['克制'],'sample':'雨停了。'})


class ContextStructureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        c=contract();c['context_mode']='bounded'
        initialize(self.path,c);self.db=connect(self.path)
        put(self.db,'pilot_approved',True)
    def tearDown(self): self.db.close();self.temp.cleanup()
    def task(self): return engine.next_task(self.db,self.path)

    def _commit_chapter(self,n,events_per_chapter=8):
        text=f'第{n}章，周行清点货栈，宋禾核对名录，逐一画押。'
        state=seed(get(self.db,'contract'))
        events=[{'op':'knowledge','entity':'hero','topic':f'ledger{n}-{i}','before':None,'stance':'knows',
                 'claim':f'条目{n}-{i}核讫','source':'清点','evidence':ev(text,n)} for i in range(events_per_chapter)]
        data={'volume':'v1','summary':f'第{n}章清点货栈。','events':events,'prose_hash':digest(text),
              'facts_review':{'verdict':'pass'},'reader_review':{'verdict':'pass'}}
        with transaction(self.db):
            self.db.execute('INSERT OR REPLACE INTO chapters VALUES (?,?,?,?,?)',('main',n,text,dump(data),digest(text)))
            self.db.execute('INSERT OR REPLACE INTO plans VALUES (?,?,?)',('main',n,dump(plan(n))))
            self.db.execute('INSERT OR REPLACE INTO plans VALUES (?,?,?)',('main',n+1,dump(plan(n+1))))
            append_events(self.db,'main',n,text,events,state)
            for i in range(3):
                self.db.execute('INSERT OR REPLACE INTO memories VALUES (?,?,?,?)',('main','batch',(n-i)*10,dump({'text':f'批次{i}记忆'*400,'sources':[]})))

    def test_history_navigation_preserves_records_and_marks_older_sources(self):
        for n in (1,2,3):
            self._commit_chapter(n)
        t=self.task()
        work=json.loads(rpath(t, 'input_path').read_text(encoding='utf-8'))
        if t['stage']=='context_scan':
            bundle=get(self.db,'context_bundle:'+work['payload']['bundle'])
            selected=json.loads(next(d['text'] for d in bundle['documents'] if d['field']=='selected'))
            omitted_rows=bundle['core']['omitted']
        else:
            selected=work['selected'];omitted_rows=work['omitted']
        history=[s for s in selected if s['kind']=='history']
        self.assertEqual(len(history),20)
        self.assertEqual(len({(h['chapter'],h['index'],h['event_hash']) for h in history}),20)
        omitted=next(o for o in omitted_rows if o['kind']=='history')
        self.assertEqual(omitted['by_entity']['hero'],4)
        self.assertTrue(all(h['event']['claim'] for h in history))

    def test_memories_capped_at_two(self):
        self._commit_chapter(1)
        t=self.task();work=json.loads(rpath(t, 'input_path').read_text(encoding='utf-8'))
        self.assertEqual(len([s for s in work['selected'] if s['kind']=='memory']),2)

    def test_config_style_anchor_sets_and_validates(self):
        import novel
        args=novel.parser().parse_args(['config','--project',str(self.path),'--key','style_anchor',
                                        '--value',json.dumps({'traits':['短句'],'sample':'雨停了。'},ensure_ascii=False)])
        out=novel.dispatch(args)
        self.assertEqual(out['style_anchor']['sample'],'雨停了。')
        bad=novel.parser().parse_args(['config','--project',str(self.path),'--key','style_anchor','--value','{"traits":1}'])
        with self.assertRaises(Error) as exc: novel.dispatch(bad)
        self.assertEqual(exc.exception.code,'style')

    def test_synthesis_review_requires_repetition(self):
        self._commit_chapter(1)
        with transaction(self.db):
            self.db.execute("UPDATE plans SET data=? WHERE branch='main' AND number=1",(dump(plan(1,ending=True)),))
        seen=None
        for _ in range(50):
            t=self.task()
            if not t.get('task_id'):break
            pack=json.loads(rpath(t, 'input_path').read_text(encoding='utf-8'))
            if t['stage'].endswith('_review') and not pack['payload'].get('scope','').endswith('_chunk'):
                seen=pack['payload']['checks'];break
            respond(self.db,self.path,t,ending=t['chapter']==1)
        self.assertIsNotNone(seen)
        self.assertIn('repetition',seen)


if __name__=='__main__':unittest.main()
