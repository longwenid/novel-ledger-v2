"""recontract: amend the stored contract in place instead of wiping the ledger.

The motivating session renamed the protagonist before any prose existed and
the only supported route was delete novel.sqlite3 + re-init + re-run every
planning worker. These tests lock the replacement behavior: renames propagate
into stored planning artifacts (hashes recomputed, nothing re-runs), structural
amendments invalidate like replan, and live prose is refused.
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path

import support  # registers scripts/ on sys.path for `import novel`
import novel
from support import contract, envelope, result, submit_review, respond, review, plan as base_plan
from novel_core import characters, design, engine
from novel_core.ledger import replay
from novel_core.store import Error, connect, initialize, get, put, transaction

EXAMPLE = json.loads((Path(__file__).resolve().parents[1] / 'assets' / 'design.example.json').read_text(encoding='utf-8'))
PROFILES = json.loads((Path(__file__).resolve().parents[1] / 'assets' / 'characters.example.json').read_text(encoding='utf-8'))


def layered(extra=None):
    c = contract()
    c['planning_mode'] = 'layered'
    c['character_mode'] = 'profiled'
    c['volumes'].append({'id': 'v2', 'purpose': '公开结算'})
    c['protagonist_drive'] = '周行查清旧案'
    if extra:
        c.update(extra)
    return c


class RecontractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.path = Path(self.tmp.name)
        self.base = layered(); initialize(self.path, self.base); self.db = connect(self.path)
    def tearDown(self): self.db.close(); self.tmp.cleanup()
    def task(self): return engine.next_task(self.db, self.path)
    def submit(self, task, data): return submit_review(self.db, task, copy.deepcopy(data))
    def plan(self, n=1):
        p = copy.deepcopy(EXAMPLE['chapter_plan']); p.update(number=n)
        shapes = ('闭环', '交接', '爆发', '静收', '反转', '余波')
        p['ending_shape'] = shapes[n % len(shapes)] + f':测试收束{n}'
        p['sequence_refs'] = [min(n, len(EXAMPLE['volume_plan']['sequence']))]
        return p
    def prepare(self):
        self.submit(self.task(), EXAMPLE['world_plan']); self.submit(self.task(), EXAMPLE['volume_plan'])
        chapter = self.plan(); chapter['goal'] = '周行查看原件'
        self.submit(self.task(), {'chapters': [chapter]})
        profiles = copy.deepcopy(PROFILES)
        profiles['profiles'][0]['biography']['public_role'] = '调查人员周行核对档案交接'
        self.submit(self.task(), profiles)
    def amend(self, new_contract, reason='作者改订'):
        target = self.path / 'amended.json'
        target.write_text(json.dumps(new_contract, ensure_ascii=False), encoding='utf-8')
        return novel.dispatch(novel.parser().parse_args(
            ['recontract', '--project', str(self.path), '--contract', str(target), '--reason', reason]))

    def test_rename_propagates_without_re_running_planning(self):
        self.prepare()
        world_before = design.current(self.db, 'main', 'world')
        new = layered()
        new['entities'][0]['name'] = '沈砚'; new['entities'][0]['aliases'] = ['老沈']
        outcome = self.amend(new)
        self.assertEqual([r['to'] for r in outcome['renames']], ['沈砚', '老沈'])
        self.assertNotIn('unmatched_new_aliases', outcome)
        self.assertIn('实体改名已同步', outcome['note'])
        self.assertNotIn('rerun_stages', outcome)

        world = design.current(self.db, 'main', 'world')
        joined = json.dumps(world['data'], ensure_ascii=False)
        self.assertIn('沈砚核对原件', joined); self.assertNotIn('周行', joined)
        self.assertNotEqual(world['hash'], world_before['hash'])
        self.assertTrue(design.usable(self.db, 'main', 'world'))

        volume = design.current(self.db, 'main', 'v1')
        self.assertEqual(volume['world_hash'], world['hash'])
        self.assertTrue(design.usable(self.db, 'main', 'v1'))

        profile = characters.current(self.db, 'main', 'hero')
        self.assertIn('沈砚', profile['data']['biography']['public_role'])
        self.assertTrue(characters.usable(self.db, 'main', 'hero'))

        stored_plan = json.loads(self.db.execute("SELECT data FROM plans WHERE number=1").fetchone()[0])
        self.assertIn('沈砚', stored_plan['goal'])

        # The author's file kept the old name in an untouched field; the global
        # rename keeps the re-seeded state consistent with propagated receipts.
        self.assertIn('沈砚查清旧案', get(self.db, 'contract')['protagonist_drive'])

        # Nothing re-runs: the pipeline advances straight into the draft task.
        task = self.task(); self.assertEqual(task['stage'], 'draft')
        self.assertIn('沈砚', envelope(task)['writer_brief']['dramatic_task']['goal'])

    def test_structural_amendment_invalidates_and_re_dispatches(self):
        self.prepare()
        done = self.db.execute("SELECT count(*) FROM tasks WHERE state='done'").fetchone()[0]
        new = layered(); new['volumes'][0]['id'] = 'v1b'
        outcome = self.amend(new)
        self.assertIn('volumes', outcome['structural'])
        self.assertEqual(outcome['rerun_stages'], ['world_plan', 'volume_plan', 'plan'])
        self.assertFalse(design.current(self.db, 'main', 'world')['active'])
        self.assertFalse(design.current(self.db, 'main', 'v1')['active'])
        self.assertEqual(self.db.execute('SELECT count(*) FROM plans').fetchone()[0], 0)
        self.assertEqual(get(self.db, 'status'), 'ready')
        self.assertEqual(self.task()['stage'], 'world_plan')
        # History survives what used to be a database wipe.
        self.assertEqual(self.db.execute("SELECT count(*) FROM tasks WHERE state='done'").fetchone()[0], done)

    def test_pending_task_blocks_amendment(self):
        self.task()  # creates the world_plan pending envelope
        with self.assertRaises(Error) as caught:
            self.amend(layered())
        self.assertEqual(caught.exception.code, 'pending')

    def test_live_prose_refuses_in_place_amendment(self):
        self.prepare()
        task = self.task(); self.assertEqual(task['stage'], 'draft')
        respond(self.db, self.path, task, prose='沈砚把收据交给宋禾，她核对后按指印。')
        task = self.task(); work = envelope(task)
        from support import pack_prose
        prose = pack_prose(self.path, work)
        data = review(work['payload']['facts_checks'], prose, 1); data.update(events=[], summary='交付凭证')
        self.submit(task, data)
        engine.approve(self.db)
        new = layered(); new['entities'][0]['name'] = '沈砚'
        with self.assertRaises(Error) as caught:
            self.amend(new)
        self.assertEqual(caught.exception.code, 'recontract')

    def test_style_amendment_updates_anchor_and_keeps_designs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            base = layered({'style': {'voice': '市井', 'traits': ['少形容词', '章末不设钩子'], 'sample': '周行把收据压在台面。'}})
            initialize(path, base)
            db = connect(path)
            try:
                task = engine.next_task(db, path)
                engine.submit(db, result(task, copy.deepcopy(EXAMPLE['world_plan'])))
                target = path / 'amended.json'
                new = layered({'style': {'voice': '市井', 'traits': ['少形容词'], 'sample': '周行把收据压在台面。'}})
                target.write_text(json.dumps(new, ensure_ascii=False), encoding='utf-8')
                novel.dispatch(novel.parser().parse_args(
                    ['recontract', '--project', str(path), '--contract', str(target)]))
                self.assertEqual(get(db, 'style_anchor')['traits'], ['少形容词'])
                self.assertTrue(design.current(db, 'main', 'world')['active'])
                self.assertEqual(engine.next_task(db, path)['stage'], 'volume_plan')
            finally:
                db.close()

    def test_identical_contract_is_a_no_op(self):
        outcome = self.amend(layered())
        self.assertEqual(outcome['changed'], [])

    def test_invalid_amendment_leaves_ledger_untouched(self):
        broken = layered(); broken.pop('ending')
        with self.assertRaises(Error) as caught:
            self.amend(broken)
        self.assertEqual(caught.exception.code, 'contract')
        self.assertEqual(get(self.db, 'contract'), self.base)

    def test_config_managed_keys_survive_a_stale_contract_file(self):
        with transaction(self.db):
            stored = get(self.db, 'contract'); stored['chapter_target_han'] = 2600
            put(self.db, 'contract', stored)
        outcome = self.amend(layered())
        self.assertEqual(outcome['preserved_config_keys'], ['chapter_target_han'])
        self.assertEqual(get(self.db, 'contract')['chapter_target_han'], 2600)

    def test_alias_pairing_aligns_retained_entries_not_raw_positions(self):
        # The session's real shape: the author kept 琴师, rewrote the rest and
        # added one more. Raw positional zip would map 老聂→琴师.
        self.prepare()
        old = layered(); old['entities'][0]['aliases'] = ['琴师', '老聂', '远舟']
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp); initialize(path, old); db = connect(path)
            try:
                engine.submit(db, result(engine.next_task(db, path), copy.deepcopy(EXAMPLE['world_plan'])))
                new = layered(); new['entities'][0]['aliases'] = ['琴师', '老莱恩', '莱恩', '阿什福德']
                target = path / 'amended.json'
                target.write_text(json.dumps(new, ensure_ascii=False), encoding='utf-8')
                outcome = novel.dispatch(novel.parser().parse_args(
                    ['recontract', '--project', str(path), '--contract', str(target)]))
                self.assertEqual(outcome['renames'], [{'from': '老聂', 'to': '老莱恩'},
                                                      {'from': '远舟', 'to': '莱恩'}])
                self.assertEqual(outcome['unmatched_new_aliases'], ['阿什福德'])
            finally:
                db.close()

    def test_entity_id_change_is_structural_not_rename(self):
        self.prepare()
        new = layered(); new['entities'][1]['id'] = 'witness'
        outcome = self.amend(new)
        self.assertIn('entities', outcome['structural'])
        self.assertEqual(outcome['renames'], [])
        self.assertEqual(self.task()['stage'], 'world_plan')


if __name__ == '__main__':
    unittest.main()
