import copy
import json
import tempfile
import unittest
from pathlib import Path
from support import *
from novel_core import design
from novel_core.memory import pack
from novel_core.ledger import replay
from novel_core.store import Error, digest, head

EXAMPLE=json.loads((Path(__file__).resolve().parents[1]/'assets/design.example.json').read_text(encoding='utf-8'))


class DesignTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        c=contract();c['planning_mode']='layered';c['volumes'].append({'id':'v2','purpose':'公开结算'})
        initialize(self.path,c);self.db=connect(self.path)
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def task(self):return engine.next_task(self.db,self.path)
    def submit(self,task,data):return submit_review(self.db,task,copy.deepcopy(data))
    def prepare(self):
        self.submit(self.task(),EXAMPLE['world_plan']);self.submit(self.task(),EXAMPLE['volume_plan'])
    def plan(self,n=1,volume='v1',end=False):
        p=copy.deepcopy(EXAMPLE['chapter_plan']);p.update(number=n,volume=volume,volume_end=end)
        shapes=('闭环','交接','爆发','静收','反转','余波')
        p['ending_shape']=shapes[n%len(shapes)]+f':测试收束{n}'
        p['sequence_refs']=[min(n,len(EXAMPLE['volume_plan']['sequence']))]
        return p
    def chapter(self,n=1,volume='v1',end=False):
        self.submit(self.task(),{'chapters':[self.plan(n,volume,end)]})
        respond(self.db,self.path,self.task())
        t=self.task();w=envelope(t);text=pack_prose(self.path, w)
        data=review(w['payload']['facts_checks'],text,n);data.update(events=[],summary='交出凭证，合作成立。')
        self.submit(t,data)
    def test_world_is_reserved_design_scope(self):
        c=contract();c['planning_mode']='layered';c['volumes']=[{'id':'world','purpose':'冲突的卷名'}]
        path=self.path/'invalid'
        with self.assertRaises(Error):initialize(path,c)
        self.assertFalse((path/'novel.sqlite3').exists())
    def test_world_volume_chapter_order_and_proposal_never_becomes_fact(self):
        before=digest(replay(self.db,'main'));a=self.task();self.assertEqual(a['stage'],'world_plan');self.assertEqual(a,self.task())
        r=result(a,copy.deepcopy(EXAMPLE['world_plan']));engine.submit(self.db,r);self.assertTrue(engine.submit(self.db,r)['idempotent'])
        self.assertEqual(self.task()['stage'],'volume_plan');self.submit(self.task(),EXAMPLE['volume_plan'])
        self.assertEqual(self.task()['stage'],'plan');self.assertEqual(before,digest(replay(self.db,'main')))
    def test_writer_receives_script_brief_reader_does_not(self):
        self.prepare();self.submit(self.task(),{'chapters':[self.plan()]})
        t=self.task();w=envelope(t)
        self.assertEqual(w['writer_brief']['dramatic_task']['necessary_change'],self.plan()['change'])
        self.assertEqual(w['design_context']['status'],'proposal_not_canon');self.assertTrue(rpath(t, 'brief_path').exists())
        rpath(t, 'brief_path').write_text('人为修改的视图', encoding='utf-8')
        self.assertEqual(self.task()['input_hash'],t['input_hash']);self.assertIn('场景任务书',rpath(t, 'brief_path').read_text(encoding='utf-8'))
        respond(self.db,self.path,t);f=self.task();fw=envelope(f)
        self.assertNotIn('writer_brief',fw);self.assertNotIn('design_context',fw)
        text=pack_prose(self.path, fw);data=review(fw['payload']['facts_checks'],text);data.update(events=[],summary='交付凭证')
        self.assertEqual(rpath(f,'first_read_path').read_text(encoding='utf-8'),text)
        self.submit(f,data)
    def test_volume_outline_requires_adjudication_log(self):
        a=self.task();self.submit(a,EXAMPLE['world_plan']);t=self.task()
        bad=copy.deepcopy(EXAMPLE['volume_plan']);bad.pop('choice_log',None)
        with self.assertRaises(Error) as exc:self.submit(t,bad)
        self.assertEqual(exc.exception.code,'design')
        wild=copy.deepcopy(EXAMPLE['volume_plan']);wild['choice_log'][0]['candidates'][1]['wild']=False
        with self.assertRaises(Error) as exc:self.submit(t,wild)
        self.assertIn('wild',str(exc.exception))
        beatless=copy.deepcopy(EXAMPLE['volume_plan']);beatless['choice_log'].append({'kind':'beat','mode':'unattended','candidates':[{'direction':'a','payoff':'p','cost':'c','risk':'r'},{'direction':'b','payoff':'p','cost':'c','risk':'r','wild':True}],'chosen':'b','reason':'ok'})
        with self.assertRaises(Error) as exc:self.submit(t,beatless)
        self.assertIn('序号',str(exc.exception))
        self.submit(t,EXAMPLE['volume_plan']);self.assertEqual(self.task()['stage'],'plan')

    def test_unprepared_cross_volume_batch_is_atomic(self):
        self.prepare();task=self.task()
        with self.assertRaises(Error):self.submit(task,{'chapters':[self.plan(end=True),self.plan(2,'v2')]})
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM plans').fetchone()[0],0)
        self.assertEqual(self.task()['task_id'],task['task_id'])
    def test_same_volume_cannot_continue_after_volume_end(self):
        self.prepare();task=self.task()
        with self.assertRaises(Error):self.submit(task,{'chapters':[self.plan(end=True),self.plan(2)]})
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM plans').fetchone()[0],0)
    def test_draft_plan_update_is_rejected_instead_of_ignored(self):
        self.prepare();self.submit(self.task(),{'chapters':[self.plan()]});task=self.task()
        with self.assertRaises(Error):self.submit(task,{'prose':'周行把凭证交给宋禾。','plan_update':{'goal':'改为离开'}})
        self.assertEqual(self.task()['task_id'],task['task_id'])
        self.assertIsNone(engine.load_draft(self.db,'main',1))
    def test_missing_scene_task_or_unknown_proposal_is_rejected(self):
        self.prepare();t=self.task();p=self.plan();p.pop('reader_experience')
        with self.assertRaises(Error):self.submit(t,{'chapters':[p]})
        p=self.plan();p['scenes'][0]['design_location']='design:invented'
        with self.assertRaises(Error):self.submit(t,{'chapters':[p]})
    def test_world_design_cannot_write_events_or_false_canon(self):
        t=self.task();data=copy.deepcopy(EXAMPLE['world_plan']);data['events']=[]
        with self.assertRaises(Error):self.submit(t,data)
        data.pop('events');data['canon_refs']=[{'entity':'hero','key':'floor','value':'3'}]
        with self.assertRaises(Error):self.submit(t,data)
        self.assertEqual(self.task()['task_id'],t['task_id'])
    def test_required_world_material_is_complete_without_character_rejection(self):
        world=copy.deepcopy(EXAMPLE['world_plan'])
        region='北岸旧城的水道决定运送证据的路线，不能在推进剧情时凭空消失。'*2500
        world['geography']['regions'].append(region)
        put(self.db,'context_chars',4096)
        self.submit(self.task(),world);self.submit(self.task(),EXAMPLE['volume_plan'])
        self.submit(self.task(),{'chapters':[self.plan()]})
        task=self.task();self.assertEqual(task['stage'],'draft')
        work=envelope(task)
        # 大块设计材料按内容寻址交付，worker 读回后与完整值等价。
        design=restore_material(self.path,work,'design_context')
        self.assertEqual(design['world']['data'],world)
        self.assertEqual(design['volume']['data'],EXAMPLE['volume_plan'])
        self.assertEqual(design['status'],'proposal_not_canon')
        self.assertEqual(self.task()['input_hash'],task['input_hash'])
        self.assertFalse(replay(self.db,'main')['facts'])
    def test_volume_transition_prepares_next_volume_after_review(self):
        self.prepare();self.chapter(end=True);engine.approve(self.db)
        for _ in range(5):
            t=self.task()
            if t['stage']=='volume_plan':break
            self.assertEqual(t['stage'],'volume_review');respond(self.db,self.path,t)
        self.assertEqual(envelope(t)['payload']['design_scope'],'v2')
        v=copy.deepcopy(EXAMPLE['volume_plan']);v['volume']='v2';v['dependencies']=[1];self.submit(t,v)
        self.assertEqual(self.task()['stage'],'plan')
        self.assertIn('1',design.current(self.db,'main','v2')['sources'])
    def test_revision_invalidates_affected_design_and_preserves_active(self):
        self.prepare();self.chapter();engine.approve(self.db)
        original=design.current(self.db,'main','v1')
        with transaction(self.db):rev=engine.begin_revision(self.db,1,'重新安排交接场景')
        self.assertTrue(design.current(self.db,'main','v1')['active'])
        self.assertFalse(design.current(self.db,rev['branch'],'v1')['active'])
        self.assertEqual(self.task()['stage'],'volume_plan')
        self.assertEqual(design.current(self.db,'main','v1'),original)
        self.assertEqual(head(self.db,'main'),1)
    def test_redesign_history_retained(self):
        self.prepare()
        with transaction(self.db):design.invalidate(self.db,'main','world','调整未使用的区域')
        t=self.task();self.assertEqual(t['stage'],'world_plan')
        updated=copy.deepcopy(EXAMPLE['world_plan']);updated['geography']['regions'].append('候选市郊')
        self.submit(t,updated);history=design.history(self.db,'main','world')
        self.assertEqual(len(history['versions']),2);self.assertEqual(history['current']['revision'],2)
        self.assertEqual(self.task()['stage'],'volume_plan')

    def test_two_volumes_reach_completion_through_normal_pipeline(self):
        stages=[]
        for _ in range(80):
            task=self.task();stage=task.get('stage');stages.append(stage)
            if task.get('status')=='completed':break
            if stage=='await_author':engine.approve(self.db);continue
            if stage=='world_plan':self.submit(task,EXAMPLE['world_plan'])
            elif stage=='volume_plan':
                data=copy.deepcopy(EXAMPLE['volume_plan']);data['volume']=envelope(task)['payload']['design_scope']
                self.submit(task,data)
            elif stage=='plan':
                n=task['chapter'];p=self.plan(n,'v'+str(n),True);p['book_end']=n==2
                self.submit(task,{'chapters':[p]})
            elif stage=='chapter_review':
                w=envelope(task);text=pack_prose(self.path, w)
                data=review(w['payload']['facts_checks'],text,task['chapter']);data.update(events=[],summary='有代价的证据交付。');self.submit(task,data)
            else:respond(self.db,self.path,task)
        self.assertEqual(task['status'],'completed');self.assertEqual(stages.count('world_plan'),1);self.assertEqual(stages.count('volume_plan'),2)
        self.assertTrue(engine.audit(self.db)['ok'])
    def test_canonical_reference_invalidated_when_source_removed(self):
        self.prepare();self.chapter();engine.approve(self.db)
        # Explicit historical assumptions are revision dependencies, unlike free future proposals.
        world=copy.deepcopy(EXAMPLE['world_plan']);world['dependencies']=[1]
        with transaction(self.db):design.save(self.db,'main','world',2,world,'test-control-layer')
        with transaction(self.db):rev=engine.begin_revision(self.db,1,'调整证据来源')
        self.assertFalse(design.usable(self.db,rev['branch'],'world'))
        self.assertEqual(self.task()['stage'],'world_plan')
if __name__=='__main__':unittest.main()
