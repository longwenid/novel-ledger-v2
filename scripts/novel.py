#!/usr/bin/env python3
"""Portable CLI for novel-ledger-v2; Python 3.9+, standard library only."""
from __future__ import annotations
import argparse
import json
import sqlite3
import sys
from pathlib import Path
from novel_core import access, atoms, engine, execution
from novel_core.wire import EVIDENCE_INPUT
from novel_core.taskview import PROTOCOL as READING_PROTOCOL
from novel_core.schedule import PROTOCOL as TIMELINE_PROTOCOL
from novel_core.store import Error, VERSION, connect, initialize, get, put, transaction, chapters, write_json, log, require, head
from novel_core.memory import recall

# Windows consoles default to a legacy code page; captured CJK output then
# arrives mojibake in host shells. Emit UTF-8 and let the reader decode it.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, 'reconfigure'):
        try:
            _stream.reconfigure(encoding='utf-8')
        except (ValueError, OSError):
            pass


def read(path):
    try:
        text=Path(path).read_text(encoding='utf-8')
    except UnicodeDecodeError:
        raise Error('encoding', f'{path} 不是 UTF-8——Windows 默认编码（GBK）写文件会损坏中文。用宿主文件写入工具整体重写；走脚本必须显式 encoding="utf-8"', {'path': str(path)}) from None
    if text.startswith(chr(0xfeff)):
        # PowerShell '>' redirection prefixes a UTF-8 BOM. The bytes are
        # unambiguous, so strip instead of bouncing a full worker retry;
        # genuinely broken encodings (GBK) still fail below.
        text = text[1:]
    return json.loads(text)


def read_receipt(db, project, given, record=True):
    """Bind task-directory outputs to their original frozen task, even on retry."""
    path=Path(given)
    path=(path if path.is_absolute() else Path(project).resolve()/path).resolve()
    envelope_path=path.parent/'task.json';bound_task=None
    if envelope_path.is_file():
        envelope=read(envelope_path)
        if isinstance(envelope,dict):
            row=db.execute('SELECT * FROM tasks WHERE id=?',(envelope.get('task_id'),)).fetchone()
            if row and path.parent==(Path(project).resolve()/engine.work_rel(row)).resolve():
                bound_task=row['id']
    return engine.normalize_receipt(db,read(path),record=record,bound_task=bound_task)


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--version',action='version',version=VERSION)
    sub=p.add_subparsers(dest='command',required=True)
    for name in ('init','status','next','advance','submit','measure','preflight','patch','approve','recall','audit','revise','pause','resume','export','complete','config','recontract','replan','doctor','map','design','character','candidate','material','dependencies','tidy','html','impact','pilot','first-read','evidence','checkpoint','task-view'):
        s=sub.add_parser(name)
        s.add_argument('--project',required=True)
        if name in ('recall','map','design','character','candidate','material'):
            s.add_argument('--page-start',type=int,default=0)
            if name not in ('candidate','material'):s.add_argument('--task')
        if name=='material':
            s.add_argument('--task',required=True);s.add_argument('--bundle',required=True);s.add_argument('--document',required=True);s.add_argument('--start',type=int,default=0)
        if name=='init': s.add_argument('--contract',required=True)
        if name=='recontract':
            s.add_argument('--contract',required=True);s.add_argument('--reason',default='')
        if name in ('submit','measure','preflight','first-read'): s.add_argument('--result',required=True)
        if name in ('status','submit'): s.add_argument('--brief',action='store_true')
        if name in ('advance','status','submit','first-read','preflight'):s.add_argument('--compact',action='store_true')
        if name=='submit':
            s.add_argument('--continue-preparation',action='store_true');s.add_argument('--continue-chapter-batch',action='store_true')
            s.add_argument('--continue-review',action='store_true')
        if name in ('first-read','evidence','patch','task-view'): s.add_argument('--task',required=True)
        if name=='task-view':s.add_argument('--read',action='store_true')
        if name=='preflight': s.add_argument('--task')  # optional; workers pass it by patch habit
        if name=='patch':
            s.add_argument('--old',required=True);s.add_argument('--new',required=True);s.add_argument('--start',type=int)
        if name=='evidence':
            s.add_argument('--quote',required=True);s.add_argument('--chapter',type=int);s.add_argument('--document')
        if name=='checkpoint':
            s.add_argument('--goal');s.add_argument('--writer-session');s.add_argument('--handled-chapter',type=int,action='append')
        if name=='advance':s.add_argument('--after');s.add_argument('--through',type=int)
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
            s.add_argument('--key',choices=['context_chars','context_output_chars','context_retrieval_chars','context_overhead_chars','context_mode','preparation_policy','published_through','style_anchor','review_chunk','chapter_target_han','character_views','writer_batch','chapter_batch_size','batch_through','revision_chain_limit','writer_session_policy','execution_mode']);s.add_argument('--value')
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
    if args.command in ('advance','next') and value.get('action',value.get('stage'))=='revision_promoted':
        _auto_render_html(args.project)
    if args.command in ('submit','approve','pause','resume','revise','complete','replan','pilot','recontract'):
        from novel_core.taskio import checkpoint
        db=connect(args.project)
        try:checkpoint(db,args.project)
        finally:db.close()
    if getattr(args,'compact',False):
        from novel_core.wire import compact_response
        return compact_response(args.project,value)
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
    """Refresh the derived page, degrading instead of swallowing (P4).

    book.html is a derived view, so a failed render must never abort a commit
    that already succeeded in the ledger. It must, however, leave a trace: the
    failure is logged and returned so the caller can surface it. A silently
    stale page is worse than a reported one.
    """
    try:
        from novel_core.bookpage import render, save
        closed_db = False
        if db is None:
            db = connect(project)
            closed_db = True
        try:
            target = Path(project).resolve() / 'book.html'
            branch = get(db, 'active_branch') or get(db, 'work_branch') or 'main'
            save(target, render(db, branch))
            return None
        finally:
            if closed_db:
                db.close()
    except Exception as exc:
        # Rendered views are derived: no failure here may change the outcome of
        # a commit that already succeeded in the ledger. Broad capture is
        # deliberate; what P4 forbids is capturing and DISCARDING.
        failed = {'code': getattr(exc, 'code', type(exc).__name__), 'message': str(exc)[:200]}
        try:
            probe = db if db is not None else connect(project)
            try:
                log(probe, 'html_render_failed', failed)
            finally:
                if db is None:
                    probe.close()
        except Exception:
            # The one sanctioned silent point, registered in
            # references/architecture.md P4: the ledger itself is unwritable, so
            # there is nowhere to record the failure. The caller still receives
            # `failed`; only the trace is lost.
            pass
        return failed


def _output_target(project, given, default):
    """Resolve a derived-view output path (P2 single policy for html/export).

    Writing outside the project is a legitimate export, so it is allowed — but
    the route is decided BEFORE anything is written and reported as
    `inside_project`. The previous `str(target).startswith(str(project))` test
    matched sibling directories, so it wrote the file outside the project and
    only then raised ValueError from relative_to: a half-performed side effect
    plus an opaque failure.
    """
    root = Path(project).resolve()
    chosen = Path(given) if given else Path(default)
    target = (chosen if chosen.is_absolute() else root / chosen).resolve()
    inside = target.is_relative_to(root)
    route = target.relative_to(root).as_posix() if inside else str(target)
    return target, inside, route


def dispatch_raw(args):
    cmd=args.command
    if cmd=='init':
        res = initialize(args.project,read(args.contract))
        failed=_auto_render_html(args.project)
        return {**res,**({'html_render_error':failed} if failed else {})}
    db=connect(args.project)
    try:
        # P2 default-deny: the tier table is the single registry; an unlisted
        # command is refused rather than silently unguarded.
        access.guard(db,cmd,args)
        if cmd=='task-view':
            task=db.execute('SELECT * FROM tasks WHERE id=?',(args.task,)).fetchone()
            require(task and task['state']=='pending' and task['branch']==get(db,'work_branch'),
                    'task','阅读视图只用于当前工作分支的 pending')
            envelope=engine.task_files(db,args.project,dict(task))
            if args.read:
                from novel_core.taskview import read_bundle
                view=envelope.get('reading_view')
                require(view is not None,'task_view_unavailable','此任务无阅读视图；按 input_path 和必读分片读取',
                        {'input_path':envelope['input_path'],'recover':'旧冻结任务不升级交付协议；沿用原任务信封。'})
                return read_bundle(args.project,read(Path(args.project).resolve()/view['manifest_path']))
            return {'task_id':task['id'],'input_hash':task['input_hash'],
                    'reading_view':envelope.get('reading_view'),'input_path':envelope['input_path'],
                    'reading_view_error':envelope.get('reading_view_error')}
        if cmd=='first-read':
            from novel_core.reviewflow import seal
            given=Path(args.result)
            return seal(db,args.project,args.task,read(given if given.is_absolute() else Path(args.project).resolve()/given))
        if cmd=='evidence':
            from novel_core.taskio import locate
            return locate(db,args.task,args.quote,args.chapter,args.document)
        if cmd=='patch':
            from novel_core.patches import locate_patch
            task=db.execute('SELECT * FROM tasks WHERE id=?',(args.task,)).fetchone()
            require(task and task['state']=='pending' and task['branch']==get(db,'work_branch'),'task','补丁定位只用于当前有效 pending')
            return locate_patch(db,task,args.old,args.new,args.start)
        if cmd=='preflight':
            receipt=read_receipt(db,args.project,args.result,record=False)
            task=db.execute('SELECT * FROM tasks WHERE id=?',(receipt.get('task_id') if isinstance(receipt,dict) else None,)).fetchone()
            require(task and task['state']=='pending' and task['stage'] in ('plan','draft','repair','redesign','chapter_review'),
                    'preflight','预检只用于当前有效规划、执笔或审稿任务')
            require(args.task is None or args.task==task['id'],'preflight','--task 与回执绑定的任务不一致，按回执 task_id 预检')
            before=engine.head(db,task['branch'])
            checked=engine.submit(db,receipt,brief=True,preview=True)
            return {'schema':'novel-ledger-v2.preflight/1','task_id':task['id'],'checked':True,'submitted':False,
                    'would_commit':checked.get('work_committed',before)>before,
                    **{k:checked[k] for k in ('settlement','numeric_correction') if k in checked}}
        if cmd=='checkpoint':
            from novel_core.taskio import checkpoint
            return checkpoint(db,args.project,args.goal,args.writer_session,args.handled_chapter)
        if cmd=='advance':
            from novel_core.taskio import advance
            if args.through is not None:
                require(args.through>=0,'chapter_range','through 为授权目标章号；0 清除范围')
                with transaction(db):
                    require(not db.execute("SELECT 1 FROM tasks WHERE state='pending'").fetchone() or args.through==get(db,'batch_through',0),
                            'pending','不能在任务执行中更改授权章范围')
                    from novel_core.batchflow import active
                    batch=active(db)
                    require(not batch or not args.through or args.through>=batch['last'],'chapter_range','已有批次超出新范围，须先处理已保存候选稿')
                    put(db,'batch_through',args.through)
            before=head(db,get(db,'work_branch'))
            out=advance(db,args.project,args.after)
            # P1 single writer for derived views: the page belongs to the
            # controller's chapter boundary. Refreshing once per landed chapter
            # keeps book.html current for the author without charging every
            # worker receipt for a full audit and a full-book render.
            if head(db,get(db,'work_branch'))>before:
                failed=_auto_render_html(args.project,db)
                if failed:out={**out,'html_render_error':failed}
            return out
        if cmd=='material':
            from novel_core.contextflow import material
            return material(db,args.task,args.bundle,args.document,args.start)
        if cmd=='status': return engine.status(db,brief=args.brief)
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
            return {'removed':len(removed),'directories':removed,'legacy_removed':swept['removed'],'legacy_kept':swept['kept'],'legacy_relocated':swept['relocated']}
        if cmd=='submit':
            receipt=read_receipt(db,args.project,args.result)
            task=db.execute('SELECT * FROM tasks WHERE id=?',(receipt.get('task_id') if isinstance(receipt,dict) else None,)).fetchone()
            frozen=json.loads(task['input']) if task else {}
            if execution.controller_owned(frozen):
                require(not any((args.continue_preparation,args.continue_chapter_batch,args.continue_review)),
                        'worker_boundary','单章写审提交后结束；由主控 advance 派下一任务，不使用连续处理选项')
            require(sum((args.continue_preparation,args.continue_chapter_batch,args.continue_review))<=1,'chapter_session','连续处理选项只能选一个')
            if args.continue_review:
                from novel_core.reviewrun import PROTOCOL,allowed
                work=json.loads(task['input']) if task else {}
                require(task and work.get('review_session',{}).get('protocol')==PROTOCOL and allowed(work,work['review_session']),
                        'review_session','连续复审只用于绑定同一独立审稿会话的任务')
            if args.continue_chapter_batch:
                from novel_core.batchflow import PROTOCOL
                work=json.loads(task['input']) if task else {}
                require(work.get('chapter_session',{}).get('protocol')==PROTOCOL,'chapter_session','只有绑定批次角色的任务可连续处理')
            if args.continue_preparation:
                from novel_core.contextflow import SESSION_PROTOCOL
                work=json.loads(task['input']) if task else {}
                require(task and task['stage'] in ('context_scan','context_reduce')
                        and work.get('preparation_session',{}).get('protocol')==SESSION_PROTOCOL
                        and work['preparation_session'].get('bundle')==work['payload'].get('bundle'),
                        'preparation_session','连续提交只用于绑定同一批次的材料任务')
            res = engine.submit(db,receipt,brief=args.brief)
            html_failed=None
            # `--brief` / `--compact` are worker hand-off modes: they shrink the
            # RESPONSE. Rendering book.html needs a full verified audit plus
            # every chapter inlined, so doing it per receipt was the "heavy
            # status" the compact contract exists to avoid — and the bare
            # `except Exception: pass` used to hide that it happened at all.
            # The page is now refreshed at the controller's chapter boundary.
            if not (getattr(args,'compact',False) or getattr(args,'brief',False)) and (not task or (task['stage'] not in ('context_scan','context_reduce') and json.loads(task['input']).get('chapter_session',{}).get('role')!='writer'
                            and (not task['stage'].endswith('_review') or task['stage']=='chapter_review'))):
                html_failed=_auto_render_html(args.project, db)
            if html_failed:
                # P4: surface the degraded step instead of reporting a clean
                # `accepted` while book.html stays stale.
                res={**res,'html_render_error':html_failed}
            if execution.controller_owned(frozen) or (execution.settings(db)['execution_mode']==execution.SINGLE and task and task['stage'] in execution.WRITER_STAGES+('chapter_review',)):
                snapshot=engine.status(db,brief=True)
                draft=engine.load_draft(db,task['branch'],task['chapter'])
                action='paused' if snapshot['status']=='paused' else ('chapter_written' if task['stage'] in execution.WRITER_STAGES
                        else 'chapter_reviewed' if head(db,task['branch'])>=task['chapter'] else 'repair_required')
                if task['stage'] in execution.WRITER_STAGES and draft and draft['stage']!='chapter_review':action='repair_required'
                return {'accepted':True,'task_id':task['id'],'action':action,'worker_boundary':'finished',
                        **{k:res[k] for k in ('idempotent','settlement','numeric_correction','html_render_error') if k in res},
                        'boundary':'当前 Worker 结束；主控核实实际结束后 advance --after 本任务 ID，不自行取下一任务'}
            if args.continue_chapter_batch:
                frozen=json.loads(task['input'])
                if frozen.get('writer_session_policy')=='chapter' and frozen.get('chapter_session',{}).get('role')=='writer':
                    return {'accepted':True,'task_id':task['id'],'action':'writer_chapter_done',
                            **{k:res[k] for k in ('settlement',) if k in res},
                            'boundary':'本章执笔会话结束；主控核实 Worker 结束后 advance --after 接续，逻辑批次不变'}
                from novel_core.batchflow import continue_session
                return {**continue_session(db,args.project,task),**{k:res[k] for k in ('settlement','numeric_correction') if k in res}}
            if args.continue_review:
                from novel_core.reviewrun import continue_session
                return continue_session(db,args.project,task)
            if args.continue_preparation:
                from novel_core.taskio import continue_preparation
                return continue_preparation(db,args.project,task)
            if args.brief:
                return {**engine.status(db,brief=True), **{k:res[k] for k in ('accepted','idempotent','task_id','review_recheck_required','fast_committed','chapter','settlement','numeric_correction') if k in res}}
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
            failed=_auto_render_html(args.project, db)
            return {**res,**({'html_render_error':failed} if failed else {})}
        if cmd=='audit': return engine.audit(db)
        if cmd=='pause': return engine.pause(db,args.reason,args.cancel_task)
        if cmd=='resume': return engine.resume(db)
        if cmd=='complete':
            with transaction(db): return engine.complete_inner(db)
        if cmd=='doctor':
            from novel_core.contextflow import usage, SCAN_UNITS, DIRECT_UNITS, EXACT_UNITS, REDUCE_INPUTS, SESSION_PROTOCOL
            from novel_core.reviewflow import PROTOCOL
            import platform
            report={'skill_root':str(Path(__file__).resolve().parents[1]),'skill_version':VERSION,'db_version':get(db,'version'),
                    'skill_realpath':str(Path(__file__).resolve().parents[1]),
                    'entry_path':str(Path(__file__).absolute()),
                    'entry_realpath':str(Path(__file__).resolve()),
                    'runtime_code_editing':'unsupported; repair story or plan through task receipts',
                    'evidence_input':EVIDENCE_INPUT,'reading_view':READING_PROTOCOL,
                    'timeline_protocol':TIMELINE_PROTOCOL,'host_token_usage':'external observations only',
                    'context_mode':get(db,'context_mode','bounded'),'preparation_policy':get(db,'preparation_policy','adaptive'),'character_limits_enforced':False,
                    'review_protocol':PROTOCOL,'review_verification':'order_and_evidence_coverage_only',
                    **execution.settings(db),'execution_protocol':execution.PROTOCOL,'execution_upgrade':get(db,'execution_upgrade'),
                    'chapter_batch_protocol':'chapter-batch/1',
                    'candidate_ledger':'drafts_only; canonical_commit_requires_independent_review',
                    'material_structure':{'scan_units_per_page':SCAN_UNITS,'scan_layout':'multi-document/1','direct_material_units':DIRECT_UNITS,
                                          'exact_record_units':EXACT_UNITS,'preparation_session':SESSION_PROTOCOL,
                                          'reduction_inputs_per_group':REDUCE_INPUTS,'limits_enforced':False},
                    'explosion_control':'exact source records, same-bundle preparation, task isolation, topic selection, complete semantic-unit pages, source-bound preparation, load-once, actual host handoff',
                    'context_window_verified':False,'usage':usage(db),'python':platform.python_version(),'sqlite':__import__('sqlite3').sqlite_version,'python_path':sys.executable,'file_roundtrip':'database opened','automatic_after_chat_close':'host main session owns continuous scheduling','independent_review':'host_must_verify_fresh_review_workers','independent_review_verified':False}
            swept=engine.sweep_legacy(args.project,apply=False)
            if swept['removed']: report['legacy_litter']=swept['removed']
            if swept['relocated']: report['stray_ledger_files']=swept['relocated']
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
                from novel_core.batchflow import state_for_task
                state=state_for_task(db,branch,args.task)
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
                if row is None and args.task:
                    from novel_core.batchflow import active
                    owner=db.execute("SELECT input,chapter,branch FROM tasks WHERE id=? AND state='pending'",(args.task,)).fetchone()
                    if owner and owner['branch']==branch and json.loads(owner['input']).get('chapter_session',{}).get('role')=='writer' and args.chapter<owner['chapter']:
                        candidate=engine.load_draft(db,branch,args.chapter)
                        if candidate:
                            return {'branch':branch,'number':args.chapter,'hash':__import__('hashlib').sha256(candidate['prose'].encode()).hexdigest(),
                                    'prose':candidate['prose'],'summary':candidate.get('candidate_summary',''),'candidate':True}
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
                    parent=atoms.branch(db,work)['parent']
                    parent_row=db.execute('SELECT status FROM branches WHERE id=?',(parent,)).fetchone() if parent else None
                    # Nested chains fall back to the parent candidate, not the active branch.
                    restore=parent if parent_row and parent_row['status']=='candidate' else get(db,'active_branch')
                    db.execute("UPDATE tasks SET state='cancelled' WHERE state='pending'")
                    db.execute("UPDATE branches SET status='discarded' WHERE id=?",(work,))
                    put(db,'work_branch',restore);put(db,'status','ready');put(db,'pause_reason',None)
                    if get(db,'style_only_branch')==work:put(db,'style_only_branch',None)
                    log(db,'revision_discard',{'branch':work,'restore':restore})
                    return engine.status(db)
                return engine.begin_revision(db,args.from_chapter,args.reason,style_only=args.style_only)
        if cmd=='config':
            effective=execution.settings(db)
            if args.value is None and args.key in effective:return {args.key:effective[args.key]}
            if not args.key: return {**{k:get(db,k) for k in ('context_chars','context_mode','published_through','style_anchor')},'preparation_policy':get(db,'preparation_policy','adaptive'),'chapter_target_han':get(db,'contract').get('chapter_target_han'),'character_views':get(db,'contract').get('character_views','full'),'writer_batch':get(db,'writer_batch',10),'writer_session_policy':get(db,'writer_session_policy','batch'),'chapter_batch_size':get(db,'chapter_batch_size',3),'batch_through':get(db,'batch_through',0),**effective}
            if args.value is None: return {args.key:get(db,'contract').get(args.key,'full' if args.key=='character_views' else None) if args.key in ('chapter_target_han','character_views') else get(db,args.key,{'preparation_policy':'adaptive','writer_batch':10,'chapter_batch_size':3,'batch_through':0,'revision_chain_limit':2,'writer_session_policy':'batch'}.get(args.key))}
            if args.key=='style_anchor':
                try:
                    from novel_core.store import validate_style
                    value=validate_style(json.loads(args.value))|{'source':'author'}
                except ValueError:
                    require(False,'config','style_anchor 需为合法 JSON 对象 {"traits":[...],"sample":"..."}')
            else:
                value=args.value if args.key in ('context_mode','character_views','preparation_policy','writer_session_policy','execution_mode') else int(args.value)
            if args.key=='execution_mode':require(value in execution.MODES,'config','无人值守仅支持 single_chapter；旧项目自动升级，不支持回切批次')
            if args.key in execution.LEGACY_OPTIONS and effective['execution_mode']==execution.SINGLE:
                require(value==effective[args.key],'execution_mode','无人值守统一固定为单章写审；旧批次参数只允许查询或原值确认，不支持回切')
            if args.key=='review_chunk':require(1<=value<=5,'config','review_chunk 为 1~5 章的分段审稿粒度;覆盖范围不变,只调成本')
            if args.key=='chapter_batch_size':require(1<=value<=5,'config','chapter_batch_size 固定为 1；旧范围只用于冻结任务兼容')
            if args.key=='batch_through':
                require(value>=0,'config','batch_through 为授权终止章号；0 清除范围')
                from novel_core.batchflow import active
                batch=active(db)
                require(not batch or not value or value>=batch['last'],'chapter_range','已有候选批次超出新范围，须先处理已保存正文')
            if args.key=='writer_session_policy':require(value in ('batch','chapter'),'config','writer_session_policy 固定为 chapter；batch 仅为旧冻结任务字段')
            if args.key=='writer_batch':require(1<=value<=10,'config','writer_batch 固定为 1；旧范围只用于冻结任务兼容')
            if args.key=='revision_chain_limit':require(1<=value<=5,'config','revision_chain_limit 为 1~5 的修订链嵌套深度上限；调整即作者决策，自主会话不得代替作者放宽')
            if args.key=='chapter_target_han':
                from novel_core.budget import validate_chapter_target
                validate_chapter_target(value)
            if args.key=='context_mode': require(value in ('bounded','legacy'),'config','context_mode 为 bounded/legacy；legacy 仅用于复现旧协议')
            if args.key=='preparation_policy':require(value in ('adaptive','semantic'),'config','preparation_policy 为 adaptive/semantic；semantic 保留全量模型提炼用于对照')
            if args.key=='character_views':require(value in ('full','scoped'),'config','character_views 为 full/scoped')
            if args.key=='published_through': require(get(db,'published_through',0)<=value<=engine.head(db,get(db,'active_branch')),'config','发布边界只能前进至已验收章号')
            with transaction(db):
                require(not db.execute("SELECT 1 FROM tasks WHERE state='pending'").fetchone(),'pending','修改配置前先完成或取消当前任务')
                if args.key=='execution_mode' and value!=effective['execution_mode']:
                    from novel_core.batchflow import active
                    branch=get(db,'work_branch')
                    require(not active(db,branch) and not engine.load_draft(db,branch,head(db,branch)+1),
                            'execution_boundary','切换执行模式须先完成当前候选批次和未验收正文，不能跳过独立审稿')
                if args.key in ('chapter_target_han','character_views'):
                    contract=get(db,'contract');contract[args.key]=value;put(db,'contract',contract)
                else:put(db,args.key,value)
                log(db,'config',{'key':args.key,'value':value})
            result={args.key:value}
            if args.key.startswith('context_') and args.key!='context_mode':result['deprecated']='字符配置仅兼容旧项目，不形成内容或读取限制；分页按完整语义单元。'
            return result
        if cmd=='recontract':
            from novel_core import recontract
            with transaction(db):
                res=recontract.apply(db,read(args.contract),args.reason)
            failed=_auto_render_html(args.project)
            return {**res,**({'html_render_error':failed} if failed else {})}
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
                from novel_core.batchflow import active, key
                batch=active(db,branch)
                if batch:
                    put(db,'chapter_batch_archive:'+batch['id'],{**batch,'cancelled_by':'replan','reason':args.reason})
                    put(db,key(branch),None)
                put(db,'status','ready');log(db,'replan',{'branch':branch,'reason':args.reason,'scope':args.scope})
            return engine.status(db)
        if cmd=='html':
            from novel_core.bookpage import render, save
            target,inside,route=_output_target(args.project,args.out,'book.html')
            page_branch=args.branch or get(db,'active_branch')
            page=render(db,page_branch)
            save(target,page)
            return {'file':route,'branch':page_branch,'chars':len(page),
                    'inside_project':inside}
        if cmd=='export':
            root,inside,_=_output_target(args.project,args.out,'export')
            root.mkdir(parents=True,exist_ok=True)
            branch=get(db,'active_branch'); manifest=[]
            # A manifest identifies the current export; old unrelated files are not deleted.
            for row in chapters(db,branch):
                filename=f'{row["number"]:04d}.md'
                path=root/filename
                temp=path.with_suffix('.tmp');temp.write_text(row['prose']+'\n',encoding='utf-8');temp.replace(path)
                manifest.append({'chapter':row['number'],'file':filename,'hash':row['hash']})
            write_json(root/'manifest.json',{'branch':branch,'title':get(db,'contract')['title'],'chapters':manifest,'status':get(db,'status')})
            return {'directory':str(root),'chapters':len(manifest),'status':get(db,'status'),
                    'inside_project':inside}
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
    except (Error,OSError,ValueError,KeyError,TypeError,sqlite3.Error) as exc:
        # stdout + nonzero exit: hosts parse one clean JSON stream, and shells
        # that paint stderr as error records (pwsh NativeCommandError) no longer
        # bury the answer the worker needs. sqlite3.Error is not an OSError
        # subclass; lock contention and corrupt files must stay JSON too.
        print(json.dumps({'ok':False,'code':getattr(exc,'code','database' if isinstance(exc,sqlite3.Error) else type(exc).__name__),'message':str(exc),'details':getattr(exc,'details',None)},ensure_ascii=False))
        return 2

if __name__=='__main__':
    sys.exit(main())
