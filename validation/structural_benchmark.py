#!/usr/bin/env python3
"""Reproducible structure/local-runtime evidence; not a literary or token test."""
import argparse
import hashlib
import json
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime,timezone,timedelta
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'tests')]
from novel_core.store import initialize,connect,transaction,digest,dump,write_json,VERSION
from novel_core import ledger,projections,budget,characters
from support import contract,plan
from test_incremental_sources import IncrementalSourcesTests,base
from test_profile_views import ProfileViewTests


def preparation_and_profiles():
    fixture=IncrementalSourcesTests();fixture.setUp()
    try:
        first,initial=fixture.prepare(base(2))
        same,repeat=fixture.prepare(fixture.alternate_transport(base(2)))
        later,later_stages=fixture.prepare(base(3))
        changed=base(4);changed['state']['knowledge']['hero:new']={'stance':'knows','claim':'new complete condition','source':'synthetic'}
        growth,changed_stages=fixture.prepare(changed)
        from novel_core.store import get
        from novel_core.contextflow import view_key
        bundle=get(fixture.db,view_key(growth['context_materials']['bundle']))
        prep={'initial_stages':initial,'equal_purpose_new_transport_stages':repeat,'next_chapter_stages':later_stages,
              'new_material_stages':changed_stages,'equal_purpose_hit':same['context_materials']['preparation_reuse']['hit'],
              'new_material_index':bundle['documents'][0]['index_stats'],
              'notes_preserved':same['context_notes']==first['context_notes'],
              'decisions':'synthetic; no real literary relevance or billed-token evidence'}
    finally:fixture.tearDown()
    fixture=ProfileViewTests();fixture.setUp()
    try:
        record=fixture.prepare(1000);fixture.index(record)
        state=ledger.replay(fixture.db,'main')
        full=characters.context(fixture.db,'main',['hero'],state)
        view=fixture.view();query=characters.profile_read(fixture.db,'main','hero','biography.experiences',query={'topics':['topic:17']})
        missing=dict(fixture.plan);missing.pop('topics')
        fallback=fixture.view(missing)
        profile={'complete_experiences':1000,'selected_experiences':len(view['profiles']['hero']['data']['biography']['experiences']),
                 'full_chars':len(dump(full))+1,'scene_view_chars':len(dump(view))+1,'targeted_read_chars':len(dump(query))+1,
                 'unknown_scope_chars':len(dump(fallback))+1,'unknown_scope_retains_all':fallback['profiles']['hero']['data']==record['data'],
                 'index_worker_input_chars':len(fixture.db.execute("SELECT input FROM tasks WHERE id='index-fixture'").fetchone()[0])+1,
                 'independent_index_semantics_verified':False,'boundary':'synthetic independent-topic fixture; do not generalize its omission ratio to real character histories'}
    finally:fixture.tearDown()
    return {'preparation':prep,'profile':profile}


def local_runtime(chapters):
    path=Path(tempfile.mkdtemp(prefix='novel-local-runtime-'))
    c=contract();c['volumes'][0]['word_budget']=max(100000,chapters*1200);c['word_range']=[1,chapters*2000]
    initialize(path,c);db=connect(path);previous='genesis';clock=0;chars=0
    try:
        started=time.perf_counter()
        with transaction(db):
            for n in range(1,chapters+1):
                opening=f'第{n}日。';prose=opening+'文'*995;h=digest(prose);chars+=len(prose)
                db.execute('INSERT INTO chapters VALUES (?,?,?,?,?)',('main',n,prose,dump({'volume':'v1','han_characters':budget.han_count(prose)}),h))
                db.execute('INSERT INTO plans VALUES (?,?,?)',('main',n,dump(plan(n))))
                for idx in range(10):
                    event={'op':'clock','before':clock,'after':clock+1,'refs':['hero' if n%10==0 else 'ally'],'topic':'Straße','evidence':{'chapter':n,'start':0,'quote':opening}}
                    hashed=digest({'prev':previous,'chapter':n,'idx':idx,'prose_hash':h,'event':event})
                    db.execute('INSERT INTO events VALUES (?,?,?,?,?,?)',('main',n,idx,dump(event),previous,hashed));previous=hashed;clock+=1
        source_build=time.perf_counter()-started
        started=time.perf_counter();ledger.replay(db,'main');snapshot_build=time.perf_counter()-started
        started=time.perf_counter();projections.recall_events(db,'main',entity='hero',limit=20);index_build=time.perf_counter()-started
        budget.overview(db,'main')
        offsets=list(range(0,min(1000,chapters),20))
        def oracle(offset):
            total=0;rows=[]
            for row in db.execute("SELECT * FROM events WHERE branch='main' ORDER BY chapter,idx"):
                event=json.loads(row['data'])
                if 'hero' not in projections.event_references(event):continue
                if offset<=total<offset+20:rows.append({'chapter':row['chapter'],'index':row['idx'],'event':event,'event_hash':row['hash']})
                total+=1
            return total,rows
        started=time.perf_counter();expected=[oracle(offset) for offset in offsets];old_seconds=time.perf_counter()-started
        started=time.perf_counter();actual=[projections.recall_events(db,'main',entity='hero',offset=offset,limit=20) for offset in offsets];new_seconds=time.perf_counter()-started
        assert all((page['total'],page['records'])==wanted for page,wanted in zip(actual,expected))
    finally:db.close()
    cold="import sys,time,json;sys.path.insert(0,sys.argv[1]);from novel_core.store import connect;from novel_core.ledger import replay;t=time.perf_counter();db=connect(sys.argv[2]);s=replay(db,'main',verify=sys.argv[3]=='full');print(json.dumps({'seconds':time.perf_counter()-t,'clock':s['clock']}));db.close()"
    def run_cold(mode):
        values=[json.loads(subprocess.check_output([sys.executable,'-c',cold,str(ROOT/'scripts'),str(path),mode],text=True))['seconds'] for _ in range(3)]
        return {'runs_seconds':values,'median_seconds':statistics.median(values)}
    full=run_cold('full');snapshot=run_cold('snapshot');status=[];recall=[]
    for _ in range(3):
        started=time.perf_counter();subprocess.check_output([sys.executable,str(ROOT/'scripts/novel.py'),'status','--project',str(path)],text=True);status.append(time.perf_counter()-started)
        started=time.perf_counter();result=json.loads(subprocess.check_output([sys.executable,str(ROOT/'scripts/novel.py'),'recall','--project',str(path),'--entity','老周','--offset','20','--limit','20'],text=True));recall.append(time.perf_counter()-started)
        assert result['total']==actual[0]['total']
    return {'project':str(path),'chapters':chapters,'events':chapters*10,'prose_unicode_characters':chars,
            'source_build_seconds':source_build,'initial_verified_snapshot_seconds':snapshot_build,'initial_event_index_seconds':index_build,
            'pages':len(offsets),'page_size':20,'entity_query_matches':actual[0]['total'],
            'legacy_scan_seconds':old_seconds,'indexed_seconds':new_seconds,'exact_records_equal':True,
            'cold_full_verify':full,'cold_snapshot':snapshot,'actual_cold_cli_status_seconds':status,'actual_cold_cli_recall_seconds':recall,
            'boundary':'Local runtime only. Untargeted substring queries may scan candidates; audit intentionally validates every canonical row.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',required=True);parser.add_argument('--chapters',type=int,default=3000)
    args=parser.parse_args()
    if args.chapters<40:parser.error('--chapters must be at least 40')
    result={'version':VERSION,'date':datetime.now(timezone(timedelta(hours=8))).date().isoformat(),'kind':'synthetic_structure_and_local_runtime_not_literary_or_billed_tokens',**preparation_and_profiles(),'local_runtime':local_runtime(args.chapters),'billed_tokens':None}
    paths=[Path(__file__),ROOT/'scripts/novel.py',*sorted((ROOT/'scripts/novel_core').glob('*.py')),ROOT/'tests/test_incremental_sources.py',ROOT/'tests/test_profile_views.py']
    result['source_sha256']={str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    write_json(args.out,result);print(dump({'out':str(Path(args.out).resolve()),'version':VERSION,'chapters':args.chapters,'completed':True}))


if __name__=='__main__':main()
