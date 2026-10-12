"""Idempotent editorial state machine and isolated retrospective revisions."""
from __future__ import annotations
import copy
from .wire import EVIDENCE_INPUT
from . import execution
from .taskview import PROTOCOL as READING_PROTOCOL
from .schedule import PROTOCOL as TIMELINE_PROTOCOL
from .schedule import INPUT_PROTOCOL as TIME_INPUT_PROTOCOL
from .reviewflow import SCRUTINY_PROTOCOL
import json
import re
import time
import uuid
from pathlib import Path
from .store import Error, Batch, require, dump, digest, get, put, transaction, log, chapters, head, branch_hash, write_json
from .ledger import replay, append_events, evidence
from .memory import pack
from . import atoms, transitions

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
EVENT_EVIDENCE_FIELDS=('evidence','change_evidence','cause_evidence')


def event_sentence_context(prose,ref):
    """Return complete enclosing sentence(s), with no character-length cap."""
    quote=ref.get('quote')
    if not isinstance(quote,str) or not quote or prose.count(quote)!=1:return None
    start=prose.index(quote);end=start+len(quote)
    boundaries='。！？!?；;\n'
    begin=max((prose.rfind(mark,0,start) for mark in boundaries),default=-1)+1
    if prose[end-1] not in boundaries:
        following=[prose.find(mark,end) for mark in boundaries]
        following=[offset for offset in following if offset>=0]
        end=min(following)+1 if following else len(prose)
    return prose[begin:end]


def load_draft(db, branch, n):
    row = db.execute('SELECT data FROM drafts WHERE branch=? AND number=?', (branch,n)).fetchone()
    return json.loads(row[0]) if row else None


def save_draft(db, branch, n, data):
    db.execute('INSERT OR REPLACE INTO drafts VALUES (?,?,?)', (branch,n,dump(data)))


def chapter_length(db,branch,n,prose=None):
    from .budget import chapter_length as observe
    row=db.execute('SELECT data FROM plans WHERE branch=? AND number=?',(branch,n)).fetchone()
    return observe(get(db,'contract'),json.loads(row[0]) if row else {},prose)


def develop_short_draft(db,branch,n,draft,observation,prior_prose=None):
    """Keep the submitted prose and ask for anchored development, never a rewrite rejection."""
    from .patches import save_source
    retained_findings=[]
    for previous in draft.get('findings',[]):
        if previous.get('kind')=='chapter_development':continue
        previous=copy.deepcopy(previous)
        if 'source_prose_hash' not in previous:
            previous['source_prose_hash']=save_source(db,prior_prose if prior_prose is not None else draft['prose'])
        previous['evidence_scope']='previous_candidate_review'
        retained_findings.append(previous)
    if retained_findings:draft.setdefault('prior_repair_scope',draft.get('repair_scope','chapter'))
    if isinstance(draft.get('events'),list):
        draft['prior_event_snapshot']={'events':copy.deepcopy(draft['events']),'prose':draft['prose'],
                                       'verified':draft.get('facts_context_hash') is not None}
    first=re.search(r'[^\n。！？!?；;]+[。！？!?；;]?',draft['prose'])
    quote=first.group() if first else draft['prose']
    finding={'severity':'quality','kind':'chapter_development',
             'impact':f"当前候选稿为 {observation['actual_han']} 汉字，作者本章目标为 {observation['target_han']} 汉字，尚需核对场景展开的承载量。候选稿已保留，数量不足不等于可以灌水。",
             'goal':'逐场核对既定 expansion_units 和当前章作用，说明哪个场景还缺哪一步试探、阻力、选择、关系变化及其后果，并据此自然展开。保留已经成立的正文，优先定点补丁；不能复制解释或为了字数新增无因果事件。',
             'evidence':{'chapter':n,'start':first.start() if first else 0,'quote':quote}}
    if draft.get('length_cycle_target_han')!=observation['target_han']:
        draft['length_attempts']=0
        draft['length_cycle_target_han']=observation['target_han']
    draft['length_attempts']=draft.get('length_attempts',0)+1
    combined_findings=retained_findings+[finding]
    # INV-4: stricter of the two ladders, so the length cycle cannot demote a
    # chapter the semantic ladder already escalated to redesign.
    stage,_=transitions.resolve_stage(draft.get('attempts',0),draft['length_attempts'])
    draft.update(chapter_length=observation,findings=combined_findings,repair_scope='chapter',stage=stage)
    if draft['length_attempts']>3:
        put(db,'status','paused')
        put(db,'pause_reason',{'code':'chapter_development_exhausted','chapter':n,
                              'chapter_length':observation,'findings':combined_findings,
                              'required_action':'两次自然展开和一次重设计后仍不足；保留候选稿，核对哪个场景缺少可演出的动作、人物选择或后果，再修改承载依据。不能降低已定目标或继续少量加字重试。'})
    # The full candidate is available through the version-bound candidate API.
    save_source(db,draft['prose'])
    for obsolete in ('facts_review','reader_review','facts_context_hash','reader_context_hash'):
        draft.pop(obsolete,None)
    save_draft(db,branch,n,draft)
    log(db,'chapter_development',{'chapter':n,'actual_han':observation['actual_han'],
                                 'target_han':observation['target_han'],'stage':draft['stage'],
                                 'candidate_hash':digest(draft['prose'])})


def status(db, brief=False):
    from .budget import overview
    from .store import VERSION
    from .contextflow import usage
    active, work = get(db,'active_branch'), get(db,'work_branch')
    execution_config=execution.settings(db)
    task = db.execute("SELECT id,stage,chapter,branch,input_hash,lease_until FROM tasks WHERE state='pending'").fetchone()
    from .recovery import snapshot
    result = {'schema':'novel-ledger-v2.status/1', 'status':get(db,'status'), 'active_branch':active,
            'work_branch':work,'committed':head(db,active), 'work_committed':head(db,work),
            'pilot_approved':get(db,'pilot_approved'), 'task':dict(task) if task else None,
            'reason':get(db,'pause_reason'),
            'review_mode':'combined',**execution_config,
            'chapter_batch_size':execution_config['chapter_batch_size'],'batch_through':get(db,'batch_through',0),
            'chapter_batch':get(db,'chapter_batch:'+work),
            'recovery':snapshot(db,task),
            'context_chars':get(db,'context_chars'),
            'skill_version':VERSION, 'db_version':get(db,'version'),
            'review_verification':'protocol_and_evidence_only; semantic judgment belongs to the reviewer'}
    if not brief: result.update(volume_budget=overview(db,work), usage=usage(db))
    # INV-6: a chapter that reached canon by carry skipped per-chapter
    # independent review. That exception must be visible to the controller and
    # the author, not only to the revision reviewer.
    from .carriage import carried_chapters
    carried=carried_chapters(db,work)
    if carried: result['carried_chapters']=carried
    return result


def approve(db):
    with transaction(db):
        require(head(db,get(db,'active_branch')) >= 1, 'pilot', '须先产出并审稿第一章，再由用户定调')
        if get(db,'style_anchor') is None:
            # The approved pilot is the concrete voice reference; capture it once.
            row = db.execute('SELECT prose FROM chapters WHERE branch=? AND number=1',(get(db,'active_branch'),)).fetchone()
            if row:
                prose = row[0]
                paragraphs=[paragraph for paragraph in prose.splitlines() if paragraph.strip()]
                if len(paragraphs)>1 and re.match(r'^\s*(?:#{1,6}\s+|第[0-9零〇一二三四五六七八九十百千两]+[章节回卷])',paragraphs[0]):
                    paragraphs=paragraphs[1:]
                sample=paragraphs[0]
                close=paragraphs[-1] if len(paragraphs)>1 else ''
                put(db,'style_anchor',{'source':'pilot','traits':[],'sample':sample,'sample_close':close})
                log(db,'style_anchor_captured',{'from':'pilot','selection':'complete_opening_and_closing_paragraphs',
                                              'sample_chars':len(sample),'close_chars':len(close)})
        put(db,'pilot_approved',True)
        log(db,'pilot_approved',{})
    return status(db)


def chapter_reader_checks(db,branch,n,observation=None):
    from .characters import enabled
    checks=READER_CHECKS+(('dialogue_identity',) if enabled(db) else ())+(('voice_match',) if get(db,'style_anchor') else ())
    if observation:checks+=('substance',)
    planrow=db.execute('SELECT data FROM plans WHERE branch=? AND number=?',(branch,n)).fetchone()
    if planrow and 'rhythm' in json.loads(planrow[0]):checks+=('pacing',)
    return checks


def choose(db):
    from .batchflow import choose as batch_choose
    return batch_choose(db,_choose_single)


def draft_job(db,branch,n,draft):
    require(draft['stage'] not in ('facts','reader'),'unsupported_review','旧分阶段审稿草稿不受支持；仅支持 chapter_review 新流程')
    observation=chapter_length(db,branch,n,draft['prose'])
    changed_length_cycle=observation and draft.get('length_cycle_target_han')!=observation['target_han'] and any(f.get('kind')=='chapter_development' for f in draft.get('findings',[]))
    if observation and observation['status']=='needs_development' and (draft['stage']=='chapter_review' or changed_length_cycle):
        develop_short_draft(db,branch,n,draft,observation)
    stage = draft['stage']
    payload = {k:draft[k] for k in ('prose','findings','attempts','repair_scope','prior_repair_scope') if k in draft}
    if stage=='draft' and draft.get('candidate_invalidated'):
        payload.update(candidate_rebase=draft['candidate_invalidated'],instruction='前态或约定已变，重核全文承接与候选事件；保留仍成立的正文，不自动沿用旧判断。')
    if stage=='repair' and isinstance(draft.get('candidate_events'),list) and execution.settings(db)['execution_mode']==execution.BATCH:
        payload['candidate_events_snapshot']=copy.deepcopy(draft['candidate_events'])
    if stage == 'chapter_review':
        from .numeric import capability
        correction=capability(draft)
        if correction:payload['numeric_correction']=correction
        state=replay(db,branch)
        extra=()
        if state.get('anchors'):extra+=('stable_facts',)
        planrow=db.execute('SELECT data FROM plans WHERE branch=? AND number=?',(branch,n)).fetchone()
        if state.get('places') or (planrow and json.loads(planrow[0]).get('scenes')):extra+=('spatial',)
        from .characters import enabled
        if enabled(db):extra+=('character_consistency',)
        payload['facts_checks'] = FACT_CHECKS+extra
        payload['reader_checks']=chapter_reader_checks(db,branch,n,observation)
        prior=prior_events(db,branch,n)
        if prior:
            prose=draft['prose']
            def resolvable(ev):
                # A surviving short quote is not enough: its complete
                # sentence context must also match the previous source.
                if not isinstance(ev,dict):return False
                old_prose=prior.get('prose')
                if not isinstance(old_prose,str) or prior.get('verified') is False:return False
                for key in EVENT_EVIDENCE_FIELDS:
                    ref=ev.get(key)
                    if isinstance(ref,dict):
                        # Historical evidence belongs to its own chapter.
                        # Without replaying that source here, require an
                        # explicit disposition rather than guessing context.
                        if ref.get('chapter')!=n:return False
                        old=event_sentence_context(old_prose,ref)
                        if old is None or old!=event_sentence_context(prose,ref):return False
                    elif key=='evidence':return False
                return True
            payload['prior_events']=prior['events']
            payload['prior_events_source']=prior['source']
            payload['auto_carry']=[i for i,ev in enumerate(prior['events']) if resolvable(ev)]
            payload['prior_events_note']='本章为返修/重设计后的重新事实登记:auto_carry 中的前序事件仅在全部证据的完整句语境未变时自动续算,不要在 events 中重复提交;其余事件在 events 中原样重报即自动视为 kept,无须 prior_dispositions;正文不再支持的事件须在 prior_dispositions 声明 dropped/replaced 并说明如何变化,既不重报也不声明会被拒绝;也可声明覆盖自动续算,条目形如 {"index":0,"disposition":"dropped","reason":"正文如何变化"}。完整语义仍需facts核验。'
    if observation:payload['chapter_length']=observation
    return stage,n,payload


def _choose_single(db):
    branch = get(db,'work_branch')
    n = head(db,branch)
    from . import design
    if design.enabled(db) and not design.usable(db,branch,'world'):
        return design.job(db,branch,'world',n+1)
    draft = load_draft(db,branch,n+1)
    if draft:return draft_job(db,branch,n+1,draft)
    if n:
        if n==1 and not get(db,'pilot_approved') and get(db,'work_branch')==get(db,'active_branch'):
            return 'await_author',n,{'reason':'第一章已通过内部审稿，等待作者定调；认可后执行 approve。'}
        # Review every full text in chunks, then inspect cross-chunk causal chains.
        if n % 10 == 0 and not reviewed(db,branch,'batch',n):
            return review_job(db,branch,'batch',max(1,n-9),n)
        plan = atoms.plan(db,branch,n)
        if plan.get('volume_end') and not reviewed(db,branch,'volume',n):
            first = next(c['number'] for c in chapters(db,branch) if json.loads(c['data'])['volume']==plan['volume'])
            return review_job(db,branch,'volume',first,n)
        revision = db.execute('SELECT * FROM branches WHERE id=?',(branch,)).fetchone()
        if revision['status']=='candidate' and n >= revision['through']:
            if not reviewed(db,branch,'revision',n):
                return review_job(db,branch,'revision',revision['start'],n)
            return 'promote',n,{}
        if plan.get('book_end'):
            # Later profiles/designs can invalidate earlier volume receipts.
            # Recheck them once at the end, never after every new chapter.
            for row in db.execute('SELECT number,data FROM plans WHERE branch=? AND number<=? ORDER BY number',(branch,n)):
                volume_plan=json.loads(row['data'])
                if volume_plan.get('volume_end') and not reviewed(db,branch,'volume',row['number']):
                    first=db.execute("SELECT MIN(number) FROM chapters WHERE branch=? AND json_extract(data,'$.volume')=?",(branch,volume_plan['volume'])).fetchone()[0]
                    return review_job(db,branch,'volume',first,row['number'])
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
        payload={'window':[n+1,n+6], 'instruction':'细化未来 5～6 章；临近结局可少于 5 章。重要转折比较两个实质不同方案。每章按作用安排真正可演出的场景展开，不以账本事件条数估算篇幅；有作者篇幅目标时用 execution.expansion_units 说明承载依据，短章须事先说明作用和目标。'}
        if get(db,'contract').get('chapter_target_han'):
            payload['chapter_target_han']=get(db,'contract')['chapter_target_han']
        return 'plan',n+1,payload
    from .characters import next_job,next_index_job,coverage
    character_job=next_job(db,branch,n+1,json.loads(next_plan[0]))
    if character_job:return character_job
    # The next chapter is covered, but a chapter already written or already
    # planned earlier can have lost its profile afterwards (rebuilt suffix,
    # edited plan, carried chapter). Fix the table before writing another word
    # for that person. The next chapter is excluded: `next_job` above owns it.
    gap=coverage(db,branch,n,first_gap=True)['gaps']
    if gap:
        return ('character_plan',gap[0]['chapter'],
                {'entities':gap[0]['entities'],'instruction':'本章此前已规划，但下列拟出场人物在当前分支没有可用档案（修订重建、计划就地修改或章被携带后常见）。先核对当前事实与成长证据再补建档案；只补表达与行为依据，不改正文，不把提案当已发生事实。'})
    character_index_job=next_index_job(db,branch,n+1,json.loads(next_plan[0]))
    if character_index_job:return character_index_job
    payload = {}
    rev = db.execute('SELECT * FROM branches WHERE id=?',(branch,)).fetchone()
    if rev['status']=='candidate':
        original=atoms.ancestor_chapter_prose(db,rev['parent'],n+1)
        payload = {'original_prose':original or '', 'revision_reason':rev['reason'], 'instruction':'检查修订传播。表达类发现(重复/收束/节奏/声线)优先在被点名章用 patches 定点修改;未受影响的章可提交 reuse_original 省去重传正文。只有该章及全部前文版本均未变化、原两类审稿通过且事件可复算时才免逐章审稿;上游发生变化时仍重新 facts/reader 核对正文历史依赖。事实/因果类发现需要检查是否改正文并重登记事件。事实/数字/清单类目标先对账再动笔：用 impact 与正文检索列出该事实在各章的全部出现位置，一次把所有耦合章改平，对齐结论写进 repair_note；只改被点名章而其他章残留旧口径，修订整体复审判未完成。'}
        if execution.settings(db)['execution_mode']==execution.SINGLE:
            payload['instruction']=('检查修订传播。表达类缺陷在被点名章优先用 patches 定点修改，仍成立的正文可用 reuse_original 避免重传；'
                '本次任务提交后重新独立 facts/reader 核验，不沿用历史 pass。事实与数字目标先用 impact 和原文检索核对全部耦合章节，'
                '一次修复相关口径，在 repair_note 记录对齐结果；保留父级未完成目标，修订整体复审核对传播。')
    observation=chapter_length(db,branch,n+1)
    if observation:payload['chapter_length']=observation
    return 'draft',n+1,payload


def review_context_hash(db,branch,first,through,canonical_through=None):
    """Hash actual review dependencies locally; do not resend their contents.

    Current profile/design versions are conservatively bound because metadata
    does not provide a complete historical read-set. Only range plans and the
    relevant canonical prefix are bound, never unwritten future plans/events.
    The engine version is deliberately excluded: check_definitions carries the
    review semantics, so skill upgrades must not cancel in-flight reviews or
    void stored receipts.
    """
    canonical_through=through if canonical_through is None else canonical_through
    plans=[(row['number'],json.loads(row['data'])) for row in db.execute('SELECT number,data FROM plans WHERE branch=? AND number BETWEEN ? AND ? ORDER BY number',(branch,first,through))]
    volumes={plan.get('volume') for _,plan in plans}
    entities=set()
    for _,plan in plans:
        entities.update(plan.get('entities',[]))
        entities.update(choice['entity'] for choice in plan.get('character_choices',[]))
        for scene in plan.get('scenes',[]):
            entities.update(scene.get('present',[]));entities.update(scene.get('speakers',[]));entities.update(scene.get('props',[]))
            for intent in scene.get('dialogue_intents',[]):
                entities.add(intent['entity'])
                if intent.get('to'):entities.add(intent['to'])
    prose=[row['prose'] for row in db.execute('SELECT prose FROM chapters WHERE branch=? AND number BETWEEN ? AND ?',(branch,first,through))]
    for number,_ in plans:
        draft=load_draft(db,branch,number)
        if draft and isinstance(draft.get('prose'),str):prose.append(draft['prose'])
    state=replay(db,branch)
    for eid,entity in state['entities'].items():
        if any(name in text for name in [entity['name']]+entity.get('aliases',[]) for text in prose):entities.add(eid)
    versions={}
    for kind in ('character','design'):
        prefix=kind+':'+branch+':'
        for row in db.execute('SELECT key,value FROM meta WHERE substr(key,1,?)=? ORDER BY key',(len(prefix),prefix)):
            ident=row['key'][len(prefix):]
            if (kind=='character' and ident in entities) or (kind=='design' and ident in volumes|{'world'}):
                versions[kind+':'+ident]=digest(json.loads(row['value']))
    last=db.execute('SELECT hash FROM events WHERE branch=? AND chapter<=? ORDER BY chapter DESC,idx DESC LIMIT 1',
                    (branch,canonical_through)).fetchone()
    return digest({'schema':'novel-ledger-v2.review-context/1',
                   'check_definitions':[FACT_CHECKS,READER_CHECKS,LONG_CHECKS],
                   'contract':get(db,'contract'),'style_anchor':get(db,'style_anchor'),
                   'plans':plans,
                   'history':[(row['number'],row['hash']) for row in db.execute('SELECT number,hash FROM chapters WHERE branch=? AND number<? ORDER BY number',(branch,first))],
                   'event_prefix':last['hash'] if last else 'genesis','versions':versions})


def chapter_review_context_hash(db,branch,chapter):
    return review_context_hash(db,branch,chapter,chapter,chapter-1)


def task_review_context_hash(db,task,work):
    if work.get('chapter_session'):
        from .batchflow import signature
        return signature(db,task,work)
    if task['stage']=='chapter_review':
        return chapter_review_context_hash(db,task['branch'],task['chapter'])
    if task['stage'].endswith('_review'):
        return review_hash(db,task['branch'],work['payload']['first'],work['payload']['through'])
    return None


def review_hash(db,branch,first,through):
    return digest({'context':review_context_hash(db,branch,first,through),
                   'chapters':[(r['number'],r['hash']) for r in db.execute('SELECT number,hash FROM chapters WHERE branch=? AND number BETWEEN ? AND ? ORDER BY number',(branch,first,through))]})


def reviewed(db,branch,scope,n):
    row = db.execute('SELECT * FROM reviews WHERE branch=? AND scope=? AND through=?',(branch,scope,n)).fetchone()
    if not row:
        return False
    data = json.loads(row['data'])
    return row['input_hash']==review_hash(db,branch,data['first'],n)


def volume_development_context(db,branch,n):
    """Freeze volume intentions and measured progress for semantic closure review.

    Reference budgets explain allocation; they never decide whether a volume
    can close. Counts come from the same canonical projection as status, even
    for contracts that have no volume allocations.
    """
    from . import design
    from .projections import chapter_counts
    contract=get(db,'contract')
    plan=json.loads(db.execute('SELECT data FROM plans WHERE branch=? AND number=?',(branch,n)).fetchone()[0])
    volumes=contract['volumes'];index=next(i for i,v in enumerate(volumes) if v['id']==plan['volume'])
    volume=volumes[index]
    counts={row['volume']:row for row in chapter_counts(db,branch)}
    written=counts.get(volume['id'],{}).get('han',0)
    total=sum(row['han'] for row in counts.values())
    reference=volume.get('word_budget')
    low,high=contract['word_range']
    return {'volume':volume['id'],'purpose':volume['purpose'],'unit':'han_characters',
            'written_han':written,'reference_han':reference,
            'deviation_han':written-reference if reference is not None else None,
            'policy':'advisory_not_quota',
            'book':{'written_han':total,'word_range':[low,high],
                    'remaining_to_range_han':[max(0,low-total),max(0,high-total)]},
            'later_volumes':[{'volume':v['id'],'purpose':v['purpose'],'reference_han':v.get('word_budget'),
                              'written_han':counts.get(v['id'],{}).get('han',0),
                              'closed':bool(counts.get(v['id'],{}).get('closed',False))}
                             for v in volumes[index+1:]],
            'design':design.current(db,branch,volume['id'])}


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
            rows=db.execute('SELECT number FROM chapters WHERE branch=? AND number BETWEEN ? AND ? ORDER BY number',(branch,chapter,end)).fetchall()
            # Prose is delivered per chapter as separate files (attach_prose);
            # no concatenation markers that could contaminate quote coordinates.
            return scope+'_review',n,{'scope':scopekey,'first':chapter,'through':end,
                                    'prose_chapters':[r['number'] for r in rows],'checks':checks}
        chapter=end+1
    receipts = [{'chapter':r['through'],'input_hash':r['input_hash']} for r in db.execute('SELECT through,input_hash FROM reviews WHERE branch=? AND scope=? AND through BETWEEN ? AND ? ORDER BY through',(branch,scopekey,first,n))]
    payload={'scope':scope,'first':first,'through':n,'coverage_receipts':{'count':len(receipts),'first':first,'through':n,'scope':scopekey,'hash':digest(receipts)},
             'instruction':'分段全文已审；按 recall 分页核对主线、人物弧、跨章重复用语和承诺的跨段证据，不能把本任务称为一次性全书通读。修订起点由 finding 决定，证据位置不等于修订位置：evidence 只用于举证，可以引用任意章（含与问题章冲突的早期章）；要指定从哪一章开始改，用 target_chapter。表达类(重复/收束/节奏/声线)标 repair_route:"patch"：定点改被点名章，其余章可原文复用省去重传。事实/因果/认知/时间类标 repair_route:"revise"：从最早受影响章重验后缀。两者都不声明时默认在本次范围的最新章就地修订——回溯重写没被点名的章代价高，且可能把已正确的正文改坏，只在确有必要时才声明。轻微缺陷（异体字、错别字、标点、体例）记 note 级 finding 随 pass 提交，不要判 fix：fix 需要 blocker/quality 级发现作依据，为留下记录而升格会触发整条修订链。本次修订范围之外的历史遗留问题（例如修订 5–10 章时在 1–3 章读到的旧缺陷）同样记 note 并写进报告——修订分支只负责本次修订的传播正确性，范围外的问题由主控决定是否另开修订。免逐章审稿须本章及全部前文版本未变。',
             'checks':checks + ('repetition',) + (('ending','length','core_promises') if scope=='book' else ())}
    if scope=='volume':
        payload['checks']+=('volume_development',)
        payload['volume_development']=volume_development_context(db,branch,n)
        payload['instruction']+=' volume_development 必须用正文证据判断本卷职责和重要过程、选择代价及余波是否充分演出；sequence_refs 出现不等于节拍完成。design 是 proposal_not_canon，须与实际正文对照。结合实际篇幅、参考预算偏离和全书剩余区间说明收卷是否合理，以及后卷职责与承载路径是否成立；未配置参考预算仍须审查展开充分性。预算仅供解释，不因字数差额、固定比例或章数自动判失败，也不能用后卷自动重分配代替展开判断。'
    if scope=='revision':
        payload['checks']+=('revision_intent',)
        from .carriage import carried_chapters
        carried=carried_chapters(db,branch)
        if carried:
            payload['carried_chapters']=carried
            payload['instruction']+=(' carried_chapters 未经逐章重审：引擎核验事件重放与依赖差分后携带了原审结论；'
                                     '复核须抽查这些章与修订目标的关联（尤其被改事实的旧口径残留），发现残留判 fix 并指认章与位置。')
        payload['instruction']+=' revision_intent 须对照 revision_context.chain 中每个修订目标核对已写范围，不能仅核验最后一次文风修改。超出 through 的长期展开目标须在有效设计和后续计划中继续保留，不要求本次历史修订写完未来全卷。旧稿传播范围结束和沿用的 volume_end 不证明父级结构目标完成；过早收卷或已写内容违背目标须 fix 并举证。原因有歧义须报告，不能自行视为已撤销。事实对齐类目标须逐章举证全部耦合章口径一致；被点名章已改而其他章残留旧口径的判 fix，并列出残留章与位置。'
    return scope+'_review',n,payload


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


LEGACY_ROOT_FILES = ('novel.db',)
LEGACY_ROOT_GLOBS = ('st[0-9]*.tmp.json', 'st[0-9]*.err', 'tmp_context_*.json', 'workch*.json')
LEGACY_WORK_FILES = ('next_task.json', 'plans_data.json', 'vol_data.json')
LEGACY_WORK_GLOBS = ('_tmp_*',)
LEGACY_DIRS = ('_review_tmp',)


def sweep_legacy(project, apply=True):
    """Sweep artifacts left by retired drivers and older engine versions.

    Matches only exact generated names and tight patterns at the project root
    and the top of work/ — never contract.json, novel.sqlite3*, book.html,
    export/, evaluation/ or author folders. novel.db is removed only when
    empty; a non-empty legacy database is kept and reported. With apply=False
    the same list is returned without deleting anything (doctor uses this)."""
    import shutil
    base = Path(project).resolve()
    removed, kept = [], []
    def sweep_dir(directory, files, globs, prefix):
        if not directory.is_dir():
            return
        for name in files:
            path = directory/name
            if not path.is_file():
                continue
            if name == 'novel.db' and path.stat().st_size:
                kept.append({'path': prefix+name, 'reason': '非空旧库,请人工确认后再处理'})
                continue
            if apply:
                path.unlink()
            removed.append(prefix+name)
        for pattern in globs:
            for path in sorted(directory.glob(pattern)):
                if path.is_file():
                    if apply:
                        path.unlink()
                    removed.append(prefix+path.name)
        for name in LEGACY_DIRS:
            path = directory/name
            if path.is_dir():
                if apply:
                    shutil.rmtree(path, ignore_errors=True)
                removed.append(prefix+name+'/')
    sweep_dir(base, LEGACY_ROOT_FILES, LEGACY_ROOT_GLOBS, '')
    sweep_dir(base/'work', LEGACY_WORK_FILES, LEGACY_WORK_GLOBS, 'work/')
    # Retired-driver databases hold author data: relocate, never delete.
    relocated = []
    for name in ('ledger.sqlite', 'ledger.sqlite3'):
        source = base/name
        if not source.is_file():
            continue
        target_dir = base/'work'/'backup-legacy-db'
        target = target_dir/name
        if apply:
            target_dir.mkdir(parents=True, exist_ok=True)
            suffix = 0
            while target.exists():
                suffix += 1
                target = target_dir/f'{name}.{suffix}'
            shutil.move(str(source), str(target))
        relocated.append({'path': name, 'moved_to': 'work/backup-legacy-db/'+target.name,
                          'reason': '旧版驱动的账本文件，已备份迁移；确认无用后可自行删除'})
    return {'removed': removed, 'kept': kept, 'relocated': relocated}


def materialize_prose(db, project, work):
    """Write every prose file the pack references from the authoritative store.

    Pack references carry path+hash only; resume paths re-materialize from the
    same store, so a crashed dispatch leaves nothing dangling. File bytes equal
    the stored prose exactly, keeping {chapter,start,quote} coordinates valid.
    """
    base = Path(project).resolve()
    refs = set()
    def visit(node):
        if isinstance(node, dict):
            for prefix in ('prose', 'original_prose'):
                path = node.get(prefix+'_path')
                hashed = node.get(prefix+'_hash')
                if isinstance(path, str) and isinstance(hashed, str):
                    refs.add((path, hashed))
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)
    visit(work)
    written = []
    for path, prose_hash in sorted(refs):
        prose = get(db, 'candidate_text:' + prose_hash)
        require(isinstance(prose, str) and digest(prose) == prose_hash,
                'prose_store', '正文快照缺失或校验失败：' + prose_hash[:12])
        target = base/path
        target.parent.mkdir(parents=True, exist_ok=True)
        data = prose.encode('utf-8')
        if not target.exists() or target.read_bytes() != data:
            target.write_bytes(data)
        written.append(path)
    return written


def task_files(db, project, task):
    root = Path(project).resolve()/work_rel(task)
    root.mkdir(parents=True,exist_ok=True)
    work = json.loads(task['input'])
    # Pretty-printed delivery view: workers read it with line-oriented tools
    # that truncate long lines; the DB-stored input stays the hashed truth.
    from . import reviewflow
    if reviewflow.enabled(work):reviewflow.prime_batch_record(db,task,work)
    gated = reviewflow.enabled(work) and reviewflow.record(db, task['id']) is None
    if gated:
        # Neither history files nor the contract are delivered before the seal.
        (root/'input.json').unlink(missing_ok=True)
        for page in root.glob('reading*.md'):page.unlink()
        (root/'reading.manifest.json').unlink(missing_ok=True)
        delivered = {k: work[k] for k in ('branch','stage','chapter')}
        delivered['payload'] = {k:work['payload'][k] for k in ('prose_path','prose_hash')}
        if work.get('chapter_session',{}).get('role')=='reviewer':
            from .batchflow import review_sources
            delivered['payload']['prose_files']=review_sources(db,work)
        prose_files = materialize_prose(db, project, delivered)
    else:
        delivered = work
        from .taskio import envelope
        # The envelope is written as an index plus per-type files: one object
        # whose size grows with the book forced workers to read it in pages,
        # and every page stayed in context for the rest of the run.
        view=envelope(project, work, root)
        write_json(root/'input.json', view)
        prose_files = materialize_prose(db, project, work)
    base = work_rel(task)
    result = {'schema':'novel-ledger-v2.task/1','task_id':task['id'],'stage':task['stage'],
              'chapter':task['chapter'],'branch':task['branch'],'input_hash':task['input_hash'],
              'project':str(Path(project).resolve()),
              'input_path':(base+'/input.json'),'output_path':(base+'/result.json'),
              'task_path':(base+'/task.json'),
              'path_base':'routes are relative to project; join with project before reading/writing',
              'state':'pending'}
    if prose_files:
        result['prose_files'] = prose_files
    if task['stage']=='chapter_review':
        if gated and work.get('chapter_session',{}).get('role')=='reviewer':
            result['first_read_files']=delivered['payload']['prose_files']
        result['first_read_path']=work['payload']['prose_path']
        result['first_read_hash']=work['payload']['prose_hash']
    if 'writer_brief' in work:
        from .design import render_brief
        brief=root/'brief.md'
        brief.write_text(render_brief(work),encoding='utf-8')
        result['brief_path']=base+'/brief.md'
    if not gated and work.get('capabilities',{}).get('reading_view')==READING_PROTOCOL:
        from .taskview import render
        try:result['reading_view']=render(project,root,view,result['input_path'],task['id'],task['input_hash'])
        except Exception as error:
            result['reading_view_error']={'code':'task_view_unavailable','message':str(error),'recover':'使用原 input_path 和必读分片继续；视图只是派生文件'}
            log(db,'task_view_unavailable',{'task':task['id'],'message':str(error)})
    from .taskio import delivery, checkpoint
    delivery(db, project, task, work, result)
    if work.get('capabilities'):
        from .hostflow import dispatch_spec
        write_json(root/'host-dispatch.json',dispatch_spec(result,work.get('writer_session_policy','batch')))
        result['host_dispatch_path']=base+'/host-dispatch.json'
    from .dependencies import record_initial
    record_initial(db,task['id'],delivered)
    write_json(root/'task.json',result)
    checkpoint(db, project)
    return result


def next_task(db,project,after=None,brief=False,preparation_bundle=None,chapter_session=None,review_session=None):
    # Dependency-derived scope: provably unaffected suffix chapters carry over
    # before dispatching anything (own transactions; no-op unless idle).
    from .carriage import fast_forward
    fast_forward(db,get(db,'work_branch'))
    created=False
    with transaction(db):
        if after is not None:
            previous=db.execute('SELECT id,state,branch FROM tasks WHERE id=?',(after,)).fetchone()
            require(previous,'handoff_task','交接任务不存在，不能凭 Worker 的口头完成继续派工')
            if previous['branch']!=get(db,'work_branch'):
                return {'stage':'resync','after_task':after,'handoff_reason':'branch_changed',
                        'note':'复核 fix 等流程已切换工作分支；旧分支的批次/审稿会话随之失效。按本响应 status 的当前 pending 任务继续派工，不重派旧分支任务。',
                        **status(db,brief=True)}
            if previous['state']!='done':
                return {'stage':'wait_for_submit' if previous['state']=='pending' else 'resync',
                        'after_task':after,'after_state':previous['state'],**status(db,brief=True)}
            latest=db.execute("SELECT id FROM tasks WHERE branch=? AND state='done' ORDER BY rowid DESC LIMIT 1",(previous['branch'],)).fetchone()
            if latest['id']!=after:
                return {'stage':'resync','after_task':after,'latest_completed_task':latest['id'],**status(db,brief=True)}
        if get(db,'status') in ('paused','completed'):
            return status(db,brief=brief)
        if review_session is not None:
            from .reviewrun import matches, allowed
            if not matches(db,review_session):
                return {'stage':'review_done','reason':'正式正文、约定、资料、目标或分支已改变，主控取得最新任务。'}
        if chapter_session is not None:
            from .batchflow import active
            batch=active(db)
            if not batch or batch['id']!=chapter_session['batch']:
                return {'stage':'chapter_batch_done','reason':'批次已结束或后续硬前件失效，主控接手；不启动下一批。'}
        pending = db.execute("SELECT * FROM tasks WHERE state='pending'").fetchone()
        if pending:
            if review_session is not None and not allowed(json.loads(pending['input']),review_session):
                return {'stage':'review_done','reason':'下一任务不属于本次独立复审，交回主控。'}
            if chapter_session is not None and json.loads(pending['input']).get('chapter_session')!=chapter_session:
                return {'stage':'chapter_batch_done','reason':'批次或角色边界由主控接手。'}
            if preparation_bundle is not None:
                pending_work=json.loads(pending['input'])
                if pending['stage'] not in ('context_scan','context_reduce') or pending_work.get('payload',{}).get('bundle')!=preparation_bundle:
                    return {'stage':'preparation_done','reason':'下一任务由主控接手；材料 Worker 不跨批次或进入写作、审稿。'}
            require(pending['stage'] not in ('facts','reader'),'unsupported_review','旧分阶段审稿任务不受支持；仅支持 chapter_review 新流程')
            old_work=json.loads(pending['input'])
            from .reviewrun import matches as review_matches
            review_changed=bool(old_work.get('review_session')) and not review_matches(db,old_work['review_session'])
            require(pending['stage']!='chapter_review' or 'review_context_hash' in old_work,'unsupported_review','综合审稿任务必须含冻结的审稿上下文签名')
            signature=task_review_context_hash(db,pending,old_work)
            if review_changed or (signature is not None and 'review_context_hash' in old_work and old_work['review_context_hash']!=signature):
                require(not pending['lease_owner'] or (pending['lease_until'] or 0)<=time.time(),'lease','审稿依赖已变化，但旧任务仍有活动租约；先等待原worker退出')
                db.execute("UPDATE tasks SET state='cancelled' WHERE id=?",(pending['id'],))
                log(db,'review_context_refresh',{'old_task':pending['id'],'reason':'作者约定、声线、计划或事实/人物/设计版本已变化'})
                pending=None
        if pending:
            task = dict(pending)
            work=json.loads(task['input'])
        else:
            execution.finish_upgrade(db)
            if review_session is not None:
                from .batchflow import choose as batch_choose
                stage,n,payload = batch_choose(db,_choose_single,allow_new_batch=False)
            else:stage,n,payload = choose(db)
            if review_session is not None:
                from .reviewrun import STAGES
                if stage not in STAGES:
                    return {'stage':'review_done','reason':'复审已结束；执笔、规划、合并、完本和作者边界由主控处理。'}
            if chapter_session is not None and stage in ('await_author','promote','complete','target_reached'):
                return {'stage':'chapter_batch_done','reason':'达到主控或作者边界。'}
            if preparation_bundle is not None and stage in ('await_author','promote','complete'):
                return {'stage':'preparation_done','reason':'到达主控或作者边界，材料 Worker 结束。'}
            if stage in ('await_author','target_reached'):
                return {'stage':stage, **payload}
            if stage=='promote':
                promote(db)
                return {'stage':'revision_promoted', **status(db,brief=brief)}
            if stage=='complete':
                return complete_inner(db)
            branch = get(db,'work_branch')
            from .patches import prepare
            payload=prepare(db,stage,payload)
            work = pack(db,branch,n,stage,payload)
            if execution.settings(db)['execution_mode']==execution.SINGLE:
                work.pop('chapter_session',None)
            if stage in ('draft','repair','redesign','chapter_review') and work.get('plan',{}).get('timeline'):
                from .schedule import bounds as time_bounds
                clock=work.get('state',{}).get('clock')
                if clock is not None:
                    expected=time_bounds(work['plan'],clock)
                    require(expected[1]>=clock,'time_plan','签发前发现日程已过期；按现有计划修订路线处理',
                            {'chapter':n,'before':clock,'expected_clock':list(expected[:2])})
                    work['time_constraint']={'protocol':TIMELINE_PROTOCOL,'before':clock,
                                             'expected_clock':list(expected[:2]),'basis':expected[2]}
            stage=work['stage']
            if review_session is not None and not allowed(work,review_session):
                return {'stage':'review_done','reason':'下一任务的复审绑定已改变，主控接手。'}
            if preparation_bundle is not None and (stage not in ('context_scan','context_reduce') or work['payload'].get('bundle')!=preparation_bundle):
                return {'stage':'preparation_done','reason':'本批材料已完成或依赖变化，主控取得最新正式任务。'}
            if chapter_session is not None and work.get('chapter_session')!=chapter_session:
                return {'stage':'chapter_batch_done','reason':'写作与独立审稿必须换会话；不跨批次、角色或作者边界。'}
            signature=task_review_context_hash(db,{'branch':branch,'chapter':n,'stage':stage},work)
            if signature is not None:
                work['review_context_hash']=signature
            tid = uuid.uuid4().hex
            work['capabilities']={'evidence_input':EVIDENCE_INPUT,
                                  'reading_view':READING_PROTOCOL, 'timeline':TIMELINE_PROTOCOL}
            if stage=='plan':work['capabilities']['timeline_input']=TIME_INPUT_PROTOCOL
            if stage=='chapter_review':
                work['capabilities']['review_scrutiny']=SCRUTINY_PROTOCOL
            work['writer_session_policy']=execution.settings(db)['writer_session_policy']
            work['execution_policy']=execution.freeze(db,work,tid)
            if execution.controller_owned(work) and stage in execution.WRITER_STAGES:
                work['guide']+='\n[单章执行] 本任务只写或修当前章，提交 summary 与具体 self_check，不提交 candidate_events。复用正文仍须新的独立综合审稿，不能复用历史 pass；提交后结束，由主控交接。'
            fingerprint = digest(work)
            task = {'id':tid,'branch':branch,'stage':stage,'chapter':n,'input_hash':fingerprint,'input':dump(work)}
            db.execute('INSERT INTO tasks(id,branch,stage,chapter,input_hash,input,state) VALUES (?,?,?,?,?,?,?)',(tid,branch,stage,n,fingerprint,dump(work),'pending'))
            created=True
            log(db,'dispatch',{'task':tid,'stage':stage,'chapter':n,'branch':branch})
    if created:
        # Consolidate output: finished task directories are derived views; keep the
        # newest few for debugging and let `tidy` remove the rest on demand.
        removed=prune_work(db,project)
        if removed:
            with transaction(db):log(db,'work_tidy',{'removed':removed,'kept':3})
    try:
        return task_files(db,project,task)
    except Exception:
        # Envelope materialization failed after the row committed: a pending row
        # without task.json wedges every later advance on the same error (QC
        # session 233b §二.4). Cancel once; the next advance rebuilds cleanly.
        if created and db.execute("SELECT 1 FROM tasks WHERE id=? AND state='pending'",(task['id'],)).fetchone():
            with transaction(db):
                db.execute("UPDATE tasks SET state='cancelled' WHERE id=?",(task['id'],))
                log(db,'dispatch_files_failed',{'task':task['id'],'error':'envelope build failed'})
        raise


def parse_prose(data):
    prose = data.get('prose')
    require(isinstance(prose,str) and prose.strip(), 'prose', '必须提交非空正文 prose')
    require('\x00' not in prose, 'prose', '正文包含无效字符')
    return prose


def nonempty_scene(scene,state):
    location=scene.get('place')
    return ((location in state.get('places',{}) if isinstance(location,str) else False) or (location is None and isinstance(scene.get('label'),str) and bool(scene['label'].strip()))) and isinstance(scene.get('present',[]),list) and all(e in state['entities'] for e in scene.get('present',[]))


def validate_plan(db,branch,start,data,explicit_time=False):
    plans = data.get('chapters')
    require(isinstance(plans,list) and 1<=len(plans)<=10, 'plan','每批计划为 1～10 章，通常 5～6 章')
    volumes = [v['id'] for v in get(db,'contract')['volumes']]
    from . import design
    layered=design.enabled(db)
    expected_volume=design.volume_for(db,branch,start) if layered else None
    state = replay(db,branch)
    last_volume = None
    prior = db.execute('SELECT data FROM plans WHERE branch=? AND number=?',(branch,start-1)).fetchone()
    if prior:
        last_volume = json.loads(prior[0])['volume']
    # One rejection answers the whole batch: every failing chapter and field is
    # collected before raising; valid rows are only inserted after the pass.
    batch=Batch(); inserts=[]; broken=False
    from .characters import enabled as characters_enabled, validate_scenes
    for i,p in enumerate(plans,start):
        mark=len(batch.issues)
        context={'chapter':i}
        if broken:context['cascade']=True
        if not isinstance(p,dict) or p.get('number')!=i:
            batch.add('plan','章号必须连续且从任务章号开始',**context); broken=True; continue
        volume=p.get('volume')
        batch.require(all(p.get(k) for k in ('goal','conflict','change','volume')) and volume in volumes,'plan','章计划缺少目标、阻力、变化或合法卷 ID',**context)
        entities=p.get('entities')
        batch.require(isinstance(entities,list) and entities and all(e in state['entities'] for e in entities),'plan','entities 应引用已登记稳定实体 ID；新人物在正文事实阶段登记',**context)
        scenes=p.get('scenes',[])
        if batch.require(isinstance(scenes,list) and all(isinstance(s,dict) for s in scenes),'scene','scenes 须为场景对象数组',**context):
            for scene in scenes:
                batch.require(nonempty_scene(scene,state),'scene','场景须引用已登记 place 或给出未确立新场所的 label；present 使用实体 ID',
                        **context, **{'known_places': sorted(state.get('places',{}))[:12],
                                      'hint': '已登记场所把 place 写成其中的 ID；全新场所删掉 place 只写 label，present 一律实体 ID'})
        if not layered:
            design.validate_chapter_execution(p,sink=batch,chapter=i)
        author_target=get(db,'contract').get('chapter_target_han')
        execution=p.get('execution',{})
        if isinstance(execution,dict) and author_target and execution.get('target_han',author_target)<author_target:
            batch.require(isinstance(execution.get('target_reason'),str) and execution['target_reason'].strip(),
                    'chapter_target','刻意短章须事先在 execution.target_reason 说明本章作用及较短目标的理由',**context)
        dependencies=p.get('dependencies',[])
        batch.require(isinstance(dependencies,list) and all(type(n) is int and 1<=n<start for n in dependencies),'plan','历史依赖须为已提交章号',**context)
        batch.require(not last_volume or not (isinstance(volume,str) and volume in volumes) or volumes.index(volume)>=volumes.index(last_volume),'plan','卷序不能倒退',**context)
        from .schedule import normalize as normalize_schedule
        try:normalize_schedule(p,state['clock'],explicit_required=explicit_time)
        except Error as error:batch.capture(error,**context)
        if last_volume and last_volume!=volume:
            prev = plans[i-start-1] if i>start else json.loads(prior[0])
            batch.require(isinstance(prev,dict) and prev.get('volume_end'), 'plan','跨卷前须标明卷末复核',**context)
        if p.get('major_turn'):
            alternatives = p.get('alternatives',[])
            batch.require(isinstance(alternatives,list) and all(isinstance(a,str) and a.strip() for a in alternatives) and len(alternatives)>=2 and len(set(alternatives))==len(alternatives) and p.get('selection_reason'), 'plan','重要转折需两个不同方案和择优依据',**context)
        if p.get('book_end'):
            branch_meta=db.execute('SELECT status,through FROM branches WHERE id=?',(branch,)).fetchone()
            batch.require(branch_meta['status']!='candidate' or i>=branch_meta['through'],'revision','候选修订不能提前结束而跳过原有后文复核',**context)
            batch.require(i==start+len(plans)-1 and volume==volumes[-1] and p.get('volume_end'), 'plan','终章须是末卷最后一章并触发卷审',**context)
        if characters_enabled(db):validate_scenes(p,state,sink=batch,chapter=i)
        if layered:
            batch.require(not p.get('volume_end') or i==start+len(plans)-1,'plan','分层计划的卷末必须是本批最后一章，不能在卷审后继续同卷',**context)
            if batch.require(volume==expected_volume and design.usable(db,branch,expected_volume),'design','每批仅细化已准备的当前卷，跨卷前先完成分卷设计',**context):
                world=design.current(db,branch,'world');volume_row=design.current(db,branch,expected_volume)
                known=set(volume_row['data']['location_ids'])
                design.validate_brief(p,state,known,volume_row['data'],sink=batch,chapter=i)
                # Repeated shapes may be deliberate; pacing and repetition require
                # semantic review rather than a forced category rotation.
                # Major turns must cite which spine touchpoints they advance when a
                # spine exists; older spineless world designs are grandfathered.
                spine=design.spine_ids(db,branch)
                refs=p.get('spine_refs',[])
                batch.require(isinstance(refs,list) and all(isinstance(r,str) for r in refs),'plan','spine_refs 为脊柱触点 ID 数组',**context)
                if spine is not None:
                    if p.get('major_turn'):batch.require(refs,'plan','重大转折须引用 event_spine 触点（spine_refs），不能脱离全书结构单独成立',**context)
                    for r in refs:
                        batch.require(r in spine,'plan',f'spine_refs 引用了不存在的脊柱触点：{r}',**context)
                p['_design_versions']={'world':world['hash'],'volume':volume_row['hash']}
        if len(batch.issues)==mark:
            inserts.append((branch,i,dump(p)))
        else:
            broken=True
        if isinstance(volume,str) and volume in volumes:
            last_volume=volume
    batch.raise_if_any()
    from .schedule import validate_sequence
    validate_sequence(plans,state['clock'])
    for row in inserts:
        db.execute('INSERT INTO plans VALUES (?,?,?)',row)


# P5: the mandatory-check list is a mechanical fact. It lives here and
# references/protocol.md must list exactly these ids; the architecture test
# asserts the two agree, so neither can drift silently.
REVIEW_MANDATORY_CHECKS = frozenset((
    'coverage', 'continuity', 'read_on', 'character', 'change', 'language',
    'substance', 'causality', 'character_arcs', 'payoffs', 'pacing', 'ending',
    'length', 'core_promises', 'stable_facts', 'spatial',
    'character_consistency', 'voice_evolution', 'voice_match',
    'volume_development', 'revision_intent'))


def validate_review(data,checks,sources):
    from .patches import validate_scope
    validate_scope(data)
    require(data.get('verdict') in ('pass','fix'), 'review','审稿结论只能 pass/fix')
    rows = data.get('checks')
    require(isinstance(rows,list) and all(isinstance(r,dict) for r in rows) and {r.get('id') for r in rows}==set(checks) and len(rows)==len(checks), 'review','审稿检查项不完整或重复')
    batch=Batch()
    mandatory=REVIEW_MANDATORY_CHECKS
    for index,row in enumerate(rows):
        where={'check_id':row.get('id'),'row_index':index}
        batch.require(row.get('result') in ('pass','fix') and isinstance(row.get('reason'),str) and row['reason'].strip(),
                'review','每项需要结论及具体理由',**where)
        refs = row.get('evidence',[])
        if batch.require(isinstance(refs,list) and all(isinstance(ref,dict) for ref in refs),'review','evidence 必须为对象数组',**where):
            exception=row.get('not_applicable')
            batch.require(bool(refs) or (row.get('id') not in mandatory and isinstance(exception,str) and exception.strip()),
                    'review','核心审稿项必须有正文证据；可选项确不适用须说明具体原因',**where)
            for item in refs:
                if item.get('chapter') not in sources:
                    batch.add('review','证据不属于本次有效审稿范围',**where,submitted_chapter=item.get('chapter'))
                    continue
                try:evidence(sources[item['chapter']],item,item['chapter'])
                except Error as error:batch.capture(error,**where)
    findings = data.get('findings',[])
    if not batch.require(isinstance(findings,list) and all(isinstance(f,dict) for f in findings),'finding','findings 必须为数组'):
        findings=[f for f in findings if isinstance(f,dict)]
    for index,item in enumerate(findings):
        where={'finding_index':index}
        batch.require(item.get('impact') and item.get('goal') and item.get('severity') in ('blocker','quality','note'),
                'finding','发现须含读者影响、修订目标和级别',**where)
        if 'target_chapter' in item:
            batch.require(type(item['target_chapter']) is int and item['target_chapter'] in sources,
                    'finding','target_chapter 须为本次审稿范围内的章号；它指定修订起点，与举证位置无关',**where)
        if 'repair_route' in item:
            batch.require(item['repair_route'] in transitions.REPAIR_ROUTES,
                    'finding','repair_route 取 patch（表达类定点改）或 revise（事实类回溯重验后缀）',**where)
        ref=item.get('evidence',{})
        if not isinstance(ref,dict) or ref.get('chapter') not in sources:
            batch.add('finding','发现须定位具体正文',**where);continue
        try:evidence(sources[ref['chapter']],ref,ref['chapter'])
        except Error as error:batch.capture(error,**where)
    needs_fix = any(isinstance(r,dict) and r.get('result')=='fix' for r in rows) or any(isinstance(f,dict) and f.get('severity') in ('blocker','quality') for f in findings)
    batch.require((data.get('verdict')=='fix')==needs_fix,'review','结论与检查/发现不一致')
    batch.require(not needs_fix or any(isinstance(f,dict) and f.get('severity') in ('blocker','quality') for f in findings),
            'review','返工须提供可执行发现')
    batch.raise_if_any()


def repair(db,branch,n,draft,findings,scope='chapter'):
    attempts = draft.get('attempts',0)+1
    draft.update(attempts=attempts, findings=findings,repair_scope=scope)
    if isinstance(draft.get('events'),list):
        draft['prior_event_snapshot']={'events':copy.deepcopy(draft['events']),'prose':draft['prose'],
                                       'verified':draft.get('facts_context_hash') is not None}
    # Preserve every candidate in the task log/receipt. Last good prose stays available.
    # INV-4: the semantic and length ladders both feed draft['stage']. Taking
    # the stricter side stops a length shortfall from demoting a chapter that
    # the semantic ladder already escalated to redesign.
    draft['stage'],_=transitions.resolve_stage(attempts,draft.get('length_attempts',0))
    if attempts>3:
        put(db,'status','paused')
        put(db,'pause_reason',{'code':'quality_exhausted','chapter':n,'findings':findings})
    draft.pop('facts_review',None)
    draft.pop('reader_review',None)
    save_draft(db,branch,n,draft)


def validate_hook_deadlines(state,chapter):
    overdue=((hid,hook) for hid,hook in state['hooks'].items() if hook['status']=='open' and hook['due']<=chapter)
    first=next(overdue,None)
    if first:
        total=1+sum(1 for _ in overdue)
        hid,hook=first
        raise Error('overdue_hook',f'第 {chapter} 章结算后仍有 {total} 项到期承诺；请核对首项并在本章兑现、关闭或按因果合法延期',
                    {'chapter':chapter,'total':total,'first':{'id':hid,'due':hook['due']},
                     'retrieval':{'command':'recall','topic':hid,'instruction':'按此话题查询完整埋设和变更证据；处理后再次提交会报告下一项'}})


def validate_reads(value):
    """Ledger coordinates the chapter's prose actually reads (P3).

    Carry decides "this chapter is unaffected" by intersecting changed ledger
    keys with the chapter's read-set. That read-set is derived structurally —
    plan entities plus entities named in the prose — so a chapter that refers
    to a value by description ("那笔本钱") contributes nothing, and could be
    carried while its prose asserted a value the revision had just changed.

    The writer knows what it read; this is where it says so. Declaring reads can
    only refuse more carries, never allow one, so an incomplete declaration is
    not a safety regression and an absent one keeps the previous behaviour.
    """
    require(isinstance(value,list) and all(isinstance(item,str) and item.strip() for item in value),
            'reads','reads 为账本坐标字符串数组，如 ["周行:装备","patron:loan"]；它声明本章正文依赖的账本键')
    return sorted(set(value))


def commit_draft(db,branch,n,draft):
    state=replay(db,branch)
    plan_row=db.execute('SELECT data FROM plans WHERE branch=? AND number=?',(branch,n)).fetchone()
    require(plan_row is not None,'plan','该章没有章计划（可能被设计重存或 replan 删除）；先完成规划再执笔',
            {'chapter':n,'recover':'运行 advance 重新取得规划任务；已有草稿的场景走审稿修订或 replan，不要重复提交'})
    plan=json.loads(plan_row[0])
    require(n==head(db,branch)+1,'sequence','不能跳章提交')
    observation=chapter_length(db,branch,n,draft['prose'])
    require(not observation or observation['status']!='needs_development','chapter_development',
            '作者已明确本章目标；须先保留短稿并完成自然展开及语义复核，不能直接入账')
    stored={k:draft[k] for k in ('events','summary','facts_review','reader_review')}
    stored.update(facts_context_hash=draft.get('facts_context_hash'),reader_context_hash=draft.get('reader_context_hash'))
    if draft.get('numeric_correction'):stored['numeric_correction']=copy.deepcopy(draft['numeric_correction'])
    from .budget import han_count
    stored.update(volume=plan['volume'],prose_hash=digest(draft['prose']),han_characters=han_count(draft['prose']))
    # Declared read-set travels with the chapter so a later revision can carry
    # it accurately instead of guessing from entity names (P3, INV-12).
    if draft.get('reads'):stored['reads']=list(draft['reads'])
    if observation:stored['chapter_length']=observation
    db.execute('INSERT INTO chapters VALUES (?,?,?,?,?)',(branch,n,draft['prose'],dump(stored),digest(draft['prose'])))
    append_events(db,branch,n,draft['prose'],draft['events'],state)
    validate_hook_deadlines(state,n)
    db.execute('DELETE FROM drafts WHERE branch=? AND number=?',(branch,n))
    log(db,'commit',{'branch':branch,'chapter':n,'hash':digest(draft['prose'])})


def prior_events(db, branch, n):
    """Previously registered events for chapter n: the newest facts receipt on
    this branch (repair loop), else the nearest ancestor's committed chain
    (revision re-drafts). Baseline for re-registration accounting."""
    draft=load_draft(db,branch,n)
    snapshot=draft.get('prior_event_snapshot') if draft else None
    if isinstance(snapshot,dict) and isinstance(snapshot.get('events'),list):
        return {**copy.deepcopy(snapshot),'source':'repair'}
    for row in db.execute("SELECT stage,receipt,input FROM tasks WHERE branch=? AND chapter=? AND stage='chapter_review' AND receipt IS NOT NULL ORDER BY rowid DESC", (branch, n)):
        receipt_data=json.loads(row['receipt']).get('data', {})
        events = receipt_data.get('facts',{}).get('events')
        if isinstance(events, list) and events:
            old_work=json.loads(row['input'])
            return {'events':events,'source':'repair','prose':old_work.get('payload',{}).get('prose'),
                    'verified':False}
    ancestor = db.execute('SELECT parent FROM branches WHERE id=?', (branch,)).fetchone()
    while ancestor and ancestor[0]:
        rows = db.execute('SELECT data FROM events WHERE branch=? AND chapter=? ORDER BY idx', (ancestor[0], n)).fetchall()
        if rows:
            prose=db.execute('SELECT prose FROM chapters WHERE branch=? AND number=?',(ancestor[0],n)).fetchone()
            return {'events': [json.loads(r[0]) for r in rows], 'source': 'revision','prose':prose[0] if prose else None}
        ancestor = db.execute('SELECT parent FROM branches WHERE id=?', (ancestor[0],)).fetchone()
    return None


ENVELOPE_KEYS = {'schema', 'task_id', 'input_hash', 'session', 'lease_owner', 'data'}


def normalize_receipt(db, result, record=True, bound_task=None):
    """Structural-only repair of worker receipts into result/1 envelopes.

    Workers frequently emit stage payloads at the top level, omit task_id or
    mistype it by one character. Task-directory outputs bind to that task;
    unscoped raw attempts retain their first binding across handoffs/restarts.
    Content is never rewritten. Semantics stay strict afterwards."""
    if not isinstance(result, dict):
        return result
    claimed = result.get('task_id')
    known = db.execute('SELECT * FROM tasks WHERE id=?',(claimed,)).fetchone() if isinstance(claimed,str) else None
    scoped=db.execute('SELECT * FROM tasks WHERE id=?',(bound_task,)).fetchone() if bound_task is not None else None
    require(bound_task is None or scoped is not None,'task','回执绑定的任务不存在')
    require(not scoped or not known or scoped['id']==known['id'],'task','回执任务与输出目录绑定的原任务不一致')
    needs_identity = not known
    needs_schema = result.get('schema') != 'novel-ledger-v2.result/1'
    needs_nesting = not isinstance(result.get('data'), dict)
    needs_version=result.get('input_hash') is None
    if not (needs_identity or needs_schema or needs_nesting or needs_version):
        return result
    binding_key='receipt_binding:'+digest(result)
    binding=get(db,binding_key)
    if scoped is not None or known is not None:
        target=scoped if scoped is not None else known
    elif binding is not None:
        target=db.execute('SELECT * FROM tasks WHERE id=?',(binding,)).fetchone()
        require(target is not None,'task','原回执绑定的任务不存在，不能改投当前任务')
    elif (pending:=db.execute("SELECT * FROM tasks WHERE state='pending'").fetchone()) is not None:
        target=pending
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
    if record:
        with transaction(db):
            if needs_identity and binding is None:put(db,binding_key,task_id)
            log(db, 'receipt_normalized', {'task': task_id,
                                       'fixed': {'schema': needs_schema, 'task_id': claimed != task_id,
                                                 'input_hash': result.get('input_hash') is None, 'data_nested': 'data' not in result}})
    return normalized


def validated_fact_events(db,branch,n,draft,work,data):
    events=data.get('events')
    require(isinstance(events,list) and isinstance(data.get('summary'),str) and data['summary'].strip(),'facts','需 events 和非空摘要')
    prior=work['payload'].get('prior_events')
    if isinstance(prior,list) and prior:
        def anchor(ev):
            clone=dict(ev)
            for key in EVENT_EVIDENCE_FIELDS:
                ref=clone.get(key)
                if isinstance(ref,dict):clone[key]={k:v for k,v in ref.items() if k!='start'}
            return dump(clone)
        auto_idx=work['payload'].get('auto_carry')
        require(isinstance(auto_idx,list),'event_accounting','综合审稿前序事件必须含 auto_carry')
        # v1.9: the engine carries prior events whose evidence still
        # uniquely resolves; the receipt only declares the delta.
        auto={i for i in auto_idx if type(i) is int and 0<=i<len(prior)}
        entries={}
        batch=Batch()
        for d in (data.get('prior_dispositions') or []):
            if not isinstance(d,dict):
                batch.add('event_accounting','prior_dispositions 须为对象数组');continue
            idx,disposition=d.get('index'),d.get('disposition')
            batch.require(type(idx) is int and 0<=idx<len(prior) and idx not in entries,'event_accounting',
                    'index 须落在前序事件范围内且不重复',index=idx)
            batch.require(disposition in ('kept','dropped','replaced'),'event_accounting','disposition 只能 kept/dropped/replaced',index=idx)
            batch.require(disposition=='kept' or (isinstance(d.get('reason'),str) and d['reason'].strip()),
                    'event_accounting','dropped/replaced 须说明正文如何变化',index=idx)
            entries[idx]=d
        def reanchor(ev):
            clone=copy.deepcopy(ev)
            for key in EVENT_EVIDENCE_FIELDS:
                ref=clone.get(key)
                if isinstance(ref,dict) and ref.get('chapter')==n and isinstance(ref.get('quote'),str):
                    clone[key]['start']=draft['prose'].find(ref['quote'])
            return clone
        carried=[]
        for i,ev in enumerate(prior):
            d=entries.get(i)
            if d and d['disposition'] in ('dropped','replaced'):continue
            if i in auto:carried.append(reanchor(ev))
        submitted={anchor(e) for e in events}
        # kept derives from resubmission: re-typing engine-held events verbatim
        # is transcription with no information gain (sessions e354/53bc ran 11
        # event_accounting rejections, mostly kept bookkeeping).
        missing=[i for i in range(len(prior)) if entries.get(i) is None and i not in auto
                 and anchor(prior[i]) not in submitted]
        batch.require(not missing,'event_accounting',
                '前序事件既未原样新报（自动视为 kept）也未声明去向；沉默丢弃不被接受',
                indices=missing,
                recover='逐条二选一：在 events 中原样重报该事件（内容与 prior_events 该条一致，仅 evidence.start 可不同），或在 prior_dispositions 写 {index,disposition:"dropped"或"replaced",reason:"正文如何变化"}')
        batch.require(all(anchor(prior[i]) in submitted for i,d in entries.items()
                    if d['disposition']=='kept' and i not in auto),'event_accounting',
                '声明 kept 但未出现在新 events 中；内容有变应标 replaced 并说明',
                indices=[i for i,d in sorted(entries.items()) if d['disposition']=='kept' and i not in auto and anchor(prior[i]) not in submitted])
        carried_anchors={anchor(c) for c in carried}
        batch.require(all(anchor(e) not in carried_anchors for e in events),'event_accounting',
                '该前序事件已由引擎自动续算,勿重复提交;要变更请用 prior_dispositions 声明 replaced/dropped',
                indices=[i for i,e in enumerate(events) if anchor(e) in carried_anchors])
        batch.raise_if_any()
        events=carried+events
    return events


def receipt_sources(db, work, chapter, prose):
    """Include only historical sources already named by frozen prior events.

    A historical cause cannot be relocated into today's text merely because
    the same words appear there. Primary review checks still have their own
    current-chapter scope enforced by validate_review/reviewflow.
    """
    sources={chapter:prose};allowed=set()
    payload=work['payload']
    for key in ('prior_events','candidate_events_snapshot'):
        for event in payload.get(key,[]):
            if not isinstance(event,dict):continue
            for field in EVENT_EVIDENCE_FIELDS:
                ref=event.get(field)
                if isinstance(ref,dict) and type(ref.get('chapter')) is int and ref['chapter']<chapter:
                    allowed.add(ref['chapter'])
    for number in allowed:
        row=db.execute('SELECT prose FROM chapters WHERE branch=? AND number=?',(work['branch'],number)).fetchone()
        require(row is not None,'evidence','冻结前序证据的历史来源缺失',{'chapter':number})
        sources[number]=row['prose']
    return sources


def submit(db, result, brief=False, preview=False):
    result = normalize_receipt(db, result, record=not preview)
    require(isinstance(result,dict) and result.get('schema')=='novel-ledger-v2.result/1','schema','结果协议版本无效')
    hashed=digest(result)
    metered=db.execute('SELECT * FROM tasks WHERE id=?',(result.get('task_id'),)).fetchone()
    if metered and metered['state']=='pending' and not preview:
        from .contextflow import charge_result
        charge_result(db,metered,json.loads(metered['input']),result)
    with transaction(db,rollback=preview):
        task=db.execute('SELECT * FROM tasks WHERE id=?',(result.get('task_id'),)).fetchone()
        require(task,'task','任务不存在')
        require(task['stage'] not in ('facts','reader'),'unsupported_review','旧分阶段审稿任务不受支持；仅支持 chapter_review 新流程')
        if task['state']=='done':
            require(task['result_hash']==hashed,'replay_conflict','同一任务不能提交不同结果',
                    {'task_id':task['id'],'recover':'该任务回执已入库不可覆盖；停止修正重提，需要改动走返修任务，主控用 advance --after '+task['id']+' 核实接续'})
            return {'idempotent':True,'task_id':task['id']}
        require(task['state']=='pending' and get(db,'status')!='paused','stale_task','任务失效或运行已暂停')
        require(task['input_hash']==result.get('input_hash'),'input_version','输入版本不匹配')
        if task['lease_owner']:
            require(result.get('lease_owner')==task['lease_owner'] and task['lease_until']>time.time(),'lease','任务租约失效')
        data=result.get('data')
        require(isinstance(data,dict),'result','data 必须为对象')
        branch,n,stage=task['branch'],task['chapter'],task['stage']
        require('numeric_correction' not in data or stage=='chapter_review','numeric_correction','数值修正只用于有能力标记的独立 chapter_review')
        require(branch==get(db,'work_branch'),'stale_branch','任务不属于当前工作分支')
        work=json.loads(task['input'])
        execution.bind_session(db,work,result.get('session'))
        if 'evidence_catalog' in data or (isinstance(data.get('reader'),dict) and 'first_read_ref' in data['reader']):
            from .wire import expand_receipt
            expanded=expand_receipt(db,task,work,data)
            put(db,'wire_receipt:'+task['id'],result)
            result={**result,'data':expanded};data=expanded
        elif work.get('capabilities',{}).get('evidence_input')==EVIDENCE_INPUT:
            put(db,'wire_receipt:'+task['id'],result)
            result={**result,'data':copy.deepcopy(data)};data=result['data']
        if work.get('review_session'):
            from .reviewrun import matches
            require(matches(db,work['review_session']),'review_session_stale','复审依赖已改变；主控恢复最新任务，不能提交旧会话判断')
        require(stage!='chapter_review' or 'review_context_hash' in work,'unsupported_review','综合审稿任务必须含冻结的审稿上下文签名')
        current_signature=task_review_context_hash(db,task,work)
        require(current_signature is None or 'review_context_hash' not in work or work['review_context_hash']==current_signature,'review_context_stale','审稿依赖已变化；重新next取得冻结的新输入，不能沿用旧审稿回执')
        signature=work.get('review_context_hash') if current_signature is not None else None
        from . import contextflow
        draft=load_draft(db,branch,n)
        settled=None;correction=None
        if 'context_more' in data:
            contextflow.request_more(db,work,data)
        elif stage in ('context_scan','context_reduce'):
            contextflow.accept(db,work,data,task['id'])
        elif stage=='character_plan':
            from .characters import save
            save(db,branch,n,data,work['payload']['entities'],task['id'])
        elif stage=='character_index':
            from .characters import save_index
            save_index(db,branch,data,task['id'],work)
        elif stage in ('world_plan','volume_plan'):
            from .design import save
            save(db,branch,work['payload']['design_scope'],n,data,task['id'])
        elif stage=='plan':
            validate_plan(db,branch,n,data,explicit_time=work.get('capabilities',{}).get('timeline_input')==TIME_INPUT_PROTOCOL)
        elif stage in ('draft','repair','redesign'):
            require('plan_update' not in data or stage=='redesign','plan_update','plan_update 仅用于 redesign；其他阶段不能静默更改章计划')
            if 'expand_context' in data:
                require(stage=='repair' and work['payload'].get('repair_scope')=='local' and data.get('expand_context') is True and set(data)=={'expand_context','reason'} and isinstance(data.get('reason'),str) and data['reason'].strip(),'repair_expand','只有局部返修可用 expand_context=true 和 reason 请求扩大上下文')
                draft['repair_scope']='chapter';draft['expansion_reason']=data['reason'];save_draft(db,branch,n,draft)
                db.execute("UPDATE tasks SET state='done',result_hash=?,receipt=? WHERE id=?",(hashed,dump(result),task['id']))
                put(db,'seq',get(db,'seq')+1);log(db,'repair_expand',{'task':task['id'],'reason':data['reason']})
                return {'accepted':True,'expanded':True,'task_id':task['id'],**status(db,brief=brief)}
            from .patches import compose
            prose=parse_prose({'prose':compose(db,stage,work,data,draft)})
            if work.get('capabilities',{}).get('evidence_input')==EVIDENCE_INPUT:
                from .wire import anchor_proofs
                anchor_proofs(data,receipt_sources(db,work,n,prose))
            rev_row=db.execute('SELECT status,parent FROM branches WHERE id=?',(branch,)).fetchone()
            if stage=='draft' and rev_row['status']=='candidate' and data.get('reuse_original') is True and not execution.is_single(work):
                # Event preconditions describe writes, not every historical fact
                # the prose reads. Without a persisted complete read-set, reuse
                # is safe only while all upstream prose versions also match.
                # Compare stored version hashes locally; no worker reread needed.
                upstream_changed=db.execute('''SELECT 1 FROM chapters p
                    LEFT JOIN chapters c ON c.branch=? AND c.number=p.number
                    WHERE p.branch=? AND p.number<?
                    AND (c.hash IS NULL OR c.hash!=p.hash) LIMIT 1''',
                    (branch,rev_row['parent'],n)).fetchone()
                prow=db.execute('SELECT prose,data,hash FROM chapters WHERE branch=? AND number=?',(rev_row['parent'],n)).fetchone()
                pdata=json.loads(prow['data']) if prow and prow['prose']==prose else None
                current_context=chapter_review_context_hash(db,branch,n)
                length=chapter_length(db,branch,n,prose)
                if not upstream_changed and (not length or not length['shortfall_han']) and isinstance(pdata,dict) and pdata.get('facts_context_hash')==pdata.get('reader_context_hash')==current_context and pdata.get('prose_hash')==prow['hash']==digest(prose) and all(isinstance(pdata.get(k),dict) and pdata[k].get('verdict')=='pass' for k in ('facts_review','reader_review')) and isinstance(pdata.get('events'),list) and isinstance(pdata.get('summary'),str):
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
                                   'facts_context_hash':pdata['facts_context_hash'],'reader_context_hash':pdata['reader_context_hash'],
                                   'attempts':0}
                        commit_draft(db,branch,n,committed)
                        log(db,'fast_commit',{'branch':branch,'chapter':n,'parent':rev_row['parent']})
                        db.execute("UPDATE tasks SET state='done',result_hash=?,receipt=? WHERE id=?",(hashed,dump(result),task['id']))
                        put(db,'seq',get(db,'seq')+1)
                        return {'accepted':True,'fast_committed':True,'chapter':n,**status(db,brief=brief)}
            if stage=='redesign':
                require(data.get('design_change'),'redesign','重写须说明设计改动，不能原样重试')
                require(draft and prose!=draft['prose'],'redesign','重设计须产生实际变化的正文；说明设计改动不能替代原样重试')
                if data.get('plan_update'):
                    old_plan=atoms.plan(db,branch,n)
                    update=data['plan_update']
                    require(isinstance(update,dict) and set(update)<=set(('goal','conflict','change','entities','topics','hooks','dependencies','scenes','reader_experience','character_choices','expression_space','execution','rhythm')), 'plan_update','重设计只能调整当前章具体内容，不可更改卷序或终局标记')
                    from .budget import chapter_length as observe
                    old_length=observe(get(db,'contract'),old_plan)
                    proposed={**old_plan,**update}
                    new_length=observe(get(db,'contract'),proposed)
                    require(not old_length or (new_length and new_length['target_han']==old_length['target_han']),
                            'chapter_target','返修不能擅改已定篇幅目标；可重设计展开依据，目标变更须来自作者约定或事先章纲')
                    old_plan.update(update)
                    db.execute('DELETE FROM plans WHERE branch=? AND number=?',(branch,n))
                    validate_plan(db,branch,n,{'chapters':[old_plan]})
            if stage=='repair':
                require(data.get('repair_note') and prose!=draft['prose'],'repair','修订须包含实际修改及说明')
            if execution.controller_owned(work):
                require(not any(key in data for key in ('candidate_events','reuse_candidate_events')),
                        'writer_scope','单章执笔不提交候选事实事件；由独立综合审稿根据正文核验')
                require(isinstance(data.get('self_check'),str) and data['self_check'].strip(),
                        'writer_self_check','单章执笔须说明具体自查结果，自查不替独立审稿')
                require(isinstance(data.get('summary'),str) and data['summary'].strip(),
                        'writer_summary','单章执笔须保留本章摘要')
            if not draft:
                draft={'attempts':0}
            if execution.controller_owned(work):
                draft.update(self_check=data['self_check'],writer_summary=data['summary'])
            prior_prose=draft.get('prose')
            if 'reads' in data:
                draft['reads']=validate_reads(data['reads'])
            for obsolete in ('events','summary','facts_review','reader_review'):draft.pop(obsolete,None)
            draft.update(prose=prose,stage='chapter_review')
            if work.get('chapter_session',{}).get('role')=='writer':
                from .batchflow import accept_candidate
                settled=accept_candidate(db,branch,n,draft,data,work)
            observation=chapter_length(db,branch,n,prose)
            if observation and observation['shortfall_han']:
                develop_short_draft(db,branch,n,draft,observation,prior_prose)
            else:
                if observation:draft['chapter_length']=observation
                save_draft(db,branch,n,draft)
        elif stage=='chapter_review':
            facts,reader=data.get('facts'),data.get('reader')
            require(isinstance(facts,dict) and isinstance(reader,dict),'review','综合审稿须分别提交 facts 和 reader 回执')
            original_prose=draft['prose'];proposed_state=None
            if work.get('chapter_session'):
                from .batchflow import candidate_state
                proposed_state=candidate_state(db,branch,n)
            if 'numeric_correction' in data:
                from .numeric import compose as numeric_compose
                require(proposed_state is not None or not work.get('chapter_session'),
                        'numeric_correction','此任务无账本基线，按正常 fix 返修')
                draft=copy.deepcopy(draft)
                name_scan=proposed_state if proposed_state is not None else replay(db,branch)
                draft['prose'],correction=numeric_compose(work,draft,data,name_scan)
            if work.get('capabilities',{}).get('evidence_input')==EVIDENCE_INPUT:
                from .wire import anchor_proofs
                anchor_proofs(data,receipt_sources(db,work,n,draft['prose']),skip_obligations=True)
            from .reviewflow import validate as validate_protocol
            validate_protocol(db,task,work,facts,reader,draft['prose'],first_read_prose=original_prose if correction else None)
            validate_review(facts,work['payload']['facts_checks'],{n:draft['prose']})
            validate_review(reader,work['payload']['reader_checks'],{n:draft['prose']})
            first_read=reader.get('first_read')
            require(isinstance(first_read,dict) and all(isinstance(first_read.get(k),str) and first_read[k].strip() for k in ('desire','obstacle','change','read_on_reason')),'first_read','须提交只读正文时的首读体验，四项均为非空文本')
            from .spatial import check_address_responses
            check_address_responses(work.get('address_signals',[]),facts,draft['prose'],n)
            events=validated_fact_events(db,branch,n,draft,work,facts) if facts['verdict']=='pass' else None
            if events is not None:
                from .settlement import evaluate
                actual_state,settled=evaluate(db,branch,n,draft['prose'],events)
                from .reviewflow import reconcile_clock
                reconcile_clock(work,facts,settled)
            if correction:
                from .numeric import verify
                verify(draft,events,proposed_state,actual_state)
                observation=chapter_length(db,branch,n,draft['prose'])
                require(not observation or not observation['shortfall_han'],'numeric_correction','数值修正后篇幅不足，须正常 fix 返修')
                correction.update(protocol='numeric-patch/1',original_prose_hash=digest(original_prose),
                                  corrected_prose_hash=digest(draft['prose']),after_state_hash=settled['after_state_hash'])
                draft['numeric_correction']=correction
                log(db,'numeric_correction',{'task':task['id'],'chapter':n,**correction})
            proposed=copy.deepcopy(draft.get('candidate_events'))
            passed=facts['verdict']=='pass' and reader['verdict']=='pass'
            if not passed:
                if events is not None:
                    draft.update(events=events,summary=facts['summary'],facts_context_hash=signature)
                    draft['prior_event_snapshot']={'events':copy.deepcopy(events),'prose':draft['prose'],'verified':signature is not None}
                findings=[f for receipt in (facts,reader) for f in receipt.get('findings',[]) if f['severity'] in ('blocker','quality')]
                # Only use a local patch when both reviewers explicitly agree.
                scope='local' if all(r.get('repair_scope','chapter')=='local' for r in (facts,reader) if r['verdict']=='fix') else 'chapter'
                repair(db,branch,n,draft,findings,scope)
            else:
                draft.update(events=events,summary=facts['summary'],facts_review=facts,reader_review=reader,
                             facts_context_hash=signature,reader_context_hash=signature)
                draft['prior_event_snapshot']={'events':copy.deepcopy(events),'prose':draft['prose'],'verified':signature is not None}
                commit_draft(db,branch,n,draft)
            if work.get('chapter_session'):
                from .batchflow import after_review
                after_review(db,branch,n,proposed,events,passed,proposed_state,scope if not passed else 'chapter')
        elif stage.endswith('_review'):
            info=work['payload']
            first,through,scope=info['first'],info['through'],info['scope']
            sources={r['number']:r['prose'] for r in chapters(db,branch) if first<=r['number']<=through}
            if work.get('capabilities',{}).get('evidence_input')==EVIDENCE_INPUT:
                from .wire import anchor_proofs
                anchor_proofs(data,sources)
            validate_review(data,info['checks'],sources)
            if data['verdict']=='fix':
                # End current task before branching; new branch re-reviews the dependent suffix.
                db.execute("UPDATE tasks SET state='done',result_hash=?,receipt=? WHERE id=?",(hashed,dump(result),task['id']))
                # INV-11: the revision start is a repair directive, derived by
                # transitions.revision_start — never inferred from where the
                # reviewer happened to quote evidence.
                start=transitions.revision_start(data['findings'],through)
                limit=get(db,'revision_chain_limit',2)
                revision=db.execute('SELECT * FROM branches WHERE id=?',(branch,)).fetchone()
                if revision['status']=='candidate' and transitions.outside_revision_scope(data['findings'],revision['start']):
                    # A revision branch answers only "did THIS change propagate
                    # correctly". Findings citing chapters before revision_start
                    # are pre-existing defects the review read on its way past,
                    # and chaining them drags the finished revision backwards
                    # (measured: one glyph in an unrevised chapter re-drafted
                    # nine chapters of work). Whether to fix them is the
                    # author's call, so record and pause instead of guessing.
                    put(db,'status','paused')
                    put(db,'pause_reason',{'code':'outside_revision_scope',
                        'branch':branch,'revision_range':[revision['start'],revision['through']],
                        'findings':data['findings'],
                        'directive':'这些发现指向本次修订范围之前的章：它们不是本次修订引入的，'
                                    '不在这里建链（否则会把已完成的修订整条拉回重做）。'
                                    '向作者报告后结束会话，由作者决定是否另开一次以该章为起点的修订。'})
                elif get(db,'revision_failures',0)>=2 or _chain_depth(db)+1>limit:
                    from .recovery import revision_context
                    put(db,'status','paused')
                    put(db,'pause_reason',{'code':'revision_exhausted','findings':data['findings'],
                        'chain_depth':_chain_depth(db)+1,'limit':limit,
                        'chain':(revision_context(db,branch) or {}).get('chain',[]),
                        'directive':'自主修订到此为止：向作者报告修订链摘要与全部发现后结束会话，等待作者决策；不得 resume 重派同一整体复审，也不得再开新链。'})
                else:
                    put(db,'revision_failures',get(db,'revision_failures',0)+1)
                    begin_revision(db,start,dump(data['findings']))
            else:
                if signature is not None and not scope.endswith('_chunk'):
                    summary=data.get('memory')
                    was_text=isinstance(summary,str)
                    if was_text:
                        # Structural repair: text-only synthesis; per-chapter provenance stays with chunk receipts.
                        summary={'text':summary,'sources':[]}
                        data['memory']=summary
                    require(isinstance(summary,dict) and isinstance(summary.get('text'),str) and summary['text'].strip() and isinstance(summary.get('sources'),list) and (was_text or summary['sources']),'memory','整体复核须提交非空提炼记忆和有效证据来源；纯文本 memory 可省略 sources，来源以分段审稿回执为准')
                    for ref in summary['sources']:
                        require(ref.get('chapter') in sources,'memory','记忆来源不在审稿范围内')
                        evidence(sources[ref['chapter']],ref,ref['chapter'])
                    db.execute('INSERT OR REPLACE INTO memories VALUES (?,?,?,?)',(branch,scope,through,dump(summary)))
                stored={**data,'first':first,'through':through,'review_context_verified':signature is not None}
                bound_hash=signature if signature is not None else 'unverified:'+task['input_hash']
                db.execute('INSERT OR REPLACE INTO reviews VALUES (?,?,?,?,?)',(branch,scope,through,bound_hash,dump(stored)))
        else:
            raise Error('stage',f'未知阶段 {stage}')
        db.execute("UPDATE tasks SET state='done',result_hash=?,receipt=? WHERE id=?",(hashed,dump(result),task['id']))
        put(db,'seq',get(db,'seq')+1)
        log(db,'submit',{'task':task['id'],'stage':stage,'chapter':n})
        extra={}
        if settled is not None:extra['settlement']=settled
        if correction is not None:extra['numeric_correction']=correction
        if current_signature is not None and signature is None:
            log(db,'legacy_review_recheck',{'task':task['id'],'stage':stage,'reason':'旧任务未冻结审稿上下文签名，保留回执但重新核验'})
            extra['review_recheck_required']=True
        return {'accepted':True,'task_id':task['id'],**extra,**status(db,brief=brief)}


def _chain_depth(db):
    """Consecutive candidate branches topped by the current work branch."""
    depth=0
    branch=get(db,'work_branch')
    while branch:
        row=db.execute('SELECT status,parent FROM branches WHERE id=?',(branch,)).fetchone()
        if not row or row['status']!='candidate':break
        depth+=1
        branch=row['parent']
    return depth


def begin_revision(db,start,reason,style_only=False):
    parent=get(db,'work_branch')
    n=head(db,parent)
    parent_meta=db.execute('SELECT * FROM branches WHERE id=?',(parent,)).fetchone()
    through=max(n,parent_meta['through'] or n)
    limit=get(db,'revision_chain_limit',2)
    depth=_chain_depth(db)
    def _chain_details():
        from .recovery import revision_context
        return {'chain_depth':depth+1,'limit':limit,
                'chain':(revision_context(db,parent) or {}).get('chain',[]),
                'options':['完成当前候选链全部任务，修订整体复核通过后合并（promote）',
                           'revise --discard 丢弃顶层候选，回到父分支续作',
                           '作者明示后 config --key revision_chain_limit --value N 调整上限；自主会话不得代替作者调整']}
    # Chain topology is checked before range sanity: at the cap the caller must see
    # the escalation options, not a range complaint against an empty child branch.
    require(depth+1<=limit,'revision_chain_limit',
            f'修订链深度将达 {depth+1}，超过上限 {limit}，嵌套候选链不再延伸：先完成并合并当前链、revise --discard 丢弃顶层候选，或由作者调整上限',
            details=_chain_details)
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
    copy_for_revision(db,parent,branch,start,style_only=style_only)
    from .characters import copy_for_revision as copy_characters
    copy_characters(db,parent,branch)
    # Reviews entirely before the changed suffix remain valid.
    for table,cols in (('reviews','scope,through,input_hash,data'),('memories','scope,through,data')):
        db.execute(f'INSERT INTO {table} SELECT ?,{cols} FROM {table} WHERE branch=? AND through<?',(branch,parent,start))
    put(db,'work_branch',branch)
    if style_only:put(db,'style_only_branch',branch)
    put(db,'status','ready')
    put(db,'pause_reason',None)
    log(db,'revision_begin',{'branch':branch,'parent':parent,'start':start,'through':through,'reason':reason,'style_only':style_only})
    result={'branch':branch,'start':start,'through':through}
    if style_only:
        result['style_only']=True
        result['boundary']='文风修订：设计与计划保持有效，分支上直接从 draft 重写；不得改变事实、事件与因果，facts 审稿仍全额核验前态'
    return result


def pilot_reset(db,reason):
    """Discard the awaiting-approval pilot chapter and re-run its chain from draft.

    Tone-setting is a write-review-rewrite loop before approval; it must not
    cost a candidate branch or a design re-alignment. After approval, style
    changes go through revise --style-only instead."""
    require(not get(db,'pilot_approved',False),'pilot','试章已定调；后续文风修订走 revise --style-only')
    require(reason.strip(),'pilot','驳回重写必须说明定调意见')
    require(get(db,'status') not in ('paused','completed'),'pilot','运行已暂停或完本，不能驳回试章')
    require(not db.execute("SELECT 1 FROM tasks WHERE state='pending'").fetchone(),'pending','先完成当前任务或显式 pause --cancel-task 再驳回重写')
    branch=get(db,'work_branch')
    require(branch==get(db,'active_branch'),'revision','存在候选修订分支，先处理完再驳回试章')
    require(head(db,branch)==1,'pilot','驳回重写只适用于尚未定调的第 1 章')
    with transaction(db):
        for table,field in (('events','chapter'),('chapters','number'),('reviews','through'),('memories','through')):
            db.execute(f'DELETE FROM {table} WHERE branch=?',(branch,))
        db.execute("UPDATE tasks SET state='cancelled' WHERE state='pending'")
        log(db,'pilot_reset',{'branch':branch,'reason':reason})
    put(db,'status','ready')
    return status(db)


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
    if get(db,'style_only_branch')==branch:put(db,'style_only_branch',None)
    put(db,'status','ready')
    put(db,'revision_failures',0)
    log(db,'revision_promote',{'from':old,'to':branch})


def _fact_summary(event):
    op=event.get('op')
    def clip(v):
        text=repr(v)
        return text if len(text)<=48 else text[:45]+'...'
    if op=='state':
        return f"{event.get('entity')}:{event.get('key')} {clip(event.get('before'))}→{clip(event.get('after'))}"
    if op=='relation':
        return f"{event.get('from')}>{event.get('to')}:{event.get('key')} {clip(event.get('before'))}→{clip(event.get('after'))}"
    if op=='knowledge':
        return f"{event.get('entity')} ~{event.get('topic')} {event.get('stance')}: {clip(event.get('claim'))}"
    return op


def fact_impact(db,branch,fact):
    """Impact surface for history revisions: committed chapters asserting a
    fact coordinate plus future plans naming the entity. Absence of hits is
    not proof of absence — plans without the entity may still touch it."""
    from .projections import impacted_events
    require(isinstance(fact,str) and fact.strip(),'impact','fact 坐标不能为空')
    fact=fact.strip()
    events=impacted_events(db,branch,fact)
    if '>' in fact and ':' in fact:
        head,_=fact.rsplit(':',1)
        entities={part for part in head.split('>') if part}
    else:
        entities={fact.split(':',1)[0]} if ':' in fact else set()
    last=events[-1]['chapter'] if events else 0
    ahead=[]
    for row in db.execute('SELECT number,data FROM plans WHERE branch=? AND number>? ORDER BY number',(branch,last)):
        data=json.loads(row['data'])
        if entities & set(data.get('entities') or []):
            ahead.append(row['number'])
    return {'schema':'novel-ledger-v2.impact/1','branch':branch,'fact':fact,
            'chapters':[e['chapter'] for e in events],
            'events':[{'chapter':e['chapter'],'idx':e['idx'],'op':e['event'].get('op'),
                       'summary':_fact_summary(e['event'])} for e in events],
            'plans_ahead':ahead,'absence_is_not_proof':True}


def audit(db,branch=None,strict=False):
    branch=branch or get(db,'active_branch')
    require(db.execute('PRAGMA integrity_check').fetchone()[0]=='ok','integrity','SQLite 完整性检查失败')
    state=replay(db,branch,verify=True)
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
    # A chapter with a speaker but no usable profile is a ledger/table
    # divergence: the dispatcher only checks the chapter it is about to plan, so
    # a suffix rebuilt by a revision or a plan edited in place can slip through.
    from .characters import coverage as character_coverage
    character_report=character_coverage(db,branch)
    problems += [{'code':'character_table_gap',**gap} for gap in character_report['gaps']]
    if strict:
        require(not problems,'audit','审计发现未解决问题：'+dump(problems))
    return {'ok':not problems,'branch':branch,'chapters':len(rows),'problems':problems,
            'character_coverage':character_report,
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
        audit(db,get(db,'work_branch'),strict=True)
        db.execute("UPDATE tasks SET lease_owner=NULL,lease_until=NULL WHERE state='pending'")
        put(db,'status','ready')
        put(db,'pause_reason',None)
        log(db,'resume',{})
    return status(db)
