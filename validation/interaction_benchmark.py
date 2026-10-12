#!/usr/bin/env python3
"""Compare host session policies on identical engineering chapters, no LLMs."""
import argparse
import json
import shlex
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'tests')]
import novel
from test_targeted_repairs import TargetedRepairTests
from novel_core import engine, execution, reviewflow, taskview
from novel_core.ledger import replay
from novel_core.store import digest, head, put
from support import fixture_first_read, ev


def multi_page_delivery():
    """Synthetic complete records: measures routing, not literary understanding."""
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);task_dir=root/'work/task';task_dir.mkdir(parents=True)
        view={field:[{'id':index,'text':f'第{index}条{field}完整事实与因果记录。'*12}
                     for index in range(100)] for field in ('contract','state','plan')}
        source=task_dir/'input.json';source.write_text(json.dumps(view,ensure_ascii=False),encoding='utf-8')
        routing=taskview.render(root,task_dir,view,'work/task/input.json','fixture',digest(view))
        manifest=json.loads((root/routing['manifest_path']).read_text(encoding='utf-8'))
        bundle=taskview.read_bundle(root,manifest)
        assert [digest(row['value']) for row in bundle['records']]==[row['value_hash'] for row in manifest['records']]
        assert len(routing['pages'])>1
        return {'scope':'Synthetic multi-page required records, no model invocation.',
                'page_reads':len(routing['pages']),'bundle_reads':1,
                'page_bytes':routing['required_read_bytes'],
                'bundle_bytes':len(json.dumps(bundle,ensure_ascii=False,separators=(',',':')).encode('utf-8')),
                'complete_records_equal':True,'input_hash_preserved':bundle['input_hash']==digest(view)}


def simulate(policy,mode=execution.BATCH):
    fixture=TargetedRepairTests(methodName='runTest');fixture.setUp()
    counts={'writer_dispatches':0,'reviewer_dispatches':0,'chapter_tasks':0,'reading_file_reads':0,'reading_bytes':0,
            'reading_bundle_calls':0,'reading_bundle_bytes':0}
    try:
        put(fixture.db,'writer_session_policy',policy)
        put(fixture.db,'execution_mode',mode)
        task=fixture.cli('advance')['task'];counts['writer_dispatches']+=1
        while head(fixture.db,'main')<4:
            task=json.loads((fixture.path/task['task_path']).read_text(encoding='utf-8'))
            if task['stage']=='chapter_review' and task['delivery_phase']=='first_read':
                if mode==execution.SINGLE:
                    task=reviewflow.seal(fixture.db,fixture.path,task['task_id'],{'task_id':task['task_id'],
                        'input_hash':task['input_hash'],'prose_hash':task['first_read_hash'],'first_read':fixture_first_read()})['task']
                else:task=fixture.seal(task)
            view=task['reading_view'];counts['reading_file_reads']+=len(view['pages']);counts['reading_bytes']+=view['required_read_bytes']
            bundle=fixture.cli('task-view','--task',task['task_id'],'--read')
            manifest=json.loads((fixture.path/view['manifest_path']).read_text(encoding='utf-8'))
            assert [digest(row['value']) for row in bundle['records']]==[row['value_hash'] for row in manifest['records']]
            counts['reading_bundle_calls']+=1
            counts['reading_bundle_bytes']+=len(json.dumps(bundle,ensure_ascii=False,separators=(',',':')).encode('utf-8'))
            if task['stage']=='draft':
                data=fixture.draft(task)
                if mode==execution.SINGLE:data.pop('candidate_events')
            elif mode==execution.SINGLE:
                n=task['chapter'];text=engine.load_draft(fixture.db,'main',n)['prose']
                events=[{'op':'state','entity':'hero','key':'route','before':replay(fixture.db,'main')['facts'].get('hero:route'),
                         'after':str(n),'evidence':ev(text,n,'周行交出凭证。')}]
                data=fixture.review(task,events=events)
            else:data=fixture.review(task)
            fixture.save(task,data)
            argv=shlex.split(task['commands']['submit'])
            out=novel.dispatch(novel.parser().parse_args(argv[2:]))
            counts['chapter_tasks']+=1
            if out.get('action')=='continue_chapter_batch':task=out['task']
            elif head(fixture.db,'main')<4:
                task=fixture.cli('advance','--after',out['task_id'])['task']
                counts['writer_dispatches' if task['stage']=='draft' else 'reviewer_dispatches']+=1
        prose=[row['prose'] for row in fixture.db.execute("SELECT prose FROM chapters WHERE branch='main' ORDER BY number")]
        return {'execution_mode':mode,'writer_session_policy':policy,**counts,'canonical_prose_hash':digest(prose),
                'canonical_state_hash':digest(replay(fixture.db,'main')),'audit_ok':engine.audit(fixture.db)['ok'],
                'reading_bundles_preserve_all_required_records':True}
    finally:fixture.tearDown()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',required=True);args=parser.parse_args()
    batch=simulate('batch');chapter=simulate('chapter');single=simulate('chapter',execution.SINGLE)
    assert batch['canonical_prose_hash']==chapter['canonical_prose_hash']
    assert batch['canonical_state_hash']==chapter['canonical_state_hash']
    assert batch['audit_ok'] and chapter['audit_ok']
    assert batch['canonical_prose_hash']==single['canonical_prose_hash']
    assert batch['canonical_state_hash']==single['canonical_state_hash'] and single['audit_ok']
    report={'scope':'Three identical synthetic engineering chapters, generated submission commands and independent sealed review; no model invocation.',
            'batch':batch,'chapter':chapter,'single_chapter':single,'canonical_results_equal':True,
            'multi_page_delivery':multi_page_delivery(),
            'actual_tokens':None,'literary_quality':'not measured; requires genuine writing and independent review'}
    path=Path(args.out);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
