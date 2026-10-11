"""Single authority for the rework escalation ladders (architecture INV-4).

Two ladders feed the same field:

  * semantic  — a chapter_review returned fix; engine.repair counts rounds.
  * length    — the composed prose is short of the author's target;
                engine.develop_short_draft counts rounds.

They used to write `draft['stage']` independently, so the later writer won and
a length shortfall silently demoted an already-escalated `redesign` back to
`repair`, letting a chapter exceed SKILL.md rule 3's "two repairs, then one
redesign". Both ladders now delegate here and the result is the STRICTER of
the two; neither can widen the other.

Layering: L1. Pure functions, no storage import.
"""

QUALITY_REPAIR_ROUNDS = 2     # 具体缺陷最多返修两轮
LENGTH_REPAIR_ROUNDS = 2      # 篇幅不足最多补足两轮
EXHAUST_AFTER = 3             # 第三次转 redesign；第四次保留现场并暂停

STAGE_ORDER = {'repair': 0, 'redesign': 1}


def ladder_stage(rounds, repair_rounds):
    """One ladder in isolation: repair while within budget, else redesign."""
    return 'repair' if rounds <= repair_rounds else 'redesign'


def resolve_stage(quality_rounds=0, length_rounds=0):
    """Return (stage, exhausted_ladders).

    `stage` is the stricter of both ladders: either ladder asking for a
    redesign makes the chapter a redesign. `exhausted_ladders` lists the
    ladders that have spent their redesign as well; a non-empty list means the
    caller must pause and report to the author rather than dispatching again.
    """
    stages = (ladder_stage(quality_rounds, QUALITY_REPAIR_ROUNDS),
              ladder_stage(length_rounds, LENGTH_REPAIR_ROUNDS))
    stage = max(stages, key=lambda name: STAGE_ORDER[name])
    exhausted = []
    if quality_rounds > EXHAUST_AFTER:
        exhausted.append('quality')
    if length_rounds > EXHAUST_AFTER:
        exhausted.append('chapter_development')
    return stage, exhausted


REPAIR_ROUTES = ('patch', 'revise')


def revision_start(findings, through):
    """Which chapter a revision starts from — the repair directive.

    Deliberately NOT the same question as "where is the evidence". A reviewer
    quotes prose to prove a finding, and proof often lives in an EARLIER
    chapter (the one the newest chapter contradicts). Deriving the start from
    `min(evidence.chapter)` therefore pulled the whole suffix backwards and
    re-drafted chapters that were correct — and `repair_route`, promised by the
    protocol and by the review instruction, was never read at all.

    Precedence:
      1. an explicit `target_chapter` on any non-note finding — the reviewer
         saying where the fix belongs;
      2. otherwise, findings that declared a `repair_route` are routed to the
         chapter they name (patch = expression-level in place, revise =
         fact-level, re-verify the suffix from the earliest affected chapter);
      3. otherwise the NEWEST chapter in range. Repairing in place is cheap and
         recoverable — a rejected fix simply returns to repair. Reaching
         backwards re-drafts chapters nobody implicated, and rewriting correct
         prose can break it. Backtracking must be declared, never inferred.
    """
    live = [f for f in findings if isinstance(f, dict) and f.get('severity') != 'note']
    declared = [f['target_chapter'] for f in live if type(f.get('target_chapter')) is int]
    if declared:
        return min(declared)
    routed = []
    for finding in live:
        if finding.get('repair_route') not in REPAIR_ROUTES:
            continue
        ref = finding.get('evidence')
        if isinstance(ref, dict) and type(ref.get('chapter')) is int:
            routed.append(ref['chapter'])
    if routed:
        return min(routed)
    return through


def outside_revision_scope(findings, revision_start):
    """True when every fix-demanding finding points before the revision began.

    A revision branch answers one question: did THIS change propagate
    correctly? Findings that cite chapters before `revision_start` are not
    consequences of the change — they are pre-existing defects the review
    happened to read on its way through. Building a chain for them drags the
    completed revision backwards: measured, one mixed-script character in an
    unrevised chapter re-drafted nine chapters' worth of work and left the
    real revision range unreviewed.

    Whether such findings should be fixed is the author's call, not the
    engine's, so this returns a fact rather than a decision.
    """
    repairing = [f for f in findings
                 if isinstance(f, dict) and f.get('severity') in ('blocker', 'quality')]
    if not repairing:
        return False
    for finding in repairing:
        ref = finding.get('evidence')
        chapter = ref.get('chapter') if isinstance(ref, dict) else None
        if type(chapter) is not int or chapter >= revision_start:
            return False
    return True
