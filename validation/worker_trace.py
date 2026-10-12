"""Trace one worker's turns: what it read, how big the result was, what it said.

Usage: python validation/worker_trace.py <root-of-extracted-session> <child-id-prefix>

Every turn re-sends the whole context, so the number of turns and the size of
each tool result are the two multipliers of a worker's cost. This prints both,
which is how a stage gets diagnosed instead of guessed at.
"""
from __future__ import annotations

import json
import os
import sys


def load(path):
    return [json.loads(line) for line in open(path, encoding='utf-8') if line.strip()]


def text_of(message):
    return ''.join(chunk.get('text') or '' for chunk in (message or {}).get('content') or []
                   if isinstance(chunk, dict))


def main():
    root, prefix = sys.argv[1], sys.argv[2]
    sub_root = os.path.join(root, 'subagents')
    target = next(name for name in os.listdir(sub_root) if name.startswith(prefix))
    records = load(os.path.join(sub_root, target, 'session.v4.jsonl'))
    print(f'===== {target[:8]} =====')
    for record in records:
        kind = record.get('type')
        body = record.get('data') or {}
        if kind == 'tool/call':
            try:
                args = json.loads(body.get('arguments') or '{}')
            except Exception:
                args = {}
            brief = args.get('file_path') or args.get('path') or args.get('command') or args.get('name') or ''
            print(f'  CALL {body.get("name")}: {str(brief)[:130]}')
        elif kind == 'tool/result':
            out = text_of(body.get('message'))
            print(f'       -> {len(out):7d}  {out.strip()[:90].replace(chr(10), " ")}')
        elif kind == 'assistant/message':
            print(f'  MSG  {text_of(body.get("message")).strip()[:90].replace(chr(10), " ")}')


if __name__ == '__main__':
    main()
