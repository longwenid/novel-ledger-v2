"""Unattended soak: the whole pipeline end to end with minimal rewrites.

Mirrors the controller protocol the way an unattended session actually runs
it (advance → worker receipt → advance --after → resync handling), writes a
small book from init to complete, and injects the measured incident shape
from session 14006c71 on the way: a batch-review chunk returns fix for ONE
middle chapter while later chapters already exist.

Success is not "the book completes" alone — the incident must resolve with
minimal rewrites, in both flavours a review can produce:

ExpressionLevelSoak (the measured glyph incident):
  * the fix receipt names ch5 via target_chapter (the v1.58.0 contract);
  * the revision branch dispatches a draft for ch5 ONLY — chapters 6..10 are
    carried byte-identical by the engine (log `revision_carry`), never
    dispatched to any worker;
  * the batch chunk that only holds a note-level finding stays pass and
    opens nothing.

FactRippleSoak (a fact the suffix really reads):
  * ch5's fix changes a ledger value that ONLY ch8 reads (declared reads +
    prose); ch6/7/9/10 carry verbatim, ch8 alone is re-drafted with the new
    value — semantic propagation without a positional-suffix rewrite.

Both must promote, continue writing to ch12, reach `complete`, and leave
`audit` green with exactly one chain and every chapter written once.
"""
import json
import tempfile
import unittest
from pathlib import Path

from support import (
    combined_review,
    connect,
    contract,
    envelope,
    ev,
    initialize,
    pack_prose,
    plan,
    result,
    review,
)
from novel_core import engine
from novel_core.store import get, head, put

CHAPTERS = 12          # book length: crosses the ch10 batch-review gate
INCIDENT = 5           # the chapter the batch-review fix names
CHUNK = (4, 6)         # the chunk that returns the fix verdict
SUFFIX = range(INCIDENT + 1, 11)   # committed chapters after the incident


def reworded(prose):
    return prose + '他抬头听了听那声铜铃。'


class SoakDriver(unittest.TestCase):
    """Shared unattended loop; subclasses provide the world and the incident."""

    def setUpWorld(self):
        raise NotImplementedError

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        setup = contract()
        setup['review_chunk'] = 3
        initialize(self.path, setup)
        self.db = connect(self.path)
        self.addCleanup(self.db.close)
        self.setUpWorld()
        self.dispatches = []          # (branch, stage, chapter) per real task
        self.resyncs = 0              # branch switches observed via advance --after
        self.after = None
        self.injected = False

    # -- world hooks ----------------------------------------------------------

    def main_prose(self, n):
        raise NotImplementedError

    def main_event(self, prose, n):
        raise NotImplementedError

    def main_plan(self, n, task):
        chapter = dict(plan(n, ending=(n == CHAPTERS)))
        chapter['entities'] = self.plan_entities(n)
        return chapter

    def plan_entities(self, n):
        return ['hero', 'ally']

    def draft_reads(self, n):
        return None

    def incident_receipt(self, payload):
        raise NotImplementedError

    def incident_site(self, payload):
        """Which review task returns the fix verdict. The batch chunk (4,6)
        also appears later inside volume/book reviews; scope-gate so the
        incident lands at the batch gate, exactly once."""
        return payload['scope'] == 'batch_chunk' and \
            (payload['first'], payload['through']) == CHUNK

    def branch_prose(self, n, base):
        """Prose for a chapter re-drafted on the revision branch."""
        raise NotImplementedError

    def branch_event(self, prose, n):
        raise NotImplementedError

    # -- controller loop ------------------------------------------------------

    def advance(self):
        reply = engine.next_task(self.db, self.path, after=self.after)
        stage = reply.get('stage')
        if stage == 'wait_for_submit':
            self.fail(f'advance 报告任务未提交：{reply.get("after_task")}')
        if stage == 'paused':
            self.fail(f'流程意外暂停：{get(self.db, "pause_reason")}')
        if stage == 'resync':
            self.assertEqual(reply.get('handoff_reason'), 'branch_changed', reply)
            self.resyncs += 1
            self.after = None
            return 'resync', None
        if stage in ('await_author', 'revision_promoted'):
            self.after = None
            return stage, None
        if not reply.get('task_id'):
            return stage or 'done', reply
        self.dispatches.append((reply['branch'], reply['stage'], reply['chapter']))
        return reply['stage'], reply

    def submit_plan(self, task):
        engine.submit(self.db, result(task, {'chapters': [self.main_plan(task['chapter'], task)]}))

    def submit_draft(self, task):
        n = task['chapter']
        on_branch = task['branch'] != 'main'
        base = self.main_prose(n)
        prose = self.branch_prose(n, base) if on_branch else base
        data = {'prose': prose}
        reads = self.draft_reads(n)
        if reads:
            data['reads'] = reads
        engine.submit(self.db, result(task, data))

    def submit_chapter_review(self, task):
        n = task['chapter']
        work = envelope(task)
        text = pack_prose(self.path, work)
        on_branch = task['branch'] != 'main'
        data = combined_review(work, text, n, events=[self.branch_event(text, n) if on_branch
                                                      else self.main_event(text, n)])
        if on_branch:
            data['facts']['prior_dispositions'] = [
                {'index': 0, 'disposition': 'replaced', 'reason': '随修订重锚，值随修订更新'}]
        engine.submit(self.db, result(task, data))

    def submit_range_review(self, task):
        work = envelope(task)
        payload = work['payload']
        first, through = payload['first'], payload['through']
        if not self.injected and self.incident_site(payload):
            data = self.incident_receipt(payload)
            self.injected = True
        else:
            source = self.db.execute('SELECT prose FROM chapters WHERE branch=? AND number=?',
                                     (task['branch'], first)).fetchone()[0]
            data = review(payload['checks'], source, first)
            if (first, through) == (1, 3):
                # The other discipline from the measured session: a minor
                # glyph-class defect rides along as note+pass and must NOT
                # open a revision chain.
                data['findings'] = [{'severity': 'note', 'impact': '一个异体字，读者会出戏',
                                     'goal': '统一为简体，不要求返工', 'evidence': ev(source, first)}]
            if not payload['scope'].endswith('_chunk'):
                data['memory'] = {'text': '工程夹具核对跨章因果。', 'sources': [ev(source, first)]}
        engine.submit(self.db, result(task, data))

    def run_unattended(self):
        for _ in range(500):
            stage, task = self.advance()
            if stage == 'await_author':
                engine.approve(self.db); continue
            if stage in ('resync', 'revision_promoted') or task is None:
                continue
            if stage == 'done':
                self.assertEqual(task.get('status'), 'completed', task)
                return task
            self.assertIn(stage, ('plan', 'draft', 'chapter_review', 'batch_review',
                                  'volume_review', 'revision_review', 'book_review'),
                          f'意外阶段 {stage}')
            if stage == 'plan':
                self.submit_plan(task)
            elif stage == 'draft':
                self.submit_draft(task)
            elif stage == 'chapter_review':
                self.submit_chapter_review(task)
            else:
                self.submit_range_review(task)
            self.after = task['task_id']
        self.fail('流程未在预算内完本')

    # -- completion audit -----------------------------------------------------

    def audit_common(self, expect_redrafts, expect_carried):
        active = get(self.db, 'active_branch')
        self.assertEqual(head(self.db, active), CHAPTERS)
        rows = self.db.execute("SELECT id,status,start,through FROM branches WHERE id!='main'").fetchall()
        self.assertEqual(len(rows), 1, f'应只有一条修订链，实际 {rows}')
        revision = rows[0]
        self.assertEqual((revision['start'], revision['through']), (INCIDENT, 10))
        self.assertEqual(revision['status'], 'active')
        self.assertEqual(get(self.db, 'revision_failures'), 0)

        # Only the implicated (and dependency-refused) chapters re-executed.
        redrafts = sorted(ch for b, s, ch in self.dispatches
                          if b == revision['id'] and s in ('draft', 'repair', 'redesign')
                          and revision['start'] <= ch <= revision['through'])
        self.assertEqual(redrafts, expect_redrafts,
                         f'修订范围内实际重写 {redrafts}，预期 {expect_redrafts}')
        carried_log = sorted(json.loads(row[0])['chapter'] for row in self.db.execute(
            "SELECT data FROM logs WHERE action='revision_carry'"))
        self.assertEqual(carried_log, expect_carried,
                         f'引擎结转日志 {carried_log}，预期 {expect_carried}')

        # Every chapter was written exactly once per branch; repair never fired.
        writes = {}
        for b, s, ch in self.dispatches:
            if s in ('draft', 'repair', 'redesign'):
                writes[(b, ch)] = writes.get((b, ch), 0) + 1
        self.assertTrue(all(count == 1 for count in writes.values()),
                        f'存在被多次重写的章：{writes}')
        self.assertNotIn('repair', {s for _, s, _ in self.dispatches}, '返修失控')

        # Carried chapters are byte-identical to what main committed.
        for n in expect_carried:
            row = self.db.execute('SELECT prose,data FROM chapters WHERE branch=? AND number=?',
                                  (active, n)).fetchone()
            self.assertEqual(row[0], self.main_prose(n), f'第{n}章结转后被改动')
            self.assertEqual(json.loads(row[1]).get('carried_from'), 'main', f'第{n}章未标记结转')

        self.assertGreaterEqual(self.resyncs, 1, '循环未经历分支切换，事故路径未真实走过')
        self.assertTrue(engine.audit(self.db)['ok'])
        return revision


class ExpressionLevelSoak(SoakDriver):
    def setUpWorld(self):
        self.prose = {n: f'第{n}天，周行在栈桥核对第{n}号回执，宋禾记下只属于这一天的批注{n}。'
                     for n in range(1, CHAPTERS + 1)}

    def main_prose(self, n):
        return self.prose[n]

    def main_event(self, prose, n):
        return {'op': 'state', 'entity': 'hero', 'key': f'栈桥批注{n}', 'before': None,
                'after': f'批注{n}', 'evidence': ev(prose, n, f'批注{n}')}

    def incident_receipt(self, payload):
        prose = self.prose[INCIDENT]
        data = review(payload['checks'], prose, INCIDENT)
        data['verdict'] = 'fix'
        data['checks'][0]['result'] = 'fix'
        data['findings'] = [{'severity': 'quality', 'impact': '正文残留拟声词外文，读者出戏',
                             'goal': f'第{INCIDENT}章改为中文拟声词', 'target_chapter': INCIDENT,
                             'evidence': ev(prose, INCIDENT)}]
        return data

    def branch_prose(self, n, base):
        return reworded(base)

    def branch_event(self, prose, n):
        return self.main_event(prose, n)

    # per-variant expectations; the single test method below runs the loop
    # once and audits against these, so subclasses only override hooks.
    def expect_redrafts(self):
        return [INCIDENT]

    def expect_carried(self):
        return list(SUFFIX)

    def audit_incident(self, revision):
        fixed = self.db.execute('SELECT prose FROM chapters WHERE branch=? AND number=?',
                                (revision['id'], INCIDENT)).fetchone()[0]
        self.assertEqual(fixed, reworded(self.prose[INCIDENT]))

    def test_unattended_incident_resolves_with_minimal_rewrites(self):
        self.run_unattended()
        self.assertTrue(self.injected, '事故注入点未到达，浸泡未覆盖修订路径')
        revision = self.audit_common(expect_redrafts=self.expect_redrafts(),
                                     expect_carried=self.expect_carried())
        self.audit_incident(revision)


class FactRippleSoak(SoakDriver):
    """ch5 fixes a ledger value; only ch8 (which reads it) re-drafts.

    The remaining suffix chapters name neither the owning entity nor the
    value, so the dependency detectors carry them verbatim — semantic
    propagation instead of a positional-suffix rewrite."""

    OLD, NEW = '铜哨令', '铜铃令'

    def setUpWorld(self):
        def line(n, who):
            return f'第{n}天，{who}在栈桥核对第{n}号回执，记下只属于这一天的批注{n}。'
        self.prose = {}
        for n in range(1, CHAPTERS + 1):
            reads_fact = n in (INCIDENT, 8)
            if reads_fact:
                token = self.OLD if n == 8 else ''
                who = '宋禾'
                self.prose[n] = line(n, who) + (f'册子上写明{token}。' if token else '')
            else:
                self.prose[n] = line(n, '周行')
        self.prose[INCIDENT] = self.prose[INCIDENT] + f'她把{self.OLD}交给他。'

    def main_prose(self, n):
        return self.prose[n]

    def plan_entities(self, n):
        return ['hero', 'ally'] if n in (INCIDENT, 8) else ['hero']

    def draft_reads(self, n):
        return ['ally:口令'] if n == 8 else None

    def main_event(self, prose, n):
        if n == INCIDENT:
            return {'op': 'state', 'entity': 'ally', 'key': '口令', 'before': None,
                    'after': self.OLD, 'evidence': ev(prose, INCIDENT, self.OLD)}
        if n == 8:
            return {'op': 'state', 'entity': 'ally', 'key': '口令', 'before': self.OLD,
                    'after': self.OLD + '已核', 'evidence': ev(prose, 8, self.OLD)}
        return {'op': 'state', 'entity': 'hero', 'key': f'栈桥批注{n}', 'before': None,
                'after': f'批注{n}', 'evidence': ev(prose, n, f'批注{n}')}

    def incident_receipt(self, payload):
        prose = self.prose[INCIDENT]
        data = review(payload['checks'], prose, INCIDENT)
        data['verdict'] = 'fix'
        data['checks'][0]['result'] = 'fix'
        data['findings'] = [{'severity': 'quality', 'impact': '口令口径全书不一',
                             'goal': f'第{INCIDENT}章口令统一为{self.NEW}', 'target_chapter': INCIDENT,
                             'evidence': ev(prose, INCIDENT, self.OLD)}]
        return data

    def branch_prose(self, n, base):
        # Keep the shared sentence scaffold: a rewrite that rewords the
        # scaffold would trip the echo sweep in every sibling chapter (the
        # sweep is deliberately conservative); a targeted value swap must not.
        if n in (INCIDENT, 8):
            return base.replace(self.OLD, self.NEW)
        return base

    def branch_event(self, prose, n):
        if n == INCIDENT:
            return {'op': 'state', 'entity': 'ally', 'key': '口令', 'before': None,
                    'after': self.NEW, 'evidence': ev(prose, INCIDENT, self.NEW)}
        if n == 8:
            return {'op': 'state', 'entity': 'ally', 'key': '口令', 'before': self.NEW,
                    'after': self.NEW + '已核', 'evidence': ev(prose, 8, self.NEW)}
        return self.main_event(prose, n)

    def test_fact_ripple_rewrites_only_the_reading_chapter(self):
        self.run_unattended()
        self.assertTrue(self.injected, '事故注入点未到达，浸泡未覆盖修订路径')
        revision = self.audit_common(expect_redrafts=[INCIDENT, 8],
                                     expect_carried=[6, 7, 9, 10])
        row = self.db.execute('SELECT prose FROM chapters WHERE branch=? AND number=?',
                              (revision['id'], 8)).fetchone()
        self.assertIn(self.NEW, row[0])
        self.assertNotIn(self.OLD, row[0])


class VolumeSoak(ExpressionLevelSoak):
    """Cross-volume: the incident names a volume-1 chapter while volume 2
    is already being written.

    v1 = ch1..6 (volume_end at 6), v2 = ch7..12. The batch review at head=10
    returns fix for ch5, so the revision range [5,10] spans the boundary and
    must CARRY the volume-1 ending chapter (6) and the volume-2 chapters
    (7..10) verbatim. After promote, both volumes' reviews must still cover
    the promoted branch (v2 at ch12; v1 re-checked once at book end) and the
    book must complete."""

    V1_END = 6

    def setUpWorld(self):
        super().setUpWorld()
        c = get(self.db, 'contract')
        c['volumes'].append({'id': 'v2', 'purpose': '跨卷验证'})
        put(self.db, 'contract', c)

    def main_plan(self, n, task):
        chapter = dict(plan(n, ending=(n == CHAPTERS)))
        chapter['volume'] = 'v1' if n <= self.V1_END else 'v2'
        chapter['volume_end'] = n in (self.V1_END, CHAPTERS)
        chapter['entities'] = self.plan_entities(n)
        return chapter

    def audit_incident(self, revision):
        super().audit_incident(revision)
        # Both volumes' reviews covered the PROMOTED branch: v2 at its end
        # (ch12) first, then v1 re-checked exactly once at book end — its
        # receipt was not copied to the branch (through >= start), and the
        # book-end recheck is the designed net for exactly that.
        volume_rows = [(row[0], row[1]) for row in self.db.execute(
            "SELECT branch,through FROM reviews WHERE scope='volume' ORDER BY rowid")]
        self.assertEqual(volume_rows,
                         [('main', self.V1_END), (revision['id'], CHAPTERS), (revision['id'], self.V1_END)],
                         f'卷复核覆盖与预期不符：{volume_rows}')


class MultiFindingSoak(ExpressionLevelSoak):
    """Multi-chapter findings: one synthesis fix names ch5 AND ch8.

    The whole-range batch synthesis (not a chunk) returns two findings with
    their own target_chapter. The revision starts at min(5,8)=5, re-drafts
    both implicated chapters (fast_forward must break at 8 and resume), and
    carries everything else verbatim."""

    def incident_site(self, payload):
        return payload['scope'] == 'batch'   # the synthesis task, whole range

    def incident_receipt(self, payload):
        data = review(payload['checks'], self.prose[INCIDENT], INCIDENT)
        data['verdict'] = 'fix'
        data['checks'][0]['result'] = 'fix'
        data['findings'] = [
            {'severity': 'quality', 'impact': '正文残留拟声词外文，读者出戏',
             'goal': f'第{INCIDENT}章改为中文拟声词', 'target_chapter': INCIDENT,
             'evidence': ev(self.prose[INCIDENT], INCIDENT)},
            {'severity': 'quality', 'impact': '第8章收束节奏与全书不合',
             'goal': '第8章定点收束', 'target_chapter': 8,
             'evidence': ev(self.prose[8], 8)}]
        return data

    def expect_redrafts(self):
        return [INCIDENT, 8]

    def expect_carried(self):
        return [6, 7, 9, 10]

    def audit_incident(self, revision):
        for n in self.expect_redrafts():
            fixed = self.db.execute('SELECT prose FROM chapters WHERE branch=? AND number=?',
                                    (revision['id'], n)).fetchone()[0]
            self.assertEqual(fixed, reworded(self.prose[n]), f'第{n}章未按点名修订')


if __name__ == '__main__':
    unittest.main()
