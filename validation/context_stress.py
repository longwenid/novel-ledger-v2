"""Reproduce v1.4.1 context risks using synthetic history, never model-written fiction.

Run: python3 validation/context_stress.py --out /absolute/results.json
Bulk fixtures bypass literary approval to isolate pack/retrieval/runtime behavior.
All databases live in a temporary directory; no user book or release is changed.
"""
import argparse
import copy
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from novel_core import engine, design, characters
from novel_core.store import initialize, connect, get, put, dump, digest, transaction, Error, write_json
from novel_core.ledger import seed, append_events, replay
from novel_core.memory import pack, recall
from novel_core.budget import han_count

DESIGN = json.loads((ROOT/'assets/design.example.json').read_text(encoding='utf-8'))
PROFILES = json.loads((ROOT/'assets/characters.example.json').read_text(encoding='utf-8'))
POINTS = (1,10,100,150,200,300,500,1000)


def contract():
    return {'title':'上下文压力测试（合成）','reader_promise':'工程测试，不是文学样本',
            'protagonist_drive':'核对记录','world_rules':['事实须有来源'],
            'ending':'记录有结算','arcs':['谨慎地认识事实'],
            'volumes':[{'id':f'v{i}','purpose':f'第{i}阶段','word_budget':300000} for i in range(1,11)],
            'word_range':[2500000,3500000], 'planning_mode':'layered','character_mode':'profiled',
            'entities':[{'id':'hero','name':'周行','kind':'person'}, {'id':'ally','name':'宋禾','kind':'person'}]}


def chapter_plan(n):
    p=copy.deepcopy(DESIGN['chapter_plan']);p.update(number=n,volume=f'v{min(10,(n-1)//100+1)}',topics=['current-only'])
    return p


def observe(db,n,stage='draft',payload=None):
    start=time.perf_counter()
    try:
        work=pack(db,'main',n+1,stage,payload or {})
        materialized=Path(db.execute('PRAGMA database_list').fetchone()[2]).parent/f'measured-{stage}-{n}.json'
        write_json(materialized,work)
        measured={'accepted':True,'compact_chars':len(dump(work)),
                  'file_chars':len(materialized.read_text(encoding='utf-8')),
                  'knowledge_items':len(work.get('state',{}).get('knowledge',{})),
                  'hook_items':len(work.get('state',{}).get('hooks',{})),
                  'fields_chars':{k:len(dump(v)) for k,v in work.items()},
                  'selected':[{k:v for k,v in x.items() if k in ('kind','chapter')} for x in work.get('selected',[])],
                  'omitted':work.get('omitted',[])}
    except Error as exc:
        measured={'accepted':False,'error':exc.code,'message':str(exc)}
    return {'history_chapters':n,'stage':stage,'capacity_chars':get(db,'context_chars'),
            'seconds':round(time.perf_counter()-start,4),**measured}


def fixture(temp,kind):
    path=Path(temp)/kind;c=contract();initialize(path,c);db=connect(path)
    samples=[];state=seed(c)
    with transaction(db):
        design.save(db,'main','world',1,copy.deepcopy(DESIGN['world_plan']),'synthetic-world')
        for v in c['volumes']:
            proposal=copy.deepcopy(DESIGN['volume_plan']);proposal['volume']=v['id']
            design.save(db,'main',v['id'],1,proposal,'synthetic-volume')
        characters.save(db,'main',1,copy.deepcopy(PROFILES),['hero','ally'],'synthetic-profile')
    # This repetitive 3000-character string is deliberately a load fixture, not a novel.
    for n in range(1,1001):
        quote=f'第{n}号凭证记载了一次交接，周行读过原件，记住了当时的签收人。'
        prose=quote+'\n'+('这段合成文本只用于测量材料长度，不代表小说质量。\n'*140)
        prose=prose[:3000]
        assert len(prose)==3000
        ev={'chapter':n,'start':0,'quote':quote}
        events=[{'op':'clock','before':n-1,'after':n,'evidence':ev}]
        if kind=='knowledge':
            events.append({'op':'knowledge','entity':'hero','topic':f'archive-{n}',
                           'before':None,'stance':'knows','claim':quote,'source':'亲自阅读原件','evidence':ev})
        if kind=='hooks':
            events.append({'op':'hook','id':f'promise-{n}','before':None,'status':'open',
                           'text':quote,'due':n+5,'important':True,'evidence':ev})
        with transaction(db):
            data={'events':events,'summary':'核对一份记录，保存出处。','volume':chapter_plan(n)['volume'],
                  'prose_hash':digest(prose),'han_characters':han_count(prose),
                  'facts_review':{'verdict':'pass','note':'synthetic fixture, not semantic review'},
                  'reader_review':{'verdict':'pass','note':'synthetic fixture, not literary review'}}
            db.execute('INSERT INTO chapters VALUES (?,?,?,?,?)',('main',n,prose,dump(data),digest(prose)))
            db.execute('INSERT OR REPLACE INTO plans VALUES (?,?,?)',('main',n,dump(chapter_plan(n))))
            append_events(db,'main',n,prose,events,state)
        if n in POINTS:
            db.execute('INSERT OR REPLACE INTO plans VALUES (?,?,?)',('main',n+1,dump(chapter_plan(n+1))))
            samples.append(observe(db,n))
    assert replay(db,'main')==state
    result={'kind':kind,'chapters':1000,'chapter_text_chars':3000,'corpus_chars':3000000,'samples':samples,
            'event_chain_replay_verified':True,
            'corpus_han':sum(json.loads(r[0])['han_characters'] for r in db.execute('SELECT data FROM chapters'))}
    if kind=='knowledge':
        # An accepted candidate requiring facts review reproduces the next_task overflow.
        engine.save_draft(db,'main',1001,{'stage':'facts','prose':'周行继续核对原件。'})
        count_before=db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]
        try:
            task_res=engine.next_task(db,path)
            stopped={'status':task_res.get('status','running'),'reason':None}
        except Error as exc:
            stopped={'status':'paused','reason':exc.code}
        after_resume=engine.resume(db)
        try:engine.next_task(db,path);retry='unexpected success'
        except Error as exc:retry=exc.code
        result['runtime_overflow']={'status':stopped['status'],'reason':stopped['reason'],
                                   'resume_status':after_resume['status'],'next_after_resume':retry,
                                   'new_tasks':db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]-count_before,
                                   'committed_chapters':engine.head(db,'main')}
        # Shows direct full-state recall has no context budget guard.
        result['full_state_compact_chars']=len(dump(state))
        result['entity_recall_first_chapters']=[r['chapter'] for r in recall(db,'main',entity='hero',limit=20)['records']]
    if kind=='baseline':
        result['book_review_chunk']=observe_review(db)
        # Populate synthetic chunk receipts only to measure the cross-chunk job shape.
        with transaction(db):
            for row in db.execute('SELECT number,hash FROM chapters WHERE branch="main"').fetchall():
                n=row['number'];data={'first':n,'through':n,'verdict':'pass','note':'synthetic coverage fixture, not literary review'}
                fingerprint=digest({'contract':c,'chapters':[(n,row['hash'])]})
                db.execute('INSERT INTO reviews VALUES (?,?,?,?,?)',('main','book_chunk',n,fingerprint,dump(data)))
        result['book_review_synthesis']=observe_review(db)
        # A valid world design may accumulate full-text source dependencies.
        proposal=copy.deepcopy(DESIGN['world_plan']);proposal['dependencies']=list(range(1,17))
        with transaction(db):
            design.save(db,'main','world',1001,proposal,'synthetic-world-revision')
            volume=copy.deepcopy(DESIGN['volume_plan']);volume['volume']='v10'
            design.save(db,'main','v10',1001,volume,'synthetic-volume-revision')
            db.execute('INSERT INTO plans VALUES (?,?,?)',('main',1001,dump(chapter_plan(1001))))
        result['sixteen_design_dependencies']=observe(db,1000)
    db.close()
    return result


def observe_review(db):
    begin=time.perf_counter();stage,n,payload=engine.review_job(db,'main','book',1,1000)
    measured=observe(db,n-1,stage,payload)
    return {**measured,'scope':payload['scope'],'total_job_seconds':round(time.perf_counter()-begin,4)}


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);args=p.parse_args()
    from novel_core.store import VERSION
    if VERSION!='1.4.1':
        p.error('此脚本只复现 v1.4.1。请在该 tag 的隔离副本中运行；当前修复版用 context_endurance.py。')
    report={'version':'1.4.1','date':'2026-10-02','scope':'Synthetic pack/retrieval/runtime stress; no model API, no literary quality or billed tokens measured.',
            'budget_unit':'Python characters in compact JSON, not tokens','cases':[]}
    with tempfile.TemporaryDirectory(prefix='novel-context-stress-') as temp:
        for kind in ('baseline','knowledge','hooks'):
            report['cases'].append(fixture(temp,kind));print(kind+' completed',flush=True)
    write_json(args.out,report)
    print('Report: '+str(Path(args.out).resolve()))


if __name__=='__main__':main()
