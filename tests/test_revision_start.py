"""Revision start routing: a repair directive, not the evidence location.

A cross-chapter review proves a finding by quoting prose, and the proof often
lives in an EARLIER chapter than the one that needs fixing — the newest chapter
contradicts what chapter 2 established, so the quote comes from chapter 2.
Deriving the start from `min(evidence.chapter)` therefore pulled the whole
suffix backwards and re-drafted chapters nobody had implicated, and a
rewritten correct chapter can come back worse. `repair_route` was documented
and instructed but never read by the engine.

These tests lock the routing: declared intent is honoured, undeclared fixes
stay in the newest chapter, and backtracking must be asked for explicitly.
"""
import json
import tempfile
import unittest
from pathlib import Path

from support import (connect, contract, ev, initialize, respond, result,
                     review, rpath)
from novel_core import engine
from novel_core.store import Error, get, head


class RevisionStartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        setup = contract()
        setup['chapter_batch_size'] = 1
        setup['review_chunk'] = 3
        initialize(self.path, setup)
        self.db = connect(self.path)
        self.addCleanup(self.db.close)
        self._step_until('volume_review')          # three chapters, ch3 ends the volume
        self.assertEqual(head(self.db, 'main'), 3)

    def _step_until(self, wanted):
        for _ in range(30):
            task = engine.next_task(self.db, self.path)
            if task.get('stage') == wanted:
                return task
            if task.get('stage') == 'await_author':
                engine.approve(self.db)
                continue
            self.assertTrue(task.get('task_id'), task)
            respond(self.db, self.path, task, ending=(task.get('chapter') == 3))
        raise AssertionError(f'流程未到达 {wanted}')

    def _fix(self, evidence_chapter, **finding):
        """Submit a verdict=fix whose proof is quoted from `evidence_chapter`."""
        task = engine.next_task(self.db, self.path)
        self.assertEqual(task['stage'], 'volume_review')
        payload = json.loads(rpath(task, 'input_path').read_text(encoding='utf-8'))['payload']
        prose = self.db.execute('SELECT prose FROM chapters WHERE branch=? AND number=?',
                                (task['branch'], evidence_chapter)).fetchone()[0]
        data = review(payload['checks'], prose, evidence_chapter)
        data['verdict'] = 'fix'
        data['findings'] = [{'severity': 'quality', 'impact': '后章与前文口径冲突',
                             'goal': '统一口径', 'evidence': ev(prose, evidence_chapter),
                             **finding}]
        engine.submit(self.db, result(task, data))
        return payload

    def _revision(self):
        row = self.db.execute('SELECT * FROM branches WHERE id=?',
                              (get(self.db, 'work_branch'),)).fetchone()
        return row

    def test_undeclared_fix_repairs_the_newest_chapter(self):
        """Regression: proof quoted from chapter 1 must not drag the revision
        back to chapter 1 when nothing says the fix belongs there."""
        payload = self._fix(1)
        self.assertEqual((payload['first'], payload['through']), (1, 3))
        revision = self._revision()
        self.assertEqual((revision['start'], revision['through']), (3, 3))
        self.assertEqual(engine.next_task(self.db, self.path)['chapter'], 3)

    def test_target_chapter_declares_the_repair_location(self):
        self._fix(3, target_chapter=2)
        revision = self._revision()
        self.assertEqual((revision['start'], revision['through']), (2, 3))

    def test_repair_route_routes_to_the_named_chapter(self):
        """`repair_route` is the field the protocol always promised; it now
        actually routes the revision instead of being ignored."""
        for route in ('patch', 'revise'):
            with self.subTest(route=route):
                self.setUp()
                self._fix(2, repair_route=route)
                self.assertEqual(self._revision()['start'], 2)
                self.tearDown()

    def test_target_chapter_outside_the_review_range_is_refused(self):
        with self.assertRaises(Error) as caught:
            self._fix(1, target_chapter=9)
        self.assertEqual(caught.exception.code, 'finding')

    def test_unknown_repair_route_is_refused(self):
        with self.assertRaises(Error) as caught:
            self._fix(1, repair_route='rewrite')
        self.assertEqual(caught.exception.code, 'finding')


if __name__ == '__main__':
    unittest.main()
