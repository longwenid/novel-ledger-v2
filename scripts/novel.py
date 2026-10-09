#!/usr/bin/env python3
"""Portable CLI for novel-ledger-v2; Python 3.9+, standard library only."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from novel_core import engine
from novel_core.store import Error, VERSION, connect, initialize, get, put, transaction, chapters, write_json, log, require
from novel_core.memory import recall


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--version',action='version',version=VERSION)
    sub=p.add_subparsers(dest='command',required=True)
    for name in ('init','status','next','submit','measure','approve','recall','audit','revise','pause','resume','export','complete','config','replan','doctor','map','design','character','candidate','material','dependencies','tidy','html','impact','pilot'):
        s=sub.add_parser(name)
        s.add_argument('--project',required=True)
        if name in ('recall','map','design','character','candidate','material'):
            s.add_argument('--page-start',type=int,default=0)
            if name not in ('candidate','material'):s.add_argument('--task')
        if name=='material':
            s.add_argument('--task',required=True);s.add_argument('--bundle',required=True);s.add_argument('--document',required=True);s.add_argument('--start',type=int,default=0)
        if name=='init': s.add_argument('--contract',required=True)
        if name in ('submit','measure'): s.add_argument('--result',required=True)
        if name=='recall':
            s.add_argument('--query',default='');s.add_argument('--entity');s.add_argument('--topic');s.add_argument('--branch')
            s.add_argument('--start',type=int,default=1);s.add_argument('--end',type=int);s.add_argument('--offset',type=int,default=0);s.add_argument('--limit',type=int,default=20)
            s.add_argument('--chapter',type=int);s.add_argument('--contract',action='store_true');s.add_argument('--state',action='store_true')
        if name=='map':
            s.add_argument('--place',required=True);s.add_argument('--through',type=int);s.add_argument('--branch');s.add_argument('--depth',type=int,default=2);s.add_argument('--format',choices=['json','mermaid'],default='json')
        if name=='revise':
            s.add_argument('--from-chapter',type=int);s.add_argument('--reason',default='');s.add_argument('--discard',action='store_true');s.add_argument('--style-only',action='store_true')
        if name=='pilot':
            s.add_argument('--reason',required=True)
        if name=='pause':
            s.add_argument('--reason',required=True);s.add_argument('--cancel-task',action='store_true')
        if name=='export': s.add_argument('--out',required=True)
        if name=='html':
            s.add_argument('--out');s.add_argument('--branch')
        if name=='config':
            s.add_argument('--key',choices=['context_chars','context_output_chars','context_retrieval_chars','context_overhead_chars','context_mode','published_through','style_anchor','review_chunk','chapter_target_han','character_views']);s.add_argument('--value')
        if name=='candidate':
            s.add_argument('--task',required=True);s.add_argument('--start',type=int,default=0);s.add_argument('--end',type=int)
        if name=='character':
            s.add_argument('--entity',required=True);s.add_argument('--branch');s.add_argument('--history',action='store_true');s.add_argument('--refresh',action='store_true');s.add_argument('--reason')
            s.add_argument('--index',action='store_true');s.add_argument('--section',choices=['profile','core','biography.experiences','voice.relationship_modes','examples']);s.add_argument('--item',type=int);s.add_argument('--revision')
            s.add_argument('--topic',action='append');s.add_argument('--related-entity',action='append');s.add_argument('--place',action='append')
        if name=='dependencies':s.add_argument('--task',required=True)
        if name=='design':
            s.add_argument('--scope',default='world');s.add_argument('--branch');s.add_argument('--history',action='store_true')
        if name=='replan':
            s.add_argument('--reason',required=True);s.add_argument('--scope',choices=['chapters','world','volume'],default='chapters');s.add_argument('--volume')
        if name=='impact':
            s.add_argument('--fact',required=True);s.add_argument('--branch')
    return p


def dispatch(args):
    value=dispatch_raw(args)
    if args.command not in ('recall','map','design','character','candidate','material','impact'):return value
    db=connect(args.project)
    try:
        from novel_core.contextflow import enabled,bounded_read
        import os
        task_id=getattr(args,'task',None)
        pending=db.execute("SELECT id FROM tasks WHERE state='pending'").fetchone()
        # Query target (e.g. historical candidate) and budget payer need not be the same task.
        payer=pending['id'] if pending else task_id
        envelope=os.environ.get('NOVEL_TASK_FILE')
        if envelope:
            active=read(envelope)['task_id']
            require(not pending or pending['id']==active,'task','驱动任务不是当前待处理任务')
            payer=active
        with transaction(db):
            delivered=bounded_read(db,value,payer,getattr(args,'page_start',0)) if enabled(db) else value
            from novel_core.dependencies import record_read
            query={key:val for key,val in vars(args).items() if key not in ('project','task') and val is not None}
            query.setdefault('branch',get(db,'work_branch'))
            record_read(db,payer,query,delivered,task_id or payer)
            return delivered
    finally:db.close()


def _auto_render_html(project, db=None):
    try:
        from novel_core.bookpage import render, save
        closed_db = False
        if db is None:
            db = connect(project)
            closed_db = True
        try:
            target = Path(project).resolve() / 'book.html'
            branch = get(db, 'active_branch') or get(db, 'work_branch') or 'main'
            page = render(db, branch)
            save(target, page)
        finally:
            if closed_db:
                db.close()
    except Exception:
        pass


def dispatch_raw(args):
    cmd=args.command
    if cmd=='init':
        res = initialize(args.project,read(args.contract))
        _auto_render_html(args.project)
        return res
    db=connect(args.project)
    try:
        if cmd=='material':
            from novel_core.contextflow import material
            return material(db,args.task,args.bundle,args.document,args.start)
        if cmd=='status': return engine.status(db)
        if cmd=='dependencies':
            from novel_core.dependencies import inspect
            return inspect(db,args.task)
        if cmd=='candidate':
            from novel_core.patches import read_slice
            task=db.execute('SELECT * FROM tasks WHERE id=?',(args.task,)).fetchone()
            require(task,'task','任务不存在')
            return read_slice(db,task,args.start,args.end)
        if cmd=='character':
            from novel_core import characters
            from novel_core.ledger import replay
            branch=args.branch or get(db,'work_branch');state=replay(db,branch)
            eid=characters.resolve(state,args.entity)
            query={key:val for key,val in {'topics':args.topic,'entities':args.related_entity,'places':args.place}.items() if val}
            view=args.section or args.item is not None or args.revision is not None or query
            require(not (args.refresh and (args.index or view)),'character','重新准备档案不能同时指定分层读取')
            require(not (args.history and (args.index or view)),'character','历史列表与定向读取请分别调用')
            require(not (args.index and view),'character','索引与具体材料请分别读取')
            if args.index:return characters.index_read(db,branch,eid)
            if view:
                revision=int(args.revision) if args.revision and args.revision.isdigit() else args.revision
                return characters.profile_read(db,branch,eid,args.section,args.item,revision,query=query or None)
            if args.refresh:
                with transaction(db):
                    require(branch==get(db,'work_branch') and get(db,'status')=='ready','character','只能重审当前可运行分支的人物；完本后修改请用 revise')
                    require(not db.execute("SELECT 1 FROM tasks WHERE state='pending'").fetchone() and not engine.load_draft(db,branch,engine.head(db,branch)+1),'pending','先完成或取消当前任务；已有草稿通过审稿返工')
                    require(isinstance(args.reason,str) and args.reason.strip(),'character','重新准备人物须说明原因')
                    record=characters.current(db,branch,eid);require(record,'character','尚无档案，首次出场计划后会自动准备')
                    record['active']=False;record['invalidation_reason']=args.reason;put(db,characters.key(branch,eid),record)
                    log(db,'character_refresh',{'entity':eid,'branch':branch,'reason':args.reason})
            result=characters.history(db,branch,eid)
            if not args.history:result.pop('versions')
            return result
        if cmd=='design':
            from novel_core import design
            branch=args.branch or get(db,'work_branch')
            require(args.scope in ['world']+[v['id'] for v in get(db,'contract')['volumes']],'design','scope 为 world 或约定中的卷 ID')
            return design.history(db,branch,args.scope) if args.history else {'current':design.current(db,branch,args.scope),'status':'proposal_not_canon'}
        if cmd=='next': return engine.next_task(db,args.project)
        if cmd=='tidy':
            with transaction(db):
                removed=engine.prune_work(db,args.project,keep=0)
                log(db,'work_tidy',{'removed':removed,'mode':'manual'})
            swept=engine.sweep_legacy(args.project)
            return {'removed':len(removed),'directories':removed,'legacy_removed':swept['removed'],'legacy_kept':swept['kept']}
        if cmd=='submit':
            given=Path(args.result)
            res = engine.submit(db,read(given if given.is_absolute() else Path(args.project).resolve()/given))
            _auto_render_html(args.project, db)
            return res
        if cmd=='measure':
            from novel_core.budget import han_count
            from novel_core.patches import compose
            pending=db.execute("SELECT * FROM tasks WHERE state='pending'").fetchone()
            require(pending and pending['stage'] in ('draft','repair','redesign'),'measure','篇幅预检只用于当前待处理的执笔或返修任务')
            given=Path(args.result)
            receipt=read(given if given.is_absolute() else Path(args.project).resolve()/given)
            require(isinstance(receipt,dict),'measure','预检结果需为 JSON 对象')
            for key in ('task_id','input_hash'):
                expected=pending['id'] if key=='task_id' else pending['input_hash']
                require(key not in receipt or receipt[key]==expected,'stale_task','预检结果不属于当前任务；不能改旧信封继续使用。result.json 直接省略 task_id/input_hash 即自动绑定当前待处理任务')
            data=receipt.get('data',receipt)
            require(isinstance(data,dict),'measure','预检 data 需为对象')
            work=json.loads(pending['input'])
            draft=engine.load_draft(db,pending['branch'],pending['chapter'])
            prose=engine.parse_prose({'prose':compose(db,pending['stage'],work,data,draft)})
            observation=engine.chapter_length(db,pending['branch'],pending['chapter'],prose)
            require('plan_update' not in data or pending['stage']=='redesign','plan_update','plan_update 仅用于 redesign')
            if data.get('plan_update'):
                from novel_core.budget import chapter_length
                from novel_core.design import validate_chapter_execution
                update=data['plan_update']
                require(isinstance(update,dict),'plan_update','plan_update 需为对象')
                row=db.execute('SELECT data FROM plans WHERE branch=? AND number=?',(pending['branch'],pending['chapter'])).fetchone()
                plan=json.loads(row['data'])
                proposed={**plan,**update}
                validate_chapter_execution(proposed)
                changed=chapter_length(get(db,'contract'),proposed,prose)
                require(not observation or (changed and changed['target_han']==observation['target_han']),
                        'chapter_target','返修不能擅改已定篇幅目标；预检不会更改当前章计划')
                observation=changed
            return {'task_id':pending['id'],'chapter':pending['chapter'],'submitted':False,
                    'chapter_length':observation or {'unit':'han_characters','actual_han':han_count(prose),'target_han':None,'status':'no_target_configured'}}
        if cmd=='approve':
            res = engine.approve(db)
            _auto_render_html(args.project, db)
            return res
        if cmd=='audit': return engine.audit(db)
        if cmd=='pause': return engine.pause(db,args.reason,args.cancel_task)
        if cmd=='resume': return engine.resume(db)
        if cmd=='complete':
            with transaction(db): return engine.complete_inner(db)
        if cmd=='doctor':
            from novel_core.contextflow import limits,usage
            import platform, shutil
            report={'skill_root':str(Path(__file__).resolve().parents[1]),'skill_version':VERSION,'db_version':get(db,'version'),
                    'context_mode':get(db,'context_mode','bounded'),'character_limits_enforced':False,'material_structure':limits(db),
                    'explosion_control':'task isolation, topic selection, complete semantic-unit pages, source-bound preparation, load-once, actual host handoff',
                    'context_window_verified':False,'usage':usage(db),'python':platform.python_version(),'sqlite':__import__('sqlite3').sqlite_version,'python_path':sys.executable,'file_roundtrip':'database opened','automatic_after_chat_close':'host main session owns continuous scheduling','independent_review':'host_must_verify_fresh_review_workers','independent_review_verified':False}
            litter=engine.sweep_legacy(args.project,apply=False)['removed']
            if litter: report['legacy_litter']=litter
            return report
        if cmd=='map':
            from novel_core.ledger import replay
            from novel_core.spatial import resolve_place,spatial_view,mermaid
            branch=args.branch or get(db,'work_branch')
            through=engine.head(db,branch) if args.through is None else args.through
            require(0<=through<=engine.head(db,branch),'chapter','地图历史版本章号超出有效范围')
            state=replay(db,branch,through)
            place=resolve_place(state,args.place)
            require(0<=args.depth<=4,'map_depth','地图层级范围为 0..4；大地图分区查看')
            view=spatial_view(state,[place],children_depth=args.depth)
            view.update(branch=branch,as_of_chapter=through)
            if args.format=='mermaid':view['diagram']=mermaid(view)
            return view
        if cmd=='recall':
            branch=args.branch or get(db,'work_branch')
            if args.contract: return get(db,'contract')
            if args.state:
                from novel_core.ledger import replay
                from novel_core.characters import resolve
                require(args.entity or get(db,'context_mode','bounded')=='legacy','retrieval_scope','bounded 状态补读须带 --entity；用 --topic 进一步限定认知，不能整包倾倒全书状态')
                state=replay(db,branch)
                if not args.entity:return state
                eid=resolve(state,args.entity)
                view={'branch':branch,'entity':eid,'clock':state['clock'],'entities':{eid:state['entities'][eid]},
                      'facts':{k:v for k,v in state['facts'].items() if k.split(':',1)[0]==eid and (not args.topic or k.split(':',1)[1]==args.topic)},
                      'knowledge':{k:v for k,v in state['knowledge'].items() if k.split(':',1)[0]==eid and (not args.topic or k.split(':',1)[1]==args.topic)},
                      'resources':{k:v for k,v in state['resources'].items() if json.loads(k)[0]==eid and (not args.topic or json.loads(k)[1]==args.topic)},
                      'relations':{k:v for k,v in state.get('relations',{}).items()
                                   if (k.split('>',1)[0]==eid or k.split('>',1)[1].rsplit(':',1)[0]==eid)
                                   and (not args.topic or k.rsplit(':',1)[1]==args.topic)},
                      'absence_is_not_proof':True}
                from novel_core.spatial import spatial_view
                view['continuity']=spatial_view(state,[],[eid])
                return view
            if args.chapter is not None:
                row=db.execute('SELECT number,hash,prose,data FROM chapters WHERE branch=? AND number=?',(branch,args.chapter)).fetchone()
                require(row,'chapter','章节不存在')
                # Reading a chapter means reading prose; the event/receipt ledger in
                # `data` is several times the prose and stays reachable via recall.
                return {'branch':branch,'number':row['number'],'hash':row['hash'],'prose':row['prose'],
                        'summary':json.loads(row['data']).get('summary','')}
            require(get(db,'context_mode','bounded')=='legacy' or args.query or args.entity or args.topic or args.start!=1 or args.end is not None,
                    'retrieval_scope','bounded recall 须带 entity/topic/query/章范围，不能无目标读取全历史')
            return recall(db,branch,args.query,args.entity,args.topic,args.start,args.end,args.offset,args.limit)
        if cmd=='impact':
            branch=args.branch or get(db,'work_branch')
            return engine.fact_impact(db,branch,args.fact)
        if cmd=='pilot':
            return engine.pilot_reset(db,args.reason)
        if cmd=='revise':
            with transaction(db):
                if args.discard:
                    work=get(db,'work_branch')
                    require(work!=get(db,'active_branch'),'revision','没有候选修订')
                    db.execute("UPDATE tasks SET state='cancelled' WHERE state='pending'")
                    db.execute("UPDATE branches SET status='discarded' WHERE id=?",(work,))
                    put(db,'work_branch',get(db,'active_branch'));put(db,'status','ready')
                    log(db,'revision_discard',{'branch':work})
                    return engine.status(db)
                return engine.begin_revision(db,args.from_chapter,args.reason,style_only=args.style_only)
        if cmd=='config':
            if not args.key: return {**{k:get(db,k) for k in ('context_chars','context_mode','published_through','style_anchor')},'chapter_target_han':get(db,'contract').get('chapter_target_han'),'character_views':get(db,'contract').get('character_views','full')}
            if args.value is None: return {args.key:get(db,'contract').get(args.key,'full' if args.key=='character_views' else None) if args.key in ('chapter_target_han','character_views') else get(db,args.key)}
            if args.key=='style_anchor':
                try:
                    from novel_core.store import validate_style
                    value=validate_style(json.loads(args.value))|{'source':'author'}
                except ValueError:
                    require(False,'config','style_anchor 需为合法 JSON 对象 {"traits":[...],"sample":"..."}')
            else:
                value=args.value if args.key in ('context_mode','character_views') else int(args.value)
            if args.key=='review_chunk':require(1<=value<=5,'config','review_chunk 为 1~5 章的分段审稿粒度;覆盖范围不变,只调成本')
            if args.key=='chapter_target_han':
                from novel_core.budget import validate_chapter_target
                validate_chapter_target(value)
            if args.key=='context_mode': require(value in ('bounded','legacy'),'config','context_mode 为 bounded/legacy；legacy 仅用于复现旧协议')
            if args.key=='character_views':require(value in ('full','scoped'),'config','character_views 为 full/scoped')
            if args.key=='published_through': require(get(db,'published_through',0)<=value<=engine.head(db,get(db,'active_branch')),'config','发布边界只能前进至已验收章号')
            with transaction(db):
                require(not db.execute("SELECT 1 FROM tasks WHERE state='pending'").fetchone(),'pending','修改配置前先完成或取消当前任务')
                if args.key in ('chapter_target_han','character_views'):
                    contract=get(db,'contract');contract[args.key]=value;put(db,'contract',contract)
                else:put(db,args.key,value)
                if args.key.startswith('context_') and args.key!='context_mode':
                    from novel_core.contextflow import limits
                    limits(db)
                log(db,'config',{'key':args.key,'value':value})
            result={args.key:value}
            if args.key.startswith('context_') and args.key!='context_mode':result['deprecated']='字符配置仅兼容旧项目，不形成内容或读取限制；分页按完整语义单元。'
            return result
        if cmd=='replan':
            with transaction(db):
                require(not db.execute("SELECT 1 FROM tasks WHERE state='pending'").fetchone(),'pending','先完成或取消当前任务')
                branch=get(db,'work_branch'); n=engine.head(db,branch)
                require(not engine.load_draft(db,branch,n+1),'draft','当前章已有草稿，应通过审稿修订设计')
                if args.scope!='chapters':
                    from novel_core import design
                    require(design.enabled(db),'design','本书未启用分层设计')
                    scope='world' if args.scope=='world' else args.volume
                    require(scope in ['world']+[v['id'] for v in get(db,'contract')['volumes']],'design','分卷重设计须提供有效 --volume')
                    design.invalidate(db,branch,scope,args.reason)
                db.execute('DELETE FROM plans WHERE branch=? AND number>?',(branch,n))
                put(db,'status','ready');log(db,'replan',{'branch':branch,'reason':args.reason,'scope':args.scope})
            return engine.status(db)
        if cmd=='html':
            from novel_core.bookpage import render, save
            from pathlib import Path as _P
            given=_P(args.out) if args.out else _P('book.html')
            target=given if given.is_absolute() else _P(args.project).resolve()/given
            page_branch=args.branch or get(db,'active_branch')
            page=render(db,page_branch)
            save(target,page)
            rel=target.relative_to(_P(args.project).resolve()).as_posix() if str(target).startswith(str(_P(args.project).resolve())) else str(target)
            return {'file':rel,'branch':page_branch,'chars':len(page)}
        if cmd=='export':
            root=Path(args.out).resolve();root.mkdir(parents=True,exist_ok=True)
            branch=get(db,'active_branch'); manifest=[]
            # A manifest identifies the current export; old unrelated files are not deleted.
            for row in chapters(db,branch):
                filename=f'{row["number"]:04d}.md'
                path=root/filename
                temp=path.with_suffix('.tmp');temp.write_text(row['prose']+'\n',encoding='utf-8');temp.replace(path)
                manifest.append({'chapter':row['number'],'file':filename,'hash':row['hash']})
            write_json(root/'manifest.json',{'branch':branch,'title':get(db,'contract')['title'],'chapters':manifest,'status':get(db,'status')})
            return {'directory':str(root),'chapters':len(manifest),'status':get(db,'status')}
    finally:
        db.close()


def main(argv=None):
    try:
        # Machine-piped output is always UTF-8 bytes, whatever the console codepage.
        for stream in (sys.stdout,sys.stderr):
            if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8')
        result=dispatch(parser().parse_args(argv))
        print(json.dumps(result,ensure_ascii=False,separators=(',',':'),allow_nan=False))
        return 0
    except (Error,OSError,ValueError,KeyError,TypeError) as exc:
        print(json.dumps({'ok':False,'code':getattr(exc,'code',type(exc).__name__),'message':str(exc),'details':getattr(exc,'details',None)},ensure_ascii=False),file=sys.stderr)
        return 2

if __name__=='__main__':
    sys.exit(main())
