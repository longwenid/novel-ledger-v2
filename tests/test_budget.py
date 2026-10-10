import tempfile
import unittest
from pathlib import Path
from support import *
from novel_core.budget import overview
from novel_core.memory import pack
from novel_core.store import Error


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.path = Path(self.tmp.name)
        c = contract(); c['volumes'] = [{'id':'v1','purpose':'查证','word_budget':20},
                                       {'id':'v2','purpose':'结算','word_budget':40}]
        c['word_range'] = [30,100]
        initialize(self.path,c); self.db = connect(self.path)

    def tearDown(self):
        self.db.close(); self.tmp.cleanup()

    def commit(self, text, volume='v1', end=False):
        t=engine.next_task(self.db,self.path)
        if t.get('stage')=='await_author':
            engine.approve(self.db); t=engine.next_task(self.db,self.path)
        while t.get('stage','').endswith('_review'):
            respond(self.db,self.path,t); t=engine.next_task(self.db,self.path)
        p=plan(t['chapter']);p.update(volume=volume,volume_end=end)
        engine.submit(self.db,result(t,{'chapters':[p]}))
        respond(self.db,self.path,engine.next_task(self.db,self.path),prose=text)
        respond(self.db,self.path,engine.next_task(self.db,self.path))

    def test_invalid_allocations_fail_before_project_creation(self):
        for i,values in enumerate(([20,None],[20,True],[20,0],[20,-1],[20,1.5],[1,1],[100,100])):
            c=get(self.db,'contract')
            for v,value in zip(c['volumes'],values):
                if value is None:v.pop('word_budget')
                else:v['word_budget']=value
            target=self.path/str(i)
            with self.assertRaises(Error):initialize(target,c)
            self.assertFalse(target.exists())

    def test_initial_allocation_and_reader_isolation(self):
        before=self.db.total_changes
        budget=overview(self.db,'main')
        self.assertEqual([v['suggested_remaining_han'] for v in budget['volumes']],[20,40])
        self.assertEqual(self.db.total_changes,before)
        self.assertIn('volume_budget',pack(self.db,'main',1,'plan',{}))
        self.assertNotIn('volume_budget',pack(self.db,'main',1,'chapter_review',{'prose':'正文'}))

    def test_short_closed_volume_reallocates_without_padding_or_contract_change(self):
        original=get(self.db,'contract');self.commit('周行交出凭证。123 ABC',end=True)
        report=engine.status(self.db)['volume_budget']
        self.assertEqual(report['written_han'],6)
        self.assertTrue(report['volumes'][0]['closed'])
        self.assertEqual([v['suggested_remaining_han'] for v in report['volumes']],[0,54])
        self.assertEqual(get(self.db,'contract'),original)
        self.commit('宋禾接过凭证。',volume='v2')
        self.assertEqual(overview(self.db,'main')['volumes'][1]['suggested_remaining_han'],48)

    def test_overrun_is_advisory_and_upper_bound_remains_visible(self):
        self.commit('周'*101)
        report=overview(self.db,'main')
        self.assertTrue(report['over_upper_bound'])
        self.assertTrue(report['baseline_exhausted_with_open_volumes'])
        self.assertEqual(report['remaining_to_range_han'],[0,0])
        self.assertEqual(sum(v['suggested_remaining_han'] for v in report['volumes']),0)
        with self.assertRaises(Error):engine.complete_inner(self.db)

    def test_revision_uses_candidate_counts_preserving_main(self):
        self.commit('周行交出凭证。')
        original=overview(self.db,'main')
        with transaction(self.db):engine.begin_revision(self.db,1,'检查改稿篇幅')
        work=get(self.db,'work_branch')
        self.assertEqual(overview(self.db,work)['written_han'],0)
        self.assertEqual(overview(self.db,'main'),original)

    def test_closed_book_surplus_does_not_force_more_prose(self):
        self.commit('周'*7,end=True)
        self.commit('宋'*8,volume='v2',end=True)
        report=overview(self.db,'main')
        self.assertEqual(report['unallocated_han'],45)
        self.assertEqual(sum(v['suggested_remaining_han'] for v in report['volumes']),0)

    def test_legacy_missing_count_is_recomputed_from_prose(self):
        self.commit('周行交出凭证。abc')
        row=self.db.execute('SELECT data FROM chapters').fetchone();data=json.loads(row[0]);data.pop('han_characters')
        self.db.execute('UPDATE chapters SET data=?',(dump(data),))
        self.assertEqual(overview(self.db,'main')['written_han'],6)

    def test_integer_remainder_preserves_remaining_total(self):
        c=get(self.db,'contract');c['volumes'][1]['word_budget']=21
        c['volumes'].append({'id':'v3','purpose':'余波','word_budget':19})
        put(self.db,'contract',c)
        self.commit('周'*7,end=True)
        report=overview(self.db,'main')
        self.assertEqual([v['suggested_remaining_han'] for v in report['volumes']],[0,28,25])
        self.assertEqual(report['written_han']+sum(v['suggested_remaining_han'] for v in report['volumes']),60)

    def test_legacy_contract_without_budgets_stays_usable(self):
        path=self.path/'legacy';initialize(path,contract());db=connect(path)
        try:
            self.assertEqual(overview(db,'main')['status'],'not_configured')
            self.assertEqual(engine.next_task(db,path)['stage'],'plan')
        finally:db.close()
