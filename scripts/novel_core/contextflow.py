"""Persistent preparation, declared-capacity sizing anchors and session checkpoints.

Context explosion is limited by structural rules: single-task visibility, scoped
paginated reads, prepare-then-enter for large history, load-once inputs, one
creative unit per receipt, growth-triggered session handoff. Character metering
only sizes pages and decides handoff timing; it is calibration against a
host-declared capacity, never a fixed quota or a tokenizer claim. Notes are
navigation interpretations, never canon. Source snapshots and every
coverage/omission decision survive restarts. No provider SDK or tokenizer claims.
"""
import copy
import json
import uuid
from .store import get, put, dump, digest, require, Error, log, head


def enabled(db):
    return get(db,'context_mode','bounded') == 'bounded'


def wire(value):
    return dump(value)+'\n'


def limits(db):
    """Sizing anchors derived from the host-declared capacity; calibration, not quotas."""
    total=get(db,'context_chars')
    require(type(total) is int and total>=4096,'context_config','声明容量 context_chars 至少 4096 字符，由宿主按真实窗口校准')
    output=get(db,'context_output_chars',total//4)
    retrieval=get(db,'context_retrieval_chars',total//6)
    overhead=get(db,'context_overhead_chars',total-total//2-total//4-total//6)
    require(all(type(n) is int and n>=256 for n in (output,retrieval,overhead)),'context_config','读页/回执/预留锚点均需至少 256 字符；锚点只校准尺寸，不构成配额')
    available=max(256,total-output-retrieval-overhead)
    return {'unit':'unicode_characters_not_tokens','capacity':total,'input':available,
            'output':output,'retrieval':retrieval,'overhead':overhead}


def stamp(db,work):
    anchors=limits(db)
    work['context_capacity']={'unit':anchors['unit'],'capacity_chars':anchors['capacity'],
            'package_chars':anchors['input'],'read_page_chars':anchors['retrieval'],
            'rules':'单任务可见：只加载本任务绑定材料，其余定向分页检索；大历史先冻结经 scan/reduce 提炼；同一输入只加载一次；回执一次一个创作单元，装不下先拆分不截断；原文补读累计不超过本工作包体量；serial 会话累计接近容量即交接。宿主按真实 token 窗口校准容量。'}
    work['chars']=0
    for _ in range(4):
        work['chars']=len(wire(work))
    require(len(wire(work))<=anchors['input'],'context_capacity','核心约束与当前正文超过声明容量的工作包锚点；需要更大宿主容量或更小的创作/审读单元，不能截断核心材料')
    return work


def view_key(bundle):return 'context_bundle:'+bundle


def _frame(base):
    return {'schema':'novel-ledger-v2.pack/1','branch':base['branch'],'chapter':base['chapter'],
            'target_stage':base['stage'],'contract':base.get('contract',{}),
            'plan':base.get('plan',{}),
            'target_payload':copy.deepcopy(base.get('payload',{})),
            'material_questions':copy.deepcopy(base.get('context_questions',[])),
            'instruction':'独立材料准备：全文扫描本页，提取影响当前任务的约束/误信/因果/风险；跨章审稿保留章节来源、选择后果、未决矛盾和兑现关联，不能只报各章 pass。记录不相关判断依据；不能把提炼当新事实，不能替目标任务审稿。引用使用本页 document/start/quote 的原始字符位置。'}


def _task(db,bundle,stage,payload):
    work=copy.deepcopy(bundle['frame']);work.update(stage=stage,payload=payload)
    return stamp(db,work)


def _page(db,bundle):
    index=bundle['doc_index'];doc=bundle['documents'][index];start=bundle['offset']
    require(digest(doc['text'])==doc['hash'],'context_integrity','材料快照损坏，不能继续生成覆盖记录')
    base={'bundle':bundle['id'],'document':doc['id'],'document_hash':doc['hash'],
          'field':doc['field'],'start':start,'total_chars':len(doc['text']),
          'notes_max_chars':bundle['note_limit'],
          'context_before':doc['text'][max(0,start-256):start]}
    # Binary-search actual serialized size, including escaping inside a fragment.
    low,high=1,len(doc['text'])-start;chosen=None
    while low<=high:
        count=(low+high)//2
        try:
            task=_task(db,bundle,'context_scan',{**base,'end':start+count,'fragment':doc['text'][start:start+count],
                            'context_after':doc['text'][start+count:start+count+256]})
            chosen=task;low=count+1
        except Error as exc:
            if exc.code!='context_capacity':raise
            high=count-1
    require(chosen is not None,'context_capacity','材料准备的核心任务框架已无分片空间')
    return chosen


def fit(db,base):
    """Return the logical work or the next durable preparation task."""
    candidate=copy.deepcopy(base)
    try:return stamp(db,candidate)
    except Error as exc:
        if exc.code!='context_capacity':raise
    ident=digest(base);bundle=get(db,view_key(ident))
    if bundle is None:
        core=copy.deepcopy(base);documents=[]
        def spill(container,key,path):
            if key in container:
                value=container.pop(key);text=dump(value)
                if value not in ({},[],None):
                    documents.append({'id':'d'+str(len(documents)),'field':path,'text':text,'hash':digest(text)})
        for key in ('state','continuity','character_context','design_context','selected','address_signals','volume_budget','synthesis_materials','plans'):
            spill(core,key,key)
        # Prior proposals can be arbitrarily large and are not immutable author rules.
        spill(core.get('payload',{}),'previous_design','payload.previous_design')
        # All author constraints and current prose remain intact in the core.
        core.pop('chars',None)
        core['context_materials']={'bundle':ident,'documents':len(documents),'status':'preparing',
                                 'index':[{'id':d['id'],'field':d['field'],'chars':len(d['text']),'hash':d['hash']} for d in documents],
                                 'retrieve':'material --task TASK_ID --bundle '+ident+' --document ID --start N',
                                 'boundary':'原始材料持久保存；提炼不是正典。必要时定向补读，不能以未选入证明不存在。'}
        require(documents,'context_capacity','当前正文或作者核心约束过大，无可拆分的历史材料')
        frame=_frame(core)
        # Reserve meaningful page space rather than creating thousands of tiny jobs.
        require(len(wire(frame))+1800 < limits(db)['input']//2,'context_capacity',
                '核心约定/本章任务过大，无法安全分批准备；先缩小当前任务或校准宿主可用窗口')
        reserve=limits(db)['input']-len(wire(core))-2400
        require(reserve>=1024,'context_capacity','核心工作包没有足够的来源结论空间')
        note_limit=min(2400,limits(db)['output']//2,reserve//2)
        bundle={'id':ident,'core':core,'frame':frame,'documents':documents,'doc_index':0,'offset':0,
                'nodes':[],'level':0,'group':0,'next_nodes':[],'note_limit':note_limit,
                'final_limit':min(reserve,limits(db)['input']//3),'scans':0,'reductions':0,'status':'preparing'}
        put(db,view_key(ident),bundle)
    if bundle['status']=='blocked':
        raise Error('context_unresolved',bundle['reason'])
    if bundle['doc_index']<len(bundle['documents']):return _page(db,bundle)
    nodes=bundle['nodes']
    if len(wire(nodes))<=bundle['final_limit']:
        work=copy.deepcopy(bundle['core'])
        work['context_notes']=nodes
        work['context_materials'].update(status='prepared',scanned_pages=bundle['scans'],
                                         reduction_tasks=bundle['reductions'],coverage_hash=digest(nodes))
        return stamp(db,work)
    require(len(nodes)>1,'context_capacity','单条材料结论仍过大，不能静默删去必要约束')
    group=nodes[bundle['group']:bundle['group']+3]
    return _task(db,bundle,'context_reduce',{'bundle':ident,'level':bundle['level'],
                    'group':bundle['group'],'inputs':group,'notes_max_chars':bundle['note_limit'],
                    'instruction':'合并重复结论，保留当前任务必要约束和来源；accounting 为每个输入 ID 记录 kept/duplicate/irrelevant 与理由。无法安全压缩时提交 cannot_compact 和 reason，不牺牲必要事实以过关。'})


def accept(db,work,data,task_id):
    payload=work['payload'];ident=payload['bundle'];bundle=get(db,view_key(ident))
    require(bundle and bundle['status']=='preparing','context_stale','材料准备版本已失效')
    if data.get('cannot_compact') is True:
        require(isinstance(data.get('reason'),str) and data['reason'].strip(),'context_notes','须说明具体无法压缩的必要材料')
        bundle.update(status='blocked',reason=data['reason']);put(db,view_key(ident),bundle)
        put(db,'status','paused');put(db,'pause_reason',{'code':'context_unresolved','bundle':ident,'reason':data['reason']})
        return
    notes=data.get('notes');require(isinstance(notes,list),'context_notes','需要 notes 数组')
    require(len(wire(notes))<=payload['notes_max_chars'],'context_notes','提炼超出本次结论预算；保留必要约束，不能原样复制整页')
    documents={d['id']:d for d in bundle['documents']}
    inherited=set()
    if work['stage']=='context_reduce':
        for node in payload['inputs']:
            for note in node['notes']:
                inherited.update(dump(e) for e in note['evidence'])
        accounting=data.get('accounting',[])
        require(isinstance(accounting,list) and all(isinstance(a,dict) for a in accounting) and
                len(accounting)==len(payload['inputs']) and {a.get('input_id') for a in accounting}=={n['id'] for n in payload['inputs']},
                'context_coverage','必须逐项说明所有输入结论的去向')
        require(all(a.get('disposition') in ('kept','duplicate','irrelevant') and isinstance(a.get('reason'),str) and a['reason'].strip() for a in accounting),
                'context_coverage','每个输入需要去向与具体理由')
    else:
        require(bundle['documents'][bundle['doc_index']]['id']==payload['document'] and bundle['offset']==payload['start'],
                'context_stale','分片游标已经变化')
        require(data.get('coverage')=={k:payload[k] for k in ('document','document_hash','start','end')},
                'context_coverage','必须确认本页完整覆盖范围与版本')
    require(notes or (isinstance(data.get('omission_reason'),str) and data['omission_reason'].strip()),'context_notes','无相关结论时须记录不相关的具体理由')
    for note in notes:
        require(isinstance(note,dict) and isinstance(note.get('text'),str) and note['text'].strip() and
                isinstance(note.get('evidence'),list) and note['evidence'],'context_notes','每条结论必须有原始材料证据')
        for evidence in note['evidence']:
            require(isinstance(evidence,dict) and set(evidence)=={'document','start','quote'},'context_evidence','来源字段为 document/start/quote')
            doc=documents.get(evidence['document']);start=evidence['start'];quote=evidence['quote']
            require(doc and digest(doc['text'])==doc['hash'],'context_integrity','材料快照损坏')
            require(doc and type(start) is int and start>=0 and isinstance(quote,str) and quote and doc['text'][start:start+len(quote)]==quote,
                    'context_evidence','材料引文或位置不匹配')
            if work['stage']=='context_scan':
                require(doc['id']==payload['document'] and start>=payload['start'] and start+len(quote)<=payload['end'],
                        'context_evidence','扫描结论只能引用本页实际材料')
            else:require(dump(evidence) in inherited,'context_evidence','归并不得捏造未审读的新来源')
    if work['stage']=='context_reduce':
        outgoing={dump(e) for note in notes for e in note['evidence']}
        for action in data['accounting']:
            source=next(n for n in payload['inputs'] if n['id']==action['input_id'])
            if action['disposition']=='kept':
                require(all(any(dump(e) in outgoing for e in note['evidence']) for note in source['notes']),
                        'context_coverage','声称 kept 的每条输入结论须有来源传入输出，不能口头保留却实际丢弃')
            elif action['disposition']=='duplicate':
                index=action.get('duplicate_of')
                require(type(index) is int and 0<=index<len(notes),'context_coverage','duplicate 需要指向保留的输出 note 索引 duplicate_of')
    node={'id':task_id,'notes':copy.deepcopy(notes)}
    if work['stage']=='context_scan':
        bundle['nodes'].append(node);bundle['scans']+=1;bundle['offset']=payload['end']
        if payload['end']==payload['total_chars']:bundle['doc_index']+=1;bundle['offset']=0
    else:
        require(bundle['level']==payload['level'] and bundle['group']==payload['group'],'context_stale','归并游标已变化')
        bundle['next_nodes'].append(node);bundle['group']+=len(payload['inputs']);bundle['reductions']+=1
        if bundle['group']>=len(bundle['nodes']):
            bundle['nodes']=bundle['next_nodes'];bundle['next_nodes']=[];bundle['group']=0;bundle['level']+=1
    put(db,view_key(ident),bundle)
    log(db,'context_prepare',{'bundle':ident,'stage':work['stage'],'task':task_id})


def input_key(db,task_id):
    return 'context_input:'+get(db,'context_session_id','initial')+':'+task_id


def session_checkpoint(db,work,task_id=None,replay_input=False):
    if not enabled(db) or get(db,'session_mode')=='fresh':return None
    used=get(db,'context_session_used',0)
    already=not replay_input and task_id and get(db,input_key(db,task_id),False)
    cost=limits(db)['output']+max(0,limits(db)['retrieval']-get(db,'context_reads:'+str(task_id),0))
    if not already:cost+=len(wire(work))+limits(db)['overhead']
    if used+cost<=limits(db)['capacity']:return None
    pending=db.execute("SELECT id FROM tasks WHERE state='pending'").fetchone()
    return {'stage':'await_session','reason':'当前 serial 会话累计已接近声明容量；在新的真实宿主会话中执行 session-start 后续跑，不能在原会话自动清零。',
            'used_chars':used,'next_reserved_chars':cost,'limit_chars':limits(db)['capacity'],
            'session_id':get(db,'context_session_id','initial'),
            'pending_task':pending['id'] if pending else None,
            'resume':{'steps':['结束当前运行器（若 runner 存在，先退出）',
                               'session-start --project <PROJECT> --id <新宿主会话ID>',
                               'next --project <PROJECT>（同一任务会从数据库重建输入文件）'],
                      'grounding':'新会话不需要旧聊天记录：工作包、任务、账本全部由数据库重建；正确性不依赖会话记忆。',
                      'boundary':'仅校验宿主声明与 ID 不复用，不证明真实模型窗口已清空'},
            'checkpoint':{'branch':get(db,'work_branch'),'committed':head(db,get(db,'work_branch'))}}


def charge_input(db,task_id,work,repeat=False):
    if not enabled(db):
        return
    put(db,'usage_input_chars',get(db,'usage_input_chars',0)+len(wire(work)))
    if get(db,'session_mode')!='fresh' and (repeat or not get(db,input_key(db,task_id),False)):
        put(db,'context_session_used',get(db,'context_session_used',0)+len(wire(work))+limits(db)['overhead'])
        put(db,input_key(db,task_id),True)


def charge_failed_attempt(db,observed_chars=0):
    # Missing/malformed outputs cannot refund a model turn. Reserve the full
    # output allowance when its actual consumption is unknown.
    if enabled(db) and get(db,'session_mode')!='fresh':
        put(db,'context_session_used',get(db,'context_session_used',0)+max(limits(db)['output'],observed_chars))


def start_session(db,session_id):
    require(isinstance(session_id,str) and session_id.strip(),'session','需要真实新宿主会话 ID')
    require(not get(db,'context_session_seen:'+session_id),'session_reused','会话已经使用，不能在原会话反复清零')
    prior=get(db,'context_session_id','initial')
    used=get(db,'context_session_used',0)
    put(db,'context_session_seen:'+session_id,True);put(db,'context_session_id',session_id)
    put(db,'context_session_used',0)
    put(db,'usage_handoffs',get(db,'usage_handoffs',0)+1)
    log(db,'session_start',{'id':session_id,'prior_session':prior,'reset_from_chars':used,'assertion':'host_attested_empty_context'})
    return {'session_id':session_id,'prior_session':prior,'reset_from_chars':used,'used_chars':0,
            'boundary':'仅校验宿主声明与 ID 不复用，不证明真实模型窗口已清空；正确性以数据库为准，续跑由 next 重建工作包，不依赖旧会话记忆'}


def charge_result(db,task,work,result):
    # Called outside the creative transaction: rejected output still consumes context.
    if not enabled(db):return
    size=len(wire(result))
    put(db,'usage_result_chars',get(db,'usage_result_chars',0)+size)
    anchors=limits(db)
    if get(db,'session_mode')=='fresh':
        session=result.get('session',{})
        require(session.get('origin')=='empty' and isinstance(session.get('id'),str) and session['id'].strip(),'session','fresh 需要真实独立会话回执')
        require(not db.execute('SELECT task FROM sessions WHERE id=?',(session['id'],)).fetchone(),'session_reused','会话已用于一次尝试，包括失败尝试；重试须使用真实新会话')
        db.execute('INSERT INTO sessions VALUES (?,?)',(session['id'],task['id']))
    if get(db,'session_mode')!='fresh':
        put(db,'context_session_used',get(db,'context_session_used',0)+size)
    # One receipt is one creative unit: beside its own input it must fit the declared capacity.
    require(size+len(wire(work))+anchors['overhead']<=anchors['capacity'],'context_output',
            '单次回执连同其输入超出声明容量；局部修订用 patches、整卷审稿按 chunk 分段、过大单元先拆分，不静默截断；宿主窗口确实更大时先校准 context_chars')
    if get(db,'session_mode')!='fresh':
        require(get(db,'context_session_used',0)<=anchors['capacity'],'context_session','会话累计已接近声明容量；候选回执保留在文件，请从真实新会话续接')


def material(db,task_id,bundle_id,document,start=0):
    row=db.execute('SELECT input FROM tasks WHERE id=?',(task_id,)).fetchone()
    require(row,'task','任务不存在');work=json.loads(row[0])
    associated=work.get('context_materials',{}).get('bundle') or work.get('payload',{}).get('bundle')
    require(associated==bundle_id,'context_source','只能读取本任务绑定的材料快照')
    bundle=get(db,view_key(bundle_id));doc=next((d for d in bundle['documents'] if d['id']==document),None)
    require(doc is not None and type(start) is int and 0<=start<len(doc['text']),'context_source','材料或偏移不存在')
    require(digest(doc['text'])==doc['hash'],'context_integrity','材料快照损坏')
    return {'bundle':bundle_id,'document':document,'document_hash':doc['hash'],'field':doc['field'],
            'text':doc['text'][start:start+max(256,limits(db)['retrieval']//2)],'start':start,
            'next_start':min(len(doc['text']),start+max(256,limits(db)['retrieval']//2)), 'total_chars':len(doc['text'])}


def bounded_read(db,value,task_id=None,start=0):
    """Scoped, paginated reads. Cumulative raw reading is bounded by the package it serves."""
    anchors=limits(db)
    allowance=anchors['retrieval']
    if task_id:
        row=db.execute('SELECT input FROM tasks WHERE id=?',(task_id,)).fetchone()
        require(row is not None,'task','补读任务不存在')
        work=json.loads(row[0])
        # A review pack declares a reading allowance sized to the prose under
        # review; other tasks may re-read up to the package they supplement.
        declared=work.get('retrieval_allowance') or len(row[0])
        # Supplements belong to preparation once they rival the package they supplement.
        allowance=max(2048,declared)-get(db,'context_reads:'+task_id,0)
    if get(db,'session_mode')!='fresh':
        allowance=min(allowance,max(0,anchors['capacity']-get(db,'context_session_used',0)-anchors['output']))
    require(allowance>=512,'context_retrieval','本任务原文补读累计已超过其工作包体量；更多材料按 context_more 转准备流程，不向同一任务继续倾倒原文')
    require(type(start) is int and start>=0,'pagination','page-start 必须是非负字符位置')
    page=min(anchors['retrieval'],allowance)
    text=wire(value)
    if len(text)<=page and start==0:
        out=value
    else:
        require(start<len(text),'pagination','page-start 超出材料范围')
        count=min(len(text)-start,page-400)
        while count>0:
            out={'format':'json_text_fragment','content_hash':digest(text),'start':start,'end':start+count,
                 'total_chars':len(text),'text':text[start:start+count],
                 'next_start':start+count if start+count<len(text) else None,
                 'instruction':'这是原始 JSON 的有界片段，尚未读完不表示其余材料不存在；同一任务原文补读累计不超过其工作包体量。'}
            if len(wire(out))<=allowance:break
            count//=2
        require(count>0,'context_retrieval','无法在剩余补读空间中返回材料')
    if task_id:put(db,'context_reads:'+task_id,get(db,'context_reads:'+task_id,0)+len(wire(out)))
    if task_id:put(db,'usage_read_chars',get(db,'usage_read_chars',0)+len(wire(out)))
    if get(db,'session_mode')!='fresh':put(db,'context_session_used',get(db,'context_session_used',0)+len(wire(out)))
    return out


def request_more(db,work,data):
    """End the current attempt without changing creative state; prepare new material."""
    identity=work.get('context_identity')
    require(identity and work['stage'] not in ('context_scan','context_reduce'),'context_more','仅正式创作/审稿任务可请求补充材料')
    requests=data.get('context_more');reason=data.get('reason')
    require(set(data)=={'context_more','reason'} and isinstance(requests,list) and 1<=len(requests)<=4 and isinstance(reason,str) and reason.strip() and len(reason)<=800,
            'context_more','context_more 为 1～4 个检索请求，须说明具体疑点 reason；本次不能混交正文或审稿')
    prior=get(db,'context_extra:'+identity,{'rounds':0,'materials':[]})
    require(prior['rounds']<3,'context_more_exhausted','已完成三轮补充材料；须重审任务范围，不能无限补读')
    materials=[]
    from .memory import recall
    for query in requests:
        require(isinstance(query,dict) and set(query)<= {'chapter','entity','topic','start','end'} and query,'context_more','可按 chapter 或 entity/topic 与章范围检索')
        if 'chapter' in query:
            require(set(query)=={'chapter'} and type(query['chapter']) is int,'context_more','chapter 请求只包含正整数章号')
            row=db.execute('SELECT number,hash,prose FROM chapters WHERE branch=? AND number=?',(work['branch'],query['chapter'])).fetchone()
            require(row,'context_more','正文不存在于当前分支');value=dict(row)
        else:
            require(query.get('entity') or query.get('topic'),'context_more','需实体或话题，不支持无目标全文倾倒')
            start=query.get('start',1);end=query.get('end')
            require(type(start) is int and start>=1 and (end is None or type(end) is int and end>=start),'context_more','检索章范围无效')
            value=[];offset=0
            while True:
                page=recall(db,work['branch'],entity=query.get('entity'),topic=query.get('topic'),start=start,end=end,offset=offset,limit=100)
                value.extend(page['records'])
                if page['next_offset'] is None:break
                offset=page['next_offset']
        materials.append({'request':query,'reason':reason,'value':value})
    # Requests are persistent inputs to the next context plan, not unmetered tool output.
    put(db,'context_extra:'+identity,{'rounds':prior['rounds']+1,'materials':prior['materials']+materials})
    log(db,'context_more',{'identity':identity,'round':prior['rounds']+1,'reason':reason})
