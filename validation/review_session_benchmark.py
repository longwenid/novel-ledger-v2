#!/usr/bin/env python3
"""Equivalent 15-chapter engineering review; counts delivery, not model billing."""
import argparse
import json
import sys
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'tests')]
import test_review_sessions as fixture_module
from test_review_sessions import ReviewSessionTests
from support import respond as fixture_respond
from novel_core import engine
from novel_core.store import digest,head
from novel_core.ledger import replay


def simulate(streaming):
    fixture=ReviewSessionTests();fixture.setUp();db=fixture.db;project=fixture.path
    loaded={};texts=[];prose_chars=0;dispatches=0;trace=[]
    def respond(db,project,task,**kwargs):
        if task['stage'] in ('draft','repair','redesign'):
            opening=f"第{task['chapter']}章，周行把收据交给宋禾。她先核对署名，再要求他承担作证的风险。"
            kwargs['prose']=opening+'\n'+('周行逐项核对记录，宋禾记下差异，两人决定先找证人，再交出凭证。\n'*100)
        return fixture_respond(db,project,task,**kwargs)
    def record(task,worker):
        nonlocal prose_chars
        text=(project/task['input_path']).read_text();texts.append(text);view=json.loads(text)
        paths=set(task.get('prose_files',[]))
        paths.update(part['path'] for ref in view.get('shared_materials',{}).values()
                     for part in ref.get('parts',[ref]))
        paths.update(row['material']['path'] for row in view.get('selected',[]) if row.get('material'))
        seen=loaded.setdefault(worker,set())
        for path in sorted(paths-seen):
            content=(project/path).read_text();texts.append(content)
            if path.startswith('work/prose/'):prose_chars+=len(content)
        seen.update(paths)
    try:
        policy=patch('novel_core.reviewrun.session',return_value=None) if not streaming else patch('novel_core.reviewrun.PROTOCOL','review-run/1')
        with policy,patch.object(fixture_module,'respond',side_effect=respond):
            branch=fixture.prepare(count=15,start=6)
            fingerprint=(head(db,branch),digest(replay(db,branch)))
            task=fixture.cli('advance')['task'];dispatches=1
            while True:
                worker='independent-reviewer' if streaming else 'reviewer-'+str(dispatches)
                record(task,worker)
                work=json.loads(db.execute('SELECT input FROM tasks WHERE id=?',(task['task_id'],)).fetchone()[0])
                p=work['payload'];trace.append({'scope':p.get('scope'),'first':p.get('first'),'through':p.get('through')})
                receipt=fixture.receipt(task)
                # Engineering checks use one complete opening sentence, not a full-text quote.
                if 'checks' in receipt['data']:
                    for row in receipt['data']['checks']:
                        for ref in row.get('evidence',[]):ref['quote']=ref['quote'].split('\n',1)[0]
                    if isinstance(receipt['data'].get('memory'),dict):
                        for ref in receipt['data']['memory']['sources']:ref['quote']=ref['quote'].split('\n',1)[0]
                if streaming:
                    out=fixture.submit(task,receipt)
                    if out['action']!='continue_review':
                        assert out['action']=='review_done';break
                    task=out['task']
                else:
                    engine.submit(db,receipt)
                    stage,_,_=engine.choose(db)
                    if stage=='promote':break
                    task=fixture.cli('advance','--after',task['task_id'])['task'];dispatches+=1
            assert fingerprint==(head(db,branch),digest(replay(db,branch)))
            assert not db.execute("SELECT 1 FROM tasks WHERE state='pending'").fetchone()
            return {'controller_dispatches':dispatches,'worker_sessions':len(loaded),'review_submissions':len(trace),
                    'delivery_input_characters':sum(map(len,texts)),'prose_read_characters':prose_chars,
                    'trace':trace,'state_hash':fingerprint[1],
                    'prose_hashes':[r[0] for r in db.execute('SELECT hash FROM chapters WHERE branch=? ORDER BY number',(branch,))]}
    finally:fixture.tearDown()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out');args=parser.parse_args()
    baseline=simulate(False);streaming=simulate(True)
    assert baseline['trace']==streaming['trace']
    assert baseline['state_hash']==streaming['state_hash'] and baseline['prose_hashes']==streaming['prose_hashes']
    report={'schema':'novel-ledger-v2.review-run-benchmark/1','chapters':15,'revision_from':6,'review_chunk':3,
        'baseline':baseline,'streaming':streaming,
        'delivery_reduction_percent':round(100*(1-streaming['delivery_input_characters']/baseline['delivery_input_characters']),2),
        'boundary':'Synthetic engineering prose and checks; delivery views and full referenced files loaded once per worker. Excludes host prompts, receipts, supplemental recalls, chat history, cache billing and actual model runtime. Not evidence of literary quality, actual speed or fees.'}
    if args.out:Path(args.out).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('baseline','streaming')},ensure_ascii=False))
    print(json.dumps({mode:{k:r[k] for k in ('controller_dispatches','worker_sessions','review_submissions','delivery_input_characters','prose_read_characters')} for mode,r in [('baseline',baseline),('streaming',streaming)]},ensure_ascii=False))

if __name__=='__main__':main()
