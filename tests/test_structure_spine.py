"""Book-spine and volume-outline absorption (v1 ideas, v2 philosophy)."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from support import *
from novel_core import design, engine
from novel_core.store import Error, put, transaction, head
EXAMPLE=json.loads((Path(__file__).resolve().parents[1]/'assets/design.example.json').read_text(encoding='utf-8'))


class SpineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        c=contract();c['planning_mode']='layered';c['volumes'].append({'id':'v2','purpose':'公开结算'})
        initialize(self.path,c);self.db=connect(self.path)
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def task(self):return engine.next_task(self.db,self.path)
    def submit(self,task,data):return engine.submit(self.db,result(task,copy.deepcopy(data)))
    def prepare(self):
        self.submit(self.task(),EXAMPLE['world_plan']);self.submit(self.task(),EXAMPLE['volume_plan'])
    def plan(self,n=1,major=False,refs=None):
        p=copy.deepcopy(EXAMPLE['chapter_plan']);p.update(number=n)
        shapes=('钩子','悬念','闭环','交接','爆发','静收','反转','余波')
        p['ending_shape']=shapes[n%len(shapes)]+f':测试收束{n}'
        p['sequence_refs']=[min(n,len(EXAMPLE['volume_plan']['sequence']))]
        if major:
            p['major_turn']=True
            p['alternatives']=['先交换后作证','先作证后交换']
            p['selection_reason']='先交换让合作有对等代价'
            if refs is not None:p['spine_refs']=refs
        return p

    def test_world_without_spine_rejected(self):
        bad=copy.deepcopy(EXAMPLE['world_plan']);bad.pop('event_spine')
        with self.assertRaises(Error) as exc:self.submit(self.task(),bad)
        self.assertEqual(exc.exception.code,'design')

    def test_spine_shape_and_unique_ids(self):
        bad=copy.deepcopy(EXAMPLE['world_plan'])
        bad['event_spine']=bad['event_spine']|{'hidden':bad['event_spine']['main']}
        with self.assertRaises(Error) as exc:self.submit(self.task(),bad)
        self.assertEqual(exc.exception.code,'design')
        bad2=copy.deepcopy(EXAMPLE['world_plan'])
        dup=copy.deepcopy(bad2['event_spine']['tension']);dup[0]['id']=bad2['event_spine']['main'][0]['id']
        bad2['event_spine']['tension']=dup
        with self.assertRaises(Error):self.submit(self.task(),bad2)

    def test_volume_outline_required(self):
        self.submit(self.task(),EXAMPLE['world_plan'])
        bad=copy.deepcopy(EXAMPLE['volume_plan']);bad.pop('handoff')
        with self.assertRaises(Error) as exc:self.submit(self.task(),bad)
        self.assertEqual(exc.exception.code,'design')

    def finish_chapter(self,n=1):
        respond(self.db,self.path,self.task())                        # draft
        t=self.task();w=json.loads(rpath(t,'input_path').read_text(encoding='utf-8'));text=w['payload']['prose']
        data=review(w['payload']['checks'],text,n);data.update(events=[],summary='s');self.submit(t,data)
        respond(self.db,self.path,self.task())                        # reader
        return head(self.db,'main')

    def test_major_turn_requires_and_validates_spine_refs(self):
        self.prepare()
        self.submit(self.task(),{'chapters':[self.plan(1)]})          # non-major: no refs needed
        self.assertEqual(self.finish_chapter(1),1)
        if self.task().get('stage')=='await_author':engine.approve(self.db)
        t=self.task();self.assertEqual(t['stage'],'plan')
        major=self.plan(2,major=True)
        with self.assertRaises(Error) as exc:self.submit(t,{'chapters':[major]})
        self.assertEqual(exc.exception.code,'plan')
        major['spine_refs']=['sp:missing-99']
        with self.assertRaises(Error):self.submit(t,{'chapters':[major]})
        major['spine_refs']=['sp:main-01','sp:ten-01']
        self.submit(t,{'chapters':[major]})

    def test_spineless_legacy_world_grandfathered(self):
        self.submit(self.task(),EXAMPLE['world_plan'])
        old=design.current(self.db,'main','world')
        old['data'].pop('event_spine')
        with transaction(self.db):put(self.db,design.key('main','world'),old)
        self.submit(self.task(),EXAMPLE['volume_plan'])
        self.submit(self.task(),{'chapters':[self.plan(1,major=True)]})   # no refs required

    def test_spine_survives_into_revision_copy(self):
        self.prepare();self.submit(self.task(),{'chapters':[self.plan(1)]})
        self.assertEqual(self.finish_chapter(1),1)
        engine.begin_revision(self.db,1,'复核')
        self.assertIsNotNone(design.spine_ids(self.db,'main'))


if __name__=='__main__':unittest.main()
