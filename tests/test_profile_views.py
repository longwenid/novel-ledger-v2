import copy
import json
import tempfile
import unittest
from pathlib import Path
from support import *
from novel_core import characters
from novel_core.ledger import replay,apply
from novel_core.store import Error,digest
from novel_core import dependencies


PROFILES=json.loads((Path(__file__).resolve().parents[1]/'assets/characters.example.json').read_text(encoding='utf-8'))


class ProfileViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        config=contract();config.update(character_mode='profiled',character_views='scoped')
        initialize(self.path,config);self.db=connect(self.path)
        self.plan=plan(2);self.plan.update(topics=['topic:17'],scenes=[{'present':['hero','ally'],'speakers':['hero','ally']}])
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def prepare(self,count=1000):
        profile=copy.deepcopy(PROFILES['profiles'][0])
        profile['biography']['experiences']=[{'text':f'话题 {number} 对应独立的既往选择与后果，只有重遇该问题时才需要重演。'+'其具体经过仍完整保存。'*8,
                'status':'design','relevance':{'topics':[f'topic:{number}']}} for number in range(count)]
        characters.save(self.db,'main',2,{'profiles':[profile]},['hero'],'profile-fixture')
        return characters.current(self.db,'main','hero')
    def pending(self,record):
        work={'branch':'main','stage':'character_index','chapter':2,'payload':{'entities':['hero']},'character_context':characters.context(self.db,'main',['hero'],replay(self.db,'main'),self.plan,'character_index')}
        self.db.execute("INSERT OR REPLACE INTO tasks(id,branch,stage,chapter,input_hash,input,state) VALUES ('index-fixture','main','character_index',2,?,?,'pending')",(digest(work),dump(work)))
        dependencies.ensure_schema(self.db);dependencies.record_initial(self.db,'index-fixture',work)
        for number in record['sources']:
            row=self.db.execute('SELECT prose,hash FROM chapters WHERE branch=? AND number=?',('main',int(number))).fetchone()
            dependencies.record_read(self.db,'index-fixture',{'command':'recall','branch':'main','chapter':int(number)},
                {'number':int(number),'hash':row['hash'],'prose':row['prose']})
        items=[]
        for (section,number),value in characters.indexed_items(record).items():
            item={'section':section,'item':number,'value_hash':digest(value),'classification':'global','reason':'测试固定全局语气，不代表文学质量证明。'}
            if section=='biography.experiences':
                item.update(classification='scoped',reason='测试材料定义此独立经历仅由同一稳定话题触发；完整核心没有跨话题影响。',scope_complete=True,scope={'topics':[f'topic:{number}']})
            items.append(item)
        index={'entity':'hero','profile_hash':record['hash'],'core_hash':digest(characters.section_value(record,'core')),'coverage':'complete','items':items,'core_depends_on':[]}
        return work,{'indexes':[index]}
    def index(self,record):
        work,data=self.pending(record)
        characters.save_index(self.db,'main',data,'index-fixture',work)
        return data
    def view(self,plan=None,stage='draft',state=None):
        return characters.context(self.db,'main',['hero'],state or replay(self.db,'main'),self.plan if plan is None else plan,stage)
    def test_thousand_experiences_use_verified_scope_without_repeating_history_directory(self):
        record=self.prepare();self.index(record);full=characters.context(self.db,'main',['hero'],replay(self.db,'main'))
        view=self.view();data=view['profiles']['hero']['data']
        self.assertEqual(data['biography']['experiences'],[record['data']['biography']['experiences'][17]])
        self.assertEqual(data['biography']['desire'],record['data']['biography']['desire'])
        self.assertEqual(data['voice'],record['data']['voice'])
        self.assertEqual(view['omitted_profiles']['hero']['biography.experiences']['count'],999)
        self.assertLess(len(dump(view)),len(dump(full))/10)
        self.assertLess(len(dump(view['omitted_profiles'])),500)
        self.assertEqual(characters.current(self.db,'main','hero'),record)
        self.assertTrue(any(ref.get('path')=='/biography/experiences/17' for ref in view['source_refs']))
        self.assertEqual(characters.next_index_job(self.db,'main',2,self.plan),None)
    def test_unreviewed_relevance_labels_do_not_authorize_omission(self):
        record=self.prepare();view=self.view()
        self.assertEqual(view['profiles']['hero']['data'],record['data'])
        self.assertEqual(view['omitted_profiles'],{})
        job=characters.next_index_job(self.db,'main',2,self.plan)
        self.assertEqual(job[0],'character_index');self.assertEqual(job[2]['entities'],['hero'])
    def test_missing_scene_topics_and_unscoped_legacy_calls_keep_all_items(self):
        record=self.prepare();self.index(record)
        missing=copy.deepcopy(self.plan);missing.pop('topics')
        self.assertEqual(self.view(missing)['profiles']['hero']['data'],record['data'])
        self.assertEqual(characters.context(self.db,'main',['hero'],replay(self.db,'main'))['profiles']['hero'],record)
        config=get(self.db,'contract');config.pop('character_views');put(self.db,'contract',config)
        self.assertEqual(self.view()['profiles']['hero']['data'],record['data'])
        self.assertIsNone(characters.next_index_job(self.db,'main',2,self.plan))
    def test_unknown_global_and_transitive_causal_dependencies_remain_complete(self):
        record=self.prepare(30);work,data=self.pending(record)
        items=data['indexes'][0]['items']
        for number,classification in ((0,'unknown'),(1,'global')):
            items[number]['classification']=classification;items[number].pop('scope');items[number].pop('scope_complete')
        items[17]['depends_on']=[{'section':'biography.experiences','item':3}]
        items[3]['depends_on']=[{'section':'biography.experiences','item':4}]
        items[4]['depends_on']=[{'section':'biography.experiences','item':3}]
        characters.save_index(self.db,'main',data,'index-fixture',work)
        selected=self.view()['profiles']['hero']['data']['biography']['experiences']
        self.assertEqual(selected,[record['data']['biography']['experiences'][number] for number in (0,1,3,4,17)])

    def test_core_dependencies_always_seed_the_transitive_selection(self):
        record=self.prepare(30);work,data=self.pending(record)
        data['indexes'][0]['core_depends_on']=[{'section':'biography.experiences','item':0}]
        data['indexes'][0]['items'][0]['depends_on']=[{'section':'biography.experiences','item':2}]
        characters.save_index(self.db,'main',data,'index-fixture',work)
        selected=self.view()['profiles']['hero']['data']['biography']['experiences']
        self.assertEqual(selected,[record['data']['biography']['experiences'][number] for number in (0,2,17)])
        query=characters.profile_read(self.db,'main','hero','biography.experiences',query={'topics':['topic:17']})
        self.assertEqual(query['value'],selected)

    def test_core_dependency_declaration_cannot_be_absent_or_invalid(self):
        record=self.prepare(30);work,data=self.pending(record)
        for value in (None,[{'section':'biography.experiences','item':999}],[{'section':'biography.experiences','item':0}]*2):
            data['indexes'][0]['core_depends_on']=value
            with self.assertRaises(Error):characters.save_index(self.db,'main',data,'index-fixture',work)
    def test_relationship_and_example_can_depend_on_old_experience(self):
        record=self.prepare(30)
        prose='周行说：“你自己决定签不签，我留下核对过的凭证。”'
        self.db.execute('INSERT INTO chapters VALUES (?,?,?,?,?)',('main',1,prose,dump({}),digest(prose)))
        profile=copy.deepcopy(record['data']);profile['change_reason']='补入已有对白来源'
        profile['examples']=[{'speaker':'hero','note':'尊重同伴自行决定','evidence':ev(prose),'relevance':{'entities':['ally']}}]
        characters.save(self.db,'main',2,{'profiles':[profile]},['hero'],'profile-fixture-2');record=characters.current(self.db,'main','hero')
        work,data=self.pending(record)
        mode=next(item for item in data['indexes'][0]['items'] if item['section']=='voice.relationship_modes' and item['item']==0)
        mode['depends_on']=[{'section':'biography.experiences','item':29}]
        example=next(item for item in data['indexes'][0]['items'] if item['section']=='examples')
        example.update(classification='scoped',scope_complete=True,scope={'entities':['ally']})
        example['depends_on']=[{'section':'biography.experiences','item':28}]
        characters.save_index(self.db,'main',data,'index-fixture',work)
        selected=self.view()['profiles']['hero']['data']['biography']['experiences']
        self.assertEqual(selected,[record['data']['biography']['experiences'][number] for number in (17,28,29)])
        self.assertEqual(self.view()['profiles']['hero']['sources'],record['sources'])
    def test_task_scope_and_full_frozen_profile_are_required(self):
        record=self.prepare(3);work,data=self.pending(record)
        bad=copy.deepcopy(work);bad['character_context']['profiles']['hero']['data']['biography']['experiences'].pop()
        with self.assertRaises(Error):characters.save_index(self.db,'main',data,'index-fixture',bad)
        self.db.execute("UPDATE tasks SET stage='character_plan' WHERE id='index-fixture'")
        with self.assertRaises(Error):characters.save_index(self.db,'main',data,'index-fixture',work)
        self.assertFalse(characters.valid_index(self.db,'main','hero'))
    def test_coverage_hash_and_exhaustive_condition_claims_are_checked(self):
        record=self.prepare(3);work,data=self.pending(record)
        changes=[lambda p:p['items'].pop(),lambda p:p['items'][0].update(value_hash='wrong'),
                 lambda p:p.update(core_hash='wrong'),lambda p:p['items'][0].pop('scope_complete'),
                 lambda p:p['items'][0].update(scope_complete=False),
                 lambda p:p['items'][0].update(depends_on=[{'section':'examples','item':50}])]
        for change in changes:
            with self.subTest(change=change):
                bad=copy.deepcopy(data);change(bad['indexes'][0])
                with self.assertRaises(Error):characters.save_index(self.db,'main',bad,'index-fixture',work)
        characters.save_index(self.db,'main',data,'index-fixture',work)
        self.assertTrue(characters.valid_index(self.db,'main','hero'))
    def test_source_version_and_core_change_invalidate_selection(self):
        record=self.prepare(30)
        prose='周行曾因为轻信口头保证，让宋禾承担了交接责任。'
        self.db.execute('INSERT INTO chapters VALUES (?,?,?,?,?)',('main',1,prose,dump({}),digest(prose)))
        profile=copy.deepcopy(record['data']);profile['change_reason']='补上已经发生的责任经历'
        profile['biography']['experiences'][0]={'text':'轻信口头保证导致同伴担责','status':'established','evidence':ev(prose)}
        characters.save(self.db,'main',2,{'profiles':[profile]},['hero'],'profile-fixture-2');record=characters.current(self.db,'main','hero');self.index(record)
        self.db.execute("UPDATE chapters SET hash='changed-source-version' WHERE branch='main' AND number=1")
        self.assertFalse(characters.valid_index(self.db,'main','hero'))
        self.assertEqual(self.view()['profiles']['hero']['data'],record['data'])
        self.db.execute('UPDATE chapters SET hash=? WHERE branch=? AND number=?',(digest(prose),'main',1))
        changed=copy.deepcopy(record);changed['data']['biography']['desire']='改为保护整个调查团队'
        put(self.db,characters.key('main','hero'),changed)
        self.assertFalse(characters.valid_index(self.db,'main','hero'))
        self.assertEqual(self.view()['profiles']['hero']['data'],changed['data'])
    def test_scoped_history_requires_actual_complete_source_delivery(self):
        record=self.prepare(3);prose='周行曾把决定权交给宋禾，而宋禾因此承担了签字责任。'
        self.db.execute('INSERT INTO chapters VALUES (?,?,?,?,?)',('main',1,prose,dump({}),digest(prose)))
        profile=copy.deepcopy(record['data']);profile['change_reason']='补入已有因果来源'
        profile['biography']['experiences'][0]={'text':'让同伴决定签字','status':'established','evidence':ev(prose)}
        characters.save(self.db,'main',2,{'profiles':[profile]},['hero'],'profile-fixture-2');record=characters.current(self.db,'main','hero')
        work,data=self.pending(record)
        self.db.execute("DELETE FROM task_reads WHERE consumer_task='index-fixture'")
        dependencies.record_initial(self.db,'index-fixture',work)
        for fragment in ({'summary':prose},{'number':1,'hash':digest(prose),'prose':prose[:3]}):
            dependencies.record_read(self.db,'index-fixture',{'command':'recall','branch':'main','chapter':1},fragment)
        with self.assertRaises(Error) as error:characters.save_index(self.db,'main',data,'index-fixture',work)
        self.assertEqual(error.exception.code,'character_index_sources')
        unknown=copy.deepcopy(data)
        for item in unknown['indexes'][0]['items']:
            item['classification']='unknown';item.pop('scope',None);item.pop('scope_complete',None)
        characters.save_index(self.db,'main',unknown,'index-fixture',work)
        self.assertEqual(self.view()['profiles']['hero']['data'],record['data'])
        dependencies.record_read(self.db,'index-fixture',{'command':'recall','branch':'main','chapter':1},
            {'number':1,'hash':digest(prose),'prose':prose})
        characters.save_index(self.db,'main',data,'index-fixture',work)
        self.assertTrue(characters.valid_index(self.db,'main','hero'))
        self.assertEqual(characters.index_read(self.db,'main','hero')['index']['verified_source_reads'],{'1':digest(prose)})
    def test_large_core_and_unknown_material_have_no_character_caps(self):
        record=self.prepare(30);record['data']['biography']['contradiction']='愿意合作却不敢交付信任，必须保留它的每个因果。'*6000
        put(self.db,characters.key('main','hero'),record);self.index(record)
        self.assertEqual(self.view()['profiles']['hero']['data']['biography']['contradiction'],record['data']['biography']['contradiction'])
    def test_unabsorbed_growth_invalidates_index_and_is_never_dropped(self):
        record=self.prepare();self.index(record);state=replay(self.db,'main');prose='周行让宋禾自己决定是否签字。'
        for _ in range(12):
            events=state.get('character_developments',{}).get('hero',[])
            apply(state,{'op':'character_development','entity':'hero','before':events[-1]['hash'] if events else None,'change':'同伴自主选择','cause':'共同核对责任','evidence':ev(prose),'cause_evidence':ev(prose)},prose,1)
        view=self.view(state=state)
        self.assertEqual(len(view['developments']['hero']),12)
        self.assertEqual(view['profiles']['hero']['data'],record['data'])
    def test_fact_and_index_tasks_read_full_material(self):
        record=self.prepare();self.index(record)
        for stage in ('chapter_review','character_index','character_plan'):
            with self.subTest(stage=stage):self.assertEqual(self.view(stage=stage)['profiles']['hero']['data'],record['data'])
    def test_historical_section_item_read_returns_complete_version_and_source(self):
        record=self.prepare(30);first=characters.profile_read(self.db,'main','hero','biography.experiences',17,1)
        profile=copy.deepcopy(record['data']);profile['change_reason']='明确当前恐惧';profile['biography']['fear']='担心承担责任的人再次受伤'
        characters.save(self.db,'main',2,{'profiles':[profile]},['hero'],'profile-fixture-2')
        old=characters.profile_read(self.db,'main','hero','core',revision=record['hash'])
        self.assertEqual(old['value']['biography']['fear'],record['data']['biography']['fear'])
        self.assertEqual(first['source_ref']['path'],'/biography/experiences/17')
        self.assertEqual(first['source_ref']['value_hash'],digest(first['value']))
        self.assertEqual(characters.profile_read(self.db,'main','hero','biography.experiences',17,1),first)
        with self.assertRaises(Error):characters.profile_read(self.db,'main','hero','biography.experiences',1000)
        with self.assertRaises(Error):characters.profile_read(self.db,'main','hero','core',revision='missing')
    def test_directional_query_returns_complete_items_core_and_dependencies(self):
        record=self.prepare();work,data=self.pending(record)
        data['indexes'][0]['items'][17]['depends_on']=[{'section':'biography.experiences','item':3}]
        characters.save_index(self.db,'main',data,'index-fixture',work)
        result=characters.profile_read(self.db,'main','hero','biography.experiences',query={'topics':['topic:17']})
        self.assertEqual(result['value'],[record['data']['biography']['experiences'][number] for number in (3,17)])
        self.assertEqual(result['core'],characters.section_value(record,'core'))
        self.assertEqual(result['query']['omitted_items'],998)
        self.assertTrue(any(ref.get('path')=='/biography/experiences/3' for ref in result['source_refs']))
        self.assertLess(len(dump(result)),len(dump(record))/10)
        missing=characters.profile_read(self.db,'main','hero','biography.experiences',query={'entities':['ally']})
        self.assertEqual(missing['value'],record['data']['biography']['experiences'])
        self.assertEqual(missing['query']['status'],'full_fallback')
        with self.assertRaises(Error):characters.profile_read(self.db,'main','hero','biography.experiences',17,query={'topics':['topic:17']})

    def test_legacy_archived_profile_keeps_authoring_branch_after_revision_copy(self):
        record=self.prepare(3);record.pop('source_branch')
        self.db.execute("INSERT INTO tasks(id,branch,stage,chapter,input_hash,input,state) VALUES ('profile-fixture','main','character_plan',2,'legacy','{}','done')")
        self.db.execute("INSERT INTO branches VALUES ('child','main',1,1,NULL,'test','candidate')")
        put(self.db,characters.version_key('child','hero',record['hash']),record)
        replacement=copy.deepcopy(record);replacement['hash']='new-profile';replacement['revision']=2
        put(self.db,characters.key('child','hero'),replacement)
        old=characters.profile_read(self.db,'child','hero','biography.experiences',0,record['hash'])
        self.assertEqual(old['requested_branch'],'child');self.assertEqual(old['source_branch'],'main')
        self.assertEqual(old['source_ref']['branch'],'main')

    def test_delivered_character_watch_includes_index_and_source_versions(self):
        record=self.prepare(30);work,data=self.pending(record)
        query={'command':'character','branch':'main','entity':'hero','section':'biography.experiences','topic':['topic:17']}
        dependencies.record_read(self.db,'index-fixture',query,characters.profile_read(self.db,'main','hero','biography.experiences',query={'topics':['topic:17']}))
        self.assertTrue(dependencies.inspect(self.db,'index-fixture')['entries'][-1]['versions_unchanged'])
        characters.save_index(self.db,'main',data,'index-fixture',work)
        self.assertFalse(dependencies.inspect(self.db,'index-fixture')['entries'][-1]['versions_unchanged'])


class ProfilePipelineTests(unittest.TestCase):
    def test_scoped_mode_dispatches_index_with_complete_profiles_before_draft(self):
        from test_characters import PLAN
        with tempfile.TemporaryDirectory() as temp:
            c=contract();c.update(character_mode='profiled',character_views='scoped')
            initialize(temp,c);db=connect(temp)
            try:
                task=engine.next_task(db,temp);engine.submit(db,result(task,{'chapters':[copy.deepcopy(PLAN)]}))
                task=engine.next_task(db,temp);self.assertEqual(task['stage'],'character_plan')
                engine.submit(db,result(task,copy.deepcopy(PROFILES)))
                index_task=engine.next_task(db,temp);self.assertEqual(index_task['stage'],'character_index')
                work=envelope(index_task);state_before=digest(replay(db,'main'));indexes=[]
                for eid in work['payload']['entities']:
                    record=characters.current(db,'main',eid)
                    self.assertEqual(work['character_context']['profiles'][eid]['data'],record['data'])
                    indexes.append({'entity':eid,'profile_hash':record['hash'],'core_hash':digest(characters.section_value(record,'core')),'core_depends_on':[],
                        'coverage':'complete','items':[{'section':section,'item':number,'value_hash':digest(value),'classification':'unknown','reason':'工程流程测试无法判断语义，保留完整材料。'} for (section,number),value in characters.indexed_items(record).items()]})
                self.assertEqual(engine.next_task(db,temp)['task_id'],index_task['task_id'])
                receipt=result(index_task,{'indexes':indexes});engine.submit(db,receipt)
                self.assertTrue(engine.submit(db,receipt)['idempotent']);self.assertEqual(digest(replay(db,'main')),state_before)
                self.assertEqual(engine.next_task(db,temp)['stage'],'draft')
            finally:db.close()


if __name__=='__main__':unittest.main()
