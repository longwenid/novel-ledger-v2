"""Resume retains the work branch; published endings do not select a new volume."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from support import *
from novel_core.store import Error, head
from novel_core import recovery, taskio


class RecoveryPositionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        c=contract();c['volumes'].append({'id':'v2','purpose':'下一卷'})
        initialize(self.path,c);self.db=connect(self.path)
        for n in (1,2):
            t=engine.next_task(self.db,self.path);respond(self.db,self.path,t)
            t=engine.next_task(self.db,self.path);respond(self.db,self.path,t)
            t=engine.next_task(self.db,self.path);respond(self.db,self.path,t)
            if n==1:engine.approve(self.db)

    def tearDown(self):self.db.close();self.tmp.cleanup()

    def nested(self):
        # The old published plan closes v1, while a structural revision is unfinished.
        old=json.loads(self.db.execute("SELECT data FROM plans WHERE branch='main' AND number=2").fetchone()[0])
        old['volume_end']=True
        self.db.execute("UPDATE plans SET data=? WHERE branch='main' AND number=2",(dump(old),))
        parent=engine.begin_revision(self.db,1,'扩充第一卷，旧收卷应重新核对')['branch']
        t=engine.next_task(self.db,self.path);respond(self.db,self.path,t)
        t=engine.next_task(self.db,self.path);respond(self.db,self.path,t)
        child=engine.begin_revision(self.db,1,'去除多余形容词')['branch']
        return parent,child

    def test_status_separates_published_and_work_and_keeps_all_goals(self):
        parent,child=self.nested()
        before=self.db.total_changes
        with patch.object(engine,'choose',side_effect=AssertionError('status must not dispatch')):
            point=engine.status(self.db,brief=True)['recovery']
        self.assertEqual(self.db.total_changes,before)
        self.assertEqual(point['published']['committed'],2)
        self.assertEqual(point['published']['volume'],'v1')
        self.assertEqual(point['work']['branch'],child)
        self.assertEqual(point['work']['committed'],0)
        self.assertEqual(point['work']['mode'],'revision')
        self.assertIsNone(point['work']['volume'])
        self.assertEqual(point['target']['volume'],'v1')
        self.assertEqual([r['id'] for r in point['revision_context']['chain']],[parent,child])
        self.assertNotIn('usage',engine.status(self.db,brief=True))

    def test_cancelled_pause_resumes_same_revision_volume_not_old_ending(self):
        parent,child=self.nested();t=engine.next_task(self.db,self.path)
        before=(get(self.db,'active_branch'),get(self.db,'work_branch'),head(self.db,child))
        engine.pause(self.db,'作者暂停',True);engine.resume(self.db)
        restored=taskio.advance(self.db,self.path)
        self.assertEqual(restored['task']['chapter'],1)
        self.assertEqual(restored['task']['stage'],'draft')
        self.assertEqual(restored['status']['recovery']['target']['volume'],'v1')
        self.assertEqual(before,(get(self.db,'active_branch'),get(self.db,'work_branch'),head(self.db,child)))
        self.assertNotEqual(t['task_id'],restored['task']['task_id'])
        point=json.loads((self.path/'work/controller.json').read_text(encoding='utf-8'))
        self.assertEqual(point['recovery'],restored['status']['recovery'])

    def test_old_frozen_pending_gets_intent_without_hash_change(self):
        parent,child=self.nested();t=engine.next_task(self.db,self.path)
        work=json.loads(self.db.execute('SELECT input FROM tasks WHERE id=?',(t['task_id'],)).fetchone()[0])
        work.pop('revision_context');wire=dump(work);hashed=engine.digest(work)
        self.db.execute('UPDATE tasks SET input=?,input_hash=? WHERE id=?',(wire,hashed,t['task_id']))
        engine.pause(self.db,'作者暂停');engine.resume(self.db)
        restored=taskio.advance(self.db,self.path)['task']
        self.assertEqual(restored['task_id'],t['task_id']);self.assertEqual(restored['input_hash'],hashed)
        self.assertEqual(self.db.execute('SELECT input FROM tasks WHERE id=?',(t['task_id'],)).fetchone()[0],wire)
        self.assertEqual(len(restored['revision_context']['chain']),2)
        self.assertEqual(restored['revision_context']['chain'][0]['reason'],'扩充第一卷，旧收卷应重新核对')

    def test_new_packs_inherit_goals_and_first_read_does_not_disclose_them(self):
        parent,child=self.nested();t=engine.next_task(self.db,self.path)
        work=json.loads(rpath(t).read_text(encoding='utf-8'))
        self.assertEqual(len(work['revision_context']['chain']),2)
        respond(self.db,self.path,t)
        reviewer=engine.next_task(self.db,self.path)
        self.assertEqual(reviewer['delivery_phase'],'first_read')
        self.assertNotIn('revision_context',reviewer)
        self.assertFalse((self.path/reviewer['input_path']).exists())
        rpath(reviewer)  # Freeze first reading through the engineering fixture.
        formal=engine.next_task(self.db,self.path)
        self.assertEqual(len(formal['revision_context']['chain']),2)
        self.assertEqual(len(json.loads(rpath(formal).read_text(encoding='utf-8'))['revision_context']['chain']),2)

    def test_final_revision_review_requires_evidence_for_inherited_goals(self):
        parent,child=self.nested()
        for n in (1,2):
            t=engine.next_task(self.db,self.path);respond(self.db,self.path,t)
            t=engine.next_task(self.db,self.path);respond(self.db,self.path,t)
        # Complete volume and revision chunk checks until the final synthesis.
        for _ in range(12):
            t=engine.next_task(self.db,self.path)
            self.assertIn('task_id',t)
            work=json.loads(rpath(t).read_text(encoding='utf-8'))
            if work['payload'].get('scope')=='revision':break
            respond(self.db,self.path,t)
        else:self.fail('missing revision synthesis')
        self.assertIn('revision_intent',work['payload']['checks'])
        revision=work.get('revision_context') or json.loads((self.path/work['shared_materials']['revision_context']['path']).read_text(encoding='utf-8'))
        self.assertEqual(len(revision['chain']),2)
        prose=self.db.execute('SELECT prose FROM chapters WHERE branch=? AND number=1',(child,)).fetchone()[0]
        data=review(work['payload']['checks'],prose,1);data['memory']='核对修订传播。'
        missing=copy.deepcopy(data);missing['checks']=[r for r in missing['checks'] if r['id']!='revision_intent']
        with self.assertRaises(Error):engine.submit(self.db,result(t,missing))
        no_proof=copy.deepcopy(data);r=next(r for r in no_proof['checks'] if r['id']=='revision_intent')
        r['evidence']=[];r['not_applicable']='仅修改文风'
        with self.assertRaises(Error):engine.submit(self.db,result(t,no_proof))
        intent=next(r for r in data['checks'] if r['id']=='revision_intent');intent['result']='fix'
        data['verdict']='fix';data['findings']=[{'severity':'quality','impact':'父级扩充目标仍未落实',
          'goal':'重新核对第一卷收束与展开','evidence':ev(prose,1)}]
        engine.submit(self.db,result(t,data))
        self.assertEqual(get(self.db,'active_branch'),'main')
        self.assertNotEqual(engine.next_task(self.db,self.path).get('stage'),'revision_promoted')

    def test_future_volume_is_only_a_plan_and_status_does_not_create_tasks(self):
        p=plan(3);p['volume']='v2'
        self.db.execute('INSERT INTO plans VALUES (?,?,?)',('main',3,dump(p)))
        count=self.db.execute('SELECT count(*) FROM tasks').fetchone()[0]
        point=engine.status(self.db,brief=True)['recovery']
        self.assertEqual(point['work']['volume'],'v1')
        self.assertEqual(point['target']['volume'],'v2')
        self.assertEqual(point['target']['source'],'plan_only');self.assertIsNone(point['target']['stage'])
        self.assertEqual(self.db.execute('SELECT count(*) FROM tasks').fetchone()[0],count)

    def test_accepted_parent_goal_is_not_resurrected(self):
        parent,child=self.nested()
        self.db.execute("UPDATE branches SET status='archived' WHERE id=?",(parent,))
        self.assertEqual([r['id'] for r in recovery.revision_context(self.db,child)['chain']],[child])

    def test_revision_range_end_does_not_point_controller_at_next_volume(self):
        parent,child=self.nested()
        for _ in range(2):
            t=engine.next_task(self.db,self.path);respond(self.db,self.path,t)
            t=engine.next_task(self.db,self.path);respond(self.db,self.path,t)
        future=plan(3);future['volume']='v2'
        self.db.execute('INSERT INTO plans VALUES (?,?,?)',(child,3,dump(future)))
        point=engine.status(self.db,brief=True)['recovery']
        self.assertEqual(point['work']['committed'],2)
        self.assertEqual(point['target']['chapter'],2)
        self.assertEqual(point['target']['volume'],'v1')
        self.assertEqual(point['target']['source'],'revision_boundary')
        self.assertIsNone(point['target']['stage'])

    def test_revised_open_volume_can_promote_then_continue_in_same_volume(self):
        parent,child=self.nested()
        edge=json.loads(self.db.execute('SELECT data FROM plans WHERE branch=? AND number=2',(child,)).fetchone()[0])
        edge['volume_end']=False
        self.db.execute('UPDATE plans SET data=? WHERE branch=? AND number=2',(dump(edge),child))
        self.db.execute('INSERT INTO plans VALUES (?,?,?)',(child,3,dump(plan(3))))
        for _ in range(2):
            t=engine.next_task(self.db,self.path);respond(self.db,self.path,t)
            t=engine.next_task(self.db,self.path);respond(self.db,self.path,t)
        for _ in range(12):
            t=engine.next_task(self.db,self.path)
            if t.get('stage')=='revision_promoted':break
            respond(self.db,self.path,t)
        else:self.fail('revision did not promote after checking the corrected scope')
        next_task=engine.next_task(self.db,self.path)
        self.assertEqual(next_task['chapter'],3)
        self.assertEqual(next_task['stage'],'draft')
        self.assertEqual(engine.status(self.db,brief=True)['recovery']['target']['volume'],'v1')
