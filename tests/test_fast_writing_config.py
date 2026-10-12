"""Fast writing defaults, compatible pending work, and full combined pipeline."""
import json
import tempfile
import unittest
from pathlib import Path

from support import *
from novel_core.store import Error, head
import novel


class FastWritingConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        c = contract()
        c.pop('review_chunk')
        initialize(self.path, c)
        self.db = connect(self.path)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def config(self, key=None, value=None):
        args = ['config', '--project', str(self.path)]
        if key is not None:
            args += ['--key', key]
        if value is not None:
            args += ['--value', str(value)]
        return novel.dispatch(novel.parser().parse_args(args))

    def test_defaults_are_visible_and_configurable(self):
        self.assertEqual(self.config()['writer_batch'], 1)
        self.assertEqual(self.config('review_chunk'), {'review_chunk': 3})
        self.assertEqual(engine.status(self.db)['review_mode'], 'combined')
        with self.assertRaises(Error):self.config('writer_batch',5)
        self.assertEqual(self.config('writer_batch', 1), {'writer_batch': 1})
        respond(self.db, self.path, engine.next_task(self.db, self.path))
        respond(self.db, self.path, engine.next_task(self.db, self.path))
        self.assertEqual(engine.next_task(self.db, self.path)['stage'], 'chapter_review')

    def test_invalid_config_and_pending_changes_rejected(self):
        for key, value in [('writer_batch', 0), ('writer_batch', 11)]:
            with self.assertRaises(Error):
                self.config(key, value)
        task = engine.next_task(self.db, self.path)
        for key, value in [('writer_batch', 1)]:
            with self.assertRaises(Error) as exc:
                self.config(key, value)
            self.assertEqual(exc.exception.code, 'pending')
        self.assertEqual(engine.next_task(self.db, self.path)['task_id'], task['task_id'])

    def test_review_mode_switch_is_not_an_available_option(self):
        with self.assertRaises(SystemExit):
            novel.parser().parse_args(['config', '--project', str(self.path), '--key', 'review_mode', '--value', 'separate'])

    def test_old_project_rejected_without_migration(self):
        put(self.db, 'schema', 'novel-ledger-v2/1')
        original_contract = get(self.db, 'contract')
        with self.assertRaises(Error) as exc:
            connect(self.path)
        self.assertEqual(exc.exception.code, 'schema')
        self.assertEqual(get(self.db, 'schema'), 'novel-ledger-v2/1')
        self.assertEqual(get(self.db, 'contract'), original_contract)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], 0)

    def test_first_read_requires_meaningful_text_fields(self):
        respond(self.db, self.path, engine.next_task(self.db, self.path))
        respond(self.db, self.path, engine.next_task(self.db, self.path))
        task = engine.next_task(self.db, self.path)
        work = envelope(task)
        prose = pack_prose(self.path, work)
        for value in (True, 42, '   ', []):
            receipt = combined_review(work, prose)
            receipt['reader']['first_read']['change'] = value
            with self.assertRaises(Error) as exc:
                engine.submit(self.db, result(task, receipt))
            self.assertEqual(exc.exception.code, 'first_read')
            self.assertEqual(head(self.db, 'main'), 0)
        self.assertEqual(engine.next_task(self.db, self.path)['task_id'], task['task_id'])

    def test_combined_pipeline_passes_ten_chapter_review_and_completion(self):
        self.config('review_chunk', 3)
        out = drive(self.db, self.path, 11)
        self.assertEqual(out['status'], 'completed')
        self.assertTrue(engine.audit(self.db)['ok'])
        tasks = self.db.execute('SELECT stage,COUNT(*) FROM tasks GROUP BY stage').fetchall()
        counts = {row[0]: row[1] for row in tasks}
        self.assertEqual(counts['chapter_review'], 11)
        self.assertNotIn('facts', counts)
        self.assertNotIn('reader', counts)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM reviews WHERE scope='batch_chunk'").fetchone()[0], 4)
        for chapter in self.db.execute('SELECT data FROM chapters'):
            stored = json.loads(chapter[0])
            self.assertEqual(stored['facts_review']['verdict'], 'pass')
            self.assertEqual(stored['reader_review']['verdict'], 'pass')

    def test_invalid_initial_settings_do_not_create_book(self):
        for setting in ({'writer_batch': True}, {'writer_batch': 11}, {'review_chunk': 0}, {'review_chunk': 6}):
            with tempfile.TemporaryDirectory() as temp:
                with self.assertRaises(Error):
                    initialize(temp, contract() | setting)
                self.assertFalse((Path(temp) / 'novel.sqlite3').exists())
