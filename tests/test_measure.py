"""Read-only preflight counts the same full candidate that submission uses."""
import tempfile
import unittest
from pathlib import Path

from support import *
from novel_core.budget import han_count
from novel_core.store import Error
from test_chapter_length import developed_plan
import novel


class MeasureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        initialize(self.path,contract());self.db=connect(self.path)

    def tearDown(self):self.db.close();self.temp.cleanup()

    def cli(self,*argv):
        return novel.dispatch(novel.parser().parse_args([argv[0],'--project',str(self.path),*argv[1:]]))

    def writing(self,target=None):
        task=engine.next_task(self.db,self.path)
        engine.submit(self.db,result(task,{'chapters':[developed_plan(target)]}))
        return engine.next_task(self.db,self.path)

    def measure(self,t,data):
        rpath(t,'output_path').write_text(dump(result(t,data)),encoding='utf-8')
        return self.cli('measure','--result',t['output_path'])

    def snapshot(self):
        return list(self.db.iterdump())

    def test_preflight_counts_han_before_first_submit_and_is_read_only(self):
        task=self.writing(90);before=self.snapshot()
        prose='第一章\n宋禾没有立刻签收，先逐项查验凭证。ABC123！'
        observed=self.measure(task,{'prose':prose})
        self.assertFalse(observed['submitted'])
        self.assertEqual(observed['chapter_length']['actual_han'],han_count(prose))
        self.assertEqual(observed['chapter_length']['shortfall_han'],90-han_count(prose))
        self.assertEqual(self.snapshot(),before)
        self.assertIsNone(engine.load_draft(self.db,'main',1))

    def test_patch_preflight_counts_assembled_candidate_without_saving(self):
        task=self.writing(90)
        engine.submit(self.db,result(task,{'prose':'宋禾迟疑。'}))
        repair=engine.next_task(self.db,self.path)
        work=envelope(repair)
        prose='宋禾先核对指印，再问清双方作证的风险。'*10
        before=self.snapshot()
        observed=self.measure(repair,{'base_hash':work['payload']['base_hash'],
            'patches':[{'start':0,'old':'宋禾迟疑。','new':prose}],
            'repair_note':'演出核验与顾虑。'})
        self.assertEqual(observed['chapter_length']['actual_han'],han_count(prose))
        self.assertEqual(observed['chapter_length']['shortfall_han'],0)
        self.assertEqual(self.snapshot(),before)
        self.assertEqual(engine.load_draft(self.db,'main',1)['prose'],'宋禾迟疑。')

    def test_without_target_still_observes_real_length_without_inventing_goal(self):
        task=self.writing()
        observed=self.measure(task,{'prose':'宋禾签收凭证。'})['chapter_length']
        self.assertEqual(observed['actual_han'],6)
        self.assertIsNone(observed['target_han'])

    def test_explicit_stale_envelope_cannot_measure_under_new_task(self):
        task=self.writing(90);bad=result(task,{'prose':'正文'})
        bad['input_hash']='stale'
        rpath(task,'output_path').write_text(dump(bad),encoding='utf-8')
        with self.assertRaises(Error) as exc:self.cli('measure','--result',task['output_path'])
        self.assertEqual(exc.exception.code,'stale_task')

    def test_config_updates_author_contract_and_requires_no_pending_task(self):
        self.cli('config','--key','chapter_target_han','--value','3000')
        self.assertEqual(get(self.db,'contract')['chapter_target_han'],3000)
        self.assertEqual(self.cli('config','--key','chapter_target_han'),{'chapter_target_han':3000})
        self.assertEqual(self.cli('config')['chapter_target_han'],3000)
        with self.assertRaises(Error):self.cli('config','--key','chapter_target_han','--value','0')
        self.writing()
        with self.assertRaises(Error) as exc:self.cli('config','--key','chapter_target_han','--value','4000')
        self.assertEqual(exc.exception.code,'pending')

    def test_redesign_preflight_observes_proposed_new_target_without_changing_plan(self):
        task=self.writing()
        engine.submit(self.db,result(task,{'prose':'宋禾迟疑。'}))
        # A pending redesign with a new explicit goal models legacy quality
        # redesign; measurement must agree with proposed-plan submission.
        draft=engine.load_draft(self.db,'main',1);draft['stage']='redesign'
        with transaction(self.db):engine.save_draft(self.db,'main',1,draft)
        task=engine.next_task(self.db,self.path)
        before=self.snapshot()
        data={'prose':'宋禾逐项核验凭证。','design_change':'展开核验过程。',
              'plan_update':{'execution':developed_plan(90)['execution']}}
        observed=self.measure(task,data)['chapter_length']
        self.assertEqual(observed['target_han'],90)
        self.assertEqual(self.snapshot(),before)
        engine.submit(self.db,result(task,data))
        self.assertEqual(engine.load_draft(self.db,'main',1)['chapter_length'],observed)


if __name__=='__main__':unittest.main()
