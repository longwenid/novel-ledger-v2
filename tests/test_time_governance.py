"""Time governance: grounded clock evidence, day schedules, thread clocks."""
import json
import tempfile
import unittest
from pathlib import Path
from support import *
from novel_core import engine
from novel_core.ledger import seed, apply, resource_key
from novel_core.store import Error, head, get


class TimeGovernanceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        c=contract();c['clock_unit']='天'
        initialize(self.path,c);self.db=connect(self.path)
    def tearDown(self): self.db.close();self.temp.cleanup()
    def task(self): return engine.next_task(self.db,self.path)

    def test_clock_evidence_must_be_a_time_statement(self):
        respond(self.db,self.path,self.task())                     # plan ch1
        t=self.task();respond(self.db,self.path,t,prose='周行在档案室核对记录，逐页写下批注。')
        t=self.task()                                              # facts
        work=json.loads(rpath(t, 'input_path').read_text());prose=work['payload']['prose']
        data=review(work['payload']['checks'],prose)
        data.update(events=[{'op':'clock','before':0,'after':1,'evidence':ev(prose)}],summary='核对档案。')
        with self.assertRaises(Error) as exc: engine.submit(self.db,result(t,data))
        self.assertEqual(exc.exception.code,'time_evidence')
        self.assertEqual(self.task()['task_id'],t['task_id'])

    def test_time_word_evidence_passes(self):
        respond(self.db,self.path,self.task())
        t=self.task();respond(self.db,self.path,t,prose='次日清晨，周行在档案室核对记录。')
        t=self.task()
        work=json.loads(rpath(t, 'input_path').read_text());prose=work['payload']['prose']
        data=review(work['payload']['checks'],prose)
        data.update(events=[{'op':'clock','before':0,'after':1,'evidence':ev(prose)}],summary='次日核对档案。')
        engine.submit(self.db,result(t,data))
        self.assertEqual(self.task()['stage'],'reader')

    def test_thread_clocks_are_independent_and_monotone(self):
        s=seed(get(self.db,'contract'))
        text='次日清晨，两条线各自推进。'
        apply(s,{'op':'clock','before':0,'after':1,'evidence':ev(text)},text,1)
        apply(s,{'op':'clock','thread':'b线','before':1,'after':3,'evidence':ev(text)},text,1)
        self.assertEqual(s['clock'],1);self.assertEqual(s['clock_threads']['b线'],3)
        with self.assertRaises(Error) as exc:
            apply(s,{'op':'clock','thread':'b线','before':1,'after':2,'evidence':ev(text)},text,1)
        self.assertEqual(exc.exception.code,'time_conflict')

    def test_day_schedule_checked_against_plan_hints(self):
        respond(self.db,self.path,self.task())
        t=self.task();respond(self.db,self.path,t,prose='次日清晨，周行在档案室核对记录。')
        t=self.task()
        work=json.loads(rpath(t, 'input_path').read_text());prose=work['payload']['prose']
        data=review(work['payload']['checks'],prose)
        data.update(events=[{'op':'clock','before':0,'after':1,'evidence':ev(prose)}],summary='次日核对。')
        engine.submit(self.db,result(t,data))
        respond(self.db,self.path,self.task())                     # reader
        if self.task().get('stage')=='await_author':engine.approve(self.db)
        p=plan(2);p['scenes']=[{'label':'货栈','present':['hero'],'time_hint':'D+2 黄昏，先清点再碰头'}]
        self.assertEqual(self.task()['stage'],'plan')
        engine.submit(self.db,result(self.task(),{'chapters':[p]})) if False else engine.submit(self.db,result(engine.next_task(self.db,self.path),{'chapters':[p]}))
        prow=json.loads(self.db.execute("SELECT data FROM plans WHERE branch='main' AND number=2").fetchone()[0])
        self.assertEqual(prow['day_anchor'],1);self.assertEqual(prow['day_span'],[2,2])
        t=self.task();respond(self.db,self.path,t,prose='两天后，周行走进货栈清点货物。')
        t=self.task()
        work=json.loads(rpath(t, 'input_path').read_text());prose=work['payload']['prose']
        data=review(work['payload']['checks'],prose,2)
        data.update(events=[{'op':'clock','before':1,'after':2,'evidence':ev(prose,2)}],summary='进货栈。')
        with self.assertRaises(Error) as exc: engine.submit(self.db,result(t,data))
        self.assertEqual(exc.exception.code,'time_schedule')
        data['events']=[{'op':'clock','before':1,'after':3,'evidence':ev(prose,2)}]
        engine.submit(self.db,result(t,data))
        self.assertEqual(self.task()['stage'],'reader')


if __name__=='__main__':unittest.main()
