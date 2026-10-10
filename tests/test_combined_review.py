"""One independent review task retains both gates and atomic chapter accounting."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from support import *
from novel_core.store import Error, digest, head
from novel_core.ledger import replay


class CombinedReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        c=contract()
        initialize(self.path,c);self.db=connect(self.path)
        self.prose='周行把收据交给宋禾。她选择按下指印。'

    def tearDown(self):self.db.close();self.temp.cleanup()
    def task(self):return engine.next_task(self.db,self.path)
    def work(self,t):return json.loads(rpath(t).read_text(encoding='utf-8'))
    def prepared(self):
        respond(self.db,self.path,self.task())
        respond(self.db,self.path,self.task(),prose=self.prose)
        t=self.task();self.assertEqual(t['stage'],'chapter_review')
        return t
    def receipt(self,t,events=None):
        w=self.work(t);p=pack_prose(self.path,w);n=t['chapter']
        facts=review(w['payload']['facts_checks'],p,n)
        facts.update(events=events or [],summary='周行交付收据，宋禾主动签字。')
        reader=review(w['payload']['reader_checks'],p,n)
        reader['first_read']=fixture_first_read()
        return {'facts':facts,'reader':reader}
    def fix(self,data,key):
        r=data[key];r['verdict']='fix';r['checks'][0]['result']='fix'
        r['findings']=[{'severity':'quality','impact':key+'问题影响理解','goal':'补足选择','evidence':ev(self.prose)}]
    def event(self):return {'op':'state','entity':'hero','key':'delivered','before':None,'after':True,'evidence':ev(self.prose,quote='把收据交给宋禾')}

    def test_atomic_commit_both_receipts_and_idempotent_replay(self):
        t=self.prepared();w=self.work(t)
        self.assertEqual(t['first_read_path'],w['payload']['prose_path'])
        self.assertEqual(t['first_read_hash'],digest(self.prose))
        self.assertIn('review_context_hash',w)
        r=result(t,self.receipt(t,[self.event()]));engine.submit(self.db,r)
        self.assertEqual(head(self.db,'main'),1)
        stored=json.loads(self.db.execute('SELECT data FROM chapters').fetchone()[0])
        self.assertEqual(stored['facts_review']['verdict'],'pass')
        self.assertEqual(stored['reader_review']['verdict'],'pass')
        self.assertEqual(stored['facts_context_hash'],stored['reader_context_hash'])
        self.assertTrue(replay(self.db,'main')['facts']['hero:delivered'])
        self.assertTrue(engine.submit(self.db,r)['idempotent'])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM events').fetchone()[0],1)

    def test_missing_invalid_reader_or_first_read_rolls_back(self):
        t=self.prepared()
        for kind in ('missing','incomplete','first_read'):
            data=self.receipt(t,[self.event()])
            if kind=='missing':data.pop('reader')
            elif kind=='incomplete':data['reader']['checks'].pop()
            else:data['reader'].pop('first_read')
            with self.subTest(kind=kind),self.assertRaises(Error):engine.submit(self.db,result(t,data))
            self.assertEqual(head(self.db,'main'),0)
            self.assertEqual(self.db.execute('SELECT COUNT(*) FROM events').fetchone()[0],0)
            self.assertEqual(engine.load_draft(self.db,'main',1)['stage'],'chapter_review')
            self.assertEqual(self.task()['task_id'],t['task_id'])

    def test_either_fix_and_union_of_findings_increment_once(self):
        t=self.prepared();data=self.receipt(t,[self.event()])
        self.fix(data,'facts');self.fix(data,'reader')
        engine.submit(self.db,result(t,data))
        draft=engine.load_draft(self.db,'main',1)
        self.assertEqual(draft['attempts'],1);self.assertEqual(len(draft['findings']),2)
        self.assertEqual(self.task()['stage'],'repair');self.assertEqual(head(self.db,'main'),0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM events').fetchone()[0],0)

    def test_reader_fix_alone_repair_and_prior_fact_events_survive(self):
        t=self.prepared();data=self.receipt(t,[self.event()]);self.fix(data,'reader')
        engine.submit(self.db,result(t,data))
        self.assertEqual(engine.load_draft(self.db,'main',1)['attempts'],1)
        prior=engine.prior_events(self.db,'main',1)
        self.assertEqual(prior['events'],[self.event()])
        changed=self.prose+'宋禾又问起明日的证人。'
        respond(self.db,self.path,self.task(),prose=changed)
        nxt=self.task();self.assertEqual(nxt['stage'],'chapter_review')
        self.assertEqual(self.work(nxt)['payload']['auto_carry'],[0])
        engine.submit(self.db,result(nxt,self.receipt(nxt)))
        self.assertTrue(replay(self.db,'main')['facts']['hero:delivered'])

    def test_facts_fix_alone_repair(self):
        t=self.prepared();data=self.receipt(t);self.fix(data,'facts')
        engine.submit(self.db,result(t,data))
        self.assertEqual(engine.load_draft(self.db,'main',1)['stage'],'repair')
        self.assertEqual(head(self.db,'main'),0)

    def test_bad_fact_event_rolls_back_all(self):
        t=self.prepared();bad=self.event();bad['evidence']['quote']='不存在'
        with self.assertRaises(Error):engine.submit(self.db,result(t,self.receipt(t,[bad])))
        self.assertEqual(head(self.db,'main'),0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM events').fetchone()[0],0)
        self.assertNotIn('facts_review',engine.load_draft(self.db,'main',1))
        self.assertEqual(self.task()['task_id'],t['task_id'])

    def test_invalid_fact_event_with_reader_fix_is_not_retained_as_verified(self):
        t=self.prepared();bad=self.event();bad['evidence']['quote']='不存在'
        data=self.receipt(t,[bad]);self.fix(data,'reader')
        with self.assertRaises(Error):engine.submit(self.db,result(t,data))
        self.assertIsNone(engine.prior_events(self.db,'main',1))
        self.assertEqual(self.task()['task_id'],t['task_id'])

    def test_bounded_profiled_combined_checks_and_context(self):
        c=get(self.db,'contract');c['character_mode']='profiled';put(self.db,'contract',c)
        put(self.db,'context_mode','bounded')
        respond(self.db,self.path,self.task())
        profiles=json.loads((Path(__file__).resolve().parents[1]/'assets/characters.example.json').read_text())
        t=self.task();self.assertEqual(t['stage'],'character_plan')
        engine.submit(self.db,result(t,profiles))
        respond(self.db,self.path,self.task(),prose=self.prose)
        t=self.task();self.assertEqual(t['stage'],'chapter_review')
        w=self.work(t)
        self.assertIn('character_consistency',w['payload']['facts_checks'])
        self.assertIn('dialogue_identity',w['payload']['reader_checks'])
        self.assertIn('character_context',w)
        self.assertEqual(rpath(t,'first_read_path').read_text(),self.prose)
        engine.submit(self.db,result(t,self.receipt(t)))
        self.assertEqual(head(self.db,'main'),1)

    def test_stale_context_refresh_and_resume(self):
        t=self.prepared();r=result(t,self.receipt(t))
        c=get(self.db,'contract');c['reader_promise']='不同的阅读承诺';put(self.db,'contract',c)
        with self.assertRaises(Error) as exc:engine.submit(self.db,r)
        self.assertEqual(exc.exception.code,'review_context_stale')
        nxt=self.task();self.assertNotEqual(nxt['task_id'],t['task_id'])
        self.db.close();self.db=connect(self.path)
        self.assertEqual(self.task()['task_id'],nxt['task_id'])
        engine.pause(self.db,'测试暂停');engine.resume(self.db)
        self.assertEqual(self.task()['task_id'],nxt['task_id'])
        engine.submit(self.db,result(nxt,self.receipt(nxt)))
        self.assertEqual(head(self.db,'main'),1)

    def test_unsigned_combined_task_is_rejected(self):
        t=self.prepared();w=self.work(t);w.pop('review_context_hash')
        fingerprint=digest(w)
        self.db.execute('UPDATE tasks SET input=?,input_hash=? WHERE id=?',(dump(w),fingerprint,t['task_id']))
        old={**t,'input_hash':fingerprint}
        with self.assertRaises(Error) as exc:engine.next_task(self.db,self.path)
        self.assertEqual(exc.exception.code,'unsupported_review')
        with self.assertRaises(Error) as exc:engine.submit(self.db,result(old,self.receipt(t)))
        self.assertEqual(exc.exception.code,'unsupported_review')
        self.assertEqual(head(self.db,'main'),0)

    def test_dynamic_reader_checks(self):
        t=self.prepared()
        put(self.db,'style_anchor',{'traits':['动作表达选择']})
        c=get(self.db,'contract');c['chapter_target_han']=1;put(self.db,'contract',c)
        self.assertIn('voice_match',engine.chapter_reader_checks(self.db,'main',1,engine.chapter_length(self.db,'main',1,self.prose)))
        nxt=self.task();p=self.work(nxt)['payload']
        self.assertIn('voice_match',p['reader_checks']);self.assertIn('substance',p['reader_checks'])
        self.assertIn('chapter_length',p)
        engine.submit(self.db,result(nxt,self.receipt(nxt)))

    def test_old_review_tasks_are_rejected(self):
        t=self.prepared();data=self.receipt(t)
        for stage in ('facts','reader'):
            with self.subTest(stage=stage):
                self.db.execute('UPDATE tasks SET stage=? WHERE id=?',(stage,t['task_id']))
                with self.assertRaises(Error) as exc:self.task()
                self.assertEqual(exc.exception.code,'unsupported_review')
                with self.assertRaises(Error) as exc:engine.submit(self.db,result(t,data))
                self.assertEqual(exc.exception.code,'unsupported_review')
        self.assertEqual(head(self.db,'main'),0)

    def test_old_draft_stages_are_rejected(self):
        t=self.prepared()
        self.db.execute("UPDATE tasks SET state='cancelled' WHERE id=?",(t['task_id'],))
        for stage in ('facts','reader'):
            draft=engine.load_draft(self.db,'main',1);draft['stage']=stage
            engine.save_draft(self.db,'main',1,draft)
            with self.assertRaises(Error) as exc:self.task()
            self.assertEqual(exc.exception.code,'unsupported_review')
