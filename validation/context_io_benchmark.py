#!/usr/bin/env python3
"""Compare exact synthetic sources/receipts; never infer actual model billing."""
import argparse
import copy
import json
import sys
import tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'tests')]
from novel_core.taskio import shared_delivery
from novel_core.wire import compact_response,expand_evidence,expand_receipt
from novel_core.store import dump
from test_context_io import catalog_receipt,rebuild_parts
import test_targeted_repairs as fixtures
from novel_core import reviewflow


def input_comparison(project):
    streams=[[],[]];seen=[set(),set()]
    for chapter in range(2,7):
        work={'stage':'draft','chapter':chapter,'branch':'main','chapter_session':{'role':'writer','batch':'fixed'},
            'character_context':{'status':'design','profiles':{
                'hero':{'data':{'biography':'周行保留收据，遇到争执先核对签名与去向。'*80}},
                'ally':{'data':{'voice':'宋禾用短句追问凭证出处，得到解释才落笔。'*80}}},
                'developments':{'hero':[{'chapter':chapter-1,'change':'交接改变去向。'}]},'validity':{'hero':True}},
            'design_context':{'status':'proposal_not_canon',
                'world':{'data':{'rules':'资源转移必须有凭证，人物认知按实际信息来源。'*100}},
                'volume':{'data':{'goal':'交接推动人物选择，选择必须留下代价。'*80}}}}
        view=shared_delivery(project,work);baseline=copy.deepcopy(view)
        for ref in baseline['shared_materials'].values():ref.pop('parts',None)
        baseline['shared_read_rule']='首次接手读完整文件；同一 Worker 仅复用自己已读且哈希未变的版本。'
        for field in ('character_context','design_context'):
            assert rebuild_parts(project,view['shared_materials'][field])==work[field]
        for index,item in enumerate((baseline,view)):
            streams[index].append(json.dumps(item,ensure_ascii=False,indent=2)+'\n')
            paths={part['path'] for ref in item['shared_materials'].values() for part in ref.get('parts',[ref])}
            for path in sorted(paths-seen[index]):streams[index].append((project/path).read_text())
            seen[index].update(paths)
    return streams


def state_comparison(project):
    """Incremental comparison to v1.38's inline state/continuity transport."""
    streams=[[],[]];seen=[set(),set()]
    for chapter in range(2,7):
        work={'stage':'draft','chapter':chapter,'chapter_session':{'role':'writer'},
            'state':{'clock':chapter,'entities':{'hero':{'name':'周行'}},
                'facts':{'hero:route':{'description':'交接凭证保留签名和去向，人物根据实际证据采取行动。'*90}},
                'knowledge':{'hero:rule':{'claim':'旧规则附有适用前提，信息来源与人物立场完整保留。'*100}},
                'resources':{'cash':str(100-chapter)}},
            'continuity':{'places':[{'id':'room','description':'房间与相邻通道的来源完整保留，不能推断未登记空间。'*100}],
                'links':{'door':{'open':True,'requires':{'hero:key':'持有'}}},'absence_is_not_proof':True}}
        view=shared_delivery(project,work);baseline=copy.deepcopy(view)
        for key in ('state','continuity'):
            assert rebuild_parts(project,view['shared_materials'][key])==work[key]
            baseline['shared_materials'].pop(key);baseline[key]=work[key]
        for index,item in enumerate((baseline,view)):
            streams[index].append(json.dumps(item,ensure_ascii=False,indent=2)+'\n')
            paths={part['path'] for ref in item['shared_materials'].values() for part in ref.get('parts',[ref])}
            for path in sorted(paths-seen[index]):streams[index].append((project/path).read_text())
            seen[index].update(paths)
    return streams


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',required=True);args=parser.parse_args()
    with tempfile.TemporaryDirectory() as temp:
        inputs=input_comparison(Path(temp));state_inputs=state_comparison(Path(temp))
    f=fixtures.TargetedRepairTests(methodName='runTest');f.setUp()
    try:
        full=f.cli('advance');thin=compact_response(f.path,full)
        assert json.loads((f.path/thin['task']['task_path']).read_text())['task_id']==full['task']['task_id']
        # Real engine fixture, independently checked by ordinary full validators.
        out=f.writer_batch(full['task']);task=f.cli('advance','--after',out['task_id'])['task']
        # Longer genuine frozen fields in an engineering fixture, not a model
        # literary verdict. Compare the same exact record, never shorter prose.
        first={'desire':'读者能指认周行交出凭证的目标，目标由动作而非说明推进。'*8,
               'obstacle':'宋禾尚未签名，是否接受交接成为当前阻力，凭证去向仍待确认。'*8,
               'change':'交出凭证使双方关系进入需要回应的状态，收据留下可核对的数字。'*8,
               'read_on_reason':'继续阅读为了看签名带来的实际责任与后果，前文条件须保持。'*8}
        seal={'task_id':task['task_id'],'input_hash':task['input_hash'],'prose_hash':task['first_read_hash'],
              'first_read':first,'first_reads':[{'chapter':r['chapter'],'prose_hash':r['prose_hash'],'first_read':first}
                                               for r in task['first_read_files']]}
        task=reviewflow.seal(f.db,f.path,task['task_id'],seal)['task']
        receipt=f.review(task)
        receipt['reader']['first_read']=first;wire=catalog_receipt(receipt)
        assert expand_evidence(wire,task['stage'])==receipt
        engine_probe=f.cli('preflight','--result',f.save(task,wire))
        assert engine_probe['checked']
        streams={'inputs':inputs,'handoff_output':[[dump(full)],[dump(thin)]],
                 'review_receipt_output':[[dump(receipt)],[dump(wire)]]}
        referenced=copy.deepcopy(wire);referenced['reader'].pop('first_read')
        referenced['reader']['first_read_ref']=reviewflow.record(f.db,task['task_id'])['hash']
        row=f.db.execute('SELECT * FROM tasks WHERE id=?',(task['task_id'],)).fetchone()
        assert expand_receipt(f.db,row,json.loads(row['input']),referenced)==receipt
        assert f.cli('preflight','--result',f.save(task,referenced))['checked']
        incremental={'state_and_continuity_inputs':state_inputs,
                     'review_output_with_first_read_ref':[[dump(wire)],[dump(referenced)]]}
        report={'scope':'synthetic five-chapter input material reuse, one real engine handoff and review-receipt fixture; identical logical source values and evidence; excludes system/host prompts, reasoning, chat history, cache billing and actual literary quality',
                'lossless_roundtrip':True,'real_preflight_passed':True,'comparisons':{}}
        try:
            import tiktoken
            encoder=tiktoken.get_encoding('o200k_base')
        except (ImportError,OSError):encoder=None
        def comparison(before,after):
            a=sum(map(len,before));b=sum(map(len,after))
            item={'before_chars':a,'after_chars':b,'char_reduction_percent':round(100*(1-b/a),2)}
            if encoder:
                x=sum(len(encoder.encode(t,disallowed_special=())) for t in before)
                y=sum(len(encoder.encode(t,disallowed_special=())) for t in after)
                item['tokens']={'tokenizer':'o200k_base comparison only','before':x,'after':y,
                                'reduction_percent':round(100*(1-y/x),2)}
            return item
        for name,(before,after) in streams.items():report['comparisons'][name]=comparison(before,after)
        report['incremental_from_v1_38']={name:comparison(before,after) for name,(before,after) in incremental.items()}
        target=Path(args.out).resolve();target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(report,ensure_ascii=False))
    finally:f.tearDown()


if __name__=='__main__':main()
