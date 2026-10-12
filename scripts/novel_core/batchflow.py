"""Bounded writing sessions with a provisional ledger and fresh batch review.

Only drafts/meta hold provisional events. Canonical chapters/events are written
by the existing chapter-review transaction, never by the writer's self-check.
"""
import copy
import json
import uuid
from .store import Error, get, put, digest, require, head, log
from .ledger import replay, apply
from . import atoms
from .execution import settings as execution_settings

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
                'candidate_sequence','候选事件必须连续且带已验证的临时事件')
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


def new_generation(db,batch,phase='writing'):
    """INV-5: the single authority for advancing a batch into a new generation.

    A generation change moves `first`, so the constraint digest — which covers
    the plan range [first,last] — must be recomputed. Without that, the very
    next `choose` compares a digest of the old range against the new one and
    invalidates the batch with the false reason "contract/plan/character
    version changed", destroying the very suffix a local repair meant to keep.
    The previous generation's hold marker is dropped here for the same reason:
    a superseded `local_repair` must not fire against the new generation.
    """
    branch=batch['branch']
    batch.update(id=uuid.uuid4().hex,first=head(db,branch)+1,phase=phase)
    batch.pop('local_repair',None)
    batch['constraints']=constraints(db,branch,batch['first'],batch['last'])
    return batch


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
        for field in ('candidate_events','candidate_prefix_hash','facts_review','reader_review','facts_context_hash','reader_context_hash','candidate_rebound'):
            draft.pop(field,None)
        engine.save_draft(db,branch,n,draft)
    new_generation(db,batch)
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
                p=atoms.plan(db,branch,n)
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
        plan=atoms.plan(db,branch,committed+1)
        job=characters.next_job(db,branch,committed+1,plan) or characters.next_index_job(db,branch,committed+1,plan)
        if job:return job
    job=single(db);stage,n,_=job
    limit=get(db,'batch_through',0)
    # A chapter cap stops new writing, not acceptance of the saved range.
    # Candidate branches must finish their reviews and promote first.
    acceptance=('batch_review','volume_review','revision_review','book_review','promote','complete','await_author')
    if (limit and committed>=limit and get(db,'pilot_approved')
            and branch==get(db,'active_branch') and stage not in acceptance):
        return 'target_reached',committed,{'through':limit,'reason':'达到主控指定范围；继续须更新范围。'}
    config=execution_settings(db)
    size=min(config['chapter_batch_size'],config['writer_batch'])
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


def accept_candidate(db,branch,n,draft,data,work=None):
    from . import engine
    require(isinstance(data.get('self_check'),str) and data['self_check'].strip(),
            'candidate_self_check','批内执笔须说明具体自查结果，自查不替独立审稿')
    if data.get('reuse_candidate_events') is True:
        require('candidate_events' not in data,'candidate_events','完整 candidate_events 与 reuse_candidate_events 只能选一种')
        from .patches import source
        snapshot=(work or {}).get('payload',{}).get('candidate_events_snapshot')
        require(isinstance(snapshot,list),'candidate_events','此冻结任务没有可沿用候选事件快照，须完整提交 candidate_events')
        base=source(db,work);events=copy.deepcopy(snapshot)
        for index,event in enumerate(events):
            for field in engine.EVENT_EVIDENCE_FIELDS:
                ref=event.get(field)
                if not isinstance(ref,dict):continue
                require(ref.get('chapter')==n and engine.event_sentence_context(base,ref) is not None
                        and engine.event_sentence_context(base,ref)==engine.event_sentence_context(draft['prose'],ref),
                        'candidate_events_changed','候选事件的证据语境已变；须核对后完整提交 candidate_events',{'event_index':index,'field':field})
                ref['start']=draft['prose'].index(ref['quote'])
    else:events=copy.deepcopy(data.get('candidate_events'))
    require(isinstance(events,list),'candidate_events','批内执笔须提交候选事件数组；无变化填 []')
    before=digest(candidate_state(db,branch,n-1))
    from .settlement import evaluate,state_hash
    state,settled=evaluate(db,branch,n,draft['prose'],events,candidate=True)
    require(isinstance(data.get('summary'),str) and data['summary'].strip(),'candidate_summary','候选章须提供实际正文摘要')
    draft.update(candidate_events=events,candidate_prefix_hash=before,candidate_summary=data['summary'],self_check=data['self_check'])
    draft.pop('candidate_invalidated',None)
    batch=active(db,branch)
    held=batch.get('local_repair') if batch else None
    if held and held['chapter']==n:
        batch.pop('local_repair')
        engine.save_draft(db,branch,n,draft)
        failed,_=rebind_suffix(db,batch,n+1,state)
        if held['after_state_hash']==state_hash(state) and failed is None:
            batch['phase']='review';put(db,key(branch),batch)
            log(db,'local_repair_suffix_retained',{'batch':batch['id'],'chapter':n,'last':batch['last']})
        else:
            invalidate(db,batch,n+1,'局部修订改变事件落定或后文事件不能沿用，须重核承接')
    return settled


def _committed_precondition(event,state):
    """Current committed value of a free-text precondition, or None.

    `state`, `relation` and `knowledge` carry prose wording; sealed review
    re-derives that wording independently, so a candidate's `before` text
    records the writer's phrasing, never the committed one. Numeric, clock,
    hook and structural preconditions are strict and are not read here."""
    from .ledger import relation_key
    op=event.get('op')
    if op=='state':return state['facts'].get(event.get('entity')+':'+str(event.get('key')))
    if op=='knowledge':
        old=state['knowledge'].get(event.get('entity')+':'+str(event.get('topic')))
        return old['claim'] if old else None
    if op=='relation':
        return state.get('relations',{}).get(relation_key(event.get('from'),event.get('to'),event.get('key')))
    return None


def _align_candidate_events(events,base_state,prose,chapter):
    """Re-derive free-text preconditions from the live chain, event by event.

    Raises the ledger's own Error on strict breaks (numeric balances, clock,
    entity existence, evidence context); those are real contradictions. Any
    suffix chapter still faces its own sealed review before committing."""
    from .ledger import apply
    probe=copy.deepcopy(base_state);notes=[]
    for event in events:
        if event.get('op') in ('state','relation','knowledge'):
            current=_committed_precondition(event,probe)
            if event.get('before')!=current:
                event['before']=current
                notes.append({'op':event['op'],'key':_precondition_key(event)})
        apply(probe,event,prose,chapter)
    return notes


def _precondition_key(event):
    op=event.get('op')
    if op=='knowledge':return str(event.get('entity'))+':'+str(event.get('topic'))
    if op=='state':return str(event.get('entity'))+':'+str(event.get('key'))
    if op=='relation':return str(event.get('from'))+'>'+str(event.get('to'))+':'+str(event.get('key'))
    return op or '?'


def rebind_suffix(db,batch,start,state,base=None):
    """Rebase the suffix candidate chain onto the committed ledger.

    Free-text preconditions are aligned to the committed wording; strict
    preconditions stay exact. Returns (first chapter that cannot be rebased
    or None, {chapter: alignment notes}). Successfully rebound chapters are
    saved even when a later chapter fails. This never creates or reuses a
    review pass."""
    from . import engine
    from .settlement import evaluate
    revised=[];aligned={};failed=None
    for number,draft in rows(db,batch['branch'],batch['last']):
        if number<start:continue
        if draft.get('stage')!='chapter_review' or not isinstance(draft.get('candidate_events'),list):
            failed=number;break
        events=copy.deepcopy(draft['candidate_events'])
        try:
            notes=_align_candidate_events(events,state,draft['prose'],number)
            prefix=digest(state)
            state,_=evaluate(db,batch['branch'],number,draft['prose'],events,candidate=True,initial_state=state)
        except Error:
            failed=number;break
        draft['candidate_events']=events;draft['candidate_prefix_hash']=prefix
        draft.pop('candidate_rebound',None)
        if notes:
            draft['candidate_rebound']={'base_chapter':base if base is not None else start-1,'aligned':notes}
            aligned[number]=notes
        revised.append((number,draft))
    for number,draft in revised:engine.save_draft(db,batch['branch'],number,draft)
    return failed,aligned


def after_review(db,branch,n,proposed,accepted_events,passed,proposed_state=None,scope='chapter'):
    batch=active(db,branch)
    if not batch:return
    if not passed:
        if scope=='local' and proposed_state is not None:
            from .settlement import state_hash
            put(db,'chapter_batch_archive:'+batch['id'],copy.deepcopy(batch))
            new_generation(db,batch)
            batch['local_repair']={'chapter':n,'after_state_hash':state_hash(proposed_state)}
            put(db,key(branch),batch)
            log(db,'local_repair_suffix_held',{'chapter':n,'last':batch['last']})
            return
        invalidate(db,batch,n,'独立审稿要求返修，后文承接须重核');return
    if n>=batch['last']:
        put(db,key(branch),None);log(db,'chapter_batch_complete',{'batch':batch['id'],'through':n});return
    from .settlement import state_hash
    actual=replay(db,branch)
    failed,aligned=rebind_suffix(db,batch,n+1,actual,base=n)
    if failed is not None:
        invalidate(db,batch,failed,'后续章候选事件与新正式记录硬前件矛盾，须重核承接')
    elif aligned or (proposed_state is not None and state_hash(proposed_state)!=state_hash(actual)):
        log(db,'chapter_batch_rebound',{'branch':branch,'after':n,'last':batch['last'],'chapters':sorted(aligned)})


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
