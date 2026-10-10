"""Rejections carry the ledger answer and a recovery path (报错自带答案)."""
import json
import tempfile
import unittest
from pathlib import Path
from support import contract, initialize, connect, respond, result, ev, plan
from novel_core import engine
from novel_core.ledger import seed, apply
from novel_core.memory import guide
from novel_core.store import Error


def spatial_state():
    c=json.loads((Path(__file__).resolve().parents[1]/'assets/spatial.example.json').read_text(encoding='utf-8'))
    c['context_mode']='legacy'  # These fixtures isolate error answers, not session handoff.
    c['word_range']=[1,100000]
    c['character_mode']='legacy'
    c['planning_mode']='legacy'
    return seed(c)


class SpatialAnswerTests(unittest.TestCase):
    def setUp(self): self.state=spatial_state()
    def apply(self,op,text='周行打开门，沿走廊下楼。',n=1,**data):
        apply(self.state,{'op':op,**data,'evidence':ev(text,n)},text,n)

    def test_placement_teleport_answers_with_current_place_and_move_shape(self):
        self.apply('state',entity='door',key='open',before=False,after=True)
        self.apply('move',entity='hero',**{'from':'r4','to':'hall4','route':['r4','hall4'],'links':['office-door'],'depart':0,'arrive':1})
        self.apply('placement',entity='lamp',before=None,after={'place':'r4'})
        with self.assertRaises(Error) as cm:
            self.apply('placement',entity='lamp',before={'place':'r4'},after={'holder':'hero'})
        self.assertEqual(cm.exception.code,'placement_teleport')
        d=cm.exception.details
        self.assertEqual(d['current_place'],'r4')
        self.assertEqual(d['target_place'],'hall4')
        self.assertIn('office-door',d['direct_path_links'])
        self.assertEqual(d['expect']['op'],'move')
        self.assertIn('map --place',d['verify'])

    def test_state_precondition_reports_ledger_current(self):
        self.apply('state',entity='hero',key='mood',before=None,after='沉')
        with self.assertRaises(Error) as cm:
            self.apply('state',entity='hero',key='mood',before='错值',after='定')
        self.assertEqual(cm.exception.code,'precondition')
        self.assertEqual(cm.exception.details['current'],'沉')
        self.assertEqual(cm.exception.details['submitted'],'错值')

    def test_alias_conflict_lists_owner(self):
        with self.assertRaises(Error) as cm:
            self.apply('entity',entity={'id':'double','name':'周行','kind':'person'})
        self.assertEqual(cm.exception.code,'alias_conflict')
        self.assertIn('周行',cm.exception.details['taken'])
        self.assertTrue(cm.exception.details['owners'])


class ReplayAnswerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        initialize(self.path,contract());self.db=connect(self.path)
    def tearDown(self):self.db.close();self.tmp.cleanup()

    def test_replay_conflict_carries_recovery(self):
        task=engine.next_task(self.db,self.path)
        n=task['chapter']
        engine.submit(self.db,result(task,{'chapters':[plan(n)]}))
        self.assertTrue(engine.submit(self.db,result(task,{'chapters':[plan(n)]}))['idempotent'])
        changed=dict(plan(n));changed['goal']=changed['goal']+'（另一版）'
        with self.assertRaises(Error) as cm:
            engine.submit(self.db,result(task,{'chapters':[changed]}))
        self.assertEqual(cm.exception.code,'replay_conflict')
        d=cm.exception.details
        self.assertEqual(d['task_id'],task['task_id'])
        self.assertIn('advance --after',d['recover'])


class GuideDisciplineTests(unittest.TestCase):
    def test_guide_carries_escalation_rule_for_every_stage(self):
        for stage in ('draft','repair','chapter_review','plan','context_scan'):
            text=guide(stage)
            self.assertIn('[纠错]',text)
            self.assertIn('不读 scripts 源码',text)
            self.assertIn('修正两轮仍失败',text)


if __name__=='__main__':
    unittest.main()
