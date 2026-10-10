"""A chapter may not leave an expired promise in canonical state."""
import json
import tempfile
import unittest
from pathlib import Path
from support import *
from novel_core.ledger import replay
from novel_core.store import Error, head, transaction


class HookDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        initialize(self.path,contract());self.db=connect(self.path)

    def tearDown(self):
        self.db.close();self.temp.cleanup()

    def task(self):return engine.next_task(self.db,self.path)

    def prepare_facts(self,number=1,prose=None):
        respond(self.db,self.path,self.task())
        prose=prose or '周行答应交出旧收据，宋禾守在桌边等候。'
        respond(self.db,self.path,self.task(),prose=prose)
        task=self.task();work=json.loads(rpath(task).read_text(encoding='utf-8'))
        data=review(work['payload']['facts_checks'],prose,number)
        data.update(events=[],summary='周行承诺交出收据。')
        return task,prose,data

    def opening(self,prose,number,due):
        return {'op':'hook','id':'receipt','before':None,'status':'open',
                'text':'交出旧收据','due':due,'evidence':ev(prose,number,'交出旧收据')}

    def test_current_deadline_rejects_unsettled_promise_and_keeps_task(self):
        task,prose,data=self.prepare_facts()
        data['events']=[self.opening(prose,1,1)]
        with self.assertRaises(Error) as caught:submit_review(self.db,task,data)
        self.assertEqual(caught.exception.code,'overdue_hook')
        self.assertEqual(self.task()['task_id'],task['task_id'])
        self.assertEqual(head(self.db,'main'),0)
        self.assertNotIn('receipt',replay(self.db,'main')['hooks'])

    def test_same_chapter_open_and_paid_is_valid(self):
        task,prose,data=self.prepare_facts(prose='周行答应交出旧收据，随后将收据摊在桌上。')
        data['events']=[self.opening(prose,1,1),
                        {'op':'hook','id':'receipt','before':'open','status':'paid',
                         'evidence':ev(prose,1,'将收据摊在桌上')}]
        submit_review(self.db,task,data)
        self.assertEqual(head(self.db,'main'),1)
        self.assertEqual(replay(self.db,'main')['hooks']['receipt']['status'],'paid')

    def test_future_deadline_is_valid_until_boundary_chapter(self):
        task,prose,data=self.prepare_facts()
        data['events']=[self.opening(prose,1,2)]
        submit_review(self.db,task,data)
        self.assertEqual(head(self.db,'main'),1)
        engine.approve(self.db)
        task,prose,data=self.prepare_facts(2)
        with self.assertRaises(Error) as caught:submit_review(self.db,task,data)
        self.assertEqual(caught.exception.code,'overdue_hook')
        self.assertEqual(head(self.db,'main'),1)
        self.assertEqual(self.task()['task_id'],task['task_id'])

    def test_commit_guard_rolls_back_prose_and_events(self):
        task,prose,data=self.prepare_facts()
        draft={'prose':prose,'events':[self.opening(prose,1,1)],'summary':data['summary'],
               'facts_review':data,'reader_review':data}
        with self.assertRaises(Error) as caught:
            with transaction(self.db):engine.commit_draft(self.db,'main',1,draft)
        self.assertEqual(caught.exception.code,'overdue_hook')
        self.assertEqual(head(self.db,'main'),0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM events WHERE branch='main'").fetchone()[0],0)

    def test_deadline_error_reports_one_entity_and_total_without_dumping_debts(self):
        state={'hooks':{f'hook-{i}':{'status':'open','due':1,'text':'完整伏笔内容。'*100}
                        for i in range(100)}}
        with self.assertRaises(Error) as caught:engine.validate_hook_deadlines(state,1)
        details=caught.exception.details
        self.assertEqual(details['total'],100)
        self.assertEqual(details['first'],{'id':'hook-0','due':1})
        self.assertEqual(details['retrieval']['topic'],'hook-0')
        self.assertNotIn('hook-99',str(caught.exception))


if __name__=='__main__':unittest.main()
