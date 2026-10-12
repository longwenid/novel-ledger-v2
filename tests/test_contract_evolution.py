"""Task-bound protocol evolution: explicit time, scrutiny and lossless reads."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from support import contract, envelope, plan, respond, result, combined_review, ev
import novel
from novel_core import engine, schedule, reviewflow, taskview, memory, wire
from novel_core.store import Error, initialize, connect, digest, dump, head


# Windows hosts without admin/developer-mode privilege reject symlink creation
# (WinError 1314); probe once so the alias test skips there instead of failing.
def _symlinks_available():
    with tempfile.TemporaryDirectory() as probe:
        try:
            alias=Path(probe)/'alias';alias.symlink_to(Path(probe),target_is_directory=True)
            return True
        except OSError:
            return False

SYMLINKS=_symlinks_available()


class ContractEvolutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        initialize(self.path,contract());self.db=connect(self.path)
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def cli(self,*args):
        return novel.dispatch(novel.parser().parse_args([args[0],'--project',str(self.path),*args[1:]]))
    def strip_capability(self,task,name):
        row=dict(self.db.execute('SELECT * FROM tasks WHERE id=?',(task['task_id'],)).fetchone())
        work=json.loads(row['input']);work['capabilities'].pop(name)
        row.update(input=dump(work),input_hash=digest(work))
        self.db.execute('UPDATE tasks SET input=?,input_hash=? WHERE id=?',(row['input'],row['input_hash'],row['id']))
        return engine.task_files(self.db,self.path,row)
    def review_task(self):
        respond(self.db,self.path,self.cli('advance')['task'])
        writer=self.cli('advance')['task']
        respond(self.db,self.path,writer,prose='今日周行交出收据。次日宋禾核对并签名。')
        return self.cli('advance')['task']
    def receipt(self,task):
        work=envelope(task);prose=engine.load_draft(self.db,'main',task['chapter'])['prose']
        data=combined_review(work,prose,task['chapter'])
        return result(task,data),prose

    def test_new_plan_rejects_batch_date_disguised_as_chapter_delta_atomically(self):
        task=self.cli('advance')['task'];p=plan(1)
        p['scenes']=[{'label':'交收处','present':['hero'],'time_hint':'倒计时第三日（D+2）'}]
        before=self.db.execute('SELECT input_hash FROM tasks WHERE id=?',(task['task_id'],)).fetchone()[0]
        for preview in (True,False):
            with self.assertRaises(Error) as caught:engine.submit(self.db,result(task,{'chapters':[copy.deepcopy(p)]}),preview=preview)
            self.assertEqual(caught.exception.code,'time_basis')
            self.assertEqual(self.db.execute('SELECT count(*) FROM plans').fetchone()[0],0)
            self.assertEqual(self.db.execute('SELECT input_hash,state FROM tasks WHERE id=?',(task['task_id'],)).fetchone()[0],before)
        p['timeline']={'basis':'batch_anchor','end_offset_range':[2,2]}
        engine.submit(self.db,result(task,{'chapters':[p]}))
        stored=json.loads(self.db.execute('SELECT data FROM plans').fetchone()[0])
        self.assertEqual(stored['timeline']['anchor_clock'],0)
        self.assertNotIn('day_span',stored)

    def test_old_frozen_plan_keeps_legacy_coordinates(self):
        task=self.strip_capability(self.cli('advance')['task'],'timeline_input');p=plan(1)
        p['scenes']=[{'label':'交收处','present':['hero'],'time_hint':'D+2'}]
        engine.submit(self.db,result(task,{'chapters':[p]}))
        stored=json.loads(self.db.execute('SELECT data FROM plans').fetchone()[0])
        self.assertEqual(stored['day_span'],[2,2])

    def test_shared_batch_anchor_does_not_accumulate_two_more_days_per_chapter(self):
        plans=[{'number':n,'timeline':{'basis':'batch_anchor','end_offset_range':[offset,offset]}}
               for n,offset in ((37,0),(38,1),(39,2),(40,2))]
        for p in plans:schedule.normalize(p,14,explicit_required=True)
        schedule.validate_sequence(plans,14)
        for p,before,after in zip(plans,(14,14,15,16),(14,15,16,16)):
            schedule.check(p,before,after,p['number'])
        with self.assertRaises(Error):schedule.check(plans[-1],17,19,40)

    def test_declared_numeric_legacy_range_cannot_bypass_new_contract(self):
        for p in ({'day_span':[2,2]},{'day_anchor':14,'day_span':[2,2]}):
            with self.assertRaises(Error):schedule.normalize(p,14,explicit_required=True)
        # Ordinary prose with no hard date is allowed; eight-day travel is also allowed.
        schedule.normalize({'scenes':[{'time_hint':'旅途漫长'}]},14,explicit_required=True)
        p={'timeline':{'basis':'chapter_start','end_offset_range':[8,8]}}
        schedule.normalize(p,14,explicit_required=True);schedule.check(p,14,22,1)

    def test_new_template_exposes_the_enforced_time_contract(self):
        task=self.cli('advance')['task'];template=json.loads((self.path/task['template_path']).read_text())
        p=template['data']['chapters'][0]
        self.assertEqual(p['timeline'],task['time_contract']['timeline_example'])
        self.assertEqual(p['scenes'][0]['time'],task['time_contract']['scene_example']['time'])

    def test_missing_risk_assessment_does_not_turn_a_positive_quote_into_pass(self):
        task=self.review_task();raw,_=self.receipt(task)
        language=next(r for r in raw['data']['reader']['checks'] if r['id']=='language')
        language.pop('challenge')
        with self.assertRaises(Error) as caught:engine.submit(self.db,raw)
        self.assertEqual(caught.exception.code,'review_scrutiny');self.assertEqual(head(self.db,'main'),0)
        self.assertEqual(self.db.execute('SELECT state FROM tasks WHERE id=?',(task['task_id'],)).fetchone()[0],'pending')

    def test_changing_clock_after_without_reconciling_reading_rolls_back(self):
        task=self.review_task();raw,prose=self.receipt(task)
        raw['data']['facts']['events']=[{'op':'clock','before':0,'after':2,'evidence':ev(prose)}]
        for preview in (True,False):
            with self.assertRaises(Error) as caught:engine.submit(self.db,raw,preview=preview)
            self.assertEqual(caught.exception.code,'time_review');self.assertEqual(head(self.db,'main'),0)
        raw['data']['facts']['events'][0]['after']=1
        next(r for r in raw['data']['facts']['checks'] if r['id']=='time')['elapsed_days']=1
        engine.submit(self.db,raw);self.assertEqual(head(self.db,'main'),1)
        self.assertTrue(engine.submit(self.db,raw)['idempotent'])

    def test_old_review_receipt_does_not_gain_new_required_fields(self):
        task=self.strip_capability(self.review_task(),'review_scrutiny');raw,_=self.receipt(task)
        self.assertTrue(all('elapsed_days' not in r and 'challenge' not in r
                            for category in ('facts','reader') for r in raw['data'][category]['checks']))
        engine.submit(self.db,raw);self.assertEqual(head(self.db,'main'),1)

    def test_one_call_read_preserves_all_records_and_first_read_isolation(self):
        task=self.cli('advance')['task'];before=task['input_hash']
        bundle=self.cli('task-view','--task',task['task_id'],'--read')
        manifest=json.loads((self.path/task['reading_view']['manifest_path']).read_text())
        self.assertEqual([digest(r['value']) for r in bundle['records']],[r['value_hash'] for r in manifest['records']])
        self.assertEqual(bundle['record_count'],len(manifest['records']))
        self.assertTrue(bundle['complete']);self.assertEqual(bundle['input_hash'],before)
        task=self.review_task()
        with self.assertRaises(Error) as caught:self.cli('task-view','--task',task['task_id'],'--read')
        self.assertEqual(caught.exception.code,'first_read_required')

    def test_bundle_refuses_damaged_pages_and_altered_record_manifest(self):
        task=self.cli('advance')['task'];manifest=json.loads((self.path/task['reading_view']['manifest_path']).read_text())
        tampered=copy.deepcopy(manifest);tampered['records'][0]['value_hash']='0'*64
        with self.assertRaises(Error):taskview.read_bundle(self.path,tampered)
        (self.path/manifest['pages'][0]['path']).write_text('截断',encoding='utf-8')
        with self.assertRaises(Error):taskview.read_bundle(self.path,manifest)

    @unittest.skipUnless(SYMLINKS,'host cannot create symlinks (Windows without privilege)')
    def test_renderer_resolves_aliases_and_rejects_external_writes_before_io(self):
        directory=self.path/'views';directory.mkdir()
        source=directory/'input.json';view={'guide':'完整记录'}
        source.write_text(dump(view),encoding='utf-8')
        alias=self.path/'alias';alias.symlink_to(directory,target_is_directory=True)
        rendering=taskview.render(self.path,alias,view,'views/input.json','fixture',digest(view))
        self.assertTrue((self.path/rendering['manifest_path']).is_file())
        with tempfile.TemporaryDirectory() as outside:
            external=Path(outside)
            with self.assertRaises(Error):taskview.render(self.path,external,view,'views/input.json','fixture',digest(view))
            self.assertEqual(list(external.iterdir()),[])

    def test_proof_guide_and_envelope_share_optional_offset_authority(self):
        task=self.cli('advance')['task'];guide=memory.guide('chapter_review')
        self.assertIn(wire.EVIDENCE_RULE,guide)
        self.assertEqual(task['evidence_contract'],wire.evidence_contract(task['chapter']))
        self.assertNotIn('start',task['evidence_contract']['example'])

    def test_schedule_rejection_returns_fix_route_not_permission_to_select_clock(self):
        with self.assertRaises(Error) as caught:schedule.check({'day_span':[2,2]},15,15,39)
        route=caught.exception.details['recovery']
        self.assertEqual(route['action'],'review_fix');self.assertEqual(route['owner'],'current_reviewer')
        self.assertEqual(route['finding']['severity'],'blocker')


if __name__=='__main__':unittest.main()
