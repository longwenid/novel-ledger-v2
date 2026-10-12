"""Frozen source proofs, lossless views, explicit time, and host observations."""
import copy
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import test_targeted_repairs as fixtures
import novel
from novel_core import engine, wire, taskview, schedule, hostflow, observability
from novel_core.store import Error, digest, dump, get, put


class InteractionTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.TargetedRepairTests(methodName='runTest');self.f.setUp()
        self.db=self.f.db;self.path=self.f.path
    def tearDown(self):self.f.tearDown()

    def test_optional_offsets_catalog_preflight_commit_and_replay(self):
        from test_context_io import catalog_receipt
        task,base,corrected,data=self.f.numeric_task()
        receipt=catalog_receipt(data)
        for proof in receipt['evidence_catalog'].values():proof.pop('start')
        raw=self.f.receipt(task,receipt);original=copy.deepcopy(raw);before=self.f.snapshot()
        engine.submit(self.db,raw,preview=True)
        self.assertEqual(self.f.snapshot(),before);self.assertEqual(raw,original)
        engine.submit(self.db,raw)
        self.assertEqual(raw,original)
        saved=json.loads(self.db.execute('SELECT receipt FROM tasks WHERE id=?',(task['task_id'],)).fetchone()[0])
        self.assertEqual(saved['data'],data)
        self.assertEqual(get(self.db,'wire_receipt:'+task['task_id']),original)
        self.assertTrue(engine.submit(self.db,raw)['idempotent'])
        changed=copy.deepcopy(raw);next(iter(changed['data']['evidence_catalog'].values()))['quote']='不同原文'
        with self.assertRaises(Error) as caught:engine.submit(self.db,changed)
        self.assertEqual(caught.exception.code,'replay_conflict')

    def test_plain_offsets_are_optional_and_input_is_not_mutated(self):
        task,base,corrected,data=self.f.numeric_task()
        for path,proof in wire.proof_slots(data):proof.pop('start',None)
        raw=self.f.receipt(task,data);original=copy.deepcopy(raw)
        engine.submit(self.db,raw)
        self.assertEqual(raw,original)
        self.assertTrue(engine.submit(self.db,raw)['idempotent'])

    def test_proof_errors_are_collected_and_narrative_values_untouched(self):
        data={'checks':[{'evidence':[{'chapter':2,'quote':'重复。'},{'chapter':2,'quote':'不存在'}]}],
              'candidate_events':[{'evidence':{'chapter':9,'quote':'正文'},'after':{'evidence':{'ref':'不是引用'}}}]}
        with self.assertRaises(Error) as caught:wire.anchor_proofs(data,{2:'重复。重复。'})
        self.assertEqual(caught.exception.details['count'],3)
        self.assertEqual(data['candidate_events'][0]['after'],{'evidence':{'ref':'不是引用'}})
        proof={'checks':[{'evidence':[{'chapter':2,'quote':'重复。','start':3}]}]}
        wire.anchor_proofs(proof,{2:'重复。重复。'})
        self.assertEqual(proof['checks'][0]['evidence'][0]['start'],3)

    def test_reading_pages_preserve_records_and_rebuild_after_damage(self):
        task=self.f.cli('advance')['task']
        view=task['reading_view'];file=self.path/view['manifest_path']
        manifest=json.loads(file.read_text(encoding='utf-8'));self.assertTrue(taskview.verify(self.path,manifest))
        records=[]
        for page in manifest['pages']:
            text=(self.path/page['path']).read_text(encoding='utf-8')
            records.extend(json.loads(blob) for blob in re.findall(r'```json\n(.*?)\n```',text,re.S))
        self.assertEqual([digest(value) for value in records],[row['value_hash'] for row in manifest['records']])
        row=self.db.execute('SELECT input,input_hash FROM tasks WHERE id=?',(task['task_id'],)).fetchone()
        (self.path/view['pages'][0]['path']).write_text('损坏',encoding='utf-8')
        with self.assertRaises(Error):taskview.verify(self.path,manifest)
        renewed=self.f.cli('task-view','--task',task['task_id'])
        self.assertEqual(renewed['input_hash'],row['input_hash'])
        after=self.db.execute('SELECT input,input_hash FROM tasks WHERE id=?',(task['task_id'],)).fetchone()
        self.assertEqual(tuple(row),tuple(after))
        self.assertTrue(taskview.verify(self.path,json.loads(file.read_text(encoding='utf-8'))))

    def test_sealed_review_cannot_read_view_or_dispatch_other_pending(self):
        task=self.f.cli('advance')['task'];out=self.f.writer_batch(task)
        task=self.f.cli('advance','--after',out['task_id'])['task']
        self.assertEqual(task['delivery_phase'],'first_read');self.assertNotIn('reading_view',task)
        self.assertFalse((self.path/Path(task['task_path']).parent/'reading.md').exists())
        with self.assertRaises(Error):self.f.cli('task-view','--task',task['task_id'])

    def test_renderer_failure_is_visible_and_json_remains_usable(self):
        task=self.f.cli('advance')['task']
        with patch.object(taskview,'render',side_effect=OSError('disk unavailable')):
            refreshed=self.f.cli('advance')['task']
        self.assertIn('reading_view_error',refreshed)
        self.assertTrue((self.path/refreshed['input_path']).is_file())
        self.assertEqual(refreshed['input_hash'],task['input_hash'])

    def test_preupgrade_task_identity_and_json_entry_are_preserved(self):
        task=self.f.cli('advance')['task']
        row=dict(self.db.execute('SELECT * FROM tasks WHERE id=?',(task['task_id'],)).fetchone())
        old=json.loads(row['input']);old.pop('capabilities');old.pop('writer_session_policy')
        frozen=dump(old);hashed=digest(old)
        self.db.execute('UPDATE tasks SET input=?,input_hash=? WHERE id=?',(frozen,hashed,row['id']))
        oldtask=engine.task_files(self.db,self.path,{**row,'input':frozen,'input_hash':hashed})
        self.assertNotIn('reading_view',oldtask);self.assertNotIn('capabilities',oldtask)
        self.assertEqual(self.db.execute('SELECT input FROM tasks WHERE id=?',(row['id'],)).fetchone()[0],frozen)

    def test_chapter_writer_policy_ends_worker_without_changing_logical_batch(self):
        put(self.db,'writer_session_policy','chapter')
        task=self.f.cli('advance')['task']
        self.assertEqual(task['chapter_session']['role'],'writer')
        self.assertNotIn('--continue-chapter-batch',task['commands']['submit'])
        spec=json.loads((self.path/task['host_dispatch_path']).read_text(encoding='utf-8'))
        self.assertFalse(spec['launched']);self.assertFalse(spec['permissions_enforced'])
        self.assertEqual(spec['reviewer_session_policy'],'batch')
        self.f.submit(task,self.f.draft(task))
        following=self.f.cli('advance','--after',task['task_id'])['task']
        self.assertEqual(following['chapter_session']['batch'],task['chapter_session']['batch'])
        self.assertEqual(following['chapter'],task['chapter']+1)


class ScheduleTests(unittest.TestCase):
    def test_coordinates_never_pass_via_an_alternative_interpretation(self):
        plan={'timeline':{'basis':'batch_anchor','end_offset_range':[2,3]}}
        schedule.normalize(plan,7);schedule.check(plan,8,9,2)
        with self.assertRaises(Error):schedule.check(plan,14,16,2)  # delta=2 used to bypass anchor=7
        plan={'timeline':{'basis':'chapter_start','end_offset_range':[2,2]}}
        schedule.normalize(plan,7);schedule.check(plan,14,16,2)
        with self.assertRaises(Error):schedule.check(plan,14,17,2)  # no hidden +1 on explicit ranges
        plan={'timeline':{'basis':'absolute_clock','end_clock_range':[16,16]}}
        schedule.normalize(plan,7);schedule.check(plan,14,16,2)

    def test_relative_eight_days_and_same_day_batch_are_valid(self):
        plan={'scenes':[{'time':{'basis':'batch_anchor','offset_days':8}}]}
        schedule.normalize(plan,7);schedule.check(plan,7,15,1)
        plan={'scenes':[{'time':{'basis':'batch_anchor','offset_days':1}}]}
        schedule.normalize(plan,7);schedule.check(plan,8,8,2)

    def test_mixed_coordinates_reverse_scenes_and_forged_anchor_rejected(self):
        for plan in ({'timeline':{'basis':'batch_anchor','anchor_clock':99,'end_offset_range':[1,1]}},
                     {'timeline':{'basis':'chapter_start','end_clock_range':[1,2]}},
                     {'scenes':[{'time':{'basis':'batch_anchor','offset_days':2}},
                                {'time':{'basis':'batch_anchor','offset_days':1}}]},
                     {'scenes':[{'time':{'basis':'chapter_start','offset_days':1}},
                                {'time':{'basis':'absolute_clock','clock':2}}]}):
            with self.subTest(plan=plan),self.assertRaises(Error):schedule.normalize(plan,7)

    def test_legacy_batch_anchor_does_not_reinterpret_chapter_delta(self):
        plan={'day_anchor':0,'day_span':[1,1]}
        schedule.check(plan,2,3,3)
        with self.assertRaises(Error):schedule.check(plan,2,2,3)

    def test_impossible_multi_chapter_plan_is_rejected_before_writing(self):
        plans=[{'number':1,'timeline':{'basis':'absolute_clock','end_clock_range':[9,9]}},
               {'number':2,'timeline':{'basis':'absolute_clock','end_clock_range':[8,8]}}]
        for plan in plans:schedule.normalize(plan,7)
        with self.assertRaises(Error):schedule.validate_sequence(plans,7)
        with self.assertRaises(Error):schedule.normalize({'timeline':{'basis':'absolute_clock','end_clock_range':[1,2]}},7)


class ObservationTests(unittest.TestCase):
    def event(self,**extra):
        return {'schema':observability.PROTOCOL,'provider':'test','session_id':'s','request_id':'1',
                'input_tokens':10,'cache_read_tokens':90,'output_tokens':2,**extra}

    def test_duplicate_requests_unknown_usage_and_conflicting_exports(self):
        self.assertEqual(observability.summary([self.event(),self.event()])['gross_tokens'],102)
        result=observability.summary([self.event(input_tokens=None)])
        self.assertIsNone(result['gross_tokens']);self.assertEqual(result['unknown_calls'],1)
        with self.assertRaises(Error):observability.summary([self.event(),self.event(output_tokens=3)])
        for bad in (-1,True,'10'):
            with self.assertRaises(Error):observability.summary([self.event(input_tokens=bad)])

    def test_antigravity_full_and_compact_do_not_double_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'transcript.jsonl'
            record={'type':'PLANNER_RESPONSE','step_index':1,'input_tokens':10,'cache_read_tokens':90,'output_tokens':2}
            path.write_text(json.dumps(record)+'\n'+json.dumps({'type':'GENERIC','step_index':2})+'\n',encoding='utf-8')
            rows=observability.import_antigravity(path,session_id='s')
            stream=Path(tmp)/'observations.jsonl'
            observability.append(stream,rows);result=observability.append(stream,rows)
            self.assertEqual(result['calls'],1);self.assertEqual(result['gross_tokens'],102)
            self.assertFalse((Path(tmp)/'novel.sqlite3').exists())

    def test_missing_session_remains_incomplete_after_saving_stream(self):
        with tempfile.TemporaryDirectory() as tmp:
            stream=Path(tmp)/'observations.jsonl'
            gap=self.event(session_id='missing',request_id='coverage-gap',observation_type='coverage_gap',
                           input_tokens=None,cache_read_tokens=None,output_tokens=None)
            observability.append(stream,[self.event(),gap])
            saved=[json.loads(line) for line in stream.read_text(encoding='utf-8').splitlines()]
            result=observability.summary(saved)
            self.assertEqual(result['calls'],1);self.assertIsNone(result['gross_tokens'])
            self.assertEqual(result['missing_sessions'][0]['session_id'],'missing')
            result=observability.append(stream,[self.event(session_id='missing')])
            self.assertTrue(result['usage_complete']);self.assertEqual(result['calls'],2)

    def test_conflicting_append_leaves_existing_stream_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            stream=Path(tmp)/'observations.jsonl';observability.append(stream,[self.event()])
            original=stream.read_bytes()
            with self.assertRaises(Error):observability.append(stream,[self.event(output_tokens=9)])
            self.assertEqual(stream.read_bytes(),original)
            self.assertFalse(stream.with_name(stream.name+'.lock').exists())
