"""Five-chapter writing sessions with a provisional ledger and fresh batch review.

Only drafts/meta hold provisional events. Canonical chapters/events are written
by the existing chapter-review transaction, never by the writer's self-check.
"""
import copy
import json
import uuid
from .store import get, put, digest, dump, require, head, log
from .ledger import replay, apply

PROTOCOL='chapter-batch/1'


def key(branch):return 'chapter_batch:'+branch

def active(db,branch=None):return get(db,key(branch or get(db,'work_branch')))


def rows(db,branch,through):
    return [(r['number'],json.loads(r['data'])) for r in db.execute(
        'SELECT number,data FROM drafts WHERE branch=? AND number<=? ORDER BY number',(branch,through))]


def candidate_state(db,branch,through):
    state=replay(db,branch);previous=head(db,branch)
    for n,draft in rows(db,branch,through):
        require(n==previous+1 and isinstance(draft.get('candidate_events'),list),
                'candidate_sequence','候选账本必须连续且带已验证的临时事件')
        require(draft.get('candidate_prefix_hash')==digest(state),'candidate_stale','候选稿前态已变化，先重核承接')
        for event in copy.deepcopy(draft['candidate_events']):apply(state,event,draft['prose'],n)
        previous=n
    require(previous>=through,'candidate_sequence','缺少承接前章的候选记录')
    return state


def constraints(db,branch,first,last):
    versions=[(r['key'],r['value']) for r in db.execute(
        "SELECT key,value FROM meta WHERE key LIKE ? OR key LIKE ? ORDER BY key",('character:'+branch+':%','design:'+branch+':%'))]
    plans=[(r['number'],json.loads(r['data'])) for r in db.execute(
        'SELECT number,data FROM plans WHERE branch=? AND number BETWEEN ? AND ? ORDER BY number',(branch,first,last))]
    return digest({'contract':get(db,'contract'),'style':get(db,'style_anchor'),'versions':versions,'plans':plans})


def session(db,branch,stage):
    batch=active(db,branch)
    if not batch:return None
    role='reviewer' if stage=='chapter_review' else 'writer'
    if stage not in ('draft','repair','redesign','chapter_review'):return None
    return {'protocol':PROTOCOL,'batch':batch['id'],'role':role,'first':batch['first'],'last':batch['last']}


def signature(db,task,work):
    from .engine import chapter_review_context_hash
    s=work.get('chapter_session')
    if not s:return None
    branch=task['branch'];batch=active(db,branch)
    prefix=digest(candidate_state(db,branch,task['chapter']-1)) if s['role']=='writer' else digest(replay(db,branch))
    versions=[(n,digest(d['prose']),digest(d.get('candidate_events'))) for n,d in rows(db,branch,s['last'])
              if n<task['chapter'] or s['role']=='reviewer']
    return digest({'session':s,'current_batch':batch and batch['id'],'prefix':prefix,'candidate_versions':versions,
                   'canonical_context':chapter_review_context_hash(db,branch,task['chapter']),
                   'constraints':constraints(db,branch,s['first'],s['last'])})


def invalidate(db,batch,start,reason):
    """Keep prose/attempt counts, but require rechecking every dependent draft."""
    from . import engine
    branch=batch['branch']
    archive=copy.deepcopy(batch);archive['invalidated_from']=start;archive['reason']=reason
    put(db,'chapter_batch_archive:'+batch['id'],archive)
    for n,draft in rows(db,branch,batch['last']):
        if n<start:continue
        put(db,'candidate_archive:'+batch['id']+':'+str(n),draft)
        if draft['stage'] not in ('repair','redesign'):draft['stage']='draft'
        draft['candidate_invalidated']={'from':start,'reason':reason}
        for field in ('candidate_events','candidate_prefix_hash','facts_review','reader_review','facts_context_hash','reader_context_hash'):
            draft.pop(field,None)
        engine.save_draft(db,branch,n,draft)
    batch.update(id=uuid.uuid4().hex,first=head(db,branch)+1,phase='writing')
    batch['constraints']=constraints(db,branch,batch['first'],batch['last'])
    put(db,key(branch),batch)
    log(db,'chapter_batch_invalidated',{'branch':branch,'from':start,'last':batch['last'],'reason':reason})


def choose(db,single,allow_new_batch=True):
    from . import engine, characters
    branch=get(db,'work_branch');batch=active(db,branch);committed=head(db,branch)
    if batch:
        if committed>=batch['last']:
            put(db,key(branch),None);return choose(db,single,allow_new_batch=allow_new_batch)
        if batch['constraints']!=constraints(db,branch,batch['first'],batch['last']):
            invalidate(db,batch,committed+1,'约定、计划或人物/设计版本改变')
        if batch['phase']=='writing':
            for n in range(committed+1,batch['last']+1):
                draft=engine.load_draft(db,branch,n)
                if draft and draft['stage']=='chapter_review':continue
                p=json.loads(db.execute('SELECT data FROM plans WHERE branch=? AND number=?',(branch,n)).fetchone()[0])
                job=characters.next_job(db,branch,n,p) or characters.next_index_job(db,branch,n,p)
                if job:
                    # Preparation of a new profile changes dependencies. Review
                    # the completed prefix before leaving this writing session.
                    if n>committed+1:
                        batch['last']=n-1;batch['phase']='review';batch['constraints']=constraints(db,branch,batch['first'],batch['last'])
                        put(db,key(branch),batch);return single(db)
                    put(db,key(branch),None);return job
                if draft and draft['stage'] in ('repair','redesign'):
                    return engine.draft_job(db,branch,n,draft)
                payload={}
                if draft:
                    payload={'prose':draft['prose'],'candidate_rebase':draft.get('candidate_invalidated'),
                             'instruction':'前态已变，重核全文承接与候选事件；可保留仍成立的正文，不能直接签 pass。'}
                return 'draft',n,payload
            batch['phase']='review';put(db,key(branch),batch)
        return single(db)
    saved=engine.load_draft(db,branch,committed+1)
    if saved and saved.get('candidate_invalidated') and saved['stage']=='draft':
        row=db.execute('SELECT data FROM plans WHERE branch=? AND number=?',(branch,committed+1)).fetchone()
        plan=json.loads(row[0])
        job=characters.next_job(db,branch,committed+1,plan) or characters.next_index_job(db,branch,committed+1,plan)
        if job:return job
    job=single(db);stage,n,_=job
    limit=get(db,'batch_through',0)
    if limit and committed>=limit and get(db,'pilot_approved'):
        return 'target_reached',committed,{'through':limit,'reason':'达到主控指定范围；继续须更新范围。'}
    size=min(get(db,'chapter_batch_size',5),get(db,'writer_batch',10))
    if not allow_new_batch:return job
    rev=db.execute('SELECT status FROM branches WHERE id=?',(branch,)).fetchone()
    if stage!='draft' or size<=1 or n<=1 or not get(db,'pilot_approved') or rev['status']!='active' or (saved and not saved.get('candidate_invalidated')):return job
    previous=db.execute('SELECT stage,input FROM tasks WHERE branch=? AND chapter=? ORDER BY rowid DESC LIMIT 1',(branch,n)).fetchone()
    if previous and previous['stage'] in ('context_scan','context_reduce') and not json.loads(previous['input']).get('chapter_session'):
        return job  # Finish an already frozen pre-upgrade preparation normally.
    last=n-1;volume=None
    for number in range(n,n+size):
        if limit and number>limit:break
        row=db.execute('SELECT data FROM plans WHERE branch=? AND number=?',(branch,number)).fetchone()
        if not row:break
        plan=json.loads(row[0])
        if volume is not None and plan['volume']!=volume:break
        volume=plan['volume'];last=number
        if plan.get('volume_end') or plan.get('book_end') or number%10==0:break
    if last<n:return job
    batch={'id':uuid.uuid4().hex,'branch':branch,'first':n,'last':last,'phase':'writing'}
    batch['constraints']=constraints(db,branch,n,last);put(db,key(branch),batch)
    log(db,'chapter_batch_started',batch)
    return job


def accept_candidate(db,branch,n,draft,data):
    from . import engine
    require(isinstance(data.get('self_check'),str) and data['self_check'].strip(),
            'candidate_self_check','批内执笔须说明具体自查结果，自查不替独立审稿')
    events=copy.deepcopy(data.get('candidate_events'))
    require(isinstance(events,list),'candidate_events','批内执笔须提交候选事件数组；无变化填 []')
    state=candidate_state(db,branch,n-1);before=digest(state)
    for event in events:
        require(isinstance(event,dict),'candidate_events','候选事件须为对象')
        if event.get('op')=='clock':
            quote=(event.get('evidence') or {}).get('quote','')
            require(any(w in quote for w in engine.TIME_TOKENS) or any(w in quote for w in engine.TIME_CHARS),'time_evidence','候选时钟也须有正文时间证据')
        apply(state,event,draft['prose'],n)
    engine.validate_hook_deadlines(state,n)
    plan=json.loads(db.execute('SELECT data FROM plans WHERE branch=? AND number=?',(branch,n)).fetchone()[0])
    if 'day_span' in plan:
        lo=plan['day_anchor']+plan['day_span'][0];hi=plan['day_anchor']+plan['day_span'][1]+1
        require(lo<=state['clock']<=hi,'time_schedule','候选章末时钟超出已规划日程')
    require(isinstance(data.get('summary'),str) and data['summary'].strip(),'candidate_summary','候选章须提供实际正文摘要')
    draft.update(candidate_events=events,candidate_prefix_hash=before,candidate_summary=data['summary'],self_check=data['self_check'])
    draft.pop('candidate_invalidated',None)


def after_review(db,branch,n,proposed,accepted_events,passed):
    batch=active(db,branch)
    if not batch:return
    if not passed:
        invalidate(db,batch,n,'独立审稿要求返修，后文承接须重核');return
    if n>=batch['last']:
        put(db,key(branch),None);log(db,'chapter_batch_complete',{'batch':batch['id'],'through':n});return
    if proposed!=accepted_events:
        invalidate(db,batch,n+1,'正式事件与候选提案不同，后续前态失效')


def prefix_material(db,branch,n):
    return [{'chapter':number,'candidate':True,'prose_hash':digest(draft['prose']),
             'summary':draft['candidate_summary'],'events':draft['candidate_events']}
            for number,draft in rows(db,branch,n-1)]


def state_for_task(db,branch,task_id):
    if task_id:
        task=db.execute('SELECT * FROM tasks WHERE id=?',(task_id,)).fetchone()
        if task and task['state']=='pending' and task['branch']==branch:
            work=json.loads(task['input']);s=work.get('chapter_session',{})
            if s.get('role')=='writer':return candidate_state(db,branch,task['chapter']-1)
    return replay(db,branch)


def review_sources(db,work):
    from .memory import prose_ref
    s=work['chapter_session'];branch=work['branch'];batch=active(db,branch)
    require(batch and batch['id']==s['batch'],'chapter_session','审稿批次已改变')
    sources=[]
    for n in range(head(db,branch)+1,batch['last']+1):
        row=db.execute('SELECT data FROM drafts WHERE branch=? AND number=?',(branch,n)).fetchone()
        require(row,'candidate_sequence','批内候选正文缺失')
        draft=json.loads(row[0]);sources.append({'chapter':n,**prose_ref(db,draft['prose'])})
    return sources


def continue_session(db,project,task):
    from . import engine
    work=json.loads(task['input']);s=work.get('chapter_session')
    require(s and s.get('protocol')==PROTOCOL,'chapter_session','只有绑定批次与角色的新任务可连续处理')
    following=engine.next_task(db,project,after=task['id'],brief=True,chapter_session=s)
    result={'schema':'novel-ledger-v2.chapter-batch/1','accepted':True,'task_id':task['id'],'session':s}
    if following.get('task_id'):result.update(action='continue_chapter_batch',task=following)
    else:result.update(action=following.get('stage',following.get('status')),transition=following,
                       boundary='结束当前批次角色，返回最后提交 ID，由主控派新的独立审稿者或返修执笔者。')
    return result
