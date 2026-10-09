"""Volume-outline alignment, adjacent rhythm and prior-volume handoff.

真实项目的缺陷模式:卷纲节拍与章计划脱钩、多章连用同一种收束模具(审稿反复
点名收束同质化)。这些测试把方向对齐从软约束变成引擎校验。
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from support import *
from novel_core import design, engine
from novel_core.memory import pack
from novel_core.store import Error, put

EXAMPLE=json.loads((Path(__file__).resolve().parents[1]/'assets/design.example.json').read_text(encoding='utf-8'))
SHAPES=design.CLOSING_SHAPES


class PlanAlignmentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        c=contract();c['planning_mode']='layered';c['volumes'].append({'id':'v2','purpose':'公开结算'})
        initialize(self.path,c);self.db=connect(self.path)
        put(self.db,'pilot_approved',True)
        self.submit(self.task(),EXAMPLE['world_plan']);self.submit(self.task(),EXAMPLE['volume_plan'])
    def tearDown(self): self.db.close();self.temp.cleanup()

    def task(self):return engine.next_task(self.db,self.path)
    def submit(self,task,data):return engine.submit(self.db,result(task,copy.deepcopy(data)))
    def plan(self,n=1,**over):
        p=copy.deepcopy(EXAMPLE['chapter_plan']);p.update(number=n)
        p['ending_shape']=over.pop('shape',SHAPES[n%len(SHAPES)]+f':测试收束{n}')
        p['sequence_refs']=over.pop('refs',[min(n,len(EXAMPLE['volume_plan']['sequence']))])
        p.update(over)
        return p
    def chapter(self,n=1,**over):
        self.submit(self.task(),{'chapters':[self.plan(n,**over)]})
        respond(self.db,self.path,self.task())
        t=self.task();w=json.loads(rpath(t, 'input_path').read_text(encoding='utf-8'));text=w['payload']['prose']
        data=review(w['payload']['checks'],text,n);data.update(events=[],summary='交出凭证，合作成立。')
        self.submit(t,data);respond(self.db,self.path,self.task())

    def test_ending_shape_is_required_with_controlled_category(self):
        p=self.plan(1);p.pop('ending_shape')
        with self.assertRaises(Error) as exc:self.submit(self.task(),{'chapters':[p]})
        self.assertEqual(exc.exception.code,'brief')
        p=self.plan(1);p['ending_shape']='高光:不在词表里'
        with self.assertRaises(Error) as exc:self.submit(self.task(),{'chapters':[p]})
        self.assertEqual(exc.exception.code,'brief')

    def test_sequence_refs_must_cite_real_beats_when_volume_has_sequence(self):
        p=self.plan(1);p.pop('sequence_refs')
        with self.assertRaises(Error) as exc:self.submit(self.task(),{'chapters':[p]})
        self.assertEqual(exc.exception.code,'brief')
        p=self.plan(1);p['sequence_refs']=[5]  # 本卷 sequence 只有 1 拍
        with self.assertRaises(Error) as exc:self.submit(self.task(),{'chapters':[p]})
        self.assertEqual(exc.exception.code,'brief')
        self.submit(self.task(),{'chapters':[self.plan(1)]})  # 合法引用通过

    def test_closing_category_can_repeat_when_story_requires_it(self):
        self.chapter(1,shape='悬念:当铺掌柜认出旧币')
        self.chapter(2,shape='闭环:案子交割')
        self.chapter(3,shape='交接:账册移交事务所')
        t=self.task()
        self.submit(t,{'chapters':[self.plan(4,shape='悬念:追索所得使旧币的归属再次成疑')]})
        self.assertEqual(self.task()['stage'],'draft')

    def test_rhythm_navigation_contains_adjacent_intentions_not_full_history(self):
        self.chapter(1,shape='悬念:当铺掌柜认出旧币')
        self.chapter(2,shape='悬念:追问留下更多疑点')
        work=pack(self.db,'main',3,'plan',{})
        rows=[item for item in work['selected'] if item['kind']=='recent_rhythm']
        self.assertEqual([r['chapter'] for r in rows],[2,1])
        self.assertTrue(all(r['written'] and r['prose_hash'] and r['plan_hash'] for r in rows))
        self.assertIn('rhythm',rows[0]['outline'])
        self.assertNotIn('scenes',rows[0]['outline'])
        reader=pack(self.db,'main',2,'reader',{'prose':'正文','checks':[]})
        self.assertFalse(any(item['kind']=='recent_rhythm' for item in reader['selected']))

    def test_volume_plan_pack_carries_prior_volume_handoff(self):
        work=pack(self.db,'main',2,'volume_plan',{'design_scope':'v2','previous_design':None})
        prior=work['prior_volume']
        self.assertEqual(prior['volume'],'v1')
        self.assertEqual(prior['carries_out']['handoff'],EXAMPLE['volume_plan']['handoff'])
        self.assertIn('以账本 recall 为准',prior['note'])
        base=pack(self.db,'main',1,'volume_plan',{'design_scope':'v1','previous_design':None})
        self.assertNotIn('prior_volume',base)  # 第一卷没有前卷可承接

    def test_writer_brief_and_discipline_carry_the_new_dimensions(self):
        self.submit(self.task(),{'chapters':[self.plan(1)]})
        t=self.task();w=json.loads(rpath(t, 'input_path').read_text(encoding='utf-8'))
        self.assertEqual(w['discipline']['plan'].count('ending_shape'),1)
        self.assertIn('sequence_refs',w['discipline']['plan'])
        brief=w['writer_brief']
        self.assertEqual(brief['dramatic_task']['ending_shape'],self.plan(1)['ending_shape'])
        self.assertEqual(brief['rhythm'],self.plan(1)['rhythm'])
        self.assertEqual(brief['execution'],self.plan(1)['execution'])
        self.assertNotIn('rhythm',w['plan'])
        self.assertNotIn('execution',w['plan'])
        self.assertEqual(w['plan_section_refs'],{'execution':'writer_brief.execution','rhythm':'writer_brief.rhythm'})
        self.assertIn('收束形态',rpath(t, 'brief_path').read_text(encoding='utf-8'))


if __name__=='__main__':unittest.main()
