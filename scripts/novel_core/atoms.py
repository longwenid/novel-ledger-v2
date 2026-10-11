"""Domain reads with explicit existence contracts (architecture INV-3).

A row that must exist is read through here, so a missing row surfaces as a
domain Error carrying the repair route instead of a `TypeError` from
`fetchone()[0]`. Reads that are genuinely optional take `required=False` and
return None; that choice is then visible at the call site.

Layering: L1. Only `store` may be imported at module level.
"""
import json
from .store import require


def plan(db, branch, number, required=True):
    """Chapter plan. Required by default: a committed or drafted chapter
    without a plan means the plan was deleted under a live workflow."""
    row = db.execute('SELECT data FROM plans WHERE branch=? AND number=?',
                     (branch, number)).fetchone()
    if row is None:
        require(not required, 'plan', '该章没有章计划（可能被设计重存或 replan 删除）',
                {'branch': branch, 'chapter': number,
                 'recover': '运行 advance 重新取得规划任务；已有草稿时走审稿返修或 replan，不要重复提交'})
        return None
    return json.loads(row[0])


def branch(db, ident, required=True):
    """Branch row. Required by default: branch ids are engine-minted."""
    row = db.execute('SELECT * FROM branches WHERE id=?', (ident,)).fetchone()
    if row is None:
        require(not required, 'branch', '分支不存在',
                {'branch': ident, 'recover': '用 status 读取当前 work_branch；不要沿用旧分支 ID'})
        return None
    return row


def parent_of(db, ident):
    """Parent branch id, or None at the root. A missing row is None, not an
    error: walking an ancestor chain terminates at the root by design."""
    row = db.execute('SELECT parent FROM branches WHERE id=?', (ident,)).fetchone()
    return row['parent'] if row else None


def ancestor_chapter_prose(db, start, number):
    """Nearest ancestor of `start` that has committed chapter `number`.

    Used to offer the original text for a historical revision. Returns the
    prose or None; the caller decides whether absence is fatal.
    """
    seen = set()
    cursor = start
    while cursor and cursor not in seen:
        seen.add(cursor)
        row = db.execute('SELECT prose FROM chapters WHERE branch=? AND number=?',
                         (cursor, number)).fetchone()
        if row is not None:
            return row[0]
        cursor = parent_of(db, cursor)
    return None


def draft(db, branch, number):
    """Draft record or None. `drafts` is by nature sparse."""
    row = db.execute('SELECT data FROM drafts WHERE branch=? AND number=?',
                     (branch, number)).fetchone()
    return json.loads(row[0]) if row else None
