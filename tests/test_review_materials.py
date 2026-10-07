"""Review-pack material correctness: evidence coordinates and per-chapter plans.

Chunk-review packs concatenate chapters behind 【第N章】 display headers, while
evidence validates against each chapter's stored raw prose. These tests pin the
header-stripping fallback, the range plan attachment, and synthesis slimming.
"""
import json
import tempfile
import unittest
from pathlib import Path
from support import *
from novel_core import engine
from novel_core.ledger import evidence
from novel_core.memory import pack
from novel_core.store import Error, dump, digest, put, transaction


class EvidenceCoordinateTests(unittest.TestCase):
    prose = '周行把收据交给宋禾。她看了许久，终于按下指印。'

    def test_header_wrapped_quote_relocates_into_raw_prose(self):
        item = {'chapter': 1, 'start': 0, 'quote': '【第1章】\n周行把收据交给宋禾。'}
        self.assertTrue(evidence(self.prose, item, 1))
        self.assertEqual(item['quote'], '周行把收据交给宋禾。')
        self.assertEqual(item['start'], 0)

    def test_drifted_start_still_recalibrates_without_header(self):
        item = {'chapter': 1, 'start': 999, 'quote': '终于按下指印'}
        self.assertTrue(evidence(self.prose, item, 1))
        self.assertEqual(item['start'], self.prose.index('终于按下指印'))
        self.assertEqual(item['quote'], '终于按下指印')

    def test_ambiguous_stripped_quote_stays_an_error(self):
        prose = '这句话出现了。前后都是这句话出现了。'
        item = {'chapter': 1, 'start': 0, 'quote': '【第2章】\n这句话出现了。'}
        with self.assertRaises(Error) as exc:
            evidence(prose, item, 1)
        self.assertEqual(exc.exception.code, 'evidence')

    def test_garbage_quote_without_header_still_rejected(self):
        item = {'chapter': 1, 'start': 0, 'quote': '正文里没有这句话'}
        with self.assertRaises(Error):
            evidence(self.prose, item, 1)

    def test_chapter_mismatch_still_enforced(self):
        item = {'chapter': 3, 'start': 0, 'quote': '周行把收据交给宋禾。'}
        with self.assertRaises(Error) as exc:
            evidence(self.prose, item, 1)
        self.assertEqual(exc.exception.code, 'evidence')


class ReviewPackMaterialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.path = Path(self.temp.name)
        initialize(self.path, contract());self.db = connect(self.path)
        put(self.db, 'pilot_approved', True)
        put(self.db, 'context_mode', 'bounded')
        self._commit_book()

    def tearDown(self): self.db.close();self.temp.cleanup()

    def _commit_book(self, n=10):
        from novel_core.ledger import seed, append_events
        state = seed(get(self.db, 'contract'))
        text = '周行在档案室核对记录，宋禾在门房登记来访。'
        with transaction(self.db):
            for i in range(1, n + 1):
                events = [{'op': 'clock', 'before': i - 1, 'after': i, 'evidence': {'chapter': i, 'start': 0, 'quote': text}}]
                data = {'volume': 'v1', 'summary': f'第{i}章例行核查。', 'events': events, 'prose_hash': digest(text),
                        'facts_review': {'verdict': 'pass'}, 'reader_review': {'verdict': 'pass'}}
                self.db.execute('INSERT OR REPLACE INTO chapters VALUES (?,?,?,?,?)', ('main', i, text, dump(data), digest(text)))
                self.db.execute('INSERT OR REPLACE INTO plans VALUES (?,?,?)', ('main', i, dump(plan(i))))
                append_events(self.db, 'main', i, text, events, state)

    def _work(self):
        task = engine.next_task(self.db, self.path)
        self.assertEqual(task['stage'], 'batch_review')
        return task, json.loads(rpath(task, 'input_path').read_text(encoding='utf-8'))

    def test_chunk_review_carries_each_reviewed_chapters_plan(self):
        with transaction(self.db): put(self.db, 'review_chunk', 3)
        task, work = self._work()
        payload = work['payload']
        self.assertEqual((payload['first'], payload['through']), (1, 3))
        self.assertNotIn('plan', work)
        self.assertEqual(set(work['plans']), {'1', '2', '3'})
        self.assertEqual(work['plans']['2']['number'], 2)

    def test_review_pack_declares_prose_sized_reading_allowance(self):
        task, work = self._work()  # default chunk (1,1)
        prose_chars = self.db.execute('SELECT SUM(LENGTH(prose)) FROM chapters WHERE branch=? AND number BETWEEN 1 AND 1', ('main',)).fetchone()[0]
        self.assertEqual(work['retrieval_allowance'], prose_chars * 3 // 2 + 4096)

    def test_bounded_read_honors_declared_allowance_over_pack_size(self):
        from novel_core import contextflow
        put(self.db, 'context_mode', 'bounded')
        with transaction(self.db):
            self.db.execute("INSERT INTO tasks(id,branch,stage,chapter,input_hash,input,state) VALUES ('t_pack','main','batch_review',10,'h',?,'done')", ('{"a":1}',))
            self.db.execute("INSERT INTO tasks(id,branch,stage,chapter,input_hash,input,state) VALUES ('t_decl','main','batch_review',10,'h',?,'pending')", ('{"a":1,"retrieval_allowance":9000}',))
        long_text = {'text': '字' * 5000}
        fragment = contextflow.bounded_read(self.db, long_text, 't_pack')
        self.assertEqual(fragment['format'], 'json_text_fragment')  # pack-sized cap
        whole = contextflow.bounded_read(self.db, long_text, 't_decl')
        self.assertEqual(whole['text'], '字' * 5000)  # declared allowance wins

    def test_recall_chapter_returns_prose_without_ledger(self):
        import novel
        out = novel.dispatch(novel.parser().parse_args(['recall', '--project', str(self.path), '--chapter', '1']))
        self.assertEqual(set(out), {'branch', 'number', 'hash', 'prose', 'summary'})
        self.assertIn('周行在档案室', out['prose'])
        self.assertTrue(out['summary'])

    def test_facts_and_reader_declare_prose_sized_allowance(self):
        from novel_core.memory import reading_allowance
        prose = '周行在档案室核对记录，宋禾在门房登记来访。'
        for stage in ('facts', 'reader'):
            work = pack(self.db, 'main', 3, stage, {'prose': prose, 'checks': ('coverage',)})
            self.assertEqual(work['retrieval_allowance'], reading_allowance(len(prose)))
        leftover = pack(self.db, 'main', 3, 'facts', {'checks': ('coverage',)})
        self.assertNotIn('retrieval_allowance', leftover)  # 无正文可核验时不声明

    def test_review_discipline_names_branch_coordinate(self):
        task, work = self._work()
        self.assertIn('branch 字段所指分支', work['discipline']['review'])
        self.assertIn('active 分支', work['discipline']['review'])

    def test_chunk_plans_track_reviewed_range_not_anchor(self):
        with transaction(self.db): put(self.db, 'review_chunk', 5)
        task, work = self._work()  # chunk 1..5
        self.assertEqual(set(work['plans']), {'1', '2', '3', '4', '5'})
        data = {'verdict': 'pass', 'first': 1, 'through': 5, 'checks': [], 'findings': []}
        with transaction(self.db):
            self.db.execute("UPDATE tasks SET state='cancelled' WHERE state='pending'")
            self.db.execute('INSERT INTO reviews VALUES (?,?,?,?,?)',
                            ('main', 'batch_chunk', 5, engine.review_hash(self.db, 'main', 1, 5), dump(data)))
        task, work = self._work()  # chunk 6..10; anchor chapter stays 10
        self.assertEqual((work['payload']['first'], work['payload']['through']), (6, 10))
        self.assertNotIn('plan', work)
        self.assertEqual(set(work['plans']), {'6', '7', '8', '9', '10'})
        self.assertIn('【第6章】', work['payload']['prose'])

    def test_synthesis_materials_drop_evidence_quotes(self):
        with transaction(self.db): put(self.db, 'review_chunk', 5)
        for first, through in ((1, 5), (6, 10)):
            task, work = self._work()
            self.assertEqual((work['payload']['first'], work['payload']['through']), (first, through))
            data = {'verdict': 'pass', 'first': first, 'through': through,
                    'checks': [{'id': 'coverage', 'result': 'pass', 'reason': '这是测试审稿回执，不是文学质量证据。',
                                'evidence': [{'chapter': first, 'start': 0, 'quote': '周行在档案室核对记录，宋禾在门房登记来访。'}]}],
                    'findings': []}
            with transaction(self.db):
                self.db.execute("UPDATE tasks SET state='cancelled' WHERE state='pending'")
                self.db.execute('INSERT INTO reviews VALUES (?,?,?,?,?)',
                                ('main', 'batch_chunk', through, engine.review_hash(self.db, 'main', first, through), dump(data)))
        task, work = self._work()  # whole-range synthesis
        self.assertNotIn('plan', work)
        materials = work['synthesis_materials']
        self.assertEqual([m['chapter'] for m in materials], [5, 10])
        self.assertNotIn('quote', dump(materials))
        self.assertEqual(materials[0]['review']['checks'][0]['evidence'], [{'chapter': 1, 'start': 0}])


if __name__ == '__main__': unittest.main()
