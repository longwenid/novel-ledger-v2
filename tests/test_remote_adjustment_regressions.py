"""Regression coverage for the imported remote workflow changes."""
import json
import tempfile
import unittest
from pathlib import Path

from support import contract, ev, plan, respond, review
import novel
from novel_core import design, engine
from novel_core.ledger import append_events, replay
from novel_core.memory import attach_prose, stage_primer
from novel_core.store import connect, digest, dump, get, initialize, put


class RemoteAdjustmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)/'book'
        initialize(self.project, contract())
        self.db = connect(self.project)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def original_chapter(self, layered=False, canon=False):
        prose = '周行把收据交给宋禾。'
        events = [{'op': 'state', 'entity': 'hero', 'key': 'receipt',
                   'before': None, 'after': '交付', 'evidence': ev(prose)}] if canon else []
        append_events(self.db, 'main', 1, prose, events, replay(self.db, 'main'))
        stored = {'events': events, 'summary': '交付收据。', 'prose_hash': digest(prose),
                  'facts_review': review(engine.FACT_CHECKS, prose),
                  'reader_review': review(engine.READER_CHECKS, prose)}
        self.db.execute('INSERT INTO chapters VALUES (?,?,?,?,?)',
                        ('main', 1, prose, dump(stored), digest(prose)))
        self.db.execute('INSERT INTO plans VALUES (?,?,?)', ('main', 1, dump(plan(1))))
        put(self.db, 'pilot_approved', True)
        if layered:
            c = get(self.db, 'contract'); c['planning_mode'] = 'layered'; put(self.db, 'contract', c)
            refs = [{'entity': 'hero', 'key': 'receipt', 'value': '交付'}] if canon else []
            world = {'active': True, 'sources': {'1': digest(prose)},
                     'data': {'overview': '追索', 'canon_refs': refs}, 'hash': 'world'}
            volume = {'active': True, 'sources': {'1': digest(prose)},
                      'data': {'volume': 'v1'}, 'world_hash': 'world', 'hash': 'volume'}
            put(self.db, design.key('main', 'world'), world)
            put(self.db, design.key('main', 'v1'), volume)
        return prose

    def test_original_prose_delivery_and_resume(self):
        prose = self.original_chapter()
        engine.begin_revision(self.db, 1, '调整文风', style_only=True)
        task = engine.next_task(self.db, self.project)
        work = json.loads((self.project/task['input_path']).read_text(encoding='utf-8'))
        path = self.project/work['payload']['original_prose_path']
        self.assertEqual(path.read_text(encoding='utf-8'), prose)
        self.assertIn(work['payload']['original_prose_path'], task['prose_files'])
        path.unlink()
        resumed = engine.next_task(self.db, self.project)
        self.assertEqual(resumed['task_id'], task['task_id'])
        self.assertEqual(path.read_text(encoding='utf-8'), prose)

    def test_both_prose_variants_are_materialized(self):
        work = {'payload': {'prose': '改稿。', 'original_prose': '原稿。'}}
        attach_prose(self.db, work)
        paths = engine.materialize_prose(self.db, self.project, work)
        self.assertEqual(len(paths), 2)
        self.assertEqual((self.project/work['payload']['prose_path']).read_text(encoding='utf-8'), '改稿。')
        self.assertEqual((self.project/work['payload']['original_prose_path']).read_text(encoding='utf-8'), '原稿。')

    def test_style_revision_keeps_source_bound_designs_usable(self):
        self.original_chapter(layered=True, canon=True)
        branch = engine.begin_revision(self.db, 1, '调整文风', style_only=True)['branch']
        self.assertTrue(design.usable(self.db, branch, 'world'))
        self.assertTrue(design.usable(self.db, branch, 'v1'))
        self.assertEqual(engine.next_task(self.db, self.project)['stage'], 'draft')
        self.assertEqual(design.current(self.db, branch, 'world')['source_branch'], 'main')

    def test_style_revision_runs_reviews_and_promotes_with_valid_designs(self):
        self.original_chapter(layered=True)
        branch = engine.begin_revision(self.db, 1, '调整文风', style_only=True)['branch']
        stages = []
        for _ in range(12):
            task = engine.next_task(self.db, self.project)
            stages.append(task['stage'])
            if task['stage'] == 'revision_promoted':
                break
            self.assertIn('task_id', task)
            respond(self.db, self.project, task, prose='周行将收据递给宋禾。')
        self.assertEqual(stages[-1], 'revision_promoted')
        self.assertIn('chapter_review', stages)
        self.assertNotIn('facts', stages)
        self.assertNotIn('reader', stages)
        self.assertEqual(get(self.db, 'active_branch'), branch)
        self.assertIsNone(get(self.db, 'style_only_branch'))
        self.assertTrue(design.usable(self.db, branch, 'world'))
        self.assertTrue(design.usable(self.db, branch, 'v1'))

        next_task = engine.next_task(self.db, self.project)
        self.assertEqual(next_task['stage'], 'plan')
        work = json.loads((self.project/next_task['input_path']).read_text(encoding='utf-8'))
        original = next(item for item in work['selected']
                        if item.get('kind') == 'required_chapter' and item.get('branch') == 'main')
        self.assertEqual((self.project/original['prose_path']).read_text(encoding='utf-8'), '周行把收据交给宋禾。')
        reads = self.db.execute('SELECT sources FROM task_reads WHERE consumer_task=?',
                                (next_task['task_id'],)).fetchall()
        refs = [ref for row in reads for ref in json.loads(row[0])]
        self.assertTrue(any(ref['kind'] == 'chapter_full' and ref['branch'] == 'main' for ref in refs))

    def test_style_source_integrity_is_still_checked(self):
        self.original_chapter(layered=True)
        branch = engine.begin_revision(self.db, 1, '调整文风', style_only=True)['branch']
        self.db.execute("UPDATE chapters SET hash='changed' WHERE branch='main' AND number=1")
        self.assertFalse(design.usable(self.db, branch, 'world'))

    def test_style_revision_does_not_hide_canon_conflicts(self):
        prose = self.original_chapter(layered=True, canon=True)
        branch = engine.begin_revision(self.db, 1, '调整文风', style_only=True)['branch']
        event = {'op': 'state', 'entity': 'hero', 'key': 'receipt', 'before': None,
                 'after': '未交付', 'evidence': ev(prose)}
        self.db.execute('INSERT INTO chapters VALUES (?,?,?,?,?)',
                        (branch, 1, prose, dump({'events': [event]}), digest(prose)))
        append_events(self.db, branch, 1, prose, [event], replay(self.db, branch))
        self.assertFalse(design.usable(self.db, branch, 'world'))

    def test_ordinary_revision_still_invalidates_source_bound_design(self):
        self.original_chapter(layered=True)
        branch = engine.begin_revision(self.db, 1, '更改因果')['branch']
        self.assertFalse(design.usable(self.db, branch, 'world'))
        self.assertEqual(engine.next_task(self.db, self.project)['stage'], 'world_plan')

    def test_all_legacy_character_config_keys_roundtrip(self):
        for key in ('context_chars', 'context_output_chars', 'context_retrieval_chars', 'context_overhead_chars'):
            args = novel.parser().parse_args(['config', '--project', str(self.project),
                                             '--key', key, '--value', '80000'])
            output = novel.dispatch_raw(args)
            self.assertEqual(output[key], 80000)
            self.assertIn('deprecated', output)
            args.value = None
            self.assertEqual(novel.dispatch_raw(args), {key: 80000})

    def test_review_examples_match_validator_and_fix_findings(self):
        for stage in ('chapter_review', 'batch_review', 'revision_review'):
            primer = stage_primer(stage)
            example, _ = json.JSONDecoder().raw_decode(primer.split('data 示例=', 1)[1])
            example['checks'][0]['id'] = 'continuity'
            engine.validate_review(example, ('continuity',), {1: '原文'})
            finding, _ = json.JSONDecoder().raw_decode(primer.split('findings 至少一项：', 1)[1])
            example['verdict'] = 'fix'; example['checks'][0]['result'] = 'fix'
            example['findings'] = [finding]
            engine.validate_review(example, ('continuity',), {1: '原文'})

    def test_synthesis_memory_and_reader_requirements_are_documented(self):
        self.assertIn('memory={text:', stage_primer('revision_review'))
        self.assertIn('scope 不以 _chunk', stage_primer('batch_review'))
        self.assertIn('first_read={desire,obstacle,change,read_on_reason}', stage_primer('chapter_review'))
