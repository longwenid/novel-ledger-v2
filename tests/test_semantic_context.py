"""Structural preparation preserves complete units and necessary source chains."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from support import contract, initialize, connect, put, get, dump
from novel_core import contextflow
from novel_core.store import digest, Error


class SemanticContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        c=contract();c['context_mode']='bounded'
        initialize(self.path,c);self.db=connect(self.path)

    def tearDown(self):self.db.close();self.tmp.cleanup()

    def base(self,count=160):
        return {'schema':'novel-ledger-v2.pack/1','branch':'main','chapter':2,'stage':'chapter_review',
                'contract':get(self.db,'contract'),'plan':{'goal':'保留不同来源的前置条件'},
                'payload':{'prose':'本章完整正文。'},
                'state':{'knowledge':{f'hero:k{i}':{'claim':f'完整条件{i}',
                          'evidence':{'chapter':1,'start':i,'quote':f'源句{i}'}} for i in range(count)}}}

    def pending(self,work):
        self.db.execute("INSERT INTO tasks(id,branch,stage,chapter,input_hash,input,state) VALUES ('read','main','chapter_review',2,'fixture',?,'pending')",(dump(work),))

    def scan_reply(self,work):
        p=work['payload'];unit=p['units'][0]
        page=get(self.db,'candidate_text:'+p['prose_hash'])  # 直调 fit/accept,无 task_files 物化
        quote=page[unit['semantic_start']-p['start']:unit['semantic_end']-p['start']]
        return {'coverage':{k:p[k] for k in ('document','document_hash','start','end')},
                'notes':[{'text':'当前任务需要的完整约束。'*600,
                          'evidence':[{'document':p['document'],'start':unit['semantic_start'],'quote':quote}]}]}

    def next_reduction(self,base):
        for index in range(100):
            work=contextflow.fit(self.db,base)
            if work['stage']=='context_reduce':return work
            self.assertEqual(work['stage'],'context_scan')
            contextflow.accept(self.db,work,self.scan_reply(work),f'scan{index}')
        self.fail('No reduction')

    def test_long_indivisible_record_and_old_character_configuration_do_not_gate(self):
        put(self.db,'context_chars',1);put(self.db,'context_output_chars',-1)
        base=self.base(1);base['contract']['world_rules']=['完整约定。'*10000]
        base['payload']['prose']='完整正文。'*20000
        base['state']['knowledge']['hero:k0']['claim']='必要条件。'*20000
        work=contextflow.fit(self.db,base)
        self.assertEqual(work['stage'],'chapter_review')
        self.assertEqual(work['payload']['prose'],base['payload']['prose'])
        self.assertEqual(work['contract'],base['contract'])
        self.assertEqual(work['state'],base['state'])
        self.assertNotIn('context_capacity',work)
        self.assertNotIn('chars',work)

    def test_record_pages_cover_exact_source_without_splitting_evidence(self):
        value={'knowledge':{f'hero:k{i}':{'claim':'带引号"和反斜杠\\的完整条件'+str(i),
                  'evidence':{'chapter':1,'quote':'完整证据'+str(i),'start':i}} for i in range(70)}}
        text=contextflow.wire(value);offset=0;pieces=[];paths=[]
        while True:
            page=contextflow.bounded_read(self.db,value,start=offset)
            self.assertTrue(page['complete_semantic_units'])
            self.assertEqual(page['content_hash'],digest(text))
            self.assertEqual(page['start'],offset)
            pieces.append(page['text'])
            for unit in page['units']:
                record=json.loads('{'+text[unit['semantic_start']:unit['semantic_end']]+'}')
                self.assertEqual(len(record),1)
                item=next(iter(record.values()));self.assertIn('evidence',item)
                paths.append(unit['path'])
            if page['next_start'] is None:break
            offset=page['next_start']
        self.assertEqual(''.join(pieces),text)
        self.assertEqual(len(paths),70)

    def test_preparation_references_long_core_without_repeating_prose_or_style(self):
        base=self.base();base['payload']['prose']='长正文段落。'*20000
        base['style_anchor']={'sample':'完整声线样例。'*20000}
        base['contract']['world_rules']=['不可删除的完整约定。'*10000]
        first=contextflow.fit(self.db,base);self.pending(first)
        self.assertNotIn('target_payload',first);self.assertNotIn('contract',first)
        self.assertNotIn(base['payload']['prose'],dump(first))
        for field in ('payload','style_anchor','contract'):
            page=contextflow.material(self.db,'read',first['payload']['bundle'],'core:'+field)
            self.assertEqual(json.loads(page['text']),base[field])
            bundle=get(self.db,contextflow.view_key(first['payload']['bundle']))
            source=bundle['core_document']['text']
            self.assertEqual(source[page['source_start']:page['source_start']+len(page['text'])],page['text'])
            self.assertEqual(page['source_document_hash'],digest(source))
        contextflow.accept(self.db,first,self.scan_reply(first),'first')
        second=contextflow.fit(self.db,base)
        self.assertNotIn(base['payload']['prose'],dump(second))
        self.assertNotIn(base['style_anchor']['sample'],dump(second))

    def test_cannot_compact_reduce_preserves_all_notes_and_retrievable_nodes(self):
        base=self.base();work=self.next_reduction(base)
        before={n['id']:copy.deepcopy(n) for n in work['payload']['inputs']}
        contextflow.accept(self.db,work,{'cannot_compact':True,'reason':'所有条件各有必要因果，必须全部保留。'},'retain')
        bundle=get(self.db,contextflow.view_key(work['payload']['bundle']))
        kept=bundle['node_archive']['retain']
        self.assertEqual(kept['notes'],[note for n in before.values() for note in n['notes']])
        self.assertEqual(kept['retained_node_ids'],list(before))
        self.assertEqual({k:bundle['node_archive'][k] for k in before},before)
        self.assertEqual(get(self.db,'status'),'ready')
        self.pending(work)
        source=contextflow.material(self.db,'read',work['payload']['bundle'],'note:'+next(iter(before)))
        self.assertEqual(json.loads(source['text']),next(iter(before.values())))
        for index in range(100):
            work=contextflow.fit(self.db,base)
            if work['stage']=='chapter_review':break
            contextflow.accept(self.db,work,{'cannot_compact':True,'reason':'保留全部来源与结论。'},f'retain{index}')
        else:self.fail('Structural reduction did not finish')
        self.assertEqual(sum(len(n['notes']) for n in work['context_notes']),10)
        self.assertGreater(len(dump(work)),48000)  # 保留体量用整包长度观测，不再有 chars 字段
        self.assertEqual(work['payload']['prose'],base['payload']['prose'])

    def test_kept_and_duplicate_reject_loss_of_any_causal_source(self):
        base=self.base();work=self.next_reduction(base)
        p=work['payload'];bundle=get(self.db,contextflow.view_key(p['bundle']))
        first=p['inputs'][0]['notes'][0]
        doc=bundle['documents'][0];unit=doc['units'][1]
        second={'document':doc['id'],'start':unit['semantic_start'],
                'quote':doc['text'][unit['semantic_start']:unit['semantic_end']]}
        first['evidence'].append(copy.deepcopy(second))
        # Add this legitimate second source to the frozen input node, as a
        # multi-source causal note that must survive the next reduction.
        bundle['nodes'][0]['notes'][0]=copy.deepcopy(first)
        put(self.db,contextflow.view_key(p['bundle']),bundle)
        all_notes=[copy.deepcopy(n) for node in p['inputs'] for n in node['notes']]
        all_notes[0]['evidence']=all_notes[0]['evidence'][:1]
        for disposition in ('kept','duplicate'):
            accounting=[{'input_id':node['id'],'disposition':'kept','reason':'必要条件'} for node in p['inputs']]
            accounting[0].update(disposition=disposition,duplicate_of=0)
            with self.assertRaises(Error) as exc:
                contextflow.accept(self.db,work,{'notes':all_notes,'accounting':accounting},'bad')
            self.assertEqual(exc.exception.code,'context_coverage')

    def test_more_material_can_exceed_three_rounds_but_identical_sources_cannot_loop(self):
        work={'stage':'draft','branch':'main','context_identity':'additional-materials'}
        for chapter in range(1,6):
            prose='完整定向来源'+str(chapter)
            self.db.execute('INSERT INTO chapters VALUES (?,?,?,?,?)',('main',chapter,prose,'{}',digest(prose)))
            contextflow.request_more(self.db,work,{'context_more':[{'chapter':chapter}],'reason':'需要核实完整因果。'*200})
        prior=get(self.db,'context_extra:additional-materials')
        self.assertEqual(prior['rounds'],5)
        self.assertEqual(len(prior['materials']),5)
        with self.assertRaises(Error) as exc:
            contextflow.request_more(self.db,work,{'context_more':[{'chapter':5}],'reason':'重复同一来源'})
        self.assertEqual(exc.exception.code,'context_more_repeated')

    def test_kept_causal_sources_cannot_be_scattered_across_unrelated_outputs(self):
        base=self.base();work=self.next_reduction(base);p=work['payload']
        first=p['inputs'][0]['notes'][0]
        second=p['inputs'][1]['notes'][0]['evidence'][0]
        first['evidence'].append(copy.deepcopy(second))
        output=[copy.deepcopy(note) for n in p['inputs'] for note in n['notes']]
        output[0]['evidence']=output[0]['evidence'][:1]
        # The output union still contains both sources, but the causal pair
        # is split across separate conclusions instead of retained together.
        accounting=[{'input_id':n['id'],'disposition':'kept','reason':'完整保留因果'} for n in p['inputs']]
        with self.assertRaises(Error) as exc:
            contextflow.accept(self.db,work,{'notes':output,'accounting':accounting},'scattered')
        self.assertEqual(exc.exception.code,'context_coverage')
        output[0]['evidence'].append(copy.deepcopy(second))
        contextflow.accept(self.db,work,{'notes':output,'accounting':accounting},'whole-cause')

    def test_nested_conditions_do_not_split_an_event_or_fact_record(self):
        event={'op':'state','entity':'hero','key':'decision','before':None,
               'after':{'claim':'必须一起理解的选择','conditions':list(range(40))},
               'evidence':{'chapter':1,'start':0,'quote':'完整证据'}}
        for value in ([event],{'facts':{'hero:decision':event['after']}},
                      {'knowledge':{'hero:decision':{'claim':event['after'],'evidence':event['evidence']}}},
                      {'facts':{'hero:request':{'request':'真实世界事实','value':list(range(40))}}}):
            units=contextflow.semantic_units(dump(value))
            self.assertEqual(len(units),1)
            self.assertEqual(contextflow.bounded_read(self.db,value),value)

        wrapper=[{'kind':'requested_material','rounds':1,'materials':[
            {'request':{'entity':'hero'},'reason':'核对','value':[{'chapter':1,'index':i,'event_hash':str(i),'event':event} for i in range(40)]}]}]
        units=contextflow.semantic_units(dump(wrapper),'selected')
        events=[u for u in units if u['path'][-2:-1]==['value']]
        self.assertEqual(len(events),40)

    def test_more_material_deduplicates_overlapping_ranges_and_entity_aliases(self):
        from novel_core.ledger import seed,append_events
        work={'stage':'draft','branch':'main','context_identity':'source-dedup'}
        state=seed(get(self.db,'contract'))
        for chapter in range(1,4):
            prose='来源版本'+str(chapter)
            event={'op':'knowledge','entity':'hero','topic':'k'+str(chapter),'before':None,
                   'stance':'knows','claim':prose,'source':'合成来源',
                   'evidence':{'chapter':chapter,'start':0,'quote':prose}}
            self.db.execute('INSERT INTO chapters VALUES (?,?,?,?,?)',('main',chapter,prose,'{}',digest(prose)))
            append_events(self.db,'main',chapter,prose,[event],state)
        contextflow.request_more(self.db,work,{'context_more':[{'entity':'hero','end':2}],'reason':'核对前情'})
        with self.assertRaises(Error) as exc:
            contextflow.request_more(self.db,work,{'context_more':[{'entity':'老周','end':2}],'reason':'别名换法不应新增来源'})
        self.assertEqual(exc.exception.code,'context_more_repeated')
        contextflow.request_more(self.db,work,{'context_more':[{'entity':'老周','end':3}],'reason':'读取新增版本'})
        records=get(self.db,'context_extra:source-dedup')['materials']
        self.assertEqual([r['chapter'] for m in records for r in m['value']],[1,2,3])


if __name__=='__main__':unittest.main()
