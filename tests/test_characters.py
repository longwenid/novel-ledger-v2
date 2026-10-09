import copy
import json
import tempfile
import unittest
from pathlib import Path
from support import *
from novel_core import characters
from novel_core.ledger import replay,apply,seed
from novel_core.store import Error,digest,head
from novel_core.memory import pack

ROOT=Path(__file__).resolve().parents[1]
PROFILES=json.loads((ROOT/'assets/characters.example.json').read_text(encoding='utf-8'))
PLAN=json.loads((ROOT/'assets/design.example.json').read_text(encoding='utf-8'))['chapter_plan']


class CharacterTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        c=contract();c['character_mode']='profiled';initialize(self.path,c);self.db=connect(self.path)
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def task(self):return engine.next_task(self.db,self.path)
    def submit(self,t,data):return engine.submit(self.db,result(t,copy.deepcopy(data)))
    def plan(self,n=1):
        p=copy.deepcopy(PLAN);p['number']=n;return p
    def prepare(self,profiles=PROFILES):
        self.submit(self.task(),{'chapters':[self.plan()]});t=self.task();self.assertEqual(t['stage'],'character_plan')
        self.submit(t,profiles)
    def write(self,prose='周行问：“最后一个签收的人是谁？”宋禾把表格推过去：“先写清你要拿什么。”',events=None):
        t=self.task();self.assertEqual(t['stage'],'draft');self.submit(t,{'prose':prose})
        t=self.task();w=json.loads(rpath(t, 'input_path').read_text(encoding='utf-8'));data=review(w['payload']['checks'],prose,t['chapter'])
        data.update(events=events or [],summary='核对交接，双方承担不同风险。');self.submit(t,data)
        t=self.task();respond(self.db,self.path,t)
    def test_profile_task_idempotent_and_not_canon(self):
        self.submit(self.task(),{'chapters':[self.plan()]});t=self.task();before=digest(replay(self.db,'main'))
        self.assertEqual(self.task(),t);r=result(t,copy.deepcopy(PROFILES));engine.submit(self.db,r)
        self.assertTrue(engine.submit(self.db,r)['idempotent']);self.assertEqual(digest(replay(self.db,'main')),before)
        self.assertEqual(self.task()['stage'],'draft')
    def test_profile_scope_and_fake_sources_rejected_atomically(self):
        self.submit(self.task(),{'chapters':[self.plan()]});t=self.task();bad=copy.deepcopy(PROFILES)
        bad['profiles'].pop()
        with self.assertRaises(Error):self.submit(t,bad)
        bad=copy.deepcopy(PROFILES);bad['profiles'][1]['examples']=[{'speaker':'ally','note':'伪造来源','evidence':ev('不存在的正文')}]
        with self.assertRaises(Error):self.submit(t,bad)
        self.assertIsNone(characters.current(self.db,'main','hero'));self.assertEqual(self.task()['task_id'],t['task_id'])
    def test_major_profile_needs_motive_and_voice_supporting_can_be_short(self):
        self.submit(self.task(),{'chapters':[self.plan()]});t=self.task();bad=copy.deepcopy(PROFILES)
        bad['profiles'][0]['biography'].pop('contradiction')
        with self.assertRaises(Error):self.submit(t,bad)
        self.submit(t,PROFILES)
    def test_writer_keeps_full_required_profile_and_reader_first_read_is_clean(self):
        profiles=copy.deepcopy(PROFILES)
        detail='他愿意合作，却害怕把同伴置于自己无法承担的危险。'*3000
        profiles['profiles'][0]['biography']['contradiction']=detail
        # Older projects may still store this setting; it cannot cut off a required profile.
        put(self.db,'context_chars',4096)
        self.prepare(profiles);t=self.task();w=json.loads(rpath(t, 'input_path').read_text(encoding='utf-8'))
        self.assertEqual(set(w['character_context']['profiles']),{'hero','ally'})
        self.assertEqual(w['character_context']['profiles']['hero']['data']['biography']['contradiction'],detail)
        self.assertIn('人物小传',rpath(t, 'brief_path').read_text(encoding='utf-8'));self.assertIn('本场景',rpath(t, 'brief_path').read_text(encoding='utf-8'))
        again=pack(self.db,'main',1,'draft',{})
        self.assertEqual(again['character_context']['profiles'],w['character_context']['profiles'])
        self.submit(t,{'prose':'周行问：“最后谁签收？”宋禾说：“把范围写清楚。”'})
        t=self.task();w=json.loads(rpath(t, 'input_path').read_text(encoding='utf-8'));self.assertIn('character_consistency',w['payload']['checks'])
        data=review(w['payload']['checks'],pack_prose(self.path, w));data.update(events=[],summary='核对责任');self.submit(t,data)
        t=self.task();w=json.loads(rpath(t, 'input_path').read_text(encoding='utf-8'))
        self.assertIn('dialogue_identity',w['payload']['checks'])
        for k in ('character_context','writer_brief','state','design_context','plan','contract'):self.assertNotIn(k,w)
        self.assertNotIn(detail,dump(w))
    def test_speaker_intentions_must_be_separate(self):
        p=self.plan();p['scenes'][0]['dialogue_intents'].pop()
        with self.assertRaises(Error):self.submit(self.task(),{'chapters':[p]})
        p=self.plan();p['scenes'][0]['speakers']=[];p['scenes'][0]['dialogue_intents']=[]
        self.submit(self.task(),{'chapters':[p]})
    def test_growth_requires_actual_cause_and_is_not_a_fact_override(self):
        state=seed(contract());text='周行把签字位置留给宋禾，第一次让她自己决定是否承担责任。'
        event={'op':'character_development','entity':'hero','before':None,'change':'愿意让同伴决定风险','cause':'宋禾主动要求签名','evidence':ev(text)}
        with self.assertRaises(Error):apply(state,event,text,1)
        event['cause_evidence']=ev(text);apply(state,event,text,1)
        self.assertNotIn('hero:personality',state['facts'])
        with self.assertRaises(Error):apply(state,event,text,1)
    def test_growth_refreshes_profile_and_preserves_history_and_sources(self):
        self.prepare();text='周行把签字位置留给宋禾，第一次让她自己决定是否承担责任。'
        event={'op':'character_development','entity':'hero','before':None,'change':'开始允许同伴自行承担风险','cause':'宋禾提出愿意签字','evidence':ev(text),'cause_evidence':ev(text)}
        self.write(text,[event]);engine.approve(self.db)
        self.submit(self.task(),{'chapters':[self.plan(2)]});t=self.task()
        self.assertEqual(t['stage'],'character_plan');w=json.loads(rpath(t, 'input_path').read_text(encoding='utf-8'));self.assertEqual(w['payload']['entities'],['hero'])
        revised=copy.deepcopy(PROFILES['profiles'][0]);revised['change_reason']='同伴争取到了决定自己风险的权利'
        revised['biography']['experiences']=[{'text':'让宋禾自行决定签字','status':'established','evidence':ev(text)}]
        wrong=copy.deepcopy(revised);wrong['biography']['experiences'][0]['evidence']['quote']='历史中不存在的话'
        with self.assertRaises(Error):self.submit(t,{'profiles':[wrong]})
        self.assertEqual(characters.current(self.db,'main','hero')['revision'],1)
        self.submit(t,{'profiles':[revised]});history=characters.history(self.db,'main','hero')
        self.assertEqual(len(history['versions']),2);self.assertEqual(history['current']['revision'],2)
        self.assertIn('1',history['current']['sources']);self.assertTrue(history['valid']);self.assertEqual(self.task()['stage'],'draft')
    def test_dialogue_quality_can_trigger_repair(self):
        self.prepare();t=self.task();text='周行说：“此事必有蹊跷。”宋禾说：“此事必有蹊跷。”';self.submit(t,{'prose':text})
        t=self.task();respond(self.db,self.path,t)
        t=self.task();w=json.loads(rpath(t, 'input_path').read_text(encoding='utf-8'));data=review(w['payload']['checks'],text)
        data['checks'][-1]['result']='fix';data['verdict']='fix'
        data['findings']=[{'severity':'quality','impact':'两人对白可互换，责任立场在正文中消失','goal':'让宋禾提出交接条件，周行核对经过','evidence':ev(text)}]
        data['first_read']={'desire':'查证','obstacle':'不信','change':'无','read_on_reason':'人物难区分'}
        self.submit(t,data);self.assertEqual(self.task()['stage'],'repair');self.assertEqual(head(self.db,'main'),0)
    def test_growth_packet_keeps_unabsorbed_events_and_diagnoses_omissions(self):
        self.prepare();state=replay(self.db,'main')
        text='周行同意让宋禾自己签字。'
        for _ in range(12):
            old=state.get('character_developments',{}).get('hero',[])
            event={'op':'character_development','entity':'hero','before':old[-1]['hash'] if old else None,'change':'逐步让对方参与','cause':'双方协商','evidence':ev(text),'cause_evidence':ev(text)}
            apply(state,event,text,1)
        context=characters.context(self.db,'main',['hero'],state)
        self.assertEqual(len(context['developments']['hero']),12) # None reflected in saved profile yet.
        record=characters.current(self.db,'main','hero');record['growth_count']=10;record['growth_hash']=digest(state['character_developments']['hero'][:10])
        put(self.db,characters.key('main','hero'),record)
        context=characters.context(self.db,'main',['hero'],state)
        self.assertEqual(len(context['developments']['hero']),3)
        self.assertEqual(context['omitted_developments']['hero']['count'],9)
    def test_profiled_book_completes_with_voice_evolution_review(self):
        p=self.plan();p.update(volume_end=True,book_end=True)
        self.submit(self.task(),{'chapters':[p]});self.submit(self.task(),PROFILES);self.write();engine.approve(self.db)
        checks=[]
        for _ in range(20):
            t=self.task()
            if t.get('status')=='completed':break
            work=json.loads(rpath(t, 'input_path').read_text(encoding='utf-8'));checks.append(work['payload']['checks'])
            respond(self.db,self.path,t)
        self.assertEqual(t['status'],'completed')
        self.assertTrue(checks and all('voice_evolution' in c for c in checks))
        self.assertTrue(engine.audit(self.db)['ok'])
    def test_alias_resolves_one_profile(self):
        self.prepare();state=replay(self.db,'main');self.assertEqual(characters.resolve(state,'老周'),'hero')
        self.assertEqual(characters.resolve(state,'周行'),'hero')
    def test_revision_does_not_keep_removed_growth_as_current(self):
        self.prepare();text='周行让宋禾自己决定是否签字，并把笔递给她。'
        event={'op':'character_development','entity':'hero','before':None,'change':'尊重对方决定','cause':'宋禾主动要求签字','evidence':ev(text),'cause_evidence':ev(text)}
        self.write(text,[event]);engine.approve(self.db)
        self.submit(self.task(),{'chapters':[self.plan(2)]});task=self.task()
        updated=copy.deepcopy(PROFILES['profiles'][0]);updated['change_reason']='经历了共同签字'
        updated['biography']['experiences']=[{'text':'让宋禾签字','status':'established','evidence':ev(text)}]
        self.submit(task,{'profiles':[updated]})
        with transaction(self.db):revision=engine.begin_revision(self.db,1,'更改开篇')
        self.assertTrue(characters.usable(self.db,'main','hero'))
        self.assertFalse(characters.usable(self.db,revision['branch'],'hero'))
        self.assertFalse(replay(self.db,revision['branch']).get('character_developments'))
        self.assertEqual(head(self.db,'main'),1)
        task=self.task();self.assertEqual(task['stage'],'character_plan')
        self.assertEqual(json.loads(rpath(task, 'input_path').read_text(encoding='utf-8'))['payload']['entities'],['hero'])


class ProfileErrorDirectionTests(unittest.TestCase):
    """诊断按因果分层:字段放错层不能误报成实体问题(真实项目四个 worker 因此重复试错)。"""
    def setUp(self):self.state=seed(contract())
    def test_unknown_top_level_fields_are_named_not_misread_as_entity(self):
        p={'entity':'hero','tier':'major','based_on_chapter':3,'status':'active','growth_hash':'x','experiences':[],
           'biography':{'public_role':'看守','desire':'查清旧案'},'voice':{'register':'平','tactics':['问']}}
        with self.assertRaises(Error) as exc:characters.validate_profile(p,self.state,lambda ref:None)
        self.assertIn('未知顶层字段',str(exc.exception))
        for key in ('based_on_chapter','status','growth_hash','experiences'):
            self.assertIn(key,str(exc.exception))
        self.assertNotIn('稳定实体',str(exc.exception))
    def test_bad_entity_gets_entity_message(self):
        p={'entity':'stranger','tier':'walk_on','biography':{'public_role':'x','desire':'y'},'voice':{'register':'r','tactics':['t']}}
        with self.assertRaises(Error) as exc:characters.validate_profile(p,self.state,lambda ref:None)
        self.assertIn('稳定实体',str(exc.exception))

if __name__=='__main__':unittest.main()
