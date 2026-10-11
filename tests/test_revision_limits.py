"""Revision chain depth guardrails: cascade cap, escalation pause, discard nesting.

Run directly: python tests/test_revision_limits.py
"""
import json
from pathlib import Path
import tempfile
import unittest

from support import contract, drive, engine, ev, initialize, connect, get, plan, respond, review, rpath, submit_review
from novel import dispatch, parser
from novel_core.store import Error, head


class RevisionChainLimits(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.book_number = 0

    def book(self):
        self.book_number += 1
        project = self.root / str(self.book_number)
        initialize(project, contract())
        db = connect(project)
        self.addCleanup(db.close)
        return project, db

    def _fix_review(self, db, task):
        """Submit an aggregate review verdict=fix anchored at the chunk's first chapter."""
        payload = json.loads(rpath(task, 'input_path').read_text(encoding='utf-8'))['payload']
        first = payload['first']
        prose = db.execute('SELECT prose FROM chapters WHERE branch=? AND number=?',
                           (task['branch'], first)).fetchone()[0]
        data = review(payload['checks'], prose, first)
        data['verdict'] = 'fix'
        data['checks'][0]['result'] = 'fix'
        data['findings'] = [{'severity': 'quality', 'impact': '口径在章间漂移',
                             'goal': '对齐耦合章口径', 'evidence': ev(prose, first)}]
        submit_review(db, task, data)

    def _sink_chapter(self, db, project):
        """Respond tasks on the current branch until its head advances once."""
        branch = get(db, 'work_branch')
        before = head(db, branch)
        for _ in range(20):
            task = engine.next_task(db, project)
            self.assertTrue(task.get('task_id'), task)
            respond(db, project, task)
            if head(db, branch) > before:
                return
        self.fail('chapter never committed')

    def test_third_nested_revision_is_refused_with_actionable_details(self):
        project, db = self.book()
        drive(db, project, 3)
        engine.begin_revision(db, 1, '一级修订')
        self._sink_chapter(db, project)
        engine.begin_revision(db, 1, '二级修订')
        with self.assertRaises(Error) as rejected:
            engine.begin_revision(db, 1, '三级修订')
        self.assertEqual(rejected.exception.code, 'revision_chain_limit')
        details = rejected.exception.details
        self.assertEqual(details['chain_depth'], 3)
        self.assertEqual(details['limit'], 2)
        self.assertEqual(len(details['chain']), 2)
        self.assertEqual(len(details['options']), 3)
        self.assertEqual(get(db, 'work_branch')[:4], 'rev-')

    def test_fix_at_depth_cap_pauses_with_directive_and_external_revise_refused(self):
        project, db = self.book()
        drive(db, project, 3)
        chain1 = engine.begin_revision(db, 1, '一级修订：统一口径')['branch']
        # Sink chain1's first chapter tasks so no pending blocks the nested begin;
        # chain1 stays an unfinished candidate, mirroring the runaway topology.
        for _ in range(4):
            task = engine.next_task(db, project)
            if not task.get('task_id'):
                break
            respond(db, project, task)
        chain2 = engine.begin_revision(db, 1, '二级修订：收束表达')['branch']
        for _ in range(300):
            task = engine.next_task(db, project)
            stage = task.get('stage')
            if stage == 'revision_promoted':
                continue
            self.assertTrue(task.get('task_id'), task)
            if stage == 'revision_review':
                self._fix_review(db, task)
                break
            respond(db, project, task, ending=task['chapter'] == 3)
        else:
            self.fail('revision review never dispatched')
        reason = get(db, 'pause_reason')
        self.assertEqual(get(db, 'status'), 'paused')
        self.assertEqual(reason['code'], 'revision_exhausted')
        self.assertEqual(reason['chain_depth'], 3)
        self.assertEqual(reason['limit'], 2)
        self.assertEqual(len(reason['chain']), 2)
        self.assertIn('不得', reason['directive'])
        with self.assertRaises(Error) as rejected:
            dispatch(parser().parse_args([
                'revise', '--project', str(project), '--from-chapter', '1',
                '--reason', '主控绕过暂停',
            ]))
        self.assertEqual(rejected.exception.code, 'revision_chain_limit')
        self.assertEqual(get(db, 'pause_reason', {}).get('code'), 'revision_exhausted')
        # Discarding the top candidate restores the parent candidate, not the active branch.
        dispatch(parser().parse_args(['revise', '--project', str(project), '--discard']))
        self.assertEqual(get(db, 'work_branch'), _parent_branch(db, chain2))
        self.assertEqual(_parent_branch(db, chain2), chain1)
        self.assertEqual(get(db, 'status'), 'ready')
        self.assertIsNone(get(db, 'pause_reason'))
        # Depth is back to one candidate, so a new chain is allowed again.
        engine.begin_revision(db, 1, '回到二级深度')

    def test_promote_resets_chain_depth(self):
        project, db = self.book()
        drive(db, project, 3)
        engine.begin_revision(db, 1, '一级修订')
        self.assertEqual(drive(db, project, 3)['status'], 'completed')
        self.assertNotEqual(get(db, 'active_branch'), 'main')
        engine.begin_revision(db, 1, '新一级修订')
        self._sink_chapter(db, project)
        engine.begin_revision(db, 1, '新二级修订')
        with self.assertRaises(Error) as rejected:
            engine.begin_revision(db, 1, '新三级修订')
        self.assertEqual(rejected.exception.code, 'revision_chain_limit')

    def test_raised_limit_allows_external_third_chain_but_not_fourth(self):
        project, db = self.book()
        drive(db, project, 3)
        engine.begin_revision(db, 1, '一级修订')
        self._sink_chapter(db, project)
        engine.begin_revision(db, 1, '二级修订')
        self._sink_chapter(db, project)
        dispatch(parser().parse_args([
            'config', '--project', str(project),
            '--key', 'revision_chain_limit', '--value', '3',
        ]))
        dispatch(parser().parse_args([
            'revise', '--project', str(project), '--from-chapter', '1',
            '--reason', '作者放宽后的三级修订',
        ]))
        self.assertEqual(get(db, 'status'), 'ready')
        with self.assertRaises(Error) as rejected:
            engine.begin_revision(db, 1, '四级修订')
        self.assertEqual(rejected.exception.code, 'revision_chain_limit')
        self.assertEqual(rejected.exception.details['limit'], 3)

    def test_discard_top_level_candidate_returns_to_active_branch(self):
        project, db = self.book()
        drive(db, project, 3)
        engine.begin_revision(db, 1, '顶层候选')
        active = get(db, 'active_branch')
        dispatch(parser().parse_args(['revise', '--project', str(project), '--discard']))
        self.assertEqual(get(db, 'work_branch'), active)
        self.assertEqual(get(db, 'status'), 'ready')

    def test_revision_payloads_carry_alignment_instructions(self):
        project, db = self.book()
        drive(db, project, 3)
        engine.begin_revision(db, 1, '统一遗物台账口径')
        draft_seen = False
        for _ in range(300):
            task = engine.next_task(db, project)
            stage = task.get('stage')
            if stage == 'revision_promoted':
                continue
            self.assertTrue(task.get('task_id'), task)
            payload = json.loads(rpath(task, 'input_path').read_text(encoding='utf-8'))['payload']
            if stage == 'draft' and not draft_seen:
                self.assertIn('先对账再动笔', payload['instruction'])
                self.assertIn('耦合章', payload['instruction'])
                draft_seen = True
            # Chunk reviews share the stage name; the alignment clause rides the
            # whole-range instruction where scope is plain 'revision'.
            if stage == 'revision_review' and payload.get('scope') == 'revision':
                self.assertIn('残留旧口径', payload['instruction'])
                self.assertTrue(draft_seen)
                break
            respond(db, project, task, ending=task['chapter'] == 3)
        else:
            self.fail('revision review never dispatched')

    def test_config_revision_chain_limit_validation(self):
        project, db = self.book()
        current = dispatch(parser().parse_args([
            'config', '--project', str(project), '--key', 'revision_chain_limit',
        ]))
        self.assertEqual(current['revision_chain_limit'], 2)
        with self.assertRaises(Error) as rejected:
            dispatch(parser().parse_args([
                'config', '--project', str(project),
                '--key', 'revision_chain_limit', '--value', '0',
            ]))
        self.assertEqual(rejected.exception.code, 'config')
        with self.assertRaises(Error) as rejected:
            dispatch(parser().parse_args([
                'config', '--project', str(project),
                '--key', 'revision_chain_limit', '--value', '6',
            ]))
        self.assertEqual(rejected.exception.code, 'config')
        dispatch(parser().parse_args([
            'config', '--project', str(project),
            '--key', 'revision_chain_limit', '--value', '1',
        ]))
        self.assertEqual(get(db, 'revision_chain_limit'), 1)


def _parent_branch(db, branch):
    return db.execute('SELECT parent FROM branches WHERE id=?', (branch,)).fetchone()[0]


if __name__ == '__main__':
    unittest.main()
