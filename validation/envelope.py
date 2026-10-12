"""Shared replay helpers for the end-to-end validation scripts.

`input.json` is an index: fields above the delivery threshold live in per-type
files, and a `chapter_review` envelope is not materialised at all until a first
read is frozen. A replay driver has to do what a worker does — and, for the
sealed review, what the host does before the worker can read anything.
"""
from __future__ import annotations

import json
from pathlib import Path


def payload_path(project, task):
    path = Path(task['input_path'])
    return path if path.is_absolute() else Path(project) / path


def load(project, task):
    """Complete envelope for a task, materialising the index first if needed."""
    return json.loads(payload_path(project, task).read_text(encoding='utf-8'))


def impression_of(task):
    """Deterministic engineering fixture for the four first-read fields.

    Both the seal and the formal receipt must carry the identical object — the
    protocol rejects a receipt that re-explains a frozen first read — so the
    value is derived from the task rather than written twice.
    """
    tag = task['task_id'][:8]
    return {'desire': '核对经手人是谁' + tag, 'obstacle': '对方不肯先给凭证',
            'change': '用交换条件替代信任', 'read_on_reason': '后果落在主角身上'}


def first_read_fields(task):
    """The impression the seal must carry for this task, as one object."""
    return impression_of(task)


def obligations(work, category, chapter, quote):
    """Pass rows for every author obligation of one category.

    Engineering fixture: it answers each obligation with real prose evidence and
    an explicit challenge, which is what the protocol checks. It makes no claim
    about the literary judgment behind the answer.
    """
    rows = []
    for row in work.get('review_obligations', []):
        if row.get('category') != category:
            continue
        ref = {'chapter': chapter, 'start': 0, 'quote': quote}
        rows.append({'id': row['id'], 'result': 'pass',
                     'reason': '按原文核对：本章未触及该约定的反面。',
                     'evidence': [dict(ref)],
                     'challenge': {'disposition': 'satisfied',
                                   'reason': '最可能违约的语段仍是行动与后果，未出现相反表达。',
                                   'evidence': dict(ref)}})
    return rows


def seal_first_read(db, project, task, impression=None):
    """Freeze a first-read receipt, then return the unlocked full envelope.

    Engineering fixture: it records a reading, it makes no claim about literary
    quality. A task that is not in the sealed-first-read phase is returned as is.
    """
    if task.get('delivery_phase') != 'first_read':
        return task
    from novel_core.reviewflow import seal
    impression = impression or impression_of(task)
    body = {'task_id': task['task_id'], 'input_hash': task['input_hash'],
            'prose_hash': task.get('first_read_hash'), 'first_read': impression}
    header = task.get('first_read_template_path')
    if header:
        path = Path(header)
        path = path if path.is_absolute() else Path(project) / path
        if path.is_file():
            template = json.loads(path.read_text(encoding='utf-8'))
            if 'first_reads' in template:
                body.pop('first_read')
                body['first_reads'] = [{'chapter': row['chapter'], 'prose_hash': row['prose_hash'],
                                        'first_read': dict(impression)} for row in template['first_reads']]
    sealed = seal(db, str(project), task['task_id'], body)
    return sealed['task']
