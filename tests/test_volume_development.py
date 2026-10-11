"""Volume closure needs evidence of development, without a length quota.

These are synthetic protocol regressions, not claims of literary quality.
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from support import *
from novel_core import design
from novel_core.budget import han_count
from novel_core.store import Error, digest, head


EXAMPLE=json.loads((Path(__file__).resolve().parents[1]/'assets/design.example.json').read_text(encoding='utf-8'))
FIRST_PROSE='周行找到原始收据，却不能独自证明签字真实。他请宋禾核验，她先问谁肯陪自己作证。'
PROSE='周行把收据交给宋禾，愿意承担作证的后果。宋禾终于按下指印，却要他陪自己到公堂核验。'


class VolumeDevelopmentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        self.db=None

    def tearDown(self):
        if self.db is not None:self.db.close()
        self.temp.cleanup()

    def task(self):return engine.next_task(self.db,self.path)

    def work(self,task):return json.loads(rpath(task).read_text(encoding='utf-8'))

    def closure_task(self,budgets=True):
        c=contract();c['planning_mode']='layered'
        c['volumes']=[{'id':'v1','purpose':'确认共同作证的选择'},
                      {'id':'v2','purpose':'核验凭证并处理作证后果'}]
        c['word_range']=[300,600]
        if budgets:
            for volume,budget in zip(c['volumes'],(100,300)):volume['word_budget']=budget
        initialize(self.path,c);self.db=connect(self.path)
        for stage in ('world_plan','volume_plan'):
            task=self.task();self.assertEqual(task['stage'],stage)
            engine.submit(self.db,result(task,copy.deepcopy(EXAMPLE[stage])))
        for number,prose in enumerate((FIRST_PROSE,PROSE),1):
            task=self.task();p=copy.deepcopy(EXAMPLE['chapter_plan'])
            p.update(number=number,volume='v1',volume_end=number==2,book_end=False)
            p['sequence_refs']=[1]
            p['ending_shape']='交接:共同作证成立' if number==2 else '反转:证人提出条件'
            engine.submit(self.db,result(task,{'chapters':[p]}))
            respond(self.db,self.path,self.task(),prose=prose)
            respond(self.db,self.path,self.task())
            if number==1:engine.approve(self.db)
        for _ in range(3):
            task=self.task();work=self.work(task)
            if work['payload']['scope']=='volume':return task
            self.assertEqual(work['payload']['scope'],'volume_chunk')
            self.assertNotIn('volume_development',work['payload']['checks'])
            self.assertNotIn('volume_development',work['payload'])
            respond(self.db,self.path,task)
        self.fail('Whole volume review was not dispatched after both chapters')

    def passing_result(self,task):
        data=review(self.work(task)['payload']['checks'],PROSE,2)
        row=next(row for row in data['checks'] if row['id']=='volume_development')
        row['reason']='合成样本：本卷只承担共同作证的选择，正文演出承担后果和附加条件；参考差额由后卷核验与后果展开承接。本回执验证协议，不评价文学质量。'
        data['memory']={'text':'共同作证已成立，核验及后果交给下一卷。','sources':[ev(PROSE,2)]}
        return data

    def test_closure_binds_actual_progress_contract_and_full_volume_design(self):
        task=self.closure_task();work=self.work(task)
        context=work['payload']['volume_development'];actual=han_count(FIRST_PROSE)+han_count(PROSE)
        self.assertEqual(context['volume'],'v1')
        self.assertEqual(context['purpose'],get(self.db,'contract')['volumes'][0]['purpose'])
        self.assertEqual(context['written_han'],actual)
        self.assertEqual((context['reference_han'],context['deviation_han']),(100,actual-100))
        self.assertEqual(context['policy'],'advisory_not_quota')
        self.assertEqual(context['book'],{'written_han':actual,'word_range':[300,600],
                                          'remaining_to_range_han':[300-actual,600-actual]})
        self.assertEqual(context['later_volumes'],[{'volume':'v2','purpose':'核验凭证并处理作证后果',
                                                   'reference_han':300,'written_han':0,'closed':False}])
        self.assertEqual(context['design'],design.current(self.db,'main','v1'))
        self.assertEqual(context['design']['status'],'proposal_not_canon')
        self.assertEqual(context['design']['data']['sequence'],EXAMPLE['volume_plan']['sequence'])
        self.assertNotIn('volume_development',engine.LONG_CHECKS)

    def test_missing_development_check_or_evidence_cannot_pass(self):
        task=self.closure_task()
        for missing in ('check','evidence'):
            with self.subTest(missing=missing):
                data=self.passing_result(task)
                if missing=='check':data['checks']=[r for r in data['checks'] if r['id']!='volume_development']
                else:
                    row=next(r for r in data['checks'] if r['id']=='volume_development')
                    row['evidence']=[];row['not_applicable']='本卷较短，跳过展开核验。'
                with self.assertRaises(Error) as exc:engine.submit(self.db,result(task,data))
                self.assertEqual(exc.exception.code,'review')
                self.assertEqual(self.task()['task_id'],task['task_id'])
                self.assertFalse(engine.reviewed(self.db,'main','volume',2))

    def test_insufficient_development_enters_revision_instead_of_next_volume(self):
        task=self.closure_task();data=self.passing_result(task)
        row=next(r for r in data['checks'] if r['id']=='volume_development')
        row.update(result='fix',reason='重要选择的代价仅作承诺，尚未演出当场后果；本卷应补实后再收卷。')
        data['verdict']='fix'
        data['findings']=[{'severity':'quality','impact':'承担风险只有一句承诺，读者无法判断选择重量。',
                           'goal':'演出当场代价和余波，重新判断收卷。','repair_route':'revise',
                           'evidence':ev(PROSE,2,quote='愿意承担作证的后果')}]
        engine.submit(self.db,result(task,data))
        branch=get(self.db,'work_branch')
        self.assertNotEqual(branch,'main');self.assertEqual(get(self.db,'active_branch'),'main')
        self.assertEqual(head(self.db,'main'),2);self.assertEqual(head(self.db,branch),1)
        self.assertFalse(engine.reviewed(self.db,'main','volume',2))
        next_task=self.task();self.assertEqual(next_task['stage'],'volume_plan')
        self.assertEqual(self.work(next_task)['payload']['design_scope'],'v1')

    def test_reasonable_short_volume_can_pass_and_continue_normally(self):
        task=self.closure_task();original=copy.deepcopy(get(self.db,'contract'))
        self.assertLess(han_count(FIRST_PROSE)+han_count(PROSE),100)
        engine.submit(self.db,result(task,self.passing_result(task)))
        next_task=self.task();self.assertEqual((next_task['stage'],next_task['chapter']),('volume_plan',3))
        self.assertEqual(self.work(next_task)['payload']['design_scope'],'v2')
        self.assertEqual(get(self.db,'status'),'ready')
        self.assertEqual(get(self.db,'contract'),original)
        self.assertTrue(engine.reviewed(self.db,'main','volume',2))

    def test_contract_without_volume_budgets_still_requires_semantic_development(self):
        task=self.closure_task(budgets=False);work=self.work(task)
        context=work['payload']['volume_development']
        self.assertIn('volume_development',work['payload']['checks'])
        actual=han_count(FIRST_PROSE)+han_count(PROSE)
        self.assertEqual(context['written_han'],actual)
        self.assertIsNone(context['reference_han']);self.assertIsNone(context['deviation_han'])
        self.assertIsNone(context['later_volumes'][0]['reference_han'])
        self.assertEqual(context['book']['remaining_to_range_han'],[300-actual,600-actual])
        engine.submit(self.db,result(task,self.passing_result(task)))
        self.assertEqual(self.work(self.task())['payload']['design_scope'],'v2')

    def test_old_frozen_pending_checks_and_input_remain_compatible(self):
        task=self.closure_task();work=json.loads(self.db.execute('SELECT input FROM tasks WHERE id=?',(task['task_id'],)).fetchone()[0])
        work.pop('review_session',None)
        # Persist the exact shape of a pre-feature, context-signed task.
        work['payload']['checks'].remove('volume_development')
        work['payload'].pop('volume_development')
        work['payload']['instruction']=work['payload']['instruction'].split(' volume_development 必须')[0]
        wire=dump(work);fingerprint=digest(work)
        self.db.execute('UPDATE tasks SET input=?,input_hash=? WHERE id=?',(wire,fingerprint,task['task_id']))
        old=self.task();self.assertEqual(old['task_id'],task['task_id'])
        self.assertEqual(old['input_hash'],fingerprint)
        self.assertEqual(self.db.execute('SELECT input FROM tasks WHERE id=?',(task['task_id'],)).fetchone()[0],wire)
        self.assertEqual(self.work(old),work)
        data=review(work['payload']['checks'],PROSE,2)
        data['memory']={'text':'兼容旧卷审回执。','sources':[ev(PROSE,2)]}
        engine.submit(self.db,result(old,data))
        self.assertTrue(engine.reviewed(self.db,'main','volume',2))
        self.assertEqual(self.work(self.task())['payload']['design_scope'],'v2')


if __name__=='__main__':unittest.main()
