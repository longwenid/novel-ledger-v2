#!/usr/bin/env python3
"""Import host observations without opening or mutating a story database."""
import argparse
import json
import sys
from pathlib import Path
from novel_core import observability
from novel_core.store import Error

if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    imp=sub.add_parser('import-antigravity')
    imp.add_argument('transcript');imp.add_argument('--tree',action='store_true')
    imp.add_argument('--session-id');imp.add_argument('--role',default='unknown');imp.add_argument('--out')
    show=sub.add_parser('summary');show.add_argument('observations')
    record=sub.add_parser('record');record.add_argument('event');record.add_argument('--out',required=True)
    args=parser.parse_args(argv)
    if args.command=='import-antigravity':
        if args.tree:
            events,missing=observability.import_tree(args.transcript,args.session_id)
        else:events=observability.import_antigravity(args.transcript,args.session_id,args.role);missing=[]
        result=observability.append(args.out,events) if args.out else observability.summary(events)
        if missing:result.update(usage_complete=False,gross_tokens=None,missing_sessions=missing)
        return result
    if args.command=='record':
        return observability.append(args.out,[json.loads(Path(args.event).read_text(encoding='utf-8'))])
    events=[json.loads(line) for line in Path(args.observations).read_text(encoding='utf-8').splitlines() if line.strip()]
    return observability.summary(events)


if __name__=='__main__':
    try:print(json.dumps(main(),ensure_ascii=False))
    except (Error,OSError,ValueError) as error:
        print(json.dumps({'error':getattr(error,'code',type(error).__name__),'message':str(error),
                          'details':getattr(error,'details',None),'story_ledger_modified':False},ensure_ascii=False))
        sys.exit(1)
