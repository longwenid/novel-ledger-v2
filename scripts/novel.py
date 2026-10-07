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
from novel_core.runner import run, validate_config


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--version',action='version',version=VERSION)
    sub=p.add_subparsers(dest='command',required=True)
    for name in ('init','status','next','submit','approve','recall','audit','revise','run','pause','resume','export','complete','config','replan','doctor','map','design','character','candidate','material','session-start','tidy','html'):
        s=sub.add_parser(name)
        s.add_argument('--project',required=True)
        if name in ('recall','map','design','character','candidate','material'):
            s.add_argument('--page-start',type=int,default=0)
            if name not in ('candidate','material'):s.add_argument('--task')
        if name=='session-start':s.add_argument('--id',required=True)
        if name=='material':
            s.add_argument('--task',required=True);s.add_argument('--bundle',required=True);s.add_argument('--document',required=True);s.add_argument('--start',type=int,default=0)
        if name=='init': s.add_argument('--contract',required=True)
        if name=='submit': s.add_argument('--result',required=True)
        if name=='recall':
            s.add_argument('--query',default='');s.add_argument('--entity');s.add_argument('--topic');s.add_argument('--branch')
            s.add_argument('--start',type=int,default=1);s.add_argument('--end',type=int);s.add_argument('--offset',type=int,default=0);s.add_argument('--limit',type=int,default=20)
            s.add_argument('--chapter',type=int);s.add_argument('--contract',action='store_true');s.add_argument('--state',action='store_true')
        if name=='map':
            s.add_argument('--place',required=True);s.add_argument('--through',type=int);s.add_argument('--branch');s.add_argument('--depth',type=int,default=2);s.add_argument('--format',choices=['json','mermaid'],default='json')
        if name=='revise':
            s.add_argument('--from-chapter',type=int);s.add_argument('--reason',default='');s.add_argument('--discard',action='store_true')
        if name=='run':
            s.add_argument('--driver',required=True);s.add_argument('--max-tasks',type=int,default=0);s.add_argument('--check-only',action='store_true')
        if name=='pause':
            s.add_argument('--reason',required=True);s.add_argument('--cancel-task',action='store_true')
        if name=='export': s.add_argument('--out',required=True)
        if name=='html':
            s.add_argument('--out');s.add_argument('--branch')
        if name=='config':
            s.add_argument('--key',choices=['context_chars','context_output_chars','context_retrieval_chars','context_overhead_chars','context_mode','session_mode','published_through','style_anchor','review_chunk']);s.add_argument('--value')
        if name=='candidate':
            s.add_argument('--task',required=True);s.add_argument('--start',type=int,default=0);s.add_argument('--end',type=int)
        if name=='character':
            s.add_argument('--entity',required=True);s.add_argument('--branch');s.add_argument('--history',action='store_true');s.add_argument('--refresh',action='store_true');s.add_argument('--reason')
        if name=='design':
            s.add_argument('--scope',default='world');s.add_argument('--branch');s.add_argument('--history',action='store_true')
        if name=='replan':
            s.add_argument('--reason',required=True);s.add_argument('--scope',choices=['chapters','world','volume'],default='chapters');s.add_argument('--volume')
    return p


def dispatch(args):
    value=dispatch_raw(args)
    if args.command not in ('recall','map','design','character','candidate','material'):return value
    db=connect(args.project)
    try:
        from novel_core.contextflow import enabled,bounded_read
        if not enabled(db):return value
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
        with transaction(db):return bounded_read(db,value,payer,getattr(args,'page_start',0))
    finally:db.close()


def dispatch_raw(args):
    cmd=args.command
    if cmd=='init': return initialize(args.project,read(args.contract))
    if cmd=='run' and args.check_only: return {'valid':True,'config':validate_config(read(args.driver))}
    db=connect(args.project)
    try:
        if cmd=='session-start':
            from novel_core.contextflow import start_session
            with transaction(db):
                require(not get(db,'runner'),'runner_busy','会话交接前先结束旧运行器')
                return start_session(db,args.id)
        if cmd=='material':
            from novel_core.contextflow import material
            return material(db,args.task,args.bundle,args.document,args.start)
        if cmd=='status': return engine.status(db)
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
            return {'removed':len(removed),'directories':removed}
        if cmd=='submit':
            given=Path(args.result)
            return engine.submit(db,read(given if given.is_absolute() else Path(args.project).resolve()/given))
        if cmd=='approve': return engine.approve(db)
        if cmd=='audit': return engine.audit(db)
        if cmd=='pause': return engine.pause(db,args.reason,args.cancel_task)
        if cmd=='resume': return engine.resume(db)
        if cmd=='run': return run(db,args.project,read(args.driver),args.max_tasks)
        if cmd=='complete':
            with transaction(db): return engine.complete_inner(db)
        if cmd=='doctor':
            from novel_core.contextflow import limits,enabled
            import platform, shutil
            anchors=limits(db);session_used=get(db,'context_session_used',0)
            headroom=max(0,anchors['capacity']-session_used)
            metered=enabled(db) and get(db,'session_mode')=='serial'
            next_cost=anchors['output']+anchors['retrieval']+anchors['input']+anchors['overhead']
            return {'skill_root':str(Path(__file__).resolve().parents[1]),'skill_version':VERSION,'db_version':get(db,'version'),
                    'context_mode':get(db,'context_mode','bounded'),'declared_capacity_chars':get(db,'context_chars'),'sizing_anchors':limits(db),
                    'session_capacity':{'metered':metered,'used_chars':session_used if metered else None,'headroom_chars':headroom if metered else None,
                        'pressure':('handoff_imminent' if headroom<next_cost else 'ok') if metered else 'not_metered',
                        'note':'bounded serial：headroom 低于下一任务成本时将转为 await_session 交接；宿主无法提供真实新会话时改用 serial 模式或重新校准声明容量'},
                    'explosion_control':'structural rules: single-task visibility, scoped pagination, prepare-then-enter, load-once, one unit per receipt, growth-triggered handoff',
                    'usage':{'tasks':get(db,'usage_tasks',0),'session_handoffs':get(db,'usage_handoffs',0),'input_chars':get(db,'usage_input_chars',0),
                             'result_chars':get(db,'usage_result_chars',0),'read_chars':get(db,'usage_read_chars',0)},
                    'token_note':'降低消耗：宿主真实窗口大于声明容量时调高 context_chars 可减少交接与重复加载；工作包按需选择材料，历史有界纳入，遗漏按 recall 定向补读',
                    'context_window_verified':False,'python':platform.python_version(),'sqlite':__import__('sqlite3').sqlite_version,'python_path':sys.executable,'session_mode':get(db,'session_mode'),'file_roundtrip':'database opened','automatic_after_chat_close':'requires externally configured driver','independent_review':get(db,'session_mode')=='fresh'}
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
                return replay(db,branch)
            if args.chapter is not None:
                row=db.execute('SELECT number,hash,prose,data FROM chapters WHERE branch=? AND number=?',(branch,args.chapter)).fetchone()
                require(row,'chapter','章节不存在')
                # Reading a chapter means reading prose; the event/receipt ledger in
                # `data` is several times the prose and stays reachable via recall.
                return {'branch':branch,'number':row['number'],'hash':row['hash'],'prose':row['prose'],
                        'summary':json.loads(row['data']).get('summary','')}
            return recall(db,branch,args.query,args.entity,args.topic,args.start,args.end,args.offset,args.limit)
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
                return engine.begin_revision(db,args.from_chapter,args.reason)
        if cmd=='config':
            if not args.key: return {k:get(db,k) for k in ('context_chars','context_mode','session_mode','published_through','style_anchor')}
            if args.value is None: return {args.key:get(db,args.key)}
            if args.key=='style_anchor':
                try:
                    from novel_core.store import validate_style
                    value=validate_style(json.loads(args.value))|{'source':'author'}
                except ValueError:
                    require(False,'config','style_anchor 需为合法 JSON 对象 {"traits":[...],"sample":"..."}')
            else:
                value=args.value if args.key in ('session_mode','context_mode') else int(args.value)
            if args.key in ('context_output_chars','context_retrieval_chars','context_overhead_chars'):require(value>=256,'context_config','读页/回执/预留锚点至少 256 字符；锚点只校准尺寸，不构成配额')
            if args.key=='review_chunk':require(1<=value<=5,'config','review_chunk 为 1~5 章的分段审稿粒度;覆盖范围不变,只调成本')
            if args.key=='context_mode': require(value in ('bounded','legacy'),'config','context_mode 为 bounded/legacy；legacy 仅用于复现旧协议')
            if args.key=='context_chars': require(value>=4096,'config','声明容量至少 4096 字符；由宿主按真实 token 窗口校准')
            if args.key=='session_mode': require(value in ('serial','fresh'),'config','模式只能 serial/fresh')
            if args.key=='published_through': require(get(db,'published_through',0)<=value<=engine.head(db,get(db,'active_branch')),'config','发布边界只能前进至已验收章号')
            with transaction(db):
                require(not db.execute("SELECT 1 FROM tasks WHERE state='pending'").fetchone(),'pending','修改配置前先完成或取消当前任务')
                put(db,args.key,value)
                if args.key.startswith('context_') and args.key!='context_mode':
                    from novel_core.contextflow import limits
                    limits(db)
                log(db,'config',{'key':args.key,'value':value})
            return {args.key:value}
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
