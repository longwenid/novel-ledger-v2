"""Cross-task reuse is about equal purpose and frozen sources, never note size."""
import copy
import json
import tempfile
import unittest
from support import *
from novel_core import contextflow, sources
from novel_core.store import transaction, digest, Error


def base(chapter=2, count=64):
    return {'schema':'novel-ledger-v2.pack/1','branch':'main','chapter':chapter,'stage':'draft',
        'contract':{'reader_promise':'同一交易中的人物选择','world_rules':['未得情报不能知晓']},
        'plan':{'number':chapter,'goal':'决定是否相信同伴','conflict':'证词矛盾','change':'信任有代价',
                'entities':['hero'],'scenes':[{'intent':'核验证词','conflict':'价格分歧','turn':'先交押金'}]},
        'payload':{},'context_identity':'transport-'+str(chapter),
        'state':{'knowledge':{'hero:k'+str(i):{'stance':'knows','claim':'档案记载'+str(i),'source':'不可变合成夹具'}
                              for i in range(count)}}}


class IncrementalSourcesTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        initialize(self.path,contract());self.db=connect(self.path)

    def tearDown(self):self.db.close();self.tmp.cleanup()

    def prepare(self, work_base, preserve=False, external_read=False):
        stages=[]
        for i in range(100):
            work=contextflow.fit(self.db,work_base)
            if work['stage'] not in ('context_scan','context_reduce'):return work,stages
            stages.append(work['stage']);tid=digest([work,i,len(stages)])
            self.db.execute('INSERT OR REPLACE INTO tasks (id,branch,stage,chapter,input_hash,input,state) '
                            'VALUES (?,?,?,?,?,?,?)',(tid,'main',work['stage'],work['chapter'],digest(work),dump(work),'done'))
            from novel_core.dependencies import record_initial, record_read
            record_initial(self.db,tid,work)
            if external_read:record_read(self.db,tid,{'command':'recall','entity':'hero'}, {'records':[]})
            p=work['payload']
            if preserve:data={'cannot_compact':True,'reason':'每项条件是本任务不可丢失的对照材料'}
            elif work['stage']=='context_scan':
                data={'coverage':{k:p[k] for k in ('document','document_hash','start','end')},
                      'notes':[{'text':'合成测试保留本页的完整已读条件','evidence':[{
                          'document':p['document'],'start':p['start'],'quote':p['fragment']}]}]}
            else:
                data={'notes':[copy.deepcopy(note) for n in p['inputs'] for note in n['notes']],
                      'accounting':[{'input_id':n['id'],'disposition':'kept','reason':'保留原条件及全部证据'} for n in p['inputs']]}
            contextflow.accept(self.db,work,data,tid)
        self.fail('synthetic preparation did not converge')

    def test_existing_source_version_skips_structural_parser(self):
        value={'knowledge':{'hero:k'+str(i):{'claim':'完整内容'+str(i)} for i in range(64)}}
        text=dump(value);identity=sources.source_id('main','state');calls=[]
        def locate(*args,**kwargs):calls.append(1);return contextflow.semantic_units(*args,**kwargs)
        first=sources.register_document(self.db,identity,'state',text,locate,value)
        second=sources.register_document(self.db,identity,'state',text,locate,value)
        self.assertEqual(len(calls),1)
        self.assertEqual(first['units'],second['units'])
        self.assertEqual(second['index_stats'],{'version_hit':True,'structural_walks':0,'new_units':0,'reused_units':64})

    def test_added_unit_reuses_every_old_complete_unit(self):
        value={'records':[{'id':'r'+str(i),'claim':'语义完整条件'+str(i),'evidence':['旧证据'+str(i)]} for i in range(64)]}
        identity=sources.source_id('main','history')
        first=sources.register_document(self.db,identity,'history',dump(value),contextflow.semantic_units,value)
        old={u['unit_id']:u['unit_version'] for u in first['units']}
        value['records'].insert(0,{'id':'new','claim':'新增一项有独立来源的条件'})
        second=sources.register_document(self.db,identity,'history',dump(value),contextflow.semantic_units,value)
        current={u['unit_id']:u['unit_version'] for u in second['units']}
        self.assertEqual(second['index_stats']['new_units'],1)
        self.assertEqual(second['index_stats']['reused_units'],64)
        self.assertTrue(all(current[k]==v for k,v in old.items()))
        self.assertEqual(sources.read_document(self.db,identity,first['version'])['text'],dump({'records':value['records'][1:]}))
        self.assertEqual(sources.source_units(self.db,identity,first['version']),first['units'])
        original=first['units'][0]
        self.assertEqual(sources.read_unit(self.db,identity,original['unit_id'],original['unit_version'])['value'],
                         value['records'][1])

    def test_source_schema_does_not_commit_enclosing_transaction(self):
        with self.assertRaisesRegex(RuntimeError,'rollback'):
            with transaction(self.db):
                sources.ensure_schema(self.db)
                put(self.db,'sentinel','must rollback')
                raise RuntimeError('rollback')
        self.assertIsNone(get(self.db,'sentinel'))

    def test_damaged_derived_directory_rebuilds_and_unit_values_cannot_override_source(self):
        value={'knowledge':{'hero:key':{'claim':'不可变完整条件'}}};text=dump(value);identity=sources.source_id('main','state')
        doc=sources.register_document(self.db,identity,'state',text,contextflow.semantic_units,value)
        self.db.execute('UPDATE material_source_versions SET units=? WHERE source_id=?',('[]',identity))
        with self.assertRaises(Error):sources.read_document(self.db,identity,doc['version'])
        rebuilt=sources.register_document(self.db,identity,'state',text,contextflow.semantic_units,value)
        self.assertEqual(rebuilt['units'],doc['units'])
        unit=doc['units'][0]
        self.db.execute('UPDATE material_source_units SET value_json=? WHERE source_id=?',(dump({'claim':'派生值错误覆盖'}),identity))
        with self.assertRaises(Error):sources.read_unit(self.db,identity,unit['unit_id'],unit['unit_version'])

    def alternate_transport(self,value):
        value=copy.deepcopy(value);value['context_identity']='different-transport'
        return value

    def test_equal_complete_purpose_new_transport_skips_preparation_tasks(self):
        first,stages=self.prepare(base(2))
        second,second_stages=self.prepare(self.alternate_transport(base(2)))
        self.assertGreater(len(stages),0)
        self.assertEqual(second_stages,[])
        self.assertTrue(second['context_materials']['preparation_reuse']['hit'])
        self.assertNotEqual(first['context_materials']['bundle'],second['context_materials']['bundle'])
        self.assertEqual(second['context_notes'],first['context_notes'])
        self.assertEqual(second['context_materials']['scanned_pages'],0)
        self.assertEqual(second['context_materials']['source_index']['new_units'],0)

    def test_chapter_number_is_semantic_even_without_deadline_keys(self):
        original=base(2);original['contract']['world_rules'].append('每逢第三章兑现先前口头约定，不能再以同一理由推迟。')
        self.prepare(original)
        changed=copy.deepcopy(original);changed['chapter']=3;changed['plan']['number']=3
        self.assertEqual(contextflow.fit(self.db,changed)['stage'],'context_scan')

    def test_different_scene_contract_prose_stage_or_deadline_cannot_hit(self):
        self.prepare(base(2))
        cases=[]
        changed=base(3);changed['plan']['scenes'][0]['turn']='拒付押金';cases.append(changed)
        changed=base(3);changed['contract']['world_rules'].append('交易不得赊欠');cases.append(changed)
        changed=base(3);changed['payload']['prose']='他没有交押金。';cases.append(changed)
        changed=base(3);changed['stage']='facts';cases.append(changed)
        changed=base(3);changed['state']['hooks']={'deal':{'due':3,'status':'open'}};cases.append(changed)
        for changed in cases:
            with self.subTest(changed=changed):
                work=contextflow.fit(self.db,changed)
                self.assertEqual(work['stage'],'context_scan')
        temporal=base(4);temporal['state']['hooks']={'deal':{'due':6,'status':'open'}}
        self.prepare(temporal)
        temporal['chapter']=5;temporal['plan']['number']=5
        self.assertEqual(contextflow.fit(self.db,temporal)['stage'],'context_scan')

    def test_material_change_never_reuses_previous_omission(self):
        self.prepare(base(2))
        changed=base(3);changed['state']['knowledge']['hero:new']={'stance':'believes','claim':'他误信对方已经付钱','source':'新正文'}
        work=contextflow.fit(self.db,changed)
        self.assertEqual(work['stage'],'context_scan')
        bundle=get(self.db,contextflow.view_key(work['payload']['bundle']))
        state=bundle['documents'][0]
        self.assertEqual(state['index_stats']['new_units'],1)
        self.assertEqual(state['index_stats']['reused_units'],64)

    def test_external_or_negative_query_disables_preparation_reuse(self):
        self.prepare(base(2),external_read=True)
        self.assertEqual(contextflow.fit(self.db,self.alternate_transport(base(2)))['stage'],'context_scan')

    def test_cached_cannot_compact_preserves_old_nodes_and_full_source(self):
        first,first_stages=self.prepare(base(2,160),preserve=True)
        second,stages=self.prepare(self.alternate_transport(base(2,160)))
        self.assertIn('context_reduce',first_stages)
        self.assertEqual(stages,[])
        bundle=get(self.db,contextflow.view_key(second['context_materials']['bundle']))
        self.assertEqual(first['context_notes'],second['context_notes'])
        self.assertTrue(all(n['id'] in bundle['node_archive'] for n in first['context_notes']))
        source=bundle['documents'][0]['text']
        retained=''.join(note['evidence'][0]['quote'] for n in second['context_notes'] for note in n['notes'])
        self.assertEqual(retained,source)
        retained_id=second['context_notes'][0]['retained_node_ids'][0]
        tid=digest(second)
        self.db.execute('INSERT INTO tasks (id,branch,stage,chapter,input_hash,input,state) VALUES (?,?,?,?,?,?,?)',
                        (tid,'main','draft',3,digest(second),dump(second),'pending'))
        read=contextflow.material(self.db,tid,bundle['id'],'note:'+retained_id)
        self.assertIn(bundle['node_archive'][retained_id]['notes'][0]['evidence'][0]['quote'],
                      json.loads(read['text'])['notes'][0]['evidence'][0]['quote'])

    def test_unknown_initial_dependencies_cannot_certify_reuse(self):
        first,_=self.prepare(base(2))
        for node_id in first['context_notes']:
            self.db.execute('DELETE FROM task_reads WHERE consumer_task=?',(node_id['id'],))
        self.assertEqual(contextflow.fit(self.db,self.alternate_transport(base(2)))['stage'],'context_scan')

    def test_new_read_after_cache_creation_invalidates_cached_interpretation(self):
        first,_=self.prepare(base(2))
        from novel_core.dependencies import record_read
        record_read(self.db,first['context_notes'][0]['id'],{'command':'recall','topic':'later'}, {'records':[]})
        self.assertEqual(contextflow.fit(self.db,self.alternate_transport(base(2)))['stage'],'context_scan')

    def test_character_index_keeps_full_profile_in_core(self):
        work_base=base(2);work_base['stage']='character_index'
        profile={'motivation':'完整动机','experiences':['完整经历'+str(i) for i in range(100)]}
        work_base['character_context']={'profiles':{'hero':profile}}
        work=contextflow.fit(self.db,work_base)
        self.assertEqual(work['stage'],'context_scan')
        bundle=get(self.db,contextflow.view_key(work['payload']['bundle']))
        self.assertEqual(bundle['core']['character_context']['profiles']['hero'],profile)
        self.assertFalse(any(d['field']=='character_context' for d in bundle['documents']))

    def test_legacy_bundle_keeps_original_snapshot_hash_and_units(self):
        original=contextflow.fit(self.db,base(2));ident=original['payload']['bundle']
        bundle=get(self.db,contextflow.view_key(ident))
        bundle.pop('cache_basis')
        for doc in bundle['documents']:
            for unit in doc['units']:
                for key in ('source_id','unit_id','unit_version'):unit.pop(key,None)
        put(self.db,contextflow.view_key(ident),bundle)
        before=dump(bundle)
        same=contextflow.fit(self.db,base(2))
        self.assertEqual(same['payload']['bundle'],ident)
        self.assertEqual(dump(get(self.db,contextflow.view_key(ident))),before)


if __name__=='__main__':unittest.main()
