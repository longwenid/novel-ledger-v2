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
