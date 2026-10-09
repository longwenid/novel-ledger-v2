"""Persistent semantic preparation; characters are observations, never gates.

Pages group complete records. Author rules, current prose and necessary notes
remain whole. Structural grouping reduces incidental history but cannot prove
an upper token bound for an arbitrarily large indivisible semantic unit.
"""
import copy
import json
from .store import get, put, dump, digest, require, log
from . import sources

SCAN_UNITS=16
DIRECT_UNITS=48
REDUCE_INPUTS=3
FINAL_NODES=8
MATERIAL_FIELDS=('state','continuity','character_context','design_context','selected',
                 'address_signals','volume_budget','synthesis_materials','plans')
RECORD_MAPS={'entities','facts','knowledge','resources','hooks','clock_threads',
             'plans','profiles','developments','placements','anchors','places','links'}
PREPARATION_RULES_VERSION='semantic-preparation/3'


def enabled(db):return get(db,'context_mode','bounded')=='bounded'
def wire(value):return dump(value)+'\n'
def view_key(bundle):return 'context_bundle:'+bundle


def limits(db):
    """Compatibility metadata: former character limits are inactive."""
    old={key:get(db,key) for key in ('context_chars','context_output_chars',
                                   'context_retrieval_chars','context_overhead_chars')}
    return {'unit':'unicode_characters_not_tokens','capacity':None,'input':None,
            'output':None,'retrieval':None,'overhead':None,'limits_enforced':False,
            'obsolete_fields':{k:v for k,v in old.items() if v is not None},
            'scan_units_per_page':SCAN_UNITS,'direct_material_units':DIRECT_UNITS,
            'reduction_inputs_per_group':REDUCE_INPUTS,
            'boundary':'旧字符配置只作兼容记录，不限制质量、输入、回执或补读；语义分页不证明 token 硬上界。'}


def stamp(db,work):
    policy=limits(db)
    work['context_capacity']={'unit':policy['unit'],'capacity_chars':None,
        'package_chars':None,'read_page_chars':None,'limits_enforced':False,
        'obsolete_fields':policy['obsolete_fields'],
        'rules':'单任务可见；按完整语义单元分页准备和定向补读；同一任务输入只加载一次；正文与作者约定完整保留；必要结论不受字符配额限制；宿主管理会话交接，不能宣称 token 硬上界。'}
    work['context_structure']={'scan_units_per_page':SCAN_UNITS,
        'direct_material_units':DIRECT_UNITS,'reduction_inputs_per_group':REDUCE_INPUTS,
        'grouping_is_not_content_quota':True}
    work['chars']=0
    for _ in range(4):work['chars']=len(wire(work))
    return work


def semantic_units(text,field=None,value=None):
    """Locate complete JSON records in explicit material containers.

    Ordinary objects retain their evidence and attributes as one semantic unit.
    String size never changes partitioning. Coverage ranges include intervening
    punctuation; semantic_start/end locate the complete record within them.
    """
    value=json.loads(text) if value is None else value;spans=[]
    def boundary(start):
        # Locate canonical JSON boundaries without decoding each old record.
        # A changed source still needs a linear structural walk; its producer
        # already supplies the decoded value, and cached records are not parsed
        # again merely because an appended record changes document offsets.
        if text[start] not in '[{"':
            end=start
            while end<len(text) and text[end] not in ',}] \t\r\n':end+=1
            return end
        quoted=False;escaped=False;depth=0
        for end in range(start,len(text)):
            char=text[end]
            if quoted:
                if escaped:escaped=False
                elif char=='\\':escaped=True
                elif char=='"':
                    quoted=False
                    if depth==0:return end+1
            elif char=='"':quoted=True
            elif char in '[{':depth+=1
            elif char in ']}':
                depth-=1
                if depth==0:return end+1
        require(False,'context_integrity','材料JSON单元边界不完整')
    def expand(item,path):
        if not path:return isinstance(item,(dict,list))
        # Records within a list/map remain atomic, regardless of nested list
        # length: before/after, conditions and all evidence stay together.
        if len(path)>1 and path[-2] in RECORD_MAPS:
            return isinstance(item,list) and path[-2]=='developments'
        if field=='selected' and isinstance(value,list) and isinstance(path[0],int):
            wrapper=value[path[0]]
            if isinstance(wrapper,dict) and wrapper.get('kind')=='requested_material':
                if len(path)==1:return True
                if len(path)==3 and path[1]=='materials' and isinstance(path[2],int):return isinstance(item,dict)
        if isinstance(path[-1],int):return False
        if isinstance(item,dict):return path[-1] in RECORD_MAPS
        return isinstance(item,list) and path[-1] in {'volumes','materials','value','experiences','notes','records','chapters','entities'}
    def walk(item,start,path,member_start=None):
        end=boundary(start)
        if isinstance(item,dict) and item and expand(item,path):
            cursor=start+1
            for key in sorted(item):
                key_start=cursor;cursor+=len(dump(key))+1
                item_end=boundary(cursor)
                walk(item[key],cursor,path+[key],key_start);cursor=item_end+1
        elif isinstance(item,list) and item and expand(item,path):
            cursor=start+1
            for index,child in enumerate(item):
                item_end=boundary(cursor)
                walk(child,cursor,path+[index]);cursor=item_end+1
        else:spans.append({'semantic_start':start if member_start is None else member_start,
                           'semantic_end':end,'path':path})
    walk(value,0,[])
    prior=0;units=[]
    for index,span in enumerate(spans):
        end=len(text) if index==len(spans)-1 else span['semantic_end']
        units.append({**span,'start':prior,'end':end});prior=end
    return units


def _slice_units(text,units,start=0):
    require(type(start) is int and 0<=start<len(text),'pagination','材料偏移必须指向已有来源')
    index=next(i for i,u in enumerate(units) if u['start']<=start<u['end'])
    page=units[index:index+SCAN_UNITS]
    return page[0]['start'],page[-1]['end'],page


def _unit_view(units):
    # Stable IDs live in the local source directory. Repeating three long
    # hashes for every record in every model page adds no reading evidence.
    return [{k:u[k] for k in ('semantic_start','semantic_end','path','start','end')} for u in units]


def _documents(base,db=None):
    core=copy.deepcopy(base);documents=[]
    def spill(container,key,path):
        if key not in container:return
        value=container.pop(key)
        if value in ({},[],None):return
        text=dump(value)
        registered=sources.register_document(db,sources.source_id(base['branch'],path),path,
            text,semantic_units,value=value) if db is not None else None
        documents.append({'id':'d'+str(len(documents)),'field':path,'text':text,
                          'hash':digest(text),'units':registered['units'] if registered else semantic_units(text,path,value=value),
                          **({k:registered[k] for k in ('source_id','version','index_stats')} if registered else {})})
    for key in MATERIAL_FIELDS:
        # A profile-index proposal must inspect the exact whole frozen profile;
        # prepared prose notes cannot substitute for that source coverage.
        if key=='character_context' and base.get('stage')=='character_index':continue
        spill(core,key,key)
    spill(core.get('payload',{}),'previous_design','payload.previous_design')
    return core,documents


def needs_preparation(base):
    """A grouping decision based on record count, never character count."""
    _,documents=_documents(base)
    return sum(len(d['units']) for d in documents)>DIRECT_UNITS


def _frame(base,ident,core_document):
    plan=base.get('plan',{});payload=base.get('payload',{})
    return {'schema':'novel-ledger-v2.pack/1','branch':base['branch'],'chapter':base['chapter'],
        'target_stage':base['stage'],
        'target_purpose':{k:copy.deepcopy(plan[k]) for k in ('goal','conflict','change','entities','topics') if k in plan},
        'target_scope':{k:copy.deepcopy(payload[k]) for k in ('scope','first','through','checks','repair_scope') if k in payload},
        'target_materials':{'bundle':ident,'document':'core','document_hash':core_document['hash'],
            'retrieve':'material --task TASK_ID --bundle '+ident+' --document core:FIELD',
            'fields':['contract','plan','payload','style_anchor','writer_brief']+
                (['character_context'] if base.get('stage')=='character_index' else []),
            'instruction':'目标核心材料完整冻结。按疑点定向读取必要子节，同一材料在本次准备会话只加载一次；未理解目标约定/正文动作时先补读，不能猜测。正式任务仍携带完整约定和当前正文。'},
        'material_questions':copy.deepcopy(base.get('context_questions',[])),
        'instruction':'独立材料准备：审读本页全部完整语义单元，保留当前任务必要的约束/误信/因果/风险；跨章审稿保留章节来源、选择后果、未决矛盾和兑现关联。取舍须有具体依据，提炼不是新事实。引用使用 document/start/quote 原始字符位置。必要结论不限字符，不能因压缩困难删除。'}


def _task(db,bundle,stage,payload):
    work=copy.deepcopy(bundle['frame']);work.update(stage=stage,payload=payload)
    return stamp(db,work)


def _upgrade_bundle(db,bundle):
    # Resume legacy snapshots at complete record boundaries, retaining old notes.
    changed=False
    for index,doc in enumerate(bundle['documents']):
        if 'units' not in doc:
            doc['units']=semantic_units(doc['text'],doc['field']);changed=True
            if index==bundle['doc_index'] and bundle['offset']<len(doc['text']):
                bundle['offset']=_slice_units(doc['text'],doc['units'],bundle['offset'])[0]
    archive=bundle.setdefault('node_archive',{})
    for node in bundle['nodes']+bundle.get('next_nodes',[]):
        if node['id'] not in archive:archive[node['id']]=copy.deepcopy(node)
    if bundle.get('status')=='blocked':bundle['status']='preparing';changed=True
    if 'core_document' not in bundle:
        text=dump(bundle['core'])
        bundle['core_document']={'id':'core','field':'target_core','text':text,'hash':digest(text),'units':semantic_units(text)}
        changed=True
    if 'target_materials' not in bundle['frame']:
        bundle['frame']=_frame(bundle['core'],bundle['id'],bundle['core_document']);changed=True
    if changed:put(db,view_key(bundle['id']),bundle)


def _page(db,bundle):
    doc=bundle['documents'][bundle['doc_index']]
    require(digest(doc['text'])==doc['hash'],'context_integrity','材料快照损坏')
    start,end,units=_slice_units(doc['text'],doc['units'],bundle['offset'])
    return _task(db,bundle,'context_scan',{'bundle':bundle['id'],'document':doc['id'],
        'document_hash':doc['hash'],'field':doc['field'],'start':start,'end':end,
        'total_chars':len(doc['text']),'fragment':doc['text'][start:end],
        'units':_unit_view(units),'page_unit_count':len(units),'total_units':len(doc['units']),
        **{k:doc[k] for k in ('source_id','version') if k in doc},
        'notes_max_chars':None,'complete_semantic_units':True})


def _preparation_basis(core,documents):
    """Freeze complete task purpose and sources, except transport metadata.

    Chapter identity affects free-form obligations and rhythm even without JSON
    deadline keys. Their absence cannot prove that a chapter number is inert.
    Source/unit indexes remain reusable across chapters and different stages.
    """
    target=copy.deepcopy(core)
    for key in ('context_identity','chars','context_capacity','context_structure','context_materials'):
        target.pop(key,None)
    manifest=[{'source_id':d['source_id'],'field':d['field'],'version':d['version'],
               'unit_versions':[[u['unit_id'],u['unit_version']] for u in d['units']]}
              for d in documents]
    purpose=digest({'rules':PREPARATION_RULES_VERSION,'target':target})
    dependencies=digest({'target':target,'sources':manifest,'rules':PREPARATION_RULES_VERSION})
    return {'key':digest({'purpose':purpose,'dependencies':dependencies}),
            'purpose_hash':purpose,'dependency_signature':dependencies,
            'chapter_invariant':False,'source_manifest':manifest,
            'rules_version':PREPARATION_RULES_VERSION}


def _cacheable_reads(db,bundle):
    """Only frozen initial input and this bundle's immutable material are mapped.

    Tool-delivered dependencies do not prove what a worker understood. Reads
    outside this closed source set therefore disable reuse of its conclusions.
    """
    try:
        from .dependencies import read_set
    except ImportError:
        # Prior hosts without dependency tracking can still index sources, but
        # cannot certify an old preparation's additional reads for cache reuse.
        return False,[]
    frozen=[]
    for task_id in bundle.get('node_archive',{}):
        if not db.execute('SELECT 1 FROM tasks WHERE id=?',(task_id,)).fetchone():return False,[]
        dependencies=read_set(db,task_id)
        entries=dependencies.get('entries',[])
        if dependencies.get('coverage')!='delivered_only' or not any(entry.get('kind')=='initial' for entry in entries):return False,[]
        for entry in entries:
            query=entry.get('query',{})
            command=query.get('command',query.get('kind')) if isinstance(query,dict) else None
            if command not in ('initial','input') and not (command=='material' and query.get('bundle')==bundle['id']):
                return False,[]
        frozen.append({'task_id':task_id,'signature':dependencies.get('signature'),
                       'coverage':dependencies.get('coverage','delivered_only'),'entries':entries})
    return True,frozen


def _cached_reads_unchanged(db,cached):
    try:
        from .dependencies import read_set,inspect
    except ImportError:return False
    for frozen in cached.get('read_dependencies',[]):
        task_id=frozen['task_id']
        if not db.execute('SELECT 1 FROM tasks WHERE id=?',(task_id,)).fetchone():return False
        if read_set(db,task_id)['signature']!=frozen['signature']:return False
        if inspect(db,task_id)['versions_unchanged'] is not True:return False
    return bool(cached.get('read_dependencies'))


def _prepared(db,bundle,ident):
    nodes=bundle['nodes'];work=copy.deepcopy(bundle['core']);work['context_notes']=copy.deepcopy(nodes)
    work['context_materials'].update(status='prepared',scanned_pages=bundle['scans'],
        reduction_tasks=bundle['reductions'],coverage_hash=digest(nodes),
        note_index=[{'id':n['id'],'hash':digest(n),'notes':len(n['notes']),
                     'retained_node_ids':n.get('retained_node_ids',[])} for n in nodes],
        retrieve_notes='material --task TASK_ID --bundle '+ident+' --document note:NODE_ID')
    if bundle.get('cache_basis'):
        basis=bundle['cache_basis']
        work['context_materials']['source_index']={'new_units':sum(d.get('index_stats',{}).get('new_units',0) for d in bundle['documents']),
            'reused_units':sum(d.get('index_stats',{}).get('reused_units',0) for d in bundle['documents']),
            'boundary':'不可变来源和完整单元目录可复用；变化来源仍需结构定位，字符不是配额。'}
        work['context_materials']['preparation_reuse']={'hit':bool(bundle.get('reused_from')),
            'source_bundle':bundle.get('reused_from'),'purpose_hash':basis['purpose_hash'],
            'dependency_signature':basis['dependency_signature'],
            'rules_version':basis['rules_version'],
            'boundary':'仅相同用途及完整冻结读依赖可复用；不是事实/读者审稿结论，缺项不证明不存在。'}
        if not bundle.get('reused_from'):
            safe,reads=_cacheable_reads(db,bundle)
            if safe:
                sources.cache_put(db,basis['key'],basis['purpose_hash'],basis['dependency_signature'],
                    {'nodes':nodes,'node_archive':bundle['node_archive'],'source_bundle':ident,
                     'read_dependencies':reads,'source_manifest':basis['source_manifest']})
    return stamp(db,work)


def fit(db,base):
    core,documents=_documents(base,db)
    if sum(len(d['units']) for d in documents)<=DIRECT_UNITS:return stamp(db,copy.deepcopy(base))
    ident=digest(base);bundle=get(db,view_key(ident))
    if bundle is None:
        core.pop('chars',None)
        basis=_preparation_basis(core,documents)
        core_text=dump(core)
        core_document={'id':'core','field':'target_core','text':core_text,'hash':digest(core_text),'units':semantic_units(core_text)}
        core['context_materials']={'bundle':ident,'documents':len(documents),'status':'preparing',
            'index':[{'id':d['id'],'field':d['field'],'chars':len(d['text']),
                      'units':len(d['units']),'hash':d['hash'],'source_id':d['source_id'],
                      'source_version':d['version']} for d in documents]+[
                      {'id':'core','field':'target_core','chars':len(core_text),'units':len(core_document['units']),
                       'hash':core_document['hash'],'scanned_as_history':False}],
            'retrieve':'material --task TASK_ID --bundle '+ident+' --document ID --start N',
            'boundary':'原始材料和必要结论全部持久保存；提炼不是正典，未选入不能证明不存在。'}
        bundle={'id':ident,'core':core,'core_document':core_document,'frame':_frame(core,ident,core_document),'documents':documents,
            'doc_index':0,'offset':0,'nodes':[],'node_archive':{},'level':0,
            'group':0,'next_nodes':[],'scans':0,'reductions':0,'status':'preparing',
            'cache_basis':basis}
        cached=sources.cache_get(db,basis['key'],basis['purpose_hash'],basis['dependency_signature'])
        if cached and _cached_reads_unchanged(db,cached):
            require(cached.get('source_manifest')==basis['source_manifest'],'context_integrity','准备缓存来源版本不匹配')
            bundle.update(nodes=copy.deepcopy(cached['nodes']),node_archive=copy.deepcopy(cached['node_archive']),
                doc_index=len(documents),reused_from=cached['source_bundle'])
        put(db,view_key(ident),bundle)
    _upgrade_bundle(db,bundle)
    if bundle['doc_index']<len(bundle['documents']):return _page(db,bundle)
    nodes=bundle['nodes']
    if len(nodes)<=FINAL_NODES:
        return _prepared(db,bundle,ident)
    group=nodes[bundle['group']:bundle['group']+REDUCE_INPUTS]
    return _task(db,bundle,'context_reduce',{'bundle':ident,'level':bundle['level'],
        'group':bundle['group'],'inputs':group,'notes_max_chars':None,
        'instruction':'按语义合并重复结论，必要内容全部保留、不限字符。accounting逐个登记输入ID与kept/duplicate/irrelevant及理由。不能归并时用cannot_compact+reason，原样保留输入节点与来源并继续，不因大小暂停。'})


def accept(db,work,data,task_id):
    payload=work['payload'];ident=payload['bundle'];bundle=get(db,view_key(ident))
    require(bundle and bundle['status']=='preparing','context_stale','材料准备版本已失效')
    preserved=data.get('cannot_compact') is True
    if preserved:
        reason=data.get('reason')
        require(isinstance(reason,str) and reason.strip(),'context_notes','须说明无法安全归并的必要内容')
        if work['stage']=='context_reduce':
            data={'notes':[copy.deepcopy(note) for n in payload['inputs'] for note in n['notes']],
                'accounting':[{'input_id':n['id'],'disposition':'kept','reason':reason} for n in payload['inputs']],
                'omission_reason':reason}
        else:
            data={'coverage':{k:payload[k] for k in ('document','document_hash','start','end')},
                'notes':[{'text':'必要原页完整保留：'+reason,'evidence':[{
                    'document':payload['document'],'start':payload['start'],'quote':payload['fragment']}]}]}
    notes=data.get('notes');require(isinstance(notes,list),'context_notes','需要 notes 数组')
    documents={d['id']:d for d in bundle['documents']};inherited=set()
    if work['stage']=='context_reduce':
        for node in payload['inputs']:
            for note in node['notes']:inherited.update(dump(e) for e in note['evidence'])
        accounting=data.get('accounting',[])
        require(isinstance(accounting,list) and all(isinstance(a,dict) for a in accounting) and
            len(accounting)==len(payload['inputs']) and {a.get('input_id') for a in accounting}=={n['id'] for n in payload['inputs']},
            'context_coverage','必须逐项说明所有输入结论的去向')
        require(all(a.get('disposition') in ('kept','duplicate','irrelevant') and isinstance(a.get('reason'),str) and a['reason'].strip() for a in accounting),
            'context_coverage','每个输入需要去向与具体理由')
    else:
        require(bundle['documents'][bundle['doc_index']]['id']==payload['document'] and bundle['offset']==payload['start'],
            'context_stale','语义页游标已经变化')
        require(data.get('coverage')=={k:payload[k] for k in ('document','document_hash','start','end')},
            'context_coverage','必须确认本页完整覆盖范围与版本')
    require(notes or (isinstance(data.get('omission_reason'),str) and data['omission_reason'].strip()),'context_notes','无相关结论时须记录具体不相关理由')
    for note in notes:
        require(isinstance(note,dict) and isinstance(note.get('text'),str) and note['text'].strip() and
            isinstance(note.get('evidence'),list) and note['evidence'],'context_notes','每条结论必须有原始材料证据')
        for evidence in note['evidence']:
            require(isinstance(evidence,dict) and set(evidence)=={'document','start','quote'},'context_evidence','来源字段为 document/start/quote')
            doc=documents.get(evidence['document']);start=evidence['start'];quote=evidence['quote']
            require(doc and digest(doc['text'])==doc['hash'],'context_integrity','材料快照损坏')
            require(type(start) is int and start>=0 and isinstance(quote,str) and quote and doc['text'][start:start+len(quote)]==quote,
                'context_evidence','材料引文或位置不匹配')
            if work['stage']=='context_scan':
                require(doc['id']==payload['document'] and start>=payload['start'] and start+len(quote)<=payload['end'],
                    'context_evidence','扫描结论只能引用本页完整材料')
            else:require(dump(evidence) in inherited,'context_evidence','归并不得捏造未审读的新来源')
    if work['stage']=='context_reduce':
        outgoing=[{dump(e) for e in note['evidence']} for note in notes]
        for action in data['accounting']:
            source=next(n for n in payload['inputs'] if n['id']==action['input_id'])
            if action['disposition']=='kept':
                require(all(any({dump(e) for e in note['evidence']}<=target for target in outgoing)
                            for note in source['notes']),
                    'context_coverage','kept的每条输入结论须由同一输出结论整体继承全部原始来源')
            elif action['disposition']=='duplicate':
                index=action.get('duplicate_of')
                require(type(index) is int and 0<=index<len(notes),'context_coverage','duplicate须指向保留的输出note索引')
                target={dump(e) for e in notes[index]['evidence']}
                require(all(dump(e) in target for note in source['notes'] for e in note['evidence']),
                    'context_coverage','duplicate输出结论须继承该输入全部来源；可去重，不能丢因果证据')
    node={'id':task_id,'notes':copy.deepcopy(notes)}
    if preserved:
        node['cannot_compact_reason']=reason
        if work['stage']=='context_reduce':node['retained_node_ids']=[n['id'] for n in payload['inputs']]
        else:node['retained_source']={k:payload[k] for k in ('document','document_hash','start','end')}
    if data.get('omission_reason'):node['omission_reason']=data['omission_reason']
    if work['stage']=='context_scan':
        node['coverage']={k:payload[k] for k in ('document','document_hash','start','end')}
        bundle['nodes'].append(node);bundle['scans']+=1;bundle['offset']=payload['end']
        if payload['end']==payload['total_chars']:bundle['doc_index']+=1;bundle['offset']=0
    else:
        require(bundle['level']==payload['level'] and bundle['group']==payload['group'],'context_stale','归并游标已变化')
        node['accounting']=copy.deepcopy(data['accounting'])
        bundle['next_nodes'].append(node);bundle['group']+=len(payload['inputs']);bundle['reductions']+=1
        if bundle['group']>=len(bundle['nodes']):
            bundle['nodes']=bundle['next_nodes'];bundle['next_nodes']=[];bundle['group']=0;bundle['level']+=1
    bundle.setdefault('node_archive',{})[task_id]=copy.deepcopy(node)
    put(db,view_key(ident),bundle)
    log(db,'context_prepare',{'bundle':ident,'stage':work['stage'],'task':task_id,'preserved':preserved})


def charge_result(db,task,work,result):
    """Observe attempted receipts; size never vetoes required content."""
    if enabled(db):
        key='context_attempt_chars:'+task['id'];put(db,key,get(db,key,0)+len(wire(result)))


def material(db,task_id,bundle_id,document,start=0):
    row=db.execute('SELECT input FROM tasks WHERE id=?',(task_id,)).fetchone()
    require(row,'task','任务不存在');work=json.loads(row[0])
    associated=work.get('context_materials',{}).get('bundle') or work.get('payload',{}).get('bundle')
    require(associated==bundle_id,'context_source','只能读取本任务绑定的材料快照')
    bundle=get(db,view_key(bundle_id));require(bundle,'context_source','材料快照不存在')
    if document=='core' or document.startswith('core:'):
        _upgrade_bundle(db,bundle);doc=bundle['core_document']
        if document.startswith('core:'):
            field=document[5:];core=json.loads(doc['text'])
            require(field in core,'context_source','目标核心子节不存在')
            decoder=json.JSONDecoder();cursor=1
            for key in sorted(core):
                _,key_end=decoder.raw_decode(doc['text'],cursor);begin=key_end+1
                _,end=decoder.raw_decode(doc['text'],begin)
                if key==field:break
                cursor=end+1
            text=doc['text'][begin:end]
            doc={'id':document,'field':'target_core.'+field,'text':text,'hash':digest(text),
                 'units':semantic_units(text),'source_document_hash':bundle['core_document']['hash'],'source_start':begin}
    elif document.startswith('note:'):
        node=bundle.get('node_archive',{}).get(document[5:]);require(node,'context_source','结论节点不存在')
        text=dump(node);doc={'id':document,'field':'preparation_notes','text':text,'hash':digest(text),'units':semantic_units(text)}
    else:doc=next((d for d in bundle['documents'] if d['id']==document),None)
    require(doc is not None,'context_source','材料不存在')
    require(digest(doc['text'])==doc['hash'],'context_integrity','材料快照损坏')
    units=doc.get('units') or semantic_units(doc['text'],doc['field']);begin,end,page=_slice_units(doc['text'],units,start)
    return {'bundle':bundle_id,'document':document,'document_hash':doc['hash'],'field':doc['field'],
        'text':doc['text'][begin:end],'start':begin,'end':end,'requested_start':start,
        'next_start':end if end<len(doc['text']) else None,'total_chars':len(doc['text']),
        'units':_unit_view(page),'complete_semantic_units':True,
        **{k:doc[k] for k in ('source_id','version') if k in doc},
        **{k:doc[k] for k in ('source_document_hash','source_start') if k in doc}}


def bounded_read(db,value,task_id=None,start=0):
    """Group complete records and observe reads, without a size quota."""
    if task_id:require(db.execute('SELECT 1 FROM tasks WHERE id=?',(task_id,)).fetchone(),'task','补读任务不存在')
    require(type(start) is int and start>=0,'pagination','page-start必须是非负来源字符位置')
    text=wire(value);units=semantic_units(text)
    if len(units)<=SCAN_UNITS and start==0:out=value
    else:
        begin,end,page=_slice_units(text,units,start)
        out={'format':'json_text_fragment','content_hash':digest(text),'start':begin,'end':end,
            'total_chars':len(text),'text':text[begin:end],
            'next_start':end if end<len(text) else None,'units':page,'complete_semantic_units':True,
            'instruction':'本页按完整结构化记录分组；字符只作定位与观测，不是配额。未读完不表示其余材料不存在；必要内容不因大小拒绝。'}
    if task_id:put(db,'context_reads:'+task_id,get(db,'context_reads:'+task_id,0)+len(wire(out)))
    return out


def usage(db):
    """Observable protocol characters; never inferred billing or token counts."""
    from .projections import usage_summary
    row=usage_summary(db)
    reads=db.execute("SELECT COALESCE(SUM(CAST(value AS INTEGER)),0) FROM meta WHERE key LIKE 'context_reads:%'").fetchone()[0]
    attempts=db.execute("SELECT COALESCE(SUM(CAST(value AS INTEGER)),0) FROM meta WHERE key LIKE 'context_attempt_chars:%'").fetchone()[0]
    return {'unit':'unicode_characters_not_tokens','tasks':row['tasks'],'task_input_chars':row['inputs'],
        'accepted_receipt_chars':row['receipts'],'attempted_receipt_chars':attempts,
        'preparation_input_chars':row['preparation'],'supplemental_read_chars':reads,
        'billed_tokens':None,'session_handoffs':None,
        'boundary':'字符仅观测，不限制必要内容；输入按持久任务计一次；不含系统提示、聊天历史和协议外读取，不换算token。'}


def request_more(db,work,data):
    """Persist new targeted sources; reject source-identical loops, not size."""
    identity=work.get('context_identity')
    require(identity and work['stage'] not in ('context_scan','context_reduce'),'context_more','仅正式创作/审稿任务可请求补充材料')
    requests=data.get('context_more');reason=data.get('reason')
    require(set(data)=={'context_more','reason'} and isinstance(requests,list) and requests and isinstance(reason,str) and reason.strip(),
        'context_more','context_more须为非空定向请求列表，并说明具体疑点reason')
    prior=get(db,'context_extra:'+identity,{'rounds':0,'materials':[]});materials=[]
    def source_identity(value):
        if isinstance(value,dict) and {'number','hash','prose'}<=set(value):
            return dump(['chapter',value['number'],value['hash']])
        if isinstance(value,dict) and {'chapter','index','event_hash'}<=set(value):
            return dump(['event',value['chapter'],value['index'],value['event_hash']])
        return None
    seen=set()
    for material_record in prior['materials']:
        value=material_record['value']
        for source in value if isinstance(value,list) else [value]:
            identity_key=source_identity(source)
            if identity_key is not None:seen.add(identity_key)
    from .memory import recall
    for query in requests:
        require(isinstance(query,dict) and set(query)<= {'chapter','entity','topic','start','end'} and query,'context_more','须按章号或实体/话题与章范围检索')
        if 'chapter' in query:
            require(set(query)=={'chapter'} and type(query['chapter']) is int and query['chapter']>=1,'context_more','chapter须为正整数章号')
            row=db.execute('SELECT number,hash,prose FROM chapters WHERE branch=? AND number=?',(work['branch'],query['chapter'])).fetchone()
            require(row,'context_more','正文不存在于当前分支');value=dict(row)
        else:
            require(query.get('entity') or query.get('topic'),'context_more','须有实体或话题，不支持无目标倾倒')
            start=query.get('start',1);end=query.get('end')
            require(type(start) is int and start>=1 and (end is None or type(end) is int and end>=start),'context_more','检索章范围无效')
            value=[];offset=0
            while True:
                page=recall(db,work['branch'],entity=query.get('entity'),topic=query.get('topic'),start=start,end=end,offset=offset,limit=100)
                value.extend(page['records'])
                if page['next_offset'] is None:break
                offset=page['next_offset']
        sources=value if isinstance(value,list) else [value];fresh=[]
        for source in sources:
            identity_key=source_identity(source)
            if identity_key is not None and identity_key not in seen:
                fresh.append(source);seen.add(identity_key)
        if fresh:
            new_value=fresh if isinstance(value,list) else fresh[0]
            materials.append({'request':query,'reason':reason,'value':new_value,
                              'source_hash':digest(new_value),'source_ids':[source_identity(v) for v in fresh]})
    require(materials,'context_more_repeated','请求与已准备来源完全重复；选择新来源，不能重复读取同一材料形成循环')
    put(db,'context_extra:'+identity,{'rounds':prior['rounds']+1,'materials':prior['materials']+materials})
    log(db,'context_more',{'identity':identity,'round':prior['rounds']+1,'reason':reason})
