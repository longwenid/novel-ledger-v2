"""Revision start routing: a repair directive, not the evidence location.

A cross-chapter review proves a finding by quoting prose, and the proof often
lives in an EARLIER chapter than the one that needs fixing — the newest chapter
contradicts what chapter 2 established, so the quote comes from chapter 2.
Deriving the start from `min(evidence.chapter)` therefore pulled the whole
suffix backwards and re-drafted chapters nobody had implicated, and a
rewritten correct chapter can come back worse. `repair_route` was documented
and instructed but never read by the engine.

Guessing was then removed rather than re-weighted: on a chunked review the
"newest chapter in range" fallback is the CHUNK's edge, not the defect's
chapter (session 14006c71: a chunk covering ch4-6 returned fix for ch5, the
branch began at ch6, and the defective ch5 was copied into the branch base
untouched — the chain could never fix its own trigger). So a cross-chapter
fix verdict must now declare its repair site (`target_chapter` or
`repair_route`); the fallback only ever sees a single-chapter range.
"""
import json
import tempfile
import unittest
from pathlib import Path

from support import (
    connect,
    contract,
    envelope,
    ev,
    initialize,
    respond,
    result,
    review,
    rpath,
)
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
        payload = envelope(task)['payload']
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

    def test_a_minor_defect_ridden_along_with_pass_never_opens_a_revision(self):
        """The measured incident, end to end.

        A batch review found one mixed-script character in an UNREVISED chapter.
        It could not report it as `note` — the contract said `pass` carries no
        findings — so it escalated to `quality`, which opened a revision from
        that chapter and re-drafted the entire suffix for one glyph. Recorded as
        a note alongside `pass`, the same input must leave the branch untouched.
        """
        task = engine.next_task(self.db, self.path)
        self.assertEqual(task['stage'], 'volume_review')
        payload = envelope(task)['payload']
        prose = self.db.execute('SELECT prose FROM chapters WHERE branch=? AND number=1',
                                (task['branch'],)).fetchone()[0]
        data = review(payload['checks'], prose, 1)
        data['findings'] = [{'severity': 'note', 'impact': '混入一个繁体字，读者会出戏',
                             'goal': '统一为简体，不要求返工', 'evidence': ev(prose, 1)}]
        engine.submit(self.db, result(task, data))          # verdict stays pass
        self.assertEqual(get(self.db, 'work_branch'), 'main', '轻微缺陷不得触发修订链')
        self.assertIsNone(self.db.execute(
            'SELECT * FROM branches WHERE status=?', ('candidate',)).fetchone())

    def test_undeclared_cross_chapter_fix_is_refused(self):
        """The engine does not guess the repair site on a multi-chapter review.

        Proof quoted from chapter 1 could mean the defect lives in chapter 1
        or in the newest chapter contradicting it; a chunk's edge can also sit
        one chapter PAST the defect (session 14006c71 started a branch at ch6
        for a ch5 finding). An undeclared blocker/quality finding is refused
        at submit with the declaration contract in the answer; the branch
        stays untouched."""
        with self.assertRaises(Error) as caught:
            self._fix(1)
        self.assertEqual(caught.exception.code, 'finding')
        self.assertIn('target_chapter', str(caught.exception))
        self.assertEqual(get(self.db, 'work_branch'), 'main', '拒收不得开链')
        self.assertIsNone(self.db.execute(
            'SELECT * FROM branches WHERE status=?', ('candidate',)).fetchone())

    def test_declared_fix_targets_the_cited_middle_chapter(self):
        """Session 14006c71, chain 2 shape: a chunk covering ch4-6 cited ch5.
        Declared `target_chapter` must land the branch exactly on ch5 — never
        on the chunk's edge, never past the defect."""
        self._fix(2, target_chapter=2)
        revision = self._revision()
        self.assertEqual((revision['start'], revision['through']), (2, 3))
        self.assertEqual(engine.next_task(self.db, self.path)['chapter'], 2)

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
