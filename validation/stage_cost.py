"""Attribute a session export's token cost to the task stage each worker ran.

Usage: python validation/stage_cost.py <root-of-extracted-session>

Reads the controller's delegation labels and each subagent's transcript to work
out which stage it served (the task envelope says so), then reports tokens per
stage. That is the number to optimise: a stage that costs a third of the run is
worth more attention than one that costs a fiftieth.
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import defaultdict

STAGES = ('world_plan', 'volume_plan', 'plan', 'character_plan', 'character_index', 'draft',
          'repair', 'redesign', 'chapter_review', 'batch_review', 'volume_review',
          'revision_review', 'book_review', 'context_scan', 'context_reduce')

# The controller names its delegations in the book's own words; these are the
# shapes the skill's own worker_prompt produces. A label that matches nothing
# stays unattributed rather than being guessed into a stage.
LABEL_HINTS = (
    ('卷规划', 'volume_plan'), ('全书设计', 'world_plan'), ('章节规划', 'plan'), ('章规划', 'plan'),
    ('人物索引', 'character_index'), ('人物档案', 'character_plan'),
    ('独立审稿', 'chapter_review'), ('复审', 'chapter_review'), ('审稿', 'chapter_review'),
    ('返修后复审', 'chapter_review'),
    ('修订执笔', 'draft'), ('执笔', 'draft'), ('返修执笔', 'repair'), ('定向返修', 'repair'),
    ('日程返修', 'repair'), ('返修', 'repair'), ('重设计', 'redesign'),
    ('整批', 'batch_review'), ('跨章', 'batch_review'), ('卷审', 'volume_review'),
    ('全书审', 'book_review'), ('材料准备', 'context_scan'),
)


def load(path):
    out = []
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def text_len(value):
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value)
    if isinstance(value, list):
        return sum(text_len(item) for item in value)
    if isinstance(value, dict):
        return sum(text_len(item) for item in value.values())
    return 0


def usage(records):
    total = defaultdict(int)
    for record in records:
        body = record.get('usage') or (record.get('data') or {}).get('usage') or {}
        for key, value in body.items():
            if isinstance(value, (int, float)):
                total[key] += value
    return total


def labels_by_child(root):
    """childId -> delegation label, from the controller's subagent catalog."""
    path = os.path.join(root, 'session.v4.jsonl')
    if not os.path.exists(path):
        return {}
    out = {}
    for record in load(path):
        if record.get('type') == 'subagent/catalog':
            body = record.get('data') or {}
            if body.get('childId'):
                out[body['childId']] = body.get('label') or ''
    return out


def served_stage(records, label=''):
    """Which stage did this worker run?

    The task envelope path is the hard evidence; the controller's label is the
    fallback for workers whose first read was something else.
    """
    for record in records:
        if record.get('type') != 'tool/call':
            continue
        args = (record.get('data') or {}).get('arguments') or ''
        match = re.search(r'work[\\/]ch\d+[\\/]([a-z_]+)-', args)
        if match:
            return match.group(1)
    for hint, stage in LABEL_HINTS:
        if hint in label:
            return stage
    return 'unattributed'


def payload_bytes(records):
    """How much tool output this worker pulled into its context."""
    total = 0
    for record in records:
        if record.get('type') == 'tool/result':
            total += text_len((record.get('data') or {}).get('message'))
    return total


def turn_count(records):
    """Model turns this worker took. Every turn re-sends the whole context."""
    return sum(1 for record in records if record.get('type') == 'step/start')


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else '.'
    main_records = load(os.path.join(root, 'session.v4.jsonl'))
    catalog = labels_by_child(root)
    rows = []
    controller = usage(main_records)
    rows.append(('controller', controller, payload_bytes(main_records), turn_count(main_records)))
    sub_root = os.path.join(root, 'subagents')
    if os.path.isdir(sub_root):
        for child in sorted(os.listdir(sub_root)):
            path = os.path.join(sub_root, child, 'session.v4.jsonl')
            if os.path.exists(path):
                records = load(path)
                rows.append((served_stage(records, catalog.get(child, '')), usage(records),
                             payload_bytes(records), turn_count(records)))

    per_stage = defaultdict(lambda: defaultdict(int))
    for stage, use, payload, turns in rows:
        per_stage[stage]['totalTokens'] += use.get('totalTokens', 0)
        per_stage[stage]['inputTokens'] += use.get('inputTokens', 0)
        per_stage[stage]['cacheReadTokens'] += use.get('cacheReadTokens', 0)
        per_stage[stage]['outputTokens'] += use.get('outputTokens', 0)
        per_stage[stage]['workers'] += 1
        per_stage[stage]['payload_chars'] += payload
        per_stage[stage]['turns'] += turns

    grand = sum(row['totalTokens'] for row in per_stage.values())
    total_turns = sum(row['turns'] for row in per_stage.values())
    print(f'{"stage":18s} {"workers":>7s} {"turns":>6s} {"total":>10s} {"share":>6s} '
          f'{"payload":>10s} {"tok/payload":>11s} {"tok/turn":>9s}')
    for stage, row in sorted(per_stage.items(), key=lambda item: -item[1]['totalTokens']):
        share = 100 * row['totalTokens'] / grand if grand else 0
        ratio = row['totalTokens'] / row['payload_chars'] if row['payload_chars'] else 0
        per_turn = row['totalTokens'] / row['turns'] if row['turns'] else 0
        print(f'{stage:18s} {row["workers"]:7d} {row["turns"]:6d} {row["totalTokens"]:10d} '
              f'{share:5.1f}% {row["payload_chars"]:10d} {ratio:11.1f} {per_turn:9.0f}')
    print(f'{"TOTAL":18s} {sum(r["workers"] for r in per_stage.values()):7d} {total_turns:6d} '
          f'{grand:10d}')
    print('\ntok/payload 高说明成本来自轮数与常驻上下文，不是读入量；tok/turn 就是每轮重发的规模。')


if __name__ == '__main__':
    main()
