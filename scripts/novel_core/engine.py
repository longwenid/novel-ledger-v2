"""Idempotent editorial state machine and isolated retrospective revisions."""
from __future__ import annotations
import copy
import json
import re
import time
import uuid
from pathlib import Path
from .store import Error, require, dump, digest, get, put, transaction, log, chapters, head, branch_hash, write_json
from .ledger import replay, append_events, evidence
from .memory import pack

FACT_CHECKS = ('coverage', 'continuity', 'resources', 'knowledge', 'time', 'world_rules')
READER_CHECKS = ('read_on', 'character', 'change', 'emotion_cost', 'language', 'repetition')
LONG_CHECKS = ('causality', 'character_arcs', 'payoffs', 'pacing', 'world_rules')
# A clock advance must be grounded: its evidence quote must itself be a time
# statement. Single chars are a strict core (avoiding surname/verb collisions
# like 周/分); multi-char tokens cover the rest. A floor, not a parser.
TIME_CHARS = ('日', '天', '夜', '晨', '早', '晚', '午', '时', '年', '月',
              '春', '夏', '秋', '冬', '晓', '暮', '昏', '宵', '旬', '翌', '昨', '今', '明')
TIME_TOKENS = ('星期', '一周', '下周', '上周', '本周', '小时', '分钟', '秒钟', '时钟', '刻钟',
               '次日', '当日', '当天', '隔日', '今日', '昨日', '明日', '翌日', '深夜', '半夜',
               '午夜', '黄昏', '黎明', '清晨', '傍晚', '正午', '上午', '下午', '上周', '周后')


def load_draft(db, branch, n):
    row = db.execute('SELECT data FROM drafts WHERE branch=? AND number=?', (branch,n)).fetchone()
    return json.loads(row[0]) if row else None


def save_draft(db, branch, n, data):
    db.execute('INSERT OR REPLACE INTO drafts VALUES (?,?,?)', (branch,n,dump(data)))


def status(db):
    from .budget import overview
    from .store import VERSION
    active, work = get(db,'active_branch'), get(db,'work_branch')
    task = db.execute("SELECT id,stage,chapter,branch,lease_until FROM tasks WHERE state='pending'").fetchone()
    return {'schema':'novel-ledger-v2.status/1', 'status':get(db,'status'), 'active_branch':active,
            'work_branch':work,'committed':head(db,active), 'work_committed':head(db,work),
            'pilot_approved':get(db,'pilot_approved'), 'task':dict(task) if task else None,
            'session_mode':get(db,'session_mode'), 'reason':get(db,'pause_reason'),
            'runner':get(db,'runner'), 'context_chars':get(db,'context_chars'),
            'skill_version':VERSION, 'db_version':get(db,'version'),
            'context_usage':{'tasks':get(db,'usage_tasks',0),'session_handoffs':get(db,'usage_handoffs',0),
                             'input_chars':get(db,'usage_input_chars',0),'result_chars':get(db,'usage_result_chars',0),
                             'read_chars':get(db,'usage_read_chars',0)},
            'volume_budget':overview(db,work)}


def approve(db):
    with transaction(db):
        require(head(db,get(db,'active_branch')) >= 1, 'pilot', '须先产出并审稿第一章，再由用户定调')
        if get(db,'style_anchor') is None:
            # The approved pilot is the concrete voice reference; capture it once.
            row = db.execute('SELECT prose FROM chapters WHERE branch=? AND number=1',(get(db,'active_branch'),)).fetchone()
            if row:
                prose = row[0]
                put(db,'style_anchor',{'source':'pilot','traits':[],'sample':prose[:800],'sample_close':prose[-400:]})
                log(db,'style_anchor_captured',{'from':'pilot','sample_chars':min(800,len(prose)),'close_chars':min(400,len(prose))})
        put(db,'pilot_approved',True)
        log(db,'pilot_approved',{})
    return status(db)


def choose(db):
    branch = get(db,'work_branch')
    n = head(db,branch)
    from . import design
    if design.enabled(db) and not design.usable(db,branch,'world'):
        return design.job(db,branch,'world',n+1)
    draft = load_draft(db,branch,n+1)
    if draft:
        stage = draft['stage']
        payload = {k:draft[k] for k in ('prose','findings','attempts','repair_scope') if k in draft}
        if stage == 'reader':
            from .characters import enabled
            checks = READER_CHECKS+(('dialogue_identity',) if enabled(db) else ())+(('voice_match',) if get(db,'style_anchor') else ())
            payload = {'prose':draft['prose'], 'checks':checks}
        elif stage == 'facts':
            state=replay(db,branch)
            extra=()
            if state.get('anchors'):extra+=('stable_facts',)
            planrow=db.execute('SELECT data FROM plans WHERE branch=? AND number=?',(branch,n+1)).fetchone()
            if state.get('places') or (planrow and json.loads(planrow[0]).get('scenes')):extra+=('spatial',)
            from .characters import enabled
            if enabled(db):extra+=('character_consistency',)
            payload['checks'] = FACT_CHECKS+extra
            prior=prior_events(db,branch,n+1)
            if prior:
                prose=draft['prose']
                def resolvable(ev):
                    # An event can be mechanically carried when every evidence
                    # quote still pins exactly one location in the new prose.
                    if not isinstance(ev,dict):return False
                    for key in ('evidence','change_evidence'):
                        ref=ev.get(key)
                        if isinstance(ref,dict):
                            q=ref.get('quote')
                            if not (isinstance(q,str) and q and prose.count(q)==1):return False
                        elif key=='evidence':return False
                    return True
                payload['prior_events']=prior['events']
                payload['prior_events_source']=prior['source']
                payload['auto_carry']=[i for i,ev in enumerate(prior['events']) if resolvable(ev)]
                payload['prior_events_note']='本章为返修/重设计后的重新事实登记:auto_carry 中的前序事件由引擎自动续算(证据仍唯一命中新正文),不要在 events 中重复提交;其余前序事件须在 prior_dispositions 中逐条声明 dropped/replaced 并说明正文如何变化;也可用同样方式覆盖自动续算。events 只提交新增或变更的事件。'
        return stage,n+1,payload
    if n:
        if n==1 and not get(db,'pilot_approved') and get(db,'work_branch')==get(db,'active_branch'):
            return 'await_author',n,{'reason':'第一章已通过内部审稿，等待作者定调；认可后执行 approve。'}
        # Review every full text in chunks, then inspect cross-chunk causal chains.
        if n % 10 == 0 and not reviewed(db,branch,'batch',n):
            return review_job(db,branch,'batch',max(1,n-9),n)
        plan = json.loads(db.execute('SELECT data FROM plans WHERE branch=? AND number=?',(branch,n)).fetchone()[0])
        if plan.get('volume_end') and not reviewed(db,branch,'volume',n):
            first = next(c['number'] for c in chapters(db,branch) if json.loads(c['data'])['volume']==plan['volume'])
            return review_job(db,branch,'volume',first,n)
        revision = db.execute('SELECT * FROM branches WHERE id=?',(branch,)).fetchone()
        if revision['status']=='candidate' and n >= revision['through']:
            if not reviewed(db,branch,'revision',n):
                return review_job(db,branch,'revision',revision['start'],n)
            return 'promote',n,{}
        if plan.get('book_end'):
            if not reviewed(db,branch,'book',n):
                return review_job(db,branch,'book',1,n)
            return 'complete',n,{}
        if n==1 and not get(db,'pilot_approved'):
            return 'await_author',n,{'reason':'第一章已通过内部审稿，等待用户试章定调；收到认可后执行 approve。'}
    if design.enabled(db):
        volume=design.volume_for(db,branch,n+1)
        if not design.usable(db,branch,volume):return design.job(db,branch,volume,n+1)
    next_plan = db.execute('SELECT data FROM plans WHERE branch=? AND number=?',(branch,n+1)).fetchone()
    if not next_plan:
        return 'plan',n+1,{'window':[n+1,n+10], 'instruction':'细化未来 5～10 章；临近结局可少于 5 章。重要转折比较两个实质不同方案。'}
    from .characters import next_job
    character_job=next_job(db,branch,n+1,json.loads(next_plan[0]))
    if character_job:return character_job
    payload = {}
    rev = db.execute('SELECT * FROM branches WHERE id=?',(branch,)).fetchone()
    if rev['status']=='candidate':
        ancestor=rev['parent']
        original=None
        while ancestor and original is None:
            original=db.execute('SELECT prose FROM chapters WHERE branch=? AND number=?',(ancestor,n+1)).fetchone()
            ancestor=db.execute('SELECT parent FROM branches WHERE id=?',(ancestor,)).fetchone()[0]
        payload = {'original_prose':original[0] if original else '', 'revision_reason':rev['reason'], 'instruction':'检查修订传播。表达类发现(重复/收束/节奏/声线)优先在被点名章用 patches 定点修改;未受影响的章提交 reuse_original——与原章逐字相同且事件可复算时直接复验通过,不再派发审稿。事实/因果类发现才需要改正文并重登记事件。'}
    return 'draft',n+1,payload


def review_hash(db,branch,first,through):
    return digest({'contract':get(db,'contract'), 'chapters':[(r['number'],r['hash']) for r in db.execute('SELECT number,hash FROM chapters WHERE branch=? AND number BETWEEN ? AND ? ORDER BY number',(branch,first,through))]})


def reviewed(db,branch,scope,n):
    row = db.execute('SELECT * FROM reviews WHERE branch=? AND scope=? AND through=?',(branch,scope,n)).fetchone()
    if not row:
        return False
    data = json.loads(row['data'])
    return row['input_hash']==review_hash(db,branch,data['first'],n)


def review_job(db,branch,scope,first,n):
    from .characters import enabled
    checks=LONG_CHECKS+(('voice_evolution',) if enabled(db) else ())
    # Coverage requires every chapter reviewed with version binding; chunk size is
    # a cost dial (default one chapter, up to five) — not a coverage change.
    scopekey = scope + '_chunk'
    step=max(1,min(5,int(get(db,'review_chunk',1) or 1)))
    chapter=first
    while chapter<=n:
        end=min(chapter+step-1,n)
        if not reviewed(db,branch,scopekey,end):
            rows=db.execute('SELECT number,prose FROM chapters WHERE branch=? AND number BETWEEN ? AND ? ORDER BY number',(branch,chapter,end)).fetchall()
            prose='\n\n'.join(f"【第{r['number']}章】\n{r['prose']}" for r in rows)
            return scope+'_review',n,{'scope':scopekey,'first':chapter,'through':end,
                                    'prose':prose,'prose_hash':digest(prose),'checks':checks}
        chapter=end+1
    receipts = [{'chapter':r['through'],'input_hash':r['input_hash']} for r in db.execute('SELECT through,input_hash FROM reviews WHERE branch=? AND scope=? AND through BETWEEN ? AND ? ORDER BY through',(branch,scopekey,first,n))]
    return scope+'_review',n,{'scope':scope,'first':first,'through':n,'coverage_receipts':{'count':len(receipts),'first':first,'through':n,'scope':scopekey,'hash':digest(receipts)},
                             'instruction':'分段全文已审；按 recall 分页核对主线、人物弧、跨章重复用语和承诺的跨段证据，不能把本任务称为一次性全书通读。发现须分流:表达类(重复/收束/节奏/声线)在 finding 上加 repair_route:"patch" 并指向可定位的单章证据——修订时只定点改被点名章,其余章原文复用免复验;事实/因果/认知/时间类标 revise 或省略,从最早受影响章修订。',
                             'checks':checks + ('repetition',) + (('ending','length','core_promises') if scope=='book' else ())}


def work_rel(task):
    """Project-relative work directory: chapter-grouped, stage-tagged, stable."""
    return (Path('work')/f"ch{task['chapter']:04d}"/f"{task['stage']}-{task['id'][:8]}").as_posix()


def prune_work(db, project, keep=3):
    """Remove work directories of finished tasks; inputs and receipts stay in SQLite.

    Never touches directories whose task.json is unreadable, is an attempt view,
    or whose task is still pending/unknown — work files are derived views, the
    database is the single source of truth."""
    import shutil
    work = Path(project).resolve()/'work'
    if not work.is_dir():
        return []
    candidates = []
    for task_json in work.rglob('task.json'):
        try:
            info = json.loads(task_json.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if not isinstance(info, dict) or info.get('attempt') is not None or not info.get('task_id'):
            continue
        row = db.execute('SELECT state FROM tasks WHERE id=?', (info['task_id'],)).fetchone()
        if row and row[0] in ('done', 'cancelled'):
            candidates.append(task_json.parent)
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    removed = []
    for folder in candidates[keep:]:
        shutil.rmtree(folder, ignore_errors=True)
        removed.append(folder.name)
    for folder in list(work.iterdir()):
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
    return removed


def task_files(project, task):
    root = Path(project).resolve()/work_rel(task)
    root.mkdir(parents=True,exist_ok=True)
    work = json.loads(task['input'])
    write_json(root/'input.json',work,compact=True)
    base = work_rel(task)
    result = {'schema':'novel-ledger-v2.task/1','task_id':task['id'],'stage':task['stage'],
              'chapter':task['chapter'],'branch':task['branch'],'input_hash':task['input_hash'],
              'project':str(Path(project).resolve()),
              'input_path':(base+'/input.json'),'output_path':(base+'/result.json'),
              'path_base':'routes are relative to project; join with project before reading/writing',
              'state':'pending'}
    if 'writer_brief' in work:
        from .design import render_brief
        brief=root/'brief.md'
        brief.write_text(render_brief(work),encoding='utf-8')
        result['brief_path']=base+'/brief.md'
    write_json(root/'task.json',result)
    return result


def next_task(db,project):
    created=False
    with transaction(db):
        if get(db,'status') in ('paused','completed'):
            return status(db)
        pending = db.execute("SELECT * FROM tasks WHERE state='pending'").fetchone()
        if pending:
            from .contextflow import enabled,limits,wire
            old_work=json.loads(pending['input'])
            if enabled(db) and not ({'context_capacity','context_budget'} & set(old_work)) and len(wire(old_work))>limits(db)['input']:
                require(not pending['lease_owner'] or (pending['lease_until'] or 0)<=time.time(),'lease','旧工作包仍有活动租约，先等待原 worker 退出')
                db.execute("UPDATE tasks SET state='cancelled' WHERE id=?",(pending['id'],))
                log(db,'context_legacy_repack',{'old_task':pending['id'],'reason':'旧工作包超过声明容量的工作包锚点，保留原任务并重新准备'})
                pending=None
        if pending:
            task = dict(pending)
            from .contextflow import session_checkpoint,charge_input
            work=json.loads(task['input'])
            checkpoint=session_checkpoint(db,work,task['id'])
            if checkpoint:return checkpoint
            charge_input(db,task['id'],work)
        else:
            stage,n,payload = choose(db)
            if stage=='await_author':
                return {'stage':stage, **payload}
            if stage=='promote':
                promote(db)
                return {'stage':'revision_promoted', **status(db)}
            if stage=='complete':
                return complete_inner(db)
            branch = get(db,'work_branch')
            from .patches import prepare
            payload=prepare(db,stage,payload)
            work = pack(db,branch,n,stage,payload)
            from .contextflow import session_checkpoint
            checkpoint=session_checkpoint(db,work)
            if checkpoint:return checkpoint
            stage=work['stage']
            tid = uuid.uuid4().hex
            fingerprint = digest(work)
            task = {'id':tid,'branch':branch,'stage':stage,'chapter':n,'input_hash':fingerprint,'input':dump(work)}
            db.execute('INSERT INTO tasks(id,branch,stage,chapter,input_hash,input,state) VALUES (?,?,?,?,?,?,?)',(tid,branch,stage,n,fingerprint,dump(work),'pending'))
            from .contextflow import charge_input
            charge_input(db,tid,work)
            put(db,'usage_tasks',get(db,'usage_tasks',0)+1)
            created=True
            log(db,'dispatch',{'task':tid,'stage':stage,'chapter':n,'branch':branch})
    if created:
        # Consolidate output: finished task directories are derived views; keep the
        # newest few for debugging and let `tidy` remove the rest on demand.
        removed=prune_work(db,project)
        if removed:
            with transaction(db):log(db,'work_tidy',{'removed':removed,'kept':3})
    return task_files(project,task)


def parse_prose(data):
    prose = data.get('prose')
    require(isinstance(prose,str) and prose.strip(), 'prose', '必须提交非空正文 prose')
    require('\x00' not in prose, 'prose', '正文包含无效字符')
    return prose


def nonempty_scene(scene,state):
    location=scene.get('place')
    return ((location in state.get('places',{}) if isinstance(location,str) else False) or (location is None and isinstance(scene.get('label'),str) and bool(scene['label'].strip()))) and isinstance(scene.get('present',[]),list) and all(e in state['entities'] for e in scene.get('present',[]))


def validate_plan(db,branch,start,data):
    plans = data.get('chapters')
    require(isinstance(plans,list) and 1<=len(plans)<=10, 'plan','每批计划为 1～10 章，通常 5～10 章')
    volumes = [v['id'] for v in get(db,'contract')['volumes']]
    from . import design
    layered=design.enabled(db)
    expected_volume=design.volume_for(db,branch,start) if layered else None
    state = replay(db,branch)
    last_volume = None
    prior = db.execute('SELECT data FROM plans WHERE branch=? AND number=?',(branch,start-1)).fetchone()
    if prior:
        last_volume = json.loads(prior[0])['volume']
    for i,p in enumerate(plans,start):
        require(isinstance(p,dict) and p.get('number')==i, 'plan', '章号必须连续且从任务章号开始')
        require(all(p.get(k) for k in ('goal','conflict','change','volume')) and p['volume'] in volumes, 'plan','章计划缺少目标、阻力、变化或合法卷 ID')
        require(isinstance(p.get('entities'),list) and p['entities'] and all(e in state['entities'] for e in p['entities']), 'plan','entities 应引用已登记稳定实体 ID；新人物在正文事实阶段登记')
        scenes=p.get('scenes',[])
        require(isinstance(scenes,list) and all(isinstance(s,dict) for s in scenes),'scene','scenes 须为场景对象数组')
        for scene in scenes:
            require(nonempty_scene(scene,state),'scene','场景须引用已登记 place 或给出未确立新场所的 label；present 使用实体 ID')
        require(all(type(n) is int and 1<=n<start for n in p.get('dependencies',[])), 'plan','历史依赖须为已提交章号')
        require(not last_volume or volumes.index(p['volume'])>=volumes.index(last_volume), 'plan','卷序不能倒退')
        spans=[]
        for scene in scenes:
            hint=scene.get('time_hint') if isinstance(scene,dict) else None
            if isinstance(hint,str):spans+=[int(m) for m in re.findall(r'D\+(\d+)',hint)]
        if spans:
            # Day schedule: D+N in scene time_hints is measured in clock units from
            # this batch's anchor (the clock when the batch is signed).
            p['day_anchor']=state['clock'];p['day_span']=[min(spans),max(spans)]
        if last_volume and last_volume!=p['volume']:
            prev = plans[i-start-1] if i>start else json.loads(prior[0])
            require(prev.get('volume_end'), 'plan','跨卷前须标明卷末复核')
        if p.get('major_turn'):
            alternatives = p.get('alternatives',[])
            require(isinstance(alternatives,list) and all(isinstance(a,str) and a.strip() for a in alternatives) and len(alternatives)>=2 and len(set(alternatives))==len(alternatives) and p.get('selection_reason'), 'plan','重要转折需两个不同方案和择优依据')
        if p.get('book_end'):
            branch_meta=db.execute('SELECT status,through FROM branches WHERE id=?',(branch,)).fetchone()
            require(branch_meta['status']!='candidate' or i>=branch_meta['through'],'revision','候选修订不能提前结束而跳过原有后文复核')
            require(i==start+len(plans)-1 and p['volume']==volumes[-1] and p.get('volume_end'), 'plan','终章须是末卷最后一章并触发卷审')
        from .characters import enabled,validate_scenes
        if enabled(db):validate_scenes(p,state)
        if layered:
            require(not p.get('volume_end') or i==start+len(plans)-1,'plan','分层计划的卷末必须是本批最后一章，不能在卷审后继续同卷')
            require(p['volume']==expected_volume and design.usable(db,branch,expected_volume),'design','每批仅细化已准备的当前卷，跨卷前先完成分卷设计')
            world=design.current(db,branch,'world');volume=design.current(db,branch,expected_volume)
            known=set(volume['data']['location_ids'])
            design.validate_brief(p,state,known,volume['data'])
            # 章末收束类别在近三章窗口内强制轮换:同模具收束(如连续以同一物件静收)
            # 是审稿反复点名的同质化,在计划层拦住比卷末返工便宜得多。
            recent=set()
            for row in db.execute('SELECT data FROM plans WHERE branch=? AND number>=? AND number<?',(branch,max(1,i-3),i)):
                category=design.closing_category(json.loads(row['data']).get('ending_shape'))
                if category:recent.add(category)
            category=design.closing_category(p.get('ending_shape'))
            require(category not in recent,'plan',f'收束类别「{category}」与近三章重复;章末收束须轮换形态,同类别至少隔三章')
            # Major turns must cite which spine touchpoints they advance when a
            # spine exists; older spineless world designs are grandfathered.
            spine=design.spine_ids(db,branch)
            refs=p.get('spine_refs',[])
            require(isinstance(refs,list) and all(isinstance(r,str) for r in refs),'plan','spine_refs 为脊柱触点 ID 数组')
            if spine is not None:
                if p.get('major_turn'):require(refs,'plan','重大转折须引用 event_spine 触点（spine_refs），不能脱离全书结构单独成立')
                for r in refs:
                    require(r in spine,'plan',f'spine_refs 引用了不存在的脊柱触点：{r}')
            p['_design_versions']={'world':world['hash'],'volume':volume['hash']}
        db.execute('INSERT INTO plans VALUES (?,?,?)',(branch,i,dump(p)))
        last_volume=p['volume']


def validate_review(data,checks,sources):
    from .patches import validate_scope
    validate_scope(data)
    require(data.get('verdict') in ('pass','fix'), 'review','审稿结论只能 pass/fix')
    rows = data.get('checks')
    require(isinstance(rows,list) and all(isinstance(r,dict) for r in rows) and {r.get('id') for r in rows}==set(checks) and len(rows)==len(checks), 'review','审稿检查项不完整或重复')
    for row in rows:
        require(row.get('result') in ('pass','fix') and isinstance(row.get('reason'),str) and row['reason'].strip(), 'review','每项需要结论及具体理由')
        refs = row.get('evidence',[])
        require(isinstance(refs,list) and all(isinstance(ref,dict) for ref in refs),'review','evidence 必须为对象数组')
        mandatory={'coverage','continuity','read_on','character','change','language','causality','character_arcs','payoffs','pacing','ending','length','core_promises','spatial','stable_facts','character_consistency','voice_evolution','voice_match'}
        exception=row.get('not_applicable')
        require(bool(refs) or (row['id'] not in mandatory and isinstance(exception,str) and exception.strip()), 'review','核心审稿项必须有正文证据；可选项确不适用须说明具体原因')
        for item in refs:
            require(item.get('chapter') in sources,'review','证据不属于本次有效审稿范围')
            evidence(sources[item['chapter']],item,item['chapter'])
    findings = data.get('findings',[])
    require(isinstance(findings,list) and all(isinstance(f,dict) for f in findings), 'review','findings 必须为数组')
    for item in findings:
        require(item.get('impact') and item.get('goal') and item.get('severity') in ('blocker','quality','note'), 'finding','发现须含读者影响、修订目标和级别')
        ref=item.get('evidence',{})
        require(ref.get('chapter') in sources,'finding','发现须定位具体正文')
        evidence(sources[ref['chapter']],ref,ref['chapter'])
    needs_fix = any(r['result']=='fix' for r in rows) or any(f['severity'] in ('blocker','quality') for f in findings)
    require((data['verdict']=='fix')==needs_fix,'review','结论与检查/发现不一致')
    require(not needs_fix or any(f['severity'] in ('blocker','quality') for f in findings),'review','返工须提供可执行发现')


def repair(db,branch,n,draft,findings,scope='chapter'):
    attempts = draft.get('attempts',0)+1
    draft.update(attempts=attempts, findings=findings,repair_scope=scope)
    # Preserve every candidate in the task log/receipt. Last good prose stays available.
    draft['stage']='repair' if attempts<=2 else 'redesign'
    if attempts>3:
        put(db,'status','paused')
        put(db,'pause_reason',{'code':'quality_exhausted','chapter':n,'findings':findings})
        draft['stage']='redesign'
    draft.pop('facts_review',None)
    draft.pop('reader_review',None)
    save_draft(db,branch,n,draft)


def commit_draft(db,branch,n,draft):
    state=replay(db,branch)
    plan=json.loads(db.execute('SELECT data FROM plans WHERE branch=? AND number=?',(branch,n)).fetchone()[0])
    require(n==head(db,branch)+1,'sequence','不能跳章提交')
    stored={k:draft[k] for k in ('events','summary','facts_review','reader_review')}
    from .budget import han_count
    stored.update(volume=plan['volume'],prose_hash=digest(draft['prose']),han_characters=han_count(draft['prose']))
    db.execute('INSERT INTO chapters VALUES (?,?,?,?,?)',(branch,n,draft['prose'],dump(stored),digest(draft['prose'])))
    append_events(db,branch,n,draft['prose'],draft['events'],state)
    db.execute('DELETE FROM drafts WHERE branch=? AND number=?',(branch,n))
    log(db,'commit',{'branch':branch,'chapter':n,'hash':digest(draft['prose'])})


def prior_events(db, branch, n):
    """Previously registered events for chapter n: the newest facts receipt on
    this branch (repair loop), else the nearest ancestor's committed chain
    (revision re-drafts). Baseline for re-registration accounting."""
    for row in db.execute("SELECT receipt FROM tasks WHERE branch=? AND chapter=? AND stage='facts' AND receipt IS NOT NULL ORDER BY rowid DESC", (branch, n)):
        events = json.loads(row[0]).get('data', {}).get('events')
        if isinstance(events, list) and events:
            return {'events': events, 'source': 'repair'}
    ancestor = db.execute('SELECT parent FROM branches WHERE id=?', (branch,)).fetchone()
    while ancestor and ancestor[0]:
        rows = db.execute('SELECT data FROM events WHERE branch=? AND chapter=? ORDER BY idx', (ancestor[0], n)).fetchall()
        if rows:
            return {'events': [json.loads(r[0]) for r in rows], 'source': 'revision'}
        ancestor = db.execute('SELECT parent FROM branches WHERE id=?', (ancestor[0],)).fetchone()
    return None


ENVELOPE_KEYS = {'schema', 'task_id', 'input_hash', 'session', 'lease_owner', 'data'}


def normalize_receipt(db, result):
    """Structural-only repair of worker receipts into result/1 envelopes.

    Workers frequently emit stage payloads at the top level, omit task_id or
    mistype it by one character. The single-pending-task invariant (unique
    partial index) makes retargeting unambiguous; content is never rewritten
    and every repair is logged for audit. Semantics stay strict afterwards."""
    if not isinstance(result, dict):
        return result
    claimed = result.get('task_id')
    known = isinstance(claimed, str) and bool(db.execute('SELECT 1 FROM tasks WHERE id=?', (claimed,)).fetchone())
    needs_identity = not known
    needs_schema = result.get('schema') != 'novel-ledger-v2.result/1'
    needs_nesting = not isinstance(result.get('data'), dict)
    if not (needs_identity or needs_schema or needs_nesting):
        return result
    pending = db.execute("SELECT * FROM tasks WHERE state='pending'").fetchone()
    if pending is not None:
        target = pending
    elif claimed:
        return result  # explicit but unknown id with nothing in flight: precise task error
    else:
        # A raw receipt retrying after completion matches its last task; replay is
        # still fenced by the stored result hash, different content stays an error.
        target = db.execute("SELECT * FROM tasks ORDER BY rowid DESC LIMIT 1").fetchone()
    if target is None:
        return result  # no retarget target; submit raises the precise task error
    task_id = target['id'] if needs_identity else claimed
    data = result.get('data')
    if not isinstance(data, dict):
        data = {k: v for k, v in result.items() if k not in ENVELOPE_KEYS}
        if not data:
            return result
    normalized = {'schema': 'novel-ledger-v2.result/1', 'task_id': task_id,
                  'input_hash': result.get('input_hash') if result.get('input_hash') is not None else target['input_hash'],
                  'data': data}
    for extra in ('session', 'lease_owner'):
        if extra in result:
            normalized[extra] = result[extra]
    with transaction(db):
        log(db, 'receipt_normalized', {'task': task_id,
                                       'fixed': {'schema': needs_schema, 'task_id': claimed != task_id,
                                                 'input_hash': result.get('input_hash') is None, 'data_nested': 'data' not in result}})
    return normalized


def submit(db, result):
    result = normalize_receipt(db, result)
    require(isinstance(result,dict) and result.get('schema')=='novel-ledger-v2.result/1','schema','结果协议版本无效')
    hashed=digest(result)
    metered=db.execute('SELECT * FROM tasks WHERE id=?',(result.get('task_id'),)).fetchone()
    if metered and metered['state']=='pending':
        from .contextflow import charge_result
        charge_result(db,metered,json.loads(metered['input']),result)
    with transaction(db):
        task=db.execute('SELECT * FROM tasks WHERE id=?',(result.get('task_id'),)).fetchone()
        require(task,'task','任务不存在')
        if task['state']=='done':
            require(task['result_hash']==hashed,'replay_conflict','同一任务不能提交不同结果')
            return {'idempotent':True,'task_id':task['id']}
        require(task['state']=='pending' and get(db,'status')!='paused','stale_task','任务失效或运行已暂停')
        require(task['input_hash']==result.get('input_hash'),'input_version','输入版本不匹配')
        if task['lease_owner']:
            require(result.get('lease_owner')==task['lease_owner'] and task['lease_until']>time.time(),'lease','任务租约失效')
        from .contextflow import enabled as bounded_context
        if get(db,'session_mode')=='fresh' and not bounded_context(db):
            session=result.get('session',{})
            require(session.get('origin')=='empty' and session.get('id'),'session','fresh 模式需要宿主提供独立会话回执')
            prior=db.execute('SELECT task FROM sessions WHERE id=?',(session['id'],)).fetchone()
            require(not prior,'session_reused','会话已用于其他任务')
            db.execute('INSERT INTO sessions VALUES (?,?)',(session['id'],task['id']))
        data=result.get('data')
        require(isinstance(data,dict),'result','data 必须为对象')
        branch,n,stage=task['branch'],task['chapter'],task['stage']
        require(branch==get(db,'work_branch'),'stale_branch','任务不属于当前工作分支')
        work=json.loads(task['input'])
        from . import contextflow
        draft=load_draft(db,branch,n)
        if 'context_more' in data:
            contextflow.request_more(db,work,data)
        elif stage in ('context_scan','context_reduce'):
            contextflow.accept(db,work,data,task['id'])
        elif stage=='character_plan':
            from .characters import save
            save(db,branch,n,data,work['payload']['entities'],task['id'])
        elif stage in ('world_plan','volume_plan'):
            from .design import save
            save(db,branch,work['payload']['design_scope'],n,data,task['id'])
        elif stage=='plan':
            validate_plan(db,branch,n,data)
        elif stage in ('draft','repair','redesign'):
            require('plan_update' not in data or stage=='redesign','plan_update','plan_update 仅用于 redesign；其他阶段不能静默更改章计划')
            if 'expand_context' in data:
                require(stage=='repair' and work['payload'].get('repair_scope')=='local' and data.get('expand_context') is True and set(data)=={'expand_context','reason'} and isinstance(data.get('reason'),str) and data['reason'].strip(),'repair_expand','只有局部返修可用 expand_context=true 和 reason 请求扩大上下文')
                draft['repair_scope']='chapter';draft['expansion_reason']=data['reason'];save_draft(db,branch,n,draft)
                db.execute("UPDATE tasks SET state='done',result_hash=?,receipt=? WHERE id=?",(hashed,dump(result),task['id']))
                put(db,'seq',get(db,'seq')+1);log(db,'repair_expand',{'task':task['id'],'reason':data['reason']})
                return {'accepted':True,'expanded':True,'task_id':task['id'],**status(db)}
            from .patches import compose
            prose=parse_prose({'prose':compose(db,stage,work,data,draft)})
            rev_row=db.execute('SELECT status,parent FROM branches WHERE id=?',(branch,)).fetchone()
            if stage=='draft' and rev_row['status']=='candidate' and data.get('reuse_original') is True:
                # Unchanged-chapter fast path: bit-identical prose plus events that
                # still replay cleanly against the new upstream state is provably
                # the chapter that already passed both reviews on the parent.
                prow=db.execute('SELECT prose,data FROM chapters WHERE branch=? AND number=?',(rev_row['parent'],n)).fetchone()
                pdata=json.loads(prow['data']) if prow and prow['prose']==prose else None
                if isinstance(pdata,dict) and all(isinstance(pdata.get(k),dict) for k in ('facts_review','reader_review')) and isinstance(pdata.get('events'),list) and isinstance(pdata.get('summary'),str):
                    try:
                        probe=replay(db,branch)
                        from .ledger import apply as ledger_apply
                        for e in pdata['events']:
                            ledger_apply(probe,e,prose,n)   # precondition probe; no writes
                    except Error:
                        pass  # upstream change broke preconditions: real facts pass required
                    else:
                        committed={'prose':prose,'events':pdata['events'],'summary':pdata['summary'],
                                   'facts_review':pdata['facts_review'],'reader_review':pdata['reader_review'],
                                   'attempts':0}
                        commit_draft(db,branch,n,committed)
                        log(db,'fast_commit',{'branch':branch,'chapter':n,'parent':rev_row['parent']})
                        db.execute("UPDATE tasks SET state='done',result_hash=?,receipt=? WHERE id=?",(hashed,dump(result),task['id']))
                        put(db,'seq',get(db,'seq')+1)
                        return {'accepted':True,'fast_committed':True,'chapter':n,**status(db)}
            if stage=='redesign':
                require(data.get('design_change'),'redesign','重写须说明设计改动，不能原样重试')
                if data.get('plan_update'):
                    old_plan=json.loads(db.execute('SELECT data FROM plans WHERE branch=? AND number=?',(branch,n)).fetchone()[0])
                    update=data['plan_update']
                    require(isinstance(update,dict) and set(update)<=set(('goal','conflict','change','entities','topics','hooks','dependencies','scenes','reader_experience','character_choices','expression_space')), 'plan_update','重设计只能调整当前章具体内容，不可更改卷序或终局标记')
                    old_plan.update(update)
                    db.execute('DELETE FROM plans WHERE branch=? AND number=?',(branch,n))
                    validate_plan(db,branch,n,{'chapters':[old_plan]})
            if stage=='repair':
                require(data.get('repair_note') and prose!=draft['prose'],'repair','修订须包含实际修改及说明')
            if not draft:
                draft={'attempts':0}
            for obsolete in ('events','summary','facts_review','reader_review'):draft.pop(obsolete,None)
            draft.update(prose=prose,stage='facts')
            save_draft(db,branch,n,draft)
        elif stage=='facts':
            validate_review(data,work['payload'].get('checks',FACT_CHECKS),{n:draft['prose']})
            from .spatial import check_address_responses
            check_address_responses(work.get('address_signals',[]),data,draft['prose'],n)
            if data['verdict']=='fix':
                repair(db,branch,n,draft,data['findings'],data.get('repair_scope','chapter'))
            else:
                events=data.get('events')
                require(isinstance(events,list) and isinstance(data.get('summary'),str) and data['summary'].strip(),'facts','需 events 和非空摘要')
                require(len(data['summary'])<=1600,'summary','章摘要超过 1600 字符，请提炼并保留正文来源')
                prior=work['payload'].get('prior_events')
                if isinstance(prior,list) and prior:
                    def anchor(ev):
                        clone=dict(ev)
                        for key in ('evidence','change_evidence'):
                            ref=clone.get(key)
                            if isinstance(ref,dict):clone[key]={k:v for k,v in ref.items() if k!='start'}
                        return dump(clone)
                    auto_idx=work['payload'].get('auto_carry')
                    if isinstance(auto_idx,list):
                        # v1.9: the engine carries prior events whose evidence still
                        # uniquely resolves; the receipt only declares the delta.
                        auto={i for i in auto_idx if type(i) is int and 0<=i<len(prior)}
                        entries={}
                        for d in (data.get('prior_dispositions') or []):
                            require(isinstance(d,dict),'event_accounting','prior_dispositions 须为对象数组')
                            idx,disposition=d.get('index'),d.get('disposition')
                            require(type(idx) is int and 0<=idx<len(prior) and idx not in entries,'event_accounting','index 须落在前序事件范围内且不重复')
                            require(disposition in ('kept','dropped','replaced'),'event_accounting','disposition 只能 kept/dropped/replaced')
                            require(disposition=='kept' or (isinstance(d.get('reason'),str) and d['reason'].strip()),'event_accounting','dropped/replaced 须说明正文如何变化')
                            entries[idx]=d
                        def reanchor(ev):
                            clone=copy.deepcopy(ev)
                            for key in ('evidence','change_evidence'):
                                ref=clone.get(key)
                                if isinstance(ref,dict) and isinstance(ref.get('quote'),str):
                                    clone[key]['start']=draft['prose'].find(ref['quote'])
                            return clone
                        carried=[]
                        for i,ev in enumerate(prior):
                            d=entries.get(i)
                            if d and d['disposition'] in ('dropped','replaced'):continue
                            if i in auto:carried.append(reanchor(ev))
                            else:
                                require(d is not None,'event_accounting',f'第 {i} 条前序事件的证据已不在新正文,须在 prior_dispositions 中声明 dropped/replaced')
                        for i,d in entries.items():
                            if d['disposition']=='kept' and i not in auto:
                                require(anchor(prior[i]) in {anchor(e) for e in events},'event_accounting',f'第 {i} 条声明 kept 但未出现在新 events 中;内容有变应标 replaced 并说明')
                        carried_anchors={anchor(c) for c in carried}
                        for e in events:
                            require(anchor(e) not in carried_anchors,'event_accounting','该前序事件已由引擎自动续算,勿重复提交;要变更请用 prior_dispositions 声明 replaced/dropped')
                        events=carried+events
                    else:
                        # Pre-v1.9 in-flight task: full per-item accounting required.
                        dispositions=data.get('prior_dispositions')
                        require(isinstance(dispositions,list) and all(isinstance(d,dict) for d in dispositions),
                                'event_accounting','本章返修过；重报事件须附 prior_dispositions，对 prior_events 逐项交代去向')
                        seen={}
                        for d in dispositions:
                            idx,disposition=d.get('index'),d.get('disposition')
                            require(type(idx) is int and 0<=idx<len(prior) and idx not in seen,'event_accounting','index 须逐一覆盖 0..%d 且不重复'%(len(prior)-1))
                            require(disposition in ('kept','dropped','replaced'),'event_accounting','disposition 只能 kept/dropped/replaced')
                            require(disposition=='kept' or (isinstance(d.get('reason'),str) and d['reason'].strip()),'event_accounting','dropped/replaced 须说明正文如何变化')
                            seen[idx]=d
                        require(len(seen)==len(prior),'event_accounting','必须覆盖全部 %d 条前次事件，不能静默丢弃未受影响的事件'%len(prior))
                        submitted={anchor(e) for e in events}
                        for idx,d in seen.items():
                            if d['disposition']=='kept':
                                require(anchor(prior[idx]) in submitted,'event_accounting','第 %d 条声称 kept 但未出现在新 events 中；内容有变应标 replaced 并说明'%idx)
                for e in events:
                    if e.get('op')=='clock':
                        quote=(e.get('evidence') or {}).get('quote','')
                        require(any(w in quote for w in TIME_TOKENS) or any(w in quote for w in TIME_CHARS),'time_evidence',
                                'clock 事件的证据须引用本章真实时间语句(含 日/夜/晨/时 等时间词),让时钟推进有正文出处,不能凭空声明')
                from .ledger import apply
                state=replay(db,branch)
                for e in events:
                    apply(state,e,draft['prose'],n)
                prow=db.execute('SELECT data FROM plans WHERE branch=? AND number=?',(branch,n)).fetchone()
                if prow:
                    p=json.loads(prow[0])
                    if 'day_span' in p:
                        lo=p['day_anchor']+p['day_span'][0];hi=p['day_anchor']+p['day_span'][1]+1
                        require(lo<=state['clock']<=hi,'time_schedule',
                                f'章末时钟 {state["clock"]} 不在计划日程 [{lo},{hi}] 内(锚 {p["day_anchor"]} + D{tuple(p["day_span"])});核对时间语句或修订计划 time_hint')
                draft.update(events=events,summary=data['summary'],facts_review=data,stage='reader')
                save_draft(db,branch,n,draft)
        elif stage=='reader':
            validate_review(data,work['payload'].get('checks',READER_CHECKS),{n:draft['prose']})
            require(all(data.get('first_read',{}).get(k) for k in ('desire','obstacle','change','read_on_reason')),'first_read','须提交只读正文时的首读体验')
            if data['verdict']=='fix':
                repair(db,branch,n,draft,data['findings'],data.get('repair_scope','chapter'))
            else:
                draft['reader_review']=data
                commit_draft(db,branch,n,draft)
        elif stage.endswith('_review'):
            info=work['payload']
            first,through,scope=info['first'],info['through'],info['scope']
            sources={r['number']:r['prose'] for r in chapters(db,branch) if first<=r['number']<=through}
            validate_review(data,info['checks'],sources)
            if data['verdict']=='fix':
                # End current task before branching; new branch re-reviews the dependent suffix.
                db.execute("UPDATE tasks SET state='done',result_hash=?,receipt=? WHERE id=?",(hashed,dump(result),task['id']))
                start=min(f['evidence']['chapter'] for f in data['findings'] if f['severity']!='note')
                if get(db,'revision_failures',0)>=2:
                    put(db,'status','paused')
                    put(db,'pause_reason',{'code':'revision_exhausted','findings':data['findings']})
                else:
                    put(db,'revision_failures',get(db,'revision_failures',0)+1)
                    begin_revision(db,start,dump(data['findings']),internal=True)
            else:
                if not scope.endswith('_chunk'):
                    summary=data.get('memory')
                    was_text=isinstance(summary,str)
                    if was_text:
                        # Structural repair: text-only synthesis; per-chapter provenance stays with chunk receipts.
                        summary={'text':summary,'sources':[]}
                        data['memory']=summary
                    require(isinstance(summary,dict) and isinstance(summary.get('text'),str) and 0<len(summary['text'])<=2400 and isinstance(summary.get('sources'),list) and (was_text or summary['sources']),'memory','整体复核须提交不超过 2400 字符的提炼记忆和证据来源；纯文本 memory 可省略 sources，来源以分段审稿回执为准')
                    for ref in summary['sources']:
                        require(ref.get('chapter') in sources,'memory','记忆来源不在审稿范围内')
                        evidence(sources[ref['chapter']],ref,ref['chapter'])
                    db.execute('INSERT OR REPLACE INTO memories VALUES (?,?,?,?)',(branch,scope,through,dump(summary)))
                stored={**data,'first':first,'through':through}
                db.execute('INSERT OR REPLACE INTO reviews VALUES (?,?,?,?,?)',(branch,scope,through,review_hash(db,branch,first,through),dump(stored)))
        else:
            raise Error('stage',f'未知阶段 {stage}')
        db.execute("UPDATE tasks SET state='done',result_hash=?,receipt=? WHERE id=?",(hashed,dump(result),task['id']))
        put(db,'seq',get(db,'seq')+1)
        log(db,'submit',{'task':task['id'],'stage':stage,'chapter':n})
        return {'accepted':True,'task_id':task['id'],**status(db)}


def begin_revision(db,start,reason,internal=False):
    parent=get(db,'work_branch')
    n=head(db,parent)
    parent_meta=db.execute('SELECT * FROM branches WHERE id=?',(parent,)).fetchone()
    through=max(n,parent_meta['through'] or n)
    require(type(start) is int and 1<=start<=n and reason.strip(),'revision','修订范围必须属于已提交正文且需说明原因')
    require(start>get(db,'published_through',0),'published','已发布章节冻结，不允许自主改写')
    pending=db.execute("SELECT id FROM tasks WHERE state='pending'").fetchone()
    require(not pending,'pending','先完成当前任务或显式 pause --cancel-task 再修订')
    # A failed candidate can itself be revised; original active book remains untouched.
    branch='rev-'+uuid.uuid4().hex[:12]
    db.execute('INSERT INTO branches VALUES (?,?,?,?,?,?,?)',(branch,parent,start,through,branch_hash(db,parent),reason,'candidate'))
    for table,cols in (('chapters','number,prose,data,hash'),('events','chapter,idx,data,prev_hash,hash')):
        field='number' if table=='chapters' else 'chapter'
        db.execute(f'INSERT INTO {table} SELECT ?,{cols} FROM {table} WHERE branch=? AND {field}<?',(branch,parent,start))
    db.execute('INSERT INTO plans SELECT ?,number,data FROM plans WHERE branch=?',(branch,parent))
    from .design import copy_for_revision
    copy_for_revision(db,parent,branch,start)
    from .characters import copy_for_revision as copy_characters
    copy_characters(db,parent,branch)
    # Reviews entirely before the changed suffix remain valid.
    for table,cols in (('reviews','scope,through,input_hash,data'),('memories','scope,through,data')):
        db.execute(f'INSERT INTO {table} SELECT ?,{cols} FROM {table} WHERE branch=? AND through<?',(branch,parent,start))
    put(db,'work_branch',branch)
    put(db,'status','ready')
    put(db,'pause_reason',None)
    log(db,'revision_begin',{'branch':branch,'parent':parent,'start':start,'through':through,'reason':reason})
    return {'branch':branch,'start':start,'through':through}


def promote(db):
    branch=get(db,'work_branch')
    rev=db.execute('SELECT * FROM branches WHERE id=?',(branch,)).fetchone()
    require(rev['status']=='candidate' and head(db,branch)>=rev['through'],'revision','修订尚未完成')
    require(branch_hash(db,rev['parent'])==rev['base_hash'],'revision_conflict','修订期间原版本发生变化')
    require(reviewed(db,branch,'revision',rev['through']),'revision','修订传播复核未通过')
    audit(db,branch,strict=True)
    old=get(db,'active_branch')
    db.execute("UPDATE branches SET status='archived' WHERE id=?",(old,))
    db.execute("UPDATE branches SET status='active' WHERE id=?",(branch,))
    put(db,'active_branch',branch)
    put(db,'status','ready')
    put(db,'revision_failures',0)
    log(db,'revision_promote',{'from':old,'to':branch})


def audit(db,branch=None,strict=False):
    branch=branch or get(db,'active_branch')
    require(db.execute('PRAGMA integrity_check').fetchone()[0]=='ok','integrity','SQLite 完整性检查失败')
    state=replay(db,branch)
    rows=chapters(db,branch)
    problems=[]
    for i,row in enumerate(rows,1):
        if row['number']!=i or digest(row['prose'])!=row['hash']:
            problems.append({'code':'chapter_integrity','chapter':row['number']})
        data=json.loads(row['data'])
        if data.get('prose_hash')!=row['hash'] or any(data.get(k,{}).get('verdict')!='pass' for k in ('facts_review','reader_review')):
            problems.append({'code':'review_binding','chapter':row['number']})
        ev=[json.loads(e[0]) for e in db.execute('SELECT data FROM events WHERE branch=? AND chapter=? ORDER BY idx',(branch,row['number']))]
        if ev!=data.get('events'):
            problems.append({'code':'event_coverage','chapter':row['number']})
    overdue=[k for k,v in state['hooks'].items() if v['status']=='open' and v['due']<=len(rows)]
    problems += [{'code':'overdue_hook','id':k} for k in overdue]
    if strict:
        require(not problems,'audit','审计发现未解决问题：'+dump(problems))
    return {'ok':not problems,'branch':branch,'chapters':len(rows),'problems':problems,
            'han_characters':sum(len(re.findall(r'[\u3400-\u4dbf\u4e00-\u9fff]',r['prose'])) for r in rows),
            'open_important_hooks':[k for k,v in state['hooks'].items() if v['status']=='open' and v.get('important',True)],
            'state_hash':digest(state)}


def complete_inner(db):
    branch=get(db,'active_branch')
    require(branch==get(db,'work_branch'),'revision','仍有未合并修订')
    report=audit(db,branch,strict=True)
    n=report['chapters']
    require(n>0 and get(db,'pilot_approved'),'completion','试章尚未定调或无正文')
    last=db.execute('SELECT data FROM plans WHERE branch=? AND number=?',(branch,n)).fetchone()
    require(last and json.loads(last[0]).get('book_end'),'completion','尚未抵达已规划终章')
    require(not db.execute('SELECT 1 FROM plans WHERE branch=? AND number>?',(branch,n)).fetchone(),'completion','存在未写计划')
    low,high=get(db,'contract')['word_range']
    require(low<=report['han_characters']<=high,'length','篇幅不在作者约定区间；不得自动修改目标或注水')
    require(not report['open_important_hooks'],'completion','重要承诺尚未兑现')
    require(reviewed(db,branch,'book',n),'completion','全书终审尚未通过')
    for row in db.execute('SELECT number,data FROM plans WHERE branch=? ORDER BY number',(branch,)):
        p=json.loads(row['data'])
        if p.get('volume_end'):
            require(reviewed(db,branch,'volume',row['number']),'completion','卷审未通过')
    put(db,'status','completed')
    log(db,'complete',report)
    return {'status':'completed','audit':report}


def pause(db,reason,cancel=False):
    with transaction(db):
        put(db,'status','paused')
        put(db,'pause_reason',reason)
        if cancel:
            db.execute("UPDATE tasks SET state='cancelled' WHERE state='pending'")
        log(db,'pause',{'reason':reason,'cancel_task':cancel})
    return status(db)


def resume(db):
    with transaction(db):
        require(get(db,'status')!='completed','completed','已完本；如需修订请使用 revise')
        from .runner import process_alive, worker_alive
        runner=get(db,'runner')
        require(not runner or (not process_alive(runner.get('pid')) and not worker_alive(runner.get('worker_pid'))),'runner_busy','恢复前须确认旧控制器和模型进程均已退出')
        audit(db,get(db,'work_branch'),strict=True)
        db.execute("UPDATE tasks SET lease_owner=NULL,lease_until=NULL WHERE state='pending'")
        put(db,'runner',None)
        put(db,'status','ready')
        put(db,'pause_reason',None)
        log(db,'resume',{})
    return status(db)
