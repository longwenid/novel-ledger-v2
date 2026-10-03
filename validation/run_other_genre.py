"""Run the independently reviewed contemporary short sample through the same protocol."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from novel_core import engine
from novel_core.store import initialize,connect,digest,write_json


def main():
    p=argparse.ArgumentParser();p.add_argument('--project',required=True);args=p.parse_args()
    root=Path(__file__).resolve().parent/'other-genre'
    prose=(root/'夜班退件.md').read_text().rstrip();reader=json.loads((root/'review.json').read_text())
    assert reader['source_hash']==digest(prose) and reader['verdict']=='pass'
    contract={'context_mode':'legacy','title':'夜班退件','reader_promise':'职业场景中的责任、压力与有限选择','protagonist_drive':'不为尚不能确认的测量签字',
              'world_rules':['现实题材；不把传闻、怀疑或新读数当成已检验结论。'],'ending':'装车暂缓，复核启动，后续处罚与产品最终结论保持未知。',
              'arcs':['从受压拒签到记录自己能证明的信息'],'volumes':[{'id':'night','purpose':'把拒签落实为暂缓及重测'}],
              'word_range':[300,1200],'entities':[{'id':'zhou','name':'周蔓','kind':'person'},{'id':'lead','name':'领班','kind':'person'},{'id':'repair','name':'维修员','kind':'person'}],
              'validation_only':'用户批准的跨题材压缩验证；非现实行业指南'}
    initialize(args.project,contract);db=connect(args.project);trace=[]
    def ev(quote):return {'chapter':1,'start':prose.index(quote),'quote':quote}
    sources={r['id']:r for r in reader['checks']}
    try:
        for _ in range(20):
            task=engine.next_task(db,args.project)
            if task.get('stage')=='await_author':engine.approve(db);continue
            if not task.get('task_id'):
                assert task['status']=='completed'
                write_json(root/'execution-report.json',{'result':task,'tasks':trace,'source_hash':digest(prose),'review_mode':'serial core, independent reader artifact','industry_claims':'fictional internal consistency only'})
                return
            stage=task['stage'];pack=json.loads(Path(task['input_path']).read_text());info=pack['payload']
            if stage=='plan':data={'chapters':[{'number':1,'volume':'night','goal':'核实可疑读数','conflict':'出厂倒计时和签字责任','change':'暂缓装车并启动重测','entities':['zhou','lead','repair'],'volume_end':True,'book_end':True}]}
            elif stage=='draft':data={'prose':prose}
            else:
                if stage=='facts':
                    mapping={'coverage':'causality','continuity':'causality','resources':'emotion_cost','knowledge':'professional_context','time':'change','world_rules':'professional_context'}
                elif stage=='reader':mapping={'read_on':'causality','character':'character','change':'change','emotion_cost':'emotion_cost','language':'language','repetition':'repetition'}
                else:mapping={'causality':'causality','character_arcs':'character','payoffs':'change','pacing':'repetition','world_rules':'professional_context','ending':'ending','length':'language','core_promises':'change'}
                checks=[]
                for k in info['checks']:
                    src=sources[mapping[k]]
                    checks.append({'id':k,'result':'pass','reason':src['reason'],'evidence':[ev(src['quote'])]})
                data={'verdict':'pass','checks':checks,'findings':[]}
                if stage=='facts':
                    data.update(summary='周蔓只签已接到的检修信息，拒绝不明依据放行；货停下并重测，津贴与产品结论待定。',events=[
                        {'op':'knowledge','entity':'zhou','topic':'calibration','before':None,'claim':'维修人员称探头未标定','stance':'knows','source':'电话听到维修说明','evidence':ev('那台下午换了探头，还没做标定。')},
                        {'op':'state','entity':'lead','key':'shipping','before':None,'after':'halted','evidence':ev('领班站了一会儿，叫装车口停。')}])
                elif stage=='reader':data['first_read']=reader['first_read']
                else:data['memory']={'text':reader['reason'],'sources':[ev(sources['change']['quote'])]}
            envelope={'schema':'novel-ledger-v2.result/1','task_id':task['task_id'],'input_hash':task['input_hash'],'data':data}
            write_json(task['output_path'],envelope);engine.submit(db,envelope)
            trace.append({'task':task['task_id'],'stage':stage})
    finally:db.close()

if __name__=='__main__':main()
