"""Synthetic thousand-chapter context recovery, without model calls or quality claims."""
import argparse
import copy
import json
import sys
import tempfile
import uuid
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


def build(path,kind):
    c=fixture.contract();c['context_mode']='bounded';initialize(path,c);db=connect(path)
    put(db,'session_mode','fresh');put(db,'pilot_approved',True)
    state=seed(c)
    with transaction(db):
        design.save(db,'main','world',1,copy.deepcopy(fixture.DESIGN['world_plan']),'fixture-world')
        for v in c['volumes']:
            p=copy.deepcopy(fixture.DESIGN['volume_plan']);p['volume']=v['id'];design.save(db,'main',v['id'],1,p,'fixture-volume')
        characters.save(db,'main',1,copy.deepcopy(fixture.PROFILES),['hero','ally'],'fixture-profile')
        for n in range(1,1001):
            quote=f'第{n}号凭证记载了一次交接，周行读过原件，记住了当时的签收人。'
            text=(quote+'\n'+('这段合成文本只用于测量材料长度，不代表小说质量。\n'*140))[:3000]
            events=[{'op':'clock','before':n-1,'after':n,'evidence':ev(text,n,quote)}]
            if kind=='knowledge':events.append({'op':'knowledge','entity':'hero','topic':f'archive-{n}','before':None,'stance':'knows','claim':quote,'source':'亲读原件','evidence':ev(text,n,quote)})
            if kind=='hooks':events.append({'op':'hook','id':f'promise-{n}','before':None,'status':'open','text':quote,'due':n+5,'important':True,'evidence':ev(text,n,quote)})
            p=fixture.chapter_plan(n)
            data={'events':events,'summary':'合成工程历史','volume':p['volume'],'prose_hash':digest(text),'han_characters':han_count(text),'facts_review':{'verdict':'pass'},'reader_review':{'verdict':'pass'}}
            db.execute('INSERT INTO chapters VALUES (?,?,?,?,?)',('main',n,text,dump(data),digest(text)))
            db.execute('INSERT INTO plans VALUES (?,?,?)',('main',n,dump(p)))
            append_events(db,'main',n,text,events,state)
        if kind=='dependencies':
            w=copy.deepcopy(fixture.DESIGN['world_plan']);w['dependencies']=list(range(1,17))
            design.save(db,'main','world',1001,w,'fixture-dependent-world')
            p=copy.deepcopy(fixture.DESIGN['volume_plan']);p['volume']='v10';design.save(db,'main','v10',1001,p,'fixture-volume')
        if kind=='profiles':
            profiles=copy.deepcopy(fixture.PROFILES)
            for profile in profiles['profiles']:
                profile['change_reason']='合成的大档案负载'
                profile['biography']['experiences']=[{'status':'design','text':f'待核实的第{i}条经历，仅作负载，不是已发生事实。'} for i in range(1000)]
            characters.save(db,'main',1001,profiles,['hero','ally'],'fixture-long-profile')
        db.execute('INSERT INTO plans VALUES (?,?,?)',('main',1001,dump(fixture.chapter_plan(1001))))
        data={'first':991,'through':1000,'verdict':'pass','fixture':'synthetic batch coverage'}
        db.execute('INSERT INTO reviews VALUES (?,?,?,?,?)',('main','batch',1000,engine.review_hash(db,'main',991,1000),dump(data)))
    assert replay(db,'main')==state
    return db


def step(db,path,trace):
    task=engine.next_task(db,path)
    assert task.get('task_id'),task
    raw=Path(task['input_path']).read_text(encoding='utf-8');work=json.loads(raw)
    assert len(raw)==work['chars']<=work['context_capacity']['package_chars']
    stage=task['stage']
    entry={'stage':stage,'input_chars':len(raw),'input_limit':work['context_capacity']['package_chars']}
    if stage in ('context_scan','context_reduce'):
        entry['field']=work['payload'].get('field','merged notes')
        data=preparation(task,work,verbose=True)
    elif stage=='draft':data={'prose':'第1001章，周行继续核对原件。宋禾留下自己的疑问，等新的凭据。'}
    elif stage in ('facts','reader'):
        prose=work['payload']['prose'];data=review(work['payload']['checks'],prose,task['chapter'])
        if stage=='facts':data.update(events=[],summary='合成续写检查点。')
        else:data['first_read']={'desire':'核对','obstacle':'疑问','change':'保留意见','read_on_reason':'合成测试，不是文学判断'}
    else:raise AssertionError(stage)
    receipt=result(task,data,session={'id':uuid.uuid4().hex,'origin':'empty'})
    entry['output_chars']=len(contextflow.wire(receipt));entry['output_window_chars']=work['context_capacity']['capacity_chars']-len(raw)
    assert entry['output_chars']<=entry['output_window_chars']
    engine.submit(db,receipt);trace.append(entry)


def scenario(root,kind):
    path=root/kind;db=build(path,kind)
    original=[(r[0],r[1]) for r in db.execute('SELECT number,hash FROM chapters ORDER BY number')]
    trace=[]
    try:
        for i in range(400):
            if engine.head(db,'main')==1001:break
            step(db,path,trace)
            if i==2:
                db.close();db=connect(path) # Continue from persistent preparation, not session history.
        assert engine.head(db,'main')==1001
        assert original==[(r[0],r[1]) for r in db.execute('SELECT number,hash FROM chapters WHERE number<=1000 ORDER BY number')]
        state=replay(db,'main')
        return {'case':kind,'history_chapters':1000,'history_characters':3000000,'continued_to':1001,
                'preexisting_prose_unchanged':True,'knowledge_records':len(state['knowledge']),
                'open_hooks':sum(h['status']=='open' for h in state['hooks'].values()),
                'tasks':len(trace),'max_input_chars':max(t['input_chars'] for t in trace),'trace':trace,
                'note':'Engineering synthetic decisions only; hooks case retains unresolved debt, not a completed novel.'}
    finally:db.close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',required=True);args=parser.parse_args()
    report={'version':VERSION,'date':'2026-10-02','scope':'Synthetic 1000-chapter history followed by actual next/submit preparation, draft, facts, reader and commit. No external model, no token/billing or literary quality claim.','cases':[]}
    with tempfile.TemporaryDirectory(prefix='novel-context-endurance-') as temp:
        for kind in ('baseline','knowledge','hooks','dependencies','profiles'):
            report['cases'].append(scenario(Path(temp),kind));print(kind+' passed',flush=True)
    write_json(args.out,report)

if __name__=='__main__':main()
