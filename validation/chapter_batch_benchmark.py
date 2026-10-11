#!/usr/bin/env python3
"""Compare five identical engineering chapters, never actual model billing.

Counts complete delivery views plus referenced prose/shared files, loaded once
per worker session. Excludes host prompts, receipts, chat history and wrappers.
Sequential mode reuses one writer and creates a fresh reviewer per chapter.
"""
import argparse
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'tests')]
from test_chapter_batches import ChapterBatchTests
from support import result,fixture_seal
from novel_core import engine
from novel_core.store import put,dump,head,branch_hash,digest
from novel_core.ledger import replay


def simulate(size):
    fixture=ChapterBatchTests();fixture.setUp();db=fixture.db;project=fixture.path
    loaded={};texts=[];dispatches=0;tasks=0;events={}
    def record(task,worker):
        paths=set(task.get('prose_files',[]))
        if task.get('first_read_files'):paths.update(r['prose_path'] for r in task['first_read_files'])
        source=project/task['input_path']
        if source.exists():
            text=source.read_text();texts.append(text);view=json.loads(text)
            paths.update(part['path'] for ref in view.get('shared_materials',{}).values()
                         for part in ref.get('parts',[ref]))
            paths.update(row['material']['path'] for row in view.get('selected',[]) if row.get('material'))
            paths.update(row['events_material']['path'] for row in view.get('candidate_ledger',{}).get('prefix',[])
                         if row.get('events_material'))
        seen=loaded.setdefault(worker,set())
        for path in sorted(paths-seen):texts.append((project/path).read_text())
        seen.update(paths)
    try:
        put(db,'chapter_batch_size',size)
        task=fixture.cli('advance','--through','6')['task'];dispatches+=1
        while head(db,'main')<6:
            s=task.get('chapter_session');worker=(s['batch']+':'+s['role']) if s else ('writer' if task['stage']=='draft' else 'reviewer:'+str(task['chapter']))
            if task['stage']=='chapter_review' and task['delivery_phase']=='first_read':
                record(task,worker)
                if s:task=fixture.seal(task)
                else:
                    fixture_seal(task);row=db.execute('SELECT * FROM tasks WHERE id=?',(task['task_id'],)).fetchone();task=engine.task_files(db,project,row)
            record(task,worker)
            if task['stage']=='draft':
                data=fixture.write(task);events[task['chapter']]=data['candidate_events']
            else:data=fixture.review(task,events=events[task['chapter']])
            if s:out=fixture.submit(task,data)
            else:
                path=project/task['output_path'];path.write_text(dump(result(task,data)))
                out=fixture.cli('submit','--result',str(path),'--brief')
            tasks+=1
            if out.get('action')=='continue_chapter_batch':task=out['task']
            elif head(db,'main')<6:
                task=fixture.cli('advance','--after',out['task_id'])['task'];dispatches+=1
        return {'controller_dispatches':dispatches,'worker_sessions':len(loaded),'chapter_tasks':tasks,
                'delivery_chars':sum(map(len,texts)),'canonical_prose_hash':branch_hash(db,'main'),
                'canonical_state_hash':digest(replay(db,'main'))},texts
    finally:fixture.tearDown()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',required=True);args=parser.parse_args()
    before,old=simulate(1);after,new=simulate(5)
    assert before['canonical_prose_hash']==after['canonical_prose_hash']
    assert before['canonical_state_hash']==after['canonical_state_hash']
    report={'scope':'synthetic identical prose/events; delivered input views + unique prose/shared files per worker; excludes host prompts, receipts, chat history, wrappers and model cache billing',
        'baseline':before,'five_chapter_batch':after,'canonical_prose_and_state_equal':True,
        'delivery_char_reduction_percent':round(100*(1-after['delivery_chars']/before['delivery_chars']),2),
        'actual_host_time_billing_and_literary_quality':'not measured'}
    try:
        import tiktoken
        encoding=tiktoken.get_encoding('o200k_base')
        a=sum(len(encoding.encode(text,disallowed_special=())) for text in old);b=sum(len(encoding.encode(text,disallowed_special=())) for text in new)
        report['token_comparison']={'tokenizer':'o200k_base; comparison ruler only','baseline':a,'batch':b,'reduction_percent':round(100*(1-b/a),2)}
    except (ImportError,OSError):report['token_comparison']=None
    out=Path(args.out).resolve();out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
