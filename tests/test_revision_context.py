"""Frozen review dependencies and context-preserving event re-registration."""
import json
import tempfile
import unittest
from pathlib import Path

from support import *
from novel_core.ledger import replay
from novel_core.store import Error, digest, head


class RevisionContextTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        c=contract();c['style']={'traits':['动作呈现选择'],'sample':'周行核对每一项证据。'}
        initialize(self.path,c);self.db=connect(self.path)

    def tearDown(self):
        self.db.close();self.temp.cleanup()

    def task(self):return engine.next_task(self.db,self.path)
    def work(self,task):return json.loads(rpath(task).read_text(encoding='utf-8'))

    def start_draft(self,prose,events=None,ending=False):
        respond(self.db,self.path,self.task(),ending=ending)
        respond(self.db,self.path,self.task(),prose=prose)
        respond(self.db,self.path,self.task(),events=events or [])

    def commit(self,prose,events=None,ending=False):
        self.start_draft(prose,events,ending)
        respond(self.db,self.path,self.task());engine.approve(self.db)

    def reader_fix(self):
        task=self.task();self.assertEqual(task['stage'],'reader')
        work=self.work(task);prose=pack_prose(self.path, work)
        data=review(work['payload']['checks'],prose,task['chapter'])
        data['verdict']='fix';data['checks'][0]['result']='fix'
        data['first_read']={'desire':'交付证据','obstacle':'犹豫','change':'决定','read_on_reason':'后果'}
        data['findings']=[{'severity':'quality','impact':'动作交代不足','goal':'补一个动作','evidence':ev(prose,task['chapter'])}]
        engine.submit(self.db,result(task,data))

    def facts_result(self,task,events=None,**extra):
        work=self.work(task);data=review(work['payload']['checks'],pack_prose(self.path, work),task['chapter'])
        data.update(events=events or [],summary='经正文核验后重新登记。',**extra)
        return engine.submit(self.db,result(task,data))

    def unsigned_pending(self,task):
        """Simulate a persisted pre-signature task without rewriting it on next."""
        work=self.work(task);work.pop('review_context_hash')
        wire=dump(work);fingerprint=digest(work)
        self.db.execute('UPDATE tasks SET input=?,input_hash=? WHERE id=?',(wire,fingerprint,task['task_id']))
        old=self.task()
        self.assertEqual(old['task_id'],task['task_id']);self.assertEqual(old['input_hash'],fingerprint)
        self.assertNotIn('review_context_hash',self.work(old))
        self.assertEqual(self.db.execute('SELECT input FROM tasks WHERE id=?',(task['task_id'],)).fetchone()[0],wire)
        return old

    def test_surviving_quote_in_negated_sentence_requires_explicit_replacement(self):
        original='周行得到玉佩。宋禾关上门。'
        events=[{'op':'state','entity':'hero','key':'has_token','before':None,'after':True,'evidence':ev(original,1,'得到玉佩')},
                {'op':'state','entity':'ally','key':'door_closed','before':None,'after':True,'evidence':ev(original,1,'关上门')}]
        self.commit(original,events)
        engine.begin_revision(self.db,1,'修正取得玉佩的事实')
        changed='周行没有得到玉佩。宋禾关上门。'
        respond(self.db,self.path,self.task(),prose=changed)
        task=self.task();self.assertEqual(self.work(task)['payload']['auto_carry'],[1])
        with self.assertRaises(Error) as exc:self.facts_result(task)
        self.assertEqual(exc.exception.code,'event_accounting')
        replacement={**events[0],'after':False,'evidence':ev(changed,1,'没有得到玉佩')}
        self.facts_result(task,[replacement],prior_dispositions=[{'index':0,'disposition':'replaced','reason':'正文改为没有取得玉佩。'}])
        respond(self.db,self.path,self.task())
        state=replay(self.db,get(self.db,'work_branch'))
        self.assertFalse(state['facts']['hero:has_token']);self.assertTrue(state['facts']['ally:door_closed'])

    def test_change_outside_evidence_sentence_keeps_automatic_carry(self):
        original='周行得到玉佩。宋禾关上门。'
        event={'op':'state','entity':'hero','key':'has_token','before':None,'after':True,'evidence':ev(original,1,'得到玉佩')}
        self.commit(original,[event]);engine.begin_revision(self.db,1,'修订未登记的收束动作')
        respond(self.db,self.path,self.task(),prose='周行得到玉佩。宋禾打开门。')
        task=self.task();self.assertEqual(self.work(task)['payload']['auto_carry'],[0])
        self.facts_result(task);respond(self.db,self.path,self.task())
        self.assertTrue(replay(self.db,get(self.db,'work_branch'))['facts']['hero:has_token'])

    def test_changed_secondary_evidence_context_prevents_automatic_carry(self):
        original='周行决定相信宋禾。宋禾救下了孩子。'
        event={'op':'state','entity':'hero','key':'trust','before':None,'after':True,
               'evidence':ev(original,1,'决定相信宋禾'),'cause_evidence':ev(original,1,'救下了孩子')}
        self.commit(original,[event]);engine.begin_revision(self.db,1,'修订形成信任的原因')
        respond(self.db,self.path,self.task(),prose='周行决定相信宋禾。宋禾没有救下了孩子。')
        task=self.task();self.assertEqual(self.work(task)['payload']['auto_carry'],[])
        with self.assertRaises(Error) as exc:self.facts_result(task)
        self.assertEqual(exc.exception.code,'event_accounting')

    def test_historical_evidence_is_not_reanchored_into_current_chapter(self):
        prose='周行得到玉佩。'
        self.commit(prose)
        respond(self.db,self.path,self.task())
        current='周行没有得到玉佩。宋禾关上门。'
        respond(self.db,self.path,self.task(),prose=current)
        draft=engine.load_draft(self.db,'main',2)
        prior={'op':'state','entity':'ally','key':'door_closed','before':None,'after':True,
               'evidence':ev(current,2,'关上门'),'cause_evidence':ev(prose,1,'得到玉佩')}
        draft['prior_event_snapshot']={'events':[prior],'prose':current,'verified':True}
        engine.save_draft(self.db,'main',2,draft)
        task=self.task();self.assertEqual(self.work(task)['payload']['auto_carry'],[])
        self.facts_result(task,[prior],prior_dispositions=[{'index':0,'disposition':'kept'}])
        stored=engine.load_draft(self.db,'main',2)['events'][0]['cause_evidence']
        self.assertEqual(stored,ev(prose,1,'得到玉佩'))

    def test_second_repair_keeps_materialized_events_including_carried_delta(self):
        prose='周行得到玉佩。宋禾关上门。'
        events=[{'op':'state','entity':'hero','key':'has_token','before':None,'after':True,'evidence':ev(prose,1,'得到玉佩')},
                {'op':'state','entity':'ally','key':'door_closed','before':None,'after':True,'evidence':ev(prose,1,'关上门')}]
        self.start_draft(prose,events);self.reader_fix()
        first=prose+'周行收起了钥匙。'
        respond(self.db,self.path,self.task(),prose=first)
        task=self.task();self.assertEqual(self.work(task)['payload']['auto_carry'],[0,1])
        extra={'op':'state','entity':'hero','key':'has_key','before':None,'after':True,'evidence':ev(first,1,'收起了钥匙')}
        self.facts_result(task,[extra]);self.reader_fix()
        respond(self.db,self.path,self.task(),prose=first+'宋禾抬头看他。')
        task=self.task();payload=self.work(task)['payload']
        self.assertEqual(len(payload['prior_events']),3);self.assertEqual(payload['auto_carry'],[0,1,2])
        self.facts_result(task);respond(self.db,self.path,self.task())
        self.assertEqual(set(replay(self.db,'main')['facts']),{'hero:has_token','ally:door_closed','hero:has_key'})

    def test_dropped_events_are_not_resurrected_by_another_repair(self):
        prose='周行得到玉佩。'
        event={'op':'state','entity':'hero','key':'has_token','before':None,'after':True,'evidence':ev(prose,1,'得到玉佩')}
        self.start_draft(prose,[event]);self.reader_fix()
        revised='周行没有得到玉佩。'
        respond(self.db,self.path,self.task(),prose=revised)
        self.facts_result(self.task(),prior_dispositions=[{'index':0,'disposition':'dropped','reason':'取得玉佩不再发生。'}])
        self.reader_fix();respond(self.db,self.path,self.task(),prose=revised+'宋禾握住门把手。')
        task=self.task();payload=self.work(task)['payload']
        self.assertEqual(payload['prior_events'],[]);self.assertEqual(payload['auto_carry'],[])
        self.facts_result(task);respond(self.db,self.path,self.task())
        self.assertNotIn('hero:has_token',replay(self.db,'main')['facts'])

    def test_range_hash_binds_relevant_metadata_but_excludes_future_scope(self):
        self.commit('周行把收据交给宋禾。')
        original=engine.review_hash(self.db,'main',1,1)
        put(self.db,'design:main:v2',{'revision':1,'active':True,'data':{'purpose':'未来第二卷'}})
        put(self.db,'character:main:outsider',{'revision':1,'active':True,'data':{'voice':'未出现人物'}})
        self.db.execute('INSERT INTO plans VALUES (?,?,?)',('main',2,dump({**plan(2),'volume':'v2'})))
        self.assertEqual(engine.review_hash(self.db,'main',1,1),original)
        for key,value in [('design:main:world',{'revision':1,'active':True,'data':{'overview':'全书因果'}}),
                          ('design:main:v1',{'revision':1,'active':True,'data':{'purpose':'本卷'}}),
                          ('character:main:hero',{'revision':1,'active':True,'data':{'voice':'相关人物'}})]:
            before=engine.review_hash(self.db,'main',1,1);put(self.db,key,value)
            self.assertNotEqual(engine.review_hash(self.db,'main',1,1),before,key)
        before=engine.review_hash(self.db,'main',1,1)
        put(self.db,'style_anchor',{**get(self.db,'style_anchor'),'voice':'改为冷静克制'})
        self.assertNotEqual(engine.review_hash(self.db,'main',1,1),before)
        before=engine.review_hash(self.db,'main',1,1)
        put(self.db,'contract',{**get(self.db,'contract'),'ending':'主角独自藏起证据'})
        self.assertNotEqual(engine.review_hash(self.db,'main',1,1),before)
        before=engine.review_hash(self.db,'main',1,1)
        self.db.execute('UPDATE plans SET data=? WHERE branch=? AND number=1',(dump({**plan(1),'goal':'让证人离开'}),'main'))
        self.assertNotEqual(engine.review_hash(self.db,'main',1,1),before)

    def test_signed_pending_rejects_changed_dependencies_and_refreshes(self):
        respond(self.db,self.path,self.task());respond(self.db,self.path,self.task())
        task=self.task();frozen=self.work(task)['review_context_hash']
        put(self.db,'style_anchor',{**get(self.db,'style_anchor'),'voice':'另一个声线'})
        with self.assertRaises(Error) as exc:self.facts_result(task)
        self.assertEqual(exc.exception.code,'review_context_stale')
        fresh=self.task();self.assertNotEqual(fresh['task_id'],task['task_id'])
        self.assertNotEqual(self.work(fresh)['review_context_hash'],frozen)
        self.assertEqual(self.db.execute('SELECT state FROM tasks WHERE id=?',(task['task_id'],)).fetchone()[0],'cancelled')

    def test_legacy_facts_pending_is_preserved_and_accepted_before_fresh_review(self):
        respond(self.db,self.path,self.task());respond(self.db,self.path,self.task())
        task=self.unsigned_pending(self.task())
        outcome=self.facts_result(task)
        self.assertTrue(outcome['accepted']);self.assertTrue(outcome['review_recheck_required'])
        fresh=self.task();self.assertEqual(fresh['stage'],'facts');self.assertNotEqual(fresh['task_id'],task['task_id'])
        self.assertIn('review_context_hash',self.work(fresh));self.assertEqual(head(self.db,'main'),0)
        self.facts_result(fresh);respond(self.db,self.path,self.task())
        self.assertEqual(head(self.db,'main'),1)

    def test_legacy_reader_pending_cannot_commit_under_a_new_signature(self):
        self.start_draft('周行把收据交给宋禾。')
        task=self.unsigned_pending(self.task())
        outcome=respond(self.db,self.path,task)
        self.assertTrue(outcome['accepted']);self.assertTrue(outcome['review_recheck_required'])
        self.assertEqual(head(self.db,'main'),0)
        fresh=self.task();self.assertEqual(fresh['stage'],'facts');self.assertIn('review_context_hash',self.work(fresh))

    def test_legacy_aggregate_review_does_not_promote_unverified_memory(self):
        self.commit('周行把收据交给宋禾。',ending=True)
        respond(self.db,self.path,self.task())  # signed volume chunk
        task=self.unsigned_pending(self.task())
        self.assertEqual(self.work(task)['payload']['scope'],'volume')
        outcome=respond(self.db,self.path,task)
        self.assertTrue(outcome['accepted']);self.assertTrue(outcome['review_recheck_required'])
        self.assertFalse(engine.reviewed(self.db,'main','volume',1))
        self.assertIsNone(self.db.execute("SELECT data FROM memories WHERE branch='main' AND scope='volume'").fetchone())
        fresh=self.task();self.assertEqual(self.work(fresh)['payload']['scope'],'volume')
        self.assertNotEqual(fresh['task_id'],task['task_id'])
        respond(self.db,self.path,fresh);self.assertTrue(engine.reviewed(self.db,'main','volume',1))

    def test_original_chapter_without_frozen_signature_requires_review(self):
        self.commit('周行把收据交给宋禾。')
        row=self.db.execute("SELECT data FROM chapters WHERE branch='main' AND number=1").fetchone()
        old=json.loads(row['data']);old.pop('facts_context_hash');old.pop('reader_context_hash')
        self.db.execute("UPDATE chapters SET data=? WHERE branch='main' AND number=1",(dump(old),))
        engine.begin_revision(self.db,1,'复核旧版本章节')
        task=self.task();payload=self.work(task)['payload']
        outcome=engine.submit(self.db,result(task,{'base_hash':payload['base_hash'],'reuse_original':True}))
        self.assertNotIn('fast_committed',outcome);self.assertEqual(self.task()['stage'],'facts')


if __name__=='__main__':unittest.main()
