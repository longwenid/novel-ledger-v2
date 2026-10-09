"""Synthetic thousand-chapter context recovery, without model calls or quality claims."""
import argparse
import copy
import json
import sys
import tempfile
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import context_stress as fixture

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
sys.path.insert(0,str(ROOT/'tests'))
from novel_core import engine,design,characters,contextflow
from novel_core.store import initialize,connect,get,put,dump,digest,transaction,write_json,VERSION
from novel_core.ledger import seed,append_events,replay
from novel_core.budget import han_count
from support import result,review,ev
from test_contextflow import preparation


CASES=('baseline','knowledge','hooks','dependencies','profiles')


def build(path,kind,chapter_count=1000,unscoped_knowledge=False):
    c=fixture.contract();c['context_mode']='bounded';initialize(path,c);db=connect(path)
    put(db,'session_mode','fresh');put(db,'pilot_approved',True)
    state=seed(c)
    with transaction(db):
        design.save(db,'main','world',1,copy.deepcopy(fixture.DESIGN['world_plan']),'fixture-world')
        for v in c['volumes']:
            p=copy.deepcopy(fixture.DESIGN['volume_plan']);p['volume']=v['id'];design.save(db,'main',v['id'],1,p,'fixture-volume')
        characters.save(db,'main',1,copy.deepcopy(fixture.PROFILES),['hero','ally'],'fixture-profile')
        for n in range(1,chapter_count+1):
            quote=f'第{n}号凭证记载了一次交接，周行读过原件，记住了当时的签收人。'
            text=(quote+'\n'+('这段合成文本只用于测量材料长度，不代表小说质量。\n'*140))[:3000]
            events=[{'op':'clock','before':n-1,'after':n,'evidence':ev(text,n,quote)}]
            if kind=='knowledge':events.append({'op':'knowledge','entity':'hero','topic':f'archive-{n}','before':None,'stance':'knows','claim':quote,'source':'亲读原件','evidence':ev(text,n,quote)})
            # Accumulate valid long-term promises whose common future deadline
            # falls within the next pack's near-due range. None is overdue at
            # the continuation chapter, so this is a valid pressure fixture.
            if kind=='hooks':events.append({'op':'hook','id':f'promise-{n}','before':None,'status':'open','text':quote,'due':chapter_count+5,'important':True,'evidence':ev(text,n,quote)})
            p=fixture.chapter_plan(n)
            data={'events':events,'summary':'合成工程历史','volume':p['volume'],'prose_hash':digest(text),'han_characters':han_count(text),'facts_review':{'verdict':'pass'},'reader_review':{'verdict':'pass'}}
            db.execute('INSERT INTO chapters VALUES (?,?,?,?,?)',('main',n,text,dump(data),digest(text)))
            db.execute('INSERT INTO plans VALUES (?,?,?)',('main',n,dump(p)))
            append_events(db,'main',n,text,events,state)
        if kind=='dependencies':
            w=copy.deepcopy(fixture.DESIGN['world_plan']);w['dependencies']=list(range(1,17))
            design.save(db,'main','world',chapter_count+1,w,'fixture-dependent-world')
            p=copy.deepcopy(fixture.DESIGN['volume_plan']);p['volume']=fixture.chapter_plan(chapter_count+1)['volume'];design.save(db,'main',p['volume'],chapter_count+1,p,'fixture-volume')
        if kind=='profiles':
            profiles=copy.deepcopy(fixture.PROFILES)
            for profile in profiles['profiles']:
                profile['change_reason']='合成的大档案负载'
                profile['biography']['experiences']=[{'status':'design','text':f'待核实的第{i}条经历，仅作负载，不是已发生事实。'} for i in range(1000)]
            characters.save(db,'main',chapter_count+1,profiles,['hero','ally'],'fixture-long-profile')
        next_plan=fixture.chapter_plan(chapter_count+1)
        if kind=='knowledge' and unscoped_knowledge:next_plan['topics']=[]
        db.execute('INSERT INTO plans VALUES (?,?,?)',('main',chapter_count+1,dump(next_plan)))
        data={'first':chapter_count-9,'through':chapter_count,'verdict':'pass','fixture':'synthetic batch coverage'}
        db.execute('INSERT INTO reviews VALUES (?,?,?,?,?)',('main','batch',chapter_count,engine.review_hash(db,'main',chapter_count-9,chapter_count),dump(data)))
    assert replay(db,'main')==state
    return db


def step(db,path,trace,continuation_chars=3000):
    task=engine.next_task(db,path)
    assert task.get('task_id'),task
    assert task['task_id'] not in {item['task_id'] for item in trace}, 'Submitted task repeated without progress'
    input_path=Path(task['input_path'])
    if not input_path.is_absolute():input_path=Path(task['project'])/input_path
    raw=input_path.read_text(encoding='utf-8');work=json.loads(raw)
    assert len(raw)==work['chars']
    stage=task['stage']
    entry={'task_id':task['task_id'],'stage':stage,'input_chars':len(raw)}
    if stage in ('context_scan','context_reduce'):
        entry['field']=work['payload'].get('field','merged notes')
        entry['source_fragment_chars']=len(work['payload'].get('fragment',''))
        data=preparation(task,work,verbose=True)
    elif stage=='draft':
        opening=f'第{task["chapter"]}章，工程夹具继续验证上下文与恢复。\n'
        filler='合成文本仅用于容量验证，不代表文学质量或事实抽取能力。\n'
        data={'prose':(opening+filler*(continuation_chars//len(filler)+1))[:continuation_chars]}
    elif stage in ('facts','reader'):
        prose=work['payload']['prose'];data=review(work['payload']['checks'],prose,task['chapter'])
        # Real receipts pin a short unique quote; echoing the full chapter for
        # every check would measure repetition of the fixture, not the protocol.
        for check in data['checks']:check['evidence']=[ev(prose,task['chapter'],prose[:14])]
        if stage=='facts':data.update(events=[],summary='合成续写检查点。')
        else:data['first_read']={'desire':'核对','obstacle':'疑问','change':'保留意见','read_on_reason':'合成测试，不是文学判断'}
    else:raise AssertionError(stage)
    receipt=result(task,data,session={'id':uuid.uuid4().hex,'origin':'empty'})
    entry['output_chars']=len(contextflow.wire(receipt))
    engine.submit(db,receipt);trace.append(entry)


def scenario(root,kind,chapter_count=1000,unscoped_knowledge=False,continuation_chars=3000):
    path=root/str(chapter_count)/kind;db=build(path,kind,chapter_count,unscoped_knowledge)
    original=[(r[0],r[1]) for r in db.execute('SELECT number,hash FROM chapters ORDER BY number')]
    history_chars=db.execute('SELECT COALESCE(SUM(LENGTH(prose)),0) FROM chapters').fetchone()[0]
    trace=[]
    try:
        while engine.head(db,'main')<chapter_count+1:
            step(db,path,trace,continuation_chars)
            if len(trace)==3:
                db.close();db=connect(path) # Continue from persistent preparation, not session history.
        assert engine.head(db,'main')==chapter_count+1
        assert original==[(r[0],r[1]) for r in db.execute('SELECT number,hash FROM chapters WHERE number<=? ORDER BY number',(chapter_count,))]
        state=replay(db,'main')
        return {'case':kind,'history_chapters':len(original),'history_characters':history_chars,'continued_to':chapter_count+1,
                'preexisting_prose_unchanged':True,'character_limits_enforced':False,'knowledge_records':len(state['knowledge']),
                'continued_prose_characters':db.execute('SELECT LENGTH(prose) FROM chapters WHERE branch=? AND number=?',('main',chapter_count+1)).fetchone()[0],
                'open_hooks':sum(h['status']=='open' for h in state['hooks'].values()),
                'overdue_open_hooks':sum(h['status']=='open' and h['due']<=chapter_count+1 for h in state['hooks'].values()),
                'knowledge_topic_scope':'unspecified' if kind=='knowledge' and unscoped_knowledge else 'explicit',
                'tasks':len(trace),'max_input_chars':max(t['input_chars'] for t in trace),
                'total_input_chars':sum(t['input_chars'] for t in trace),
                'total_output_chars':sum(t['output_chars'] for t in trace),
                'preparation_input_chars':sum(t['input_chars'] for t in trace if t['stage'] in ('context_scan','context_reduce')),
                'preparation_source_fragment_chars':sum(t.get('source_fragment_chars',0) for t in trace),
                'protocol_usage':contextflow.usage(db),'trace':trace,
                'note':'Engineering synthetic decisions only; hooks case retains future long-term promises approaching a common deadline, not overdue debt or a completed novel.'}
    finally:db.close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',required=True)
    parser.add_argument('--chapters',type=int,nargs='+',default=[1000])
    parser.add_argument('--cases',choices=CASES,nargs='+',default=list(CASES))
    parser.add_argument('--unscoped-knowledge',action='store_true',help='Exercise full relevant knowledge when the current plan declares no topics.')
    parser.add_argument('--continuation-chars',type=int,default=3000,help='Actual synthetic chapter length for draft/facts/reader, default 3000.')
    parser.add_argument('--date',type=date.fromisoformat,default=None,help='Client date override; default is the current date in Asia/Shanghai.')
    args=parser.parse_args()
    if any(n<20 or n%10 for n in args.chapters):parser.error('--chapters requires multiples of 10, at least 20')
    if args.continuation_chars<256:parser.error('--continuation-chars must be at least 256')
    report_date=args.date or datetime.now(timezone(timedelta(hours=8))).date()
    report={'version':VERSION,'date':report_date.isoformat(),'scope':'Synthetic historical corpus followed by actual next/submit preparation, draft, facts, reader and commit. Reports cumulative protocol characters, without external models, billed tokens or literary quality claims.','cases':[]}
    with tempfile.TemporaryDirectory(prefix='novel-context-endurance-') as temp:
        for chapter_count in args.chapters:
            for kind in args.cases:
                report['cases'].append(scenario(Path(temp),kind,chapter_count,args.unscoped_knowledge,args.continuation_chars));print(f'{kind} {chapter_count} chapters passed',flush=True)
    write_json(args.out,report)

if __name__=='__main__':main()
