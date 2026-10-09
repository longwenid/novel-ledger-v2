"""Real planning coordinates, without claiming to grade literary rhythm."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from support import *
from novel_core import design
from novel_core.store import Error

EXAMPLE=json.loads((Path(__file__).resolve().parents[1]/'assets/design.example.json').read_text(encoding='utf-8'))


class ChapterRhythmTests(unittest.TestCase):
    def plan(self):
        return copy.deepcopy(EXAMPLE['chapter_plan'])

    def test_one_event_can_carry_several_developments_and_an_explicit_target(self):
        p=self.plan();p['execution']['target_han']=6500
        p['execution']['target_reason']='同一次交换中的博弈和责任落实需要充分演出；不靠新增四五件事。'
        design.validate_chapter_execution(p)
        self.assertEqual(len(p['scenes']),1)
        self.assertEqual(len(p['execution']['expansion_units']),2)
        self.assertEqual(design.writer_brief(p)['execution']['target_han'],6500)

    def test_legacy_plan_without_new_fields_remains_valid(self):
        p=self.plan();p.pop('execution');p.pop('rhythm')
        design.validate_chapter_execution(p)
        brief=design.writer_brief(p)
        self.assertNotIn('execution',brief);self.assertNotIn('rhythm',brief)

    def test_sustained_pressure_does_not_require_a_breathing_point_or_alternation(self):
        p=self.plan();p['rhythm']['pressure_curve']=[
            {'scene':1,'movement':'持续收紧','cause':'交出凭证后的每次要求都将人物责任具体化'},
            {'scene':1,'movement':'继续收紧','cause':'期限迫近，签字后的退路变窄'}]
        p['rhythm']['breathing_points']=[]
        design.validate_chapter_execution(p)
        self.assertEqual(design.writer_brief(p)['rhythm']['breathing_points'],[])

    def test_declared_units_must_refer_to_actual_scenes_and_distinct_ids(self):
        for change in ('scene','duplicate_id','empty_development','boolean_target','missing_units','unknown_field'):
            with self.subTest(change=change):
                p=self.plan()
                if change=='scene':p['execution']['expansion_units'][0]['scene']=2
                elif change=='duplicate_id':p['execution']['expansion_units'][1]['id']=p['execution']['expansion_units'][0]['id']
                elif change=='empty_development':p['execution']['expansion_units'][0]['development']=' '
                elif change=='boolean_target':p['execution']['target_han']=True
                elif change=='missing_units':p['execution'].pop('expansion_units')
                else:p['execution']['guaranteed_quality']=True
                with self.assertRaises(Error) as exc:design.validate_chapter_execution(p)
                self.assertEqual(exc.exception.code,'chapter_execution')

    def test_rhythm_requires_real_links_and_order_without_numeric_quality_scores(self):
        for change in ('missing_handoff','unknown_scene','boolean_scene','empty_cause','reverse_order','breathing_string'):
            with self.subTest(change=change):
                p=self.plan()
                if change=='missing_handoff':p['rhythm'].pop('handoff')
                elif change=='unknown_scene':p['rhythm']['breathing_points'][0]['scene']=2
                elif change=='boolean_scene':p['rhythm']['pressure_curve'][0]['scene']=True
                elif change=='empty_cause':p['rhythm']['pressure_curve'][0]['cause']=''
                elif change=='reverse_order':
                    p['scenes'].append(copy.deepcopy(p['scenes'][0]))
                    p['rhythm']['pressure_curve'][0]['scene']=2
                else:p['rhythm']['breathing_points']='每章必须放松'
                with self.assertRaises(Error) as exc:design.validate_chapter_execution(p)
                self.assertEqual(exc.exception.code,'chapter_rhythm')

    def test_large_dramatization_and_target_are_preserved_without_a_character_cap(self):
        p=self.plan();long_development='人物检查每份凭证的责任归属，并依据对方反应改变交换条件。'*2000
        p['scenes'][0]['dramatization']=[long_development]
        p['execution']['target_han']=1000000
        p['execution']['expansion_units'][0]['weight']=10**400
        design.validate_chapter_execution(p)
        brief=design.writer_brief(p)
        self.assertEqual(brief['scene_proposals'][0]['dramatization'][0],long_development)
        self.assertEqual(brief['execution']['target_han'],1000000)
        rendered=design.render_brief({'chapter':1,'writer_brief':brief})
        self.assertEqual(rendered.count(long_development),1)
        self.assertIn('目标汉字数：1000000',rendered)

    def test_scene_dramatization_requires_actual_text(self):
        p=self.plan();p['scenes'][0]['dramatization']=[{'event_count':5}]
        with self.assertRaises(Error) as exc:design.validate_chapter_execution(p)
        self.assertEqual(exc.exception.code,'chapter_execution')

    def test_rendered_brief_carries_the_function_of_the_pause_and_consequences(self):
        p=self.plan();brief=design.writer_brief(p)
        rendered=design.render_brief({'chapter':1,'writer_brief':brief})
        rhythm=p['rhythm'];point=rhythm['breathing_points'][0]
        for value in (rhythm['chapter_role'],rhythm['carries_in'],rhythm['handoff'],point['placement'],point['function']):
            self.assertIn(value,rendered)
        for unit in p['execution']['expansion_units']:
            self.assertIn(unit['development'],rendered);self.assertIn(unit['expected_change'],rendered)

    def test_rendered_brief_displays_inherited_author_target_once(self):
        p=self.plan();work={'chapter':1,'writer_brief':design.writer_brief(p),
                            'payload':{'chapter_length':{'target_han':4200}}}
        rendered=design.render_brief(work)
        self.assertEqual(rendered.count('本章目标汉字数：4200'),1)
        self.assertNotIn('target_han',work['writer_brief']['execution'])

    def test_unlayered_pipeline_also_validates_declared_structure_atomically(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp);initialize(path,contract());db=connect(path)
            try:
                task=engine.next_task(db,path);p=self.plan()
                p['rhythm']['pressure_curve'][0]['scene']=2
                with self.assertRaises(Error) as exc:engine.submit(db,result(task,{'chapters':[p]}))
                self.assertEqual(exc.exception.code,'chapter_rhythm')
                self.assertEqual(db.execute('SELECT COUNT(*) FROM plans').fetchone()[0],0)
                self.assertEqual(engine.next_task(db,path)['task_id'],task['task_id'])
                engine.submit(db,result(task,{'chapters':[self.plan()]}))
                self.assertEqual(engine.next_task(db,path)['stage'],'draft')
            finally:db.close()


if __name__=='__main__':unittest.main()
