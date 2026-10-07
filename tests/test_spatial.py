import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from support import *
from novel_core.store import Error, digest
from novel_core.ledger import seed,apply,replay
from novel_core.memory import pack,recall
from novel_core.spatial import address,at,spatial_view,address_signals,mermaid


def spatial_contract():
    c=json.loads((Path(__file__).resolve().parents[1]/'assets/spatial.example.json').read_text(encoding='utf-8'))
    c['context_mode']='legacy' # These fixtures isolate spatial gates, not session handoff.
    c['word_range']=[1,100000]
    c['character_mode']='legacy'
    c['planning_mode']='legacy' # Preserve v1.1 spatial regression scenarios.
    return c


class SpatialTests(unittest.TestCase):
    def setUp(self):self.state=seed(spatial_contract())
    def apply(self,op,text='周行打开门，沿走廊下楼。',n=1,**data):
        e={'op':op,**data,'evidence':ev(text,n)}
        apply(self.state,e,text,n)
    def open_door(self):self.apply('state',entity='door',key='open',before=False,after=True)
    def move_out(self,**overrides):
        data={'entity':'hero','from':'r4','to':'hall4','route':['r4','hall4'],'links':['office-door'],'depart':0,'arrive':1}
        data.update(overrides);self.apply('move',**data)
    def test_room_number_does_not_infer_floor(self):
        self.assertEqual(address(self.state,'r4')['floor'],'4')
        self.assertEqual(address(self.state,'r4')['room_number'],'101')
        self.assertEqual(address(self.state,'r3')['floor'],'3')
    def test_repeated_wrong_address_cannot_replace_anchor(self):
        with self.assertRaises(Error):self.apply('anchor',entity='r4',key='floor',value='3')
        with self.assertRaises(Error):self.apply('anchor_assert',entity='r4',key='room_number',value='505')
        with self.assertRaises(Error):self.apply('state',entity='r4',key='floor',before='4',after='3')
    def test_office_can_move_only_with_explicit_cause(self):
        with self.assertRaises(Error):self.apply('anchor_change',entity='office',key='office_place',before='r4',after='r3')
        text='局长办公室因整修迁往三楼505室，原四楼101室封存。'
        self.apply('anchor_change',text,entity='office',key='office_place',before='r4',after='r3',reason='整修搬迁',change_evidence=ev(text))
        self.assertEqual(self.state['facts']['office:office_place'],'r3')
        self.assertEqual(address(self.state,'r4')['room_number'],'101')
    def test_fixed_birthday_cannot_change_as_normal_state(self):
        with self.assertRaises(Error):self.apply('state',entity='hero',key='birth_date',before='1990-04-02',after='1992-04-02')
        with self.assertRaises(Error):self.apply('anchor_change',entity='hero',key='birth_date',before='1990-04-02',after='1992-04-02',reason='换说法',change_evidence=ev('周行打开门，沿走廊下楼。'))
    def test_renumber_preserves_physical_place_id(self):
        text='全楼统一换门牌，四楼101室改编为401室。'
        self.apply('place_change',text,entity='r4',before={'room_number':'101'},after={'room_number':'401'},reason='统一门牌编号',change_evidence=ev(text))
        self.assertEqual(self.state['facts']['office:office_place'],'r4')
        self.assertEqual(address(self.state,'r4')['room_number'],'401')
    def test_closed_door_blocks_route(self):
        with self.assertRaises(Error):self.move_out()
        self.open_door();self.move_out();self.assertEqual(at(self.state,'hero'),'hall4')
    def test_known_route_minimum_blocks_instant_travel(self):
        self.open_door()
        with self.assertRaises(Error):self.move_out(arrive=0)
    def test_unknown_route_is_not_automatically_invented(self):
        with self.assertRaises(Error):self.apply('move',entity='hero',**{'from':'r4','to':'r3'},route=['r4','r3'],links=['secret-tunnel'])
    def test_carrying_object_follows_person_and_cannot_be_in_two_places(self):
        self.open_door();self.move_out()
        self.assertEqual(at(self.state,'key'),'hall4')
        with self.assertRaises(Error):self.apply('placement',entity='key',before={'holder':'hero'},after={'place':'r4'})
        with self.assertRaises(Error):self.apply('placement',entity='key',before={'holder':'hero'},after={'place':'hall4','holder':'ally'})
        self.apply('placement',entity='key',before={'holder':'hero'},after={'holder':'ally'})
        self.assertEqual(self.state['placements']['key']['position'],{'holder':'ally'})
    def test_nested_holder_cycle_rejected(self):
        with self.assertRaises(Error):self.apply('placement',entity='hero',before={'place':'r4'},after={'holder':'key'})
    def test_moves_do_not_overlap(self):
        self.open_door();self.move_out()
        with self.assertRaises(Error):self.apply('move',entity='hero',**{'from':'hall4','to':'r3'},route=['hall4','r3'],links=['stairs'],depart=0,arrive=5)
    def test_visibility_requires_correct_channel_direction_and_light(self):
        args={'observer':'ally','target':'hero','channel':'sight','from':'hall4','to':'r4','link':'look-in'}
        with self.assertRaises(Error):self.apply('perceive',**args)
        self.open_door();self.apply('perceive',**args)
        self.apply('state',entity='lamp',key='lit',before=True,after=False)
        with self.assertRaises(Error):self.apply('perceive',**args)
        with self.assertRaises(Error):self.apply('perceive',**{**args,'channel':'sound'})
        self.assertFalse(self.state['knowledge'])
    def test_floor_inheritance_and_duplicate_rooms(self):
        s=copy.deepcopy(self.state)
        text='隔间仍在四楼。'
        apply(s,{'op':'entity','entity':{'id':'closet','name':'101室储物间','kind':'place'},'evidence':ev(text)},text,1)
        with self.assertRaises(Error):apply(s,{'op':'place','entity':'closet','parent':'r4','attributes':{'floor':'3'},'evidence':ev(text)},text,1)
        apply(s,{'op':'place','entity':'closet','parent':'r4','attributes':{},'evidence':ev(text)},text,1)
        self.assertEqual(address(s,'closet')['floor'],'4')
    def test_lexical_drift_signals_are_not_world_facts(self):
        text='局长办公室在3楼505。'
        signals=address_signals(self.state,text)
        self.assertEqual({s['key'] for s in signals},{'floor','room_number'})
        self.assertFalse(address_signals(self.state,'局长办公室在四楼101。'))
        self.assertTrue(address_signals(self.state,'他说：“局长办公室在3楼505。”')) # Requires a judgment, not an automatic conviction.
        self.assertEqual(self.state['facts']['office:office_place'],'r4')
    def test_anchor_cannot_create_second_position(self):
        for key in ('location','location_id','holder'):
            with self.assertRaises(Error):self.apply('anchor',entity='hero',key=key,value='r3')
    def test_unknown_carrier_cannot_erase_known_location(self):
        self.apply('entity',entity={'id':'visitor','name':'访客','kind':'person'})
        with self.assertRaises(Error):self.apply('placement',entity='key',before={'holder':'hero'},after={'holder':'visitor'})
        self.assertEqual(at(self.state,'key'),'r4')
    def test_carried_time_survives_drop_and_handoff(self):
        self.open_door();self.move_out(arrive=100)
        self.apply('placement',entity='key',before={'holder':'hero'},after={'holder':'ally'})
        self.apply('placement',entity='key',before={'holder':'ally'},after={'place':'hall4'})
        args={'entity':'key','from':'hall4','to':'r3','route':['hall4','r3'],'links':['stairs']}
        with self.assertRaises(Error):self.apply('move',**args,depart=0,arrive=3)
        self.apply('move',**args,depart=100,arrive=103)
        self.assertEqual(at(self.state,'key'),'r3')
    def test_receiver_cannot_depart_before_timed_handoff(self):
        self.open_door();self.move_out(arrive=100)
        self.apply('placement',entity='key',before={'holder':'hero'},after={'holder':'ally'})
        args={'entity':'ally','from':'hall4','to':'r3','route':['hall4','r3'],'links':['stairs']}
        with self.assertRaises(Error):self.apply('move',**args,depart=1,arrive=4)
        self.apply('move',**args,depart=100,arrive=103)
        self.assertEqual(at(self.state,'key'),'r3')
    def test_new_item_inherits_carrier_arrival(self):
        self.open_door();self.move_out(arrive=100)
        self.apply('entity',entity={'id':'note','name':'便条','kind':'object'})
        self.apply('placement',entity='note',before=None,after={'holder':'hero'})
        self.apply('placement',entity='note',before={'holder':'hero'},after={'place':'hall4'})
        self.assertEqual(self.state['placements']['note']['last_arrival'],100)
    def test_binding_cannot_point_to_unknown_room(self):
        self.apply('entity',entity={'id':'other_office','name':'值班室','kind':'function'})
        with self.assertRaises(Error):self.apply('anchor',entity='other_office',key='office_place',value='missing')
    def test_map_uses_current_number_after_renumber(self):
        from novel_core.spatial import resolve_place
        self.test_renumber_preserves_physical_place_id()
        view=spatial_view(self.state,[resolve_place(self.state,'局长办公室')])
        self.assertIn('401',next(p for p in view['places'] if p['id']=='r4')['display_name'])
        self.assertNotIn('101室',mermaid(view))
    def test_partial_map_and_mermaid(self):
        view=spatial_view(self.state,['hq'],children_depth=1)
        self.assertEqual(len(view['places']),4)
        self.assertTrue(view['absence_is_not_proof'])
        self.assertIn('101',mermaid(view))
        self.assertFalse(view['links']['office-door']['traversable_or_perceptible'])


class SpatialPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        initialize(self.path,spatial_contract());self.db=connect(self.path)
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def to_facts(self,prose):
        t=engine.next_task(self.db,self.path)
        p=plan(1);p['entities']=['hero','office'];p['scenes']=[{'place':'r4','present':['hero']}]
        engine.submit(self.db,result(t,{'chapters':[p]}))
        t=engine.next_task(self.db,self.path);engine.submit(self.db,result(t,{'prose':prose}))
        return engine.next_task(self.db,self.path)
    def test_writer_pack_includes_unmentioned_office_binding_and_map(self):
        t=engine.next_task(self.db,self.path);p=plan(1);p['entities']=['office']
        engine.submit(self.db,result(t,{'chapters':[p]}));t=engine.next_task(self.db,self.path)
        work=json.loads(rpath(t, 'input_path').read_text(encoding='utf-8'))
        self.assertEqual(work['continuity']['anchors']['r4:floor']['value'],'4')
        self.assertEqual(work['continuity']['conditions']['door:open'],False)
    def test_direct_place_reference_includes_layout_and_package_anchor_is_hard(self):
        task=engine.next_task(self.db,self.path);p=plan(1);p['entities']=['r4']
        engine.submit(self.db,result(task,{'chapters':[p]}))
        work=pack(self.db,'main',1,'draft',{})
        self.assertIn('r4',{p['id'] for p in work['continuity']['places']})
        self.assertLessEqual(len(dump(work)),work['context_anchors']['package_chars'])
        put(self.db,'context_chars',len(dump(work))-500)
        with self.assertRaises(Error):pack(self.db,'main',1,'draft',{})
    def test_omitted_events_do_not_silently_ignore_explicit_address_drift(self):
        text='周行来到局长办公室，办公室在3楼505。'
        task=self.to_facts(text);work=json.loads(rpath(task, 'input_path').read_text(encoding='utf-8'))
        self.assertIn('spatial',work['payload']['checks']);self.assertIn('stable_facts',work['payload']['checks'])
        data=review(work['payload']['checks'],text);data.update(summary='到达办公室',events=[])
        with self.assertRaises(Error):engine.submit(self.db,result(task,data))
        data['verdict']='fix';data['checks'][0]['result']='fix'
        data['findings']=[{'severity':'blocker','impact':'办公室无故由四楼101变为三楼505','goal':'恢复既有地址或先演出合法搬迁','evidence':ev(text)}]
        data['address_responses']=[{'id':s['id'],'status':'error','reason':'没有搬迁记录','evidence':ev(text)} for s in work['address_signals']]
        engine.submit(self.db,result(task,data))
        self.assertEqual(engine.next_task(self.db,self.path)['stage'],'repair')
        self.assertEqual(engine.head(self.db,'main'),0)
    def test_historical_map_replays_prior_address(self):
        text='全楼统一换门牌，四楼101室改编为401室。'
        task=self.to_facts(text);work=json.loads(rpath(task, 'input_path').read_text(encoding='utf-8'))
        e={'op':'place_change','entity':'r4','before':{'room_number':'101'},'after':{'room_number':'401'},'reason':'编号调整','change_evidence':ev(text),'evidence':ev(text)}
        data=review(work['payload']['checks'],text);data.update(summary='统一重编门牌',events=[e])
        # A historical label in a renumbering sentence is not an unexplained new address.
        data['address_responses']=[{'id':s['id'],'status':'not_conflict','reason':'正文正在演出重编门牌','evidence':ev(text)} for s in work['address_signals']]
        engine.submit(self.db,result(task,data));respond(self.db,self.path,engine.next_task(self.db,self.path))
        self.assertEqual(address(replay(self.db,'main',0),'r4')['room_number'],'101')
        self.assertEqual(address(replay(self.db,'main',1),'r4')['room_number'],'401')
        self.assertEqual(recall(self.db,'main',entity='市局四楼101室')['total'],1)

class FirstAppearanceTests(unittest.TestCase):
    def test_chapter_one_establishes_address_chapter_two_must_respect_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);initialize(path,contract());db=connect(path)
            try:
                plans=[plan(1),plan(2)]
                plans[0]['scenes']=[{'label':'局长办公室','present':['hero']}]
                t=engine.next_task(db,path);engine.submit(db,result(t,{'chapters':plans}))
                text='市局大楼四楼的101室是局长办公室。周行站在门内。'
                respond(db,path,engine.next_task(db,path),prose=text)
                t=engine.next_task(db,path);work=json.loads(rpath(t, 'input_path').read_text(encoding='utf-8'))
                events=[{'op':'entity','entity':{'id':i,'name':name,'kind':kind}} for i,name,kind in [('hq','市局大楼','place'),('r4','市局四楼101室','place'),('office','局长办公室','function')]]
                events.extend([{'op':'place','entity':'hq','parent':None,'attributes':{}},
                               {'op':'place','entity':'r4','parent':'hq','attributes':{'floor':'4','room_number':'101'}},
                               {'op':'anchor','entity':'office','key':'office_place','value':'r4','policy':'eventful'},
                               {'op':'placement','entity':'hero','before':None,'after':{'place':'r4'}}])
                for e in events:e['evidence']=ev(text)
                data=review(work['payload']['checks'],text);data.update(events=events,summary='四楼101明确为局长办公室。')
                engine.submit(db,result(t,data));respond(db,path,engine.next_task(db,path));engine.approve(db)
                wrong='周行再次走进局长办公室，三楼505的门牌刚擦亮。'
                respond(db,path,engine.next_task(db,path),prose=wrong)
                t=engine.next_task(db,path);work=json.loads(rpath(t, 'input_path').read_text(encoding='utf-8'))
                self.assertEqual({x['key'] for x in work['address_signals']},{'floor','room_number'})
                self.assertEqual(work['continuity']['anchors']['r4:floor']['source']['chapter'],1)
                data=review(work['payload']['checks'],wrong,2);data.update(events=[],summary='回到办公室。')
                with self.assertRaises(Error):engine.submit(db,result(t,data))
                self.assertEqual(engine.head(db,'main'),1)
                self.assertTrue(engine.audit(db)['ok'])
            finally:db.close()

if __name__=='__main__':unittest.main()
