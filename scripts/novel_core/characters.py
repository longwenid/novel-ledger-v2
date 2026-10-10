"""Versioned character interpretations with source-bound growth, not new world facts."""
import copy
import json
from .store import get,put,require,digest,dump,log,head
from .ledger import replay,evidence


def enabled(db):return get(db,'contract').get('character_mode')=='profiled'
def text(v):return isinstance(v,str) and bool(v.strip())
def strings(v):return isinstance(v,list) and all(text(x) for x in v)
def person(state,eid):return eid in state['entities'] and state['entities'][eid]['kind'] in ('person','character')
def key(branch,eid):return 'character:'+branch+':'+eid
def current(db,branch,eid):return get(db,key(branch,eid))
def growth_hash(state,eid):return digest(state.get('character_developments',{}).get(eid,[]))
def version_key(branch,eid,version):return 'character-version:'+branch+':'+eid+':'+version


def validate_relevance(item,state):
    """Hints are searchable claims, never sufficient authority to omit an item."""
    relevance=item.get('relevance')
    if relevance is None:return
    require(isinstance(relevance,dict) and bool(relevance) and set(relevance)<={'entities','topics','places'},'character','relevance 只支持 entities/topics/places 相关性声明')
    for dimension,values in relevance.items():
        require(strings(values) and values and len(values)==len(set(values)),'character','relevance 各维度须为非空且不重复的字符串数组')
        if dimension in ('entities','places'):
            require(all(value in state['entities'] for value in values),'character','relevance 必须引用稳定实体 ID')


def participants(plan,state):
    ids=set(plan.get('entities',[]))
    ids.update(c['entity'] for c in plan.get('character_choices',[]))
    for scene in plan.get('scenes',[]):
        ids.update(scene.get('present',[]));ids.update(scene.get('speakers',[]))
        for intent in scene.get('dialogue_intents',[]):
            ids.add(intent['entity'])
            if intent.get('to'):ids.add(intent['to'])
    return sorted(e for e in ids if person(state,e))


def usable(db,branch,eid,state=None):
    record=current(db,branch,eid)
    if not record or not record['active']:return False
    state=state if state is not None else replay(db,branch)
    if record['growth_hash']!=growth_hash(state,eid):return False
    for n,h in record['sources'].items():
        row=db.execute('SELECT hash FROM chapters WHERE branch=? AND number=?',(branch,int(n))).fetchone()
        if not row or row[0]!=h:return False
    for ref in record['data'].get('canon_refs',[]):
        name=ref['entity']+':'+ref['key']
        if name not in state['facts'] or state['facts'][name]!=ref['value']:return False
    return True


def next_job(db,branch,chapter,plan):
    if not enabled(db):return None
    state=replay(db,branch)
    missing=[e for e in participants(plan,state) if not usable(db,branch,e,state)]
    if not missing:return None
    return ('character_plan',chapter,{'entities':missing,'instruction':'为本章缺少有效档案的人物建立或更新三层人物资料。先核对当前事实和成长证据，按 references/characters.md 返回 profiles。只设计表达与行为依据，不把小传里的提案当已发生事实。'})


def validate_profile(data,state,source):
    allowed={'entity','tier','biography','voice','arc','examples','canon_refs','change_reason'}
    require(isinstance(data,dict),'character','人物档案须为对象')
    # 诊断按因果分层:结构错、字段错位、实体错各报各的,不能把字段放错层
    # 误报成实体问题——真实项目里同一误导性报错让四个 worker 重复试错。
    unknown=sorted(set(data)-allowed) if isinstance(data,dict) else []
    require(not unknown,'character',
            f'人物档案包含未知顶层字段:{",".join(unknown)};based_on_chapter/status/growth_hash/sources 由引擎生成不必提交,experiences 须放 biography 内')
    require(person(state,data.get('entity')),'character','人物档案须引用 person/character 稳定实体')
    require(data.get('tier') in ('major','supporting','walk_on'),'character','tier 为 major/supporting/walk_on')
    bio=data.get('biography');voice=data.get('voice')
    require(isinstance(bio,dict) and set(bio)<= {'public_role','desire','fear','values','boundary','contradiction','experiences'},'character','biography 字段无效')
    require(all(text(bio.get(k)) for k in ('public_role','desire')),'character','小传至少明确社会身份与自身诉求')
    require(isinstance(voice,dict) and set(voice)<= {'register','rhythm','tactics','stress_shift','relationship_modes','avoid'},'character','voice 字段无效')
    require(text(voice.get('register')) and strings(voice.get('tactics')) and voice['tactics'],'character','表达习惯至少包含 register 和非空 tactics')
    if data['tier']=='major':
        require(all(text(bio.get(k)) for k in ('fear','boundary','contradiction')) and strings(bio.get('values')) and bio['values'],'character','主要人物需要恐惧、价值排序、底线与内在矛盾')
        require(all(text(voice.get(k)) for k in ('rhythm','stress_shift')),'character','主要人物需要句式节奏与压力下的变化')
        require(isinstance(data.get('arc'),dict) and text(data['arc'].get('direction')) and strings(data['arc'].get('not_yet_earned')),'character','主要人物需成长方向与尚未获得依据的变化')
    if 'arc' in data:
        require(isinstance(data['arc'],dict) and set(data['arc'])<= {'direction','not_yet_earned'} and text(data['arc'].get('direction')) and strings(data['arc'].get('not_yet_earned',[])),'character','arc 字段无效')
    experiences=bio.get('experiences',[])
    require(isinstance(experiences,list),'character','experiences 为数组')
    for e in experiences:
        require(isinstance(e,dict) and set(e)<= {'text','status','evidence','relevance'} and text(e.get('text')) and e.get('status') in ('design','established'),'character','经历必须明确为设计或已确立')
        validate_relevance(e,state)
        if e['status']=='established':source(e.get('evidence'))
        else:require('evidence' not in e,'character','未发生经历不能伪装成已有证据')
    modes=voice.get('relationship_modes',[])
    require(isinstance(modes,list) and all(isinstance(m,dict) and text(m.get('audience')) and text(m.get('approach')) for m in modes),'character','关系语气需要 audience/approach')
    for mode in modes:validate_relevance(mode,state)
    require(strings(voice.get('avoid',[])),'character','avoid 为写法提醒数组，不设置禁词配额')
    examples=data.get('examples',[])
    require(isinstance(examples,list),'character','examples 为已有正文片段数组')
    for example in examples:
        require(isinstance(example,dict) and example.get('speaker')==data['entity'] and text(example.get('note')),'character','代表性对白须标同一人物 speaker 及语境 note')
        validate_relevance(example,state)
        source(example.get('evidence'))
    refs=data.get('canon_refs',[])
    require(isinstance(refs,list),'character','canon_refs 为事实引用数组')
    for ref in refs:
        require(isinstance(ref,dict) and text(ref.get('entity')) and text(ref.get('key')) and 'value' in ref,'character','canon_refs 需要 entity/key/value')
        name=ref['entity']+':'+ref['key']
        require(name in state['facts'] and state['facts'][name]==ref['value'],'character_canon_conflict','人物档案引用与当前事实不一致')


def save(db,branch,chapter,data,expected,task_id):
    require(set(data)=={'profiles'} and isinstance(data['profiles'],list),'character','character_plan data 只包含 profiles 数组')
    ids=[p.get('entity') for p in data['profiles'] if isinstance(p,dict)]
    require(len(ids)==len(data['profiles'])==len(expected) and set(ids)==set(expected),'character','只能且必须提交本次请求的人物档案，不遗漏或重复')
    state=replay(db,branch)
    for profile in data['profiles']:
        sources={}
        def source(ref):
            require(isinstance(ref,dict) and type(ref.get('chapter')) is int and 1<=ref['chapter']<chapter,'character_source','人物来源须为已提交的历史正文')
            row=db.execute('SELECT prose,hash FROM chapters WHERE branch=? AND number=?',(branch,ref['chapter'])).fetchone()
            require(row,'character_source','来源不在当前有效分支')
            evidence(row['prose'],ref,ref['chapter']);sources[str(ref['chapter'])]=row['hash']
        validate_profile(profile,state,source)
        eid=profile['entity'];old=current(db,branch,eid)
        require(not old or text(profile.get('change_reason')),'character','更新人物档案须说明变化原因，不能无声重置人设')
        record={'data':copy.deepcopy(profile),'revision':old['revision']+1 if old else 1,'active':True,'sources':sources,'source_branch':branch,
                'growth_hash':growth_hash(state,eid),'growth_count':len(state.get('character_developments',{}).get(eid,[])),'task_id':task_id,'status':'interpretation_and_design_not_canon','based_on_chapter':head(db,branch)}
        record['hash']=digest(record)
        if old and get(db,version_key(branch,eid,old['hash'])) is None:put(db,version_key(branch,eid,old['hash']),old)
        put(db,version_key(branch,eid,record['hash']),record)
        put(db,key(branch,eid),record)
        log(db,'character_submit',{'branch':branch,'entity':eid,'revision':record['revision'],'hash':record['hash']})


PROFILE_SECTIONS=('biography.experiences','voice.relationship_modes','examples')


def section_value(record,section):
    if section in (None,'profile'):return record['data']
    if section=='core':
        result=copy.deepcopy(record['data'])
        result['biography'].pop('experiences',None)
        result['voice'].pop('relationship_modes',None)
        result.pop('examples',None)
        return result
    require(section in PROFILE_SECTIONS,'character_section','section 为 core/profile/biography.experiences/voice.relationship_modes/examples')
    value=record['data']
    for part in section.split('.'):value=value.get(part,[])
    return value


def source_ref(branch,eid,record,section=None,item=None):
    value=section_value(record,section)
    if item is not None:
        require(section in PROFILE_SECTIONS and type(item) is int and 0<=item<len(value),'character_item','item 为 section 内从 0 起的有效条目序号')
        value=value[item]
    path='/'+'/'.join(section.split('.')) if section and section!='profile' else ''
    if item is not None:path+='/'+str(item)
    return {'kind':'character','branch':record.get('source_branch',branch),'entity':eid,'revision':record['revision'],'record_hash':record['hash'],'path':path,'value_hash':digest(value)}


def referenced_sources(record,value):
    """Return exact cited chapter versions, without repeating an entire archive."""
    chapters=set()
    def visit(node):
        if isinstance(node,dict):
            for name,child in node.items():
                if name in ('evidence','cause_evidence') and isinstance(child,dict) and type(child.get('chapter')) is int:chapters.add(str(child['chapter']))
                visit(child)
        elif isinstance(node,list):
            for child in node:visit(child)
    visit(value)
    return {number:record['sources'][number] for number in sorted(chapters,key=int) if number in record['sources']}


def profile_read(db,branch,eid,section=None,item=None,revision=None,query=None):
    """Read complete, version-addressed material; no character or item truncation."""
    record=current(db,branch,eid)
    require(record,'character','尚无人物档案')
    if revision is not None and revision not in (record['hash'],record['revision']):
        matches={};origins={};cursor=branch
        while cursor:
            prefix='character-version:'+cursor+':'+eid+':'
            for row in db.execute('SELECT value FROM meta WHERE substr(key,1,?)=?',(len(prefix),prefix)):
                candidate=json.loads(row[0])
                if revision in (candidate['hash'],candidate['revision']):
                    matches[candidate['hash']]=candidate;origins.setdefault(candidate['hash'],cursor)
            parent=db.execute('SELECT parent FROM branches WHERE id=?',(cursor,)).fetchone();cursor=parent[0] if parent else None
        require(len(matches)==1,'character_version','人物历史版本不存在或无法唯一定位')
        record=next(iter(matches.values()))
        origin=db.execute('SELECT branch FROM tasks WHERE id=?',(record.get('task_id'),)).fetchone()
        branch_origin=record.get('source_branch',origin[0] if origin else origins[record['hash']])
    else:
        origin=db.execute('SELECT branch FROM tasks WHERE id=?',(record.get('task_id'),)).fetchone()
        branch_origin=record.get('source_branch',origin[0] if origin else branch)
    value=section_value(record,section)
    if item is not None:
        require(section in PROFILE_SECTIONS and type(item) is int and 0<=item<len(value),'character_item','item 为 section 内从 0 起的有效条目序号')
        value=value[item]
    result={'status':'interpretation_and_design_not_canon','value':copy.deepcopy(value),'requested_branch':branch,'source_branch':branch_origin,'source_ref':source_ref(branch_origin,eid,record,section,item),'sources':referenced_sources(record,value)}
    if query is None:return result
    require(item is None,'character_query','指定 item 时直接读取完整条目，不同时使用 query')
    state=replay(db,branch);validate_relevance({'relevance':query},state)
    result['query']={'conditions':copy.deepcopy(query),'policy':'定向查阅，不证明实际正文中其他因果不存在。未知条件、失效索引或历史版本保留原材料。'}
    if record['hash']!=current(db,branch,eid)['hash'] or not valid_index(db,branch,eid,state):
        result['query']['status']='full_fallback';return result
    scope={dimension:set(query[dimension]) if dimension in query else None for dimension in ('entities','topics','places')}
    index=get(db,index_key(branch,eid,record['hash']));selected=selection_closure(index,scope)
    if section not in PROFILE_SECTIONS:
        result['query']['status']='full_fallback';return result
    values=section_value(record,section);positions=[number for number in range(len(values)) if (section,number) in selected]
    if len(positions)==len(values):
        result['query']['status']='full_fallback';return result
    related=[];refs=[]
    for candidate,number in sorted(selected):
        refs.append(source_ref(branch,eid,record,candidate,number))
        if candidate!=section:
            related.append({'section':candidate,'item':number,'value':copy.deepcopy(section_value(record,candidate)[number]),'source_ref':refs[-1]})
    result.update(value=[copy.deepcopy(values[number]) for number in positions],
                  source_refs=refs,core=section_value(record,'core'),related=related,
                  sources=referenced_sources(record,[values[number] for number in positions]+[item['value'] for item in related]))
    result.pop('source_ref')
    result['source_refs'].append(source_ref(branch,eid,record,'core'))
    result['source_refs'].append({'kind':'character_index','branch':branch,'entity':eid,'record_hash':record['hash'],'index_hash':index['hash'],'query_hash':digest(query)})
    result['query'].update(status='scoped',selected_items=len(positions),total_items=len(values),omitted_items=len(values)-len(positions))
    return result


def index_key(branch,eid,version):return 'character-index:'+branch+':'+eid+':'+version


def indexed_items(record):
    return {(section,item):value for section in PROFILE_SECTIONS for item,value in enumerate(section_value(record,section))}


def index_dependency(record):
    return {'profile_hash':record['hash'],'profile_data_hash':digest(record['data']),
            'core_hash':digest(section_value(record,'core')),'sources':record['sources'],
            'growth_hash':record['growth_hash'],'growth_count':record.get('growth_count',0)}


def valid_index(db,branch,eid,state=None):
    record=current(db,branch,eid)
    if not record or not usable(db,branch,eid,state):return False
    index=get(db,index_key(branch,eid,record['hash']))
    if not index or index.get('schema')!='novel-ledger-v2.character-index/2' or 'core_depends_on' not in index or index.get('dependencies')!=index_dependency(record):return False
    seal={k:v for k,v in index.items() if k!='hash'}
    return index.get('hash')==digest(seal)


def index_read(db,branch,eid):
    record=current(db,branch,eid)
    require(record,'character','尚无人物档案')
    index=get(db,index_key(branch,eid,record['hash']))
    return {'valid':valid_index(db,branch,eid),'index':index,
            'instruction':'索引为独立解读，不是新事实；未知、全局或失效条件需保留完整材料。'}


def next_index_job(db,branch,chapter,plan,state=None):
    if not enabled(db) or get(db,'contract').get('character_views','full')!='scoped':return None
    state=state if state is not None else replay(db,branch)
    ids=[eid for eid in participants(plan,state) if usable(db,branch,eid,state) and indexed_items(current(db,branch,eid)) and not valid_index(db,branch,eid,state)]
    if not ids:return None
    return ('character_index',chapter,{'entities':ids,'instruction':'独立完整阅读本次人物档案与历史来源，逐条建立适用范围和因果依赖索引。relevance 标签只是提案，不能直接认定无关。只有适用范围穷尽且可解释时写 scoped；不确定写 unknown，普遍影响动机/声音写 global。逐条覆盖 experiences、relationship_modes、examples；必填 core_depends_on 列出完整核心动机/声音所依赖的条目，无关联才可空，跨条目影响用 depends_on 明确关联。全部 sources 原章用 recall --chapter 实际完整补读。不得以关键词、最近经历或字符截断代替判断；见 references/characters.md。'})


def save_index(db,branch,data,task_id,work):
    """Accept a complete independent interpretation against the frozen full source.

    Structural validation binds the judgment to its actual read materials; it does
    not claim to mechanically prove literary relevance or the reviewer's reasoning.
    """
    require(isinstance(data,dict) and set(data)=={'indexes'} and isinstance(data['indexes'],list),'character_index','character_index data 只包含 indexes 数组')
    expected=work.get('payload',{}).get('entities',[])
    ids=[index.get('entity') for index in data['indexes'] if isinstance(index,dict)]
    require(len(ids)==len(expected)==len(data['indexes']) and set(ids)==set(expected),'character_index','只能且必须索引本次请求的人物，不遗漏或重复')
    frozen=work.get('character_context',{}).get('profiles',{})
    state=replay(db,branch)
    pending=db.execute('SELECT stage,state FROM tasks WHERE id=?',(task_id,)).fetchone()
    require(pending and pending['stage']=='character_index' and pending['state']=='pending','character_index','索引只能由独立 character_index 任务提交')
    from .dependencies import read_chapters,read_set
    delivered=read_chapters(db,task_id,branch)
    for proposal in data['indexes']:
        eid=proposal['entity'];record=current(db,branch,eid)
        require(record and usable(db,branch,eid,state),'character_index','人物档案已失效，不能建立旧条件索引')
        require(eid in frozen and frozen[eid].get('data')==record['data'] and frozen[eid].get('hash')==record['hash'],'character_index','索引需读取本任务冻结的完整人物档案')
        require(set(proposal)<= {'entity','profile_hash','core_hash','coverage','items','core_depends_on'} and proposal.get('profile_hash')==record['hash'] and proposal.get('core_hash')==digest(section_value(record,'core')) and proposal.get('coverage')=='complete','character_index','索引须绑定完整档案与核心版本，并声明完整覆盖')
        expected_items=indexed_items(record);items=proposal.get('items')
        core_dependencies=proposal.get('core_depends_on')
        require(isinstance(core_dependencies,list),'character_index','须明确列出 core_depends_on，核心动机/声线的历史依据不能被场景省略；无关联可空数组')
        core_keys=[]
        for dependency in core_dependencies:
            require(isinstance(dependency,dict) and set(dependency)=={'section','item'} and dependency.get('section') in PROFILE_SECTIONS and type(dependency.get('item')) is int and (dependency.get('section'),dependency['item']) in expected_items,'character_index','核心因果依赖须引用同版本内有效条目')
            core_keys.append((dependency['section'],dependency['item']))
        require(len(core_keys)==len(set(core_keys)),'character_index','核心因果依赖不能重复')
        require(isinstance(items,list) and len(items)==len(expected_items),'character_index','索引必须覆盖每个经历、关系语气和例句条目')
        keys=[]
        for item in items:
            require(isinstance(item,dict) and set(item)<= {'section','item','value_hash','classification','reason','scope','scope_complete','depends_on'},'character_index','索引条目字段无效')
            ident=(item.get('section'),item.get('item'));keys.append(ident)
            require(item.get('section') in PROFILE_SECTIONS and type(item.get('item')) is int and ident in expected_items and item.get('value_hash')==digest(expected_items[ident]),'character_index','索引条目须绑定真实 section/item 与完整内容 hash')
            classification=item.get('classification')
            require(classification in ('global','unknown','scoped') and text(item.get('reason')),'character_index','条目须说明 global/unknown/scoped 分类及依据')
            if classification=='scoped':
                require(item.get('scope_complete') is True and isinstance(item.get('scope'),dict) and bool(item['scope']),'character_index','scoped 须明确适用条件穷尽，不能以相关性标签代替')
                validate_relevance({'relevance':item['scope']},state)
            else:
                require('scope' not in item and 'scope_complete' not in item,'character_index','unknown/global 不得伪装成可省略的穷尽条件')
            dependencies=item.get('depends_on',[])
            require(isinstance(dependencies,list),'character_index','depends_on 为完整条目关联数组')
            dependency_keys=[]
            for dependency in dependencies:
                require(isinstance(dependency,dict) and set(dependency)=={'section','item'} and dependency.get('section') in PROFILE_SECTIONS and type(dependency.get('item')) is int and (dependency.get('section'),dependency['item']) in expected_items,'character_index','因果依赖须引用同版本内有效条目')
                dependency_keys.append((dependency['section'],dependency['item']))
            require(len(dependency_keys)==len(set(dependency_keys)),'character_index','因果依赖不能重复')
        require(len(set(keys))==len(expected_items),'character_index','索引不能重复条目或遗漏条目')
        if any(item['classification']=='scoped' for item in items):
            require(all(delivered.get(number)==version for number,version in record['sources'].items()),'character_index_sources','scoped 索引需由工具实际返回全部历史来源原章；摘要、引文、部分页或自报已读不能证明完整来源')
        index={'schema':'novel-ledger-v2.character-index/2','entity':eid,'task_id':task_id,'core_depends_on':copy.deepcopy(core_dependencies),
               'dependencies':copy.deepcopy(index_dependency(record)),'items':copy.deepcopy(items),
               'verified_source_reads':{number:delivered[number] for number in record['sources'] if number in delivered},
               'read_set_signature':read_set(db,task_id)['signature'],
               'status':'independent_scope_interpretation_not_canon'}
        index['hash']=digest(index)
        put(db,index_key(branch,eid,record['hash']),index)
        log(db,'character_index_submit',{'branch':branch,'entity':eid,'profile_hash':record['hash'],'index_hash':index['hash'],'task_id':task_id})


def scene_scope(plan,state):
    """Structured planning scope. Missing dimensions stay unknown, never empty."""
    entities=set(plan.get('entities',[]));topics=set(plan.get('topics',[]));places=set()
    for choice in plan.get('character_choices',[]):entities.add(choice['entity'])
    for scene in plan.get('scenes',[]):
        entities.update(scene.get('present',[]));entities.update(scene.get('speakers',[]));entities.update(scene.get('props',[]))
        if scene.get('place'):places.add(scene['place'])
        if scene.get('design_location'):places.add(scene['design_location'])
        for intent in scene.get('dialogue_intents',[]):
            entities.add(intent['entity'])
            if intent.get('to'):entities.add(intent['to'])
            topics.update(intent.get('knowledge_topics',[]))
    return {'entities':entities or None,'topics':topics or None,'places':places or None}


def applicable(item,scope):
    if item['classification']!='scoped':return True
    # Every declared dimension is an alternative trigger. Any unknown trigger
    # prevents proving that the item can be omitted for this scene.
    return any(scope.get(dimension) is None or set(values)&scope[dimension] for dimension,values in item['scope'].items())


def selection_closure(index,scope):
    items={(item['section'],item['item']):item for item in index['items']}
    selected={ident for ident,item in items.items() if applicable(item,scope)}
    selected.update((item['section'],item['item']) for item in index.get('core_depends_on',[]))
    frontier=list(selected)
    while frontier:
        ident=frontier.pop()
        for dependency in items[ident].get('depends_on',[]):
            target=(dependency['section'],dependency['item'])
            if target not in selected:selected.add(target);frontier.append(target)
    return selected


def scoped_profile(db,branch,eid,record,plan,state):
    index=get(db,index_key(branch,eid,record['hash']));scope=scene_scope(plan,state)
    items={(item['section'],item['item']):item for item in index['items']}
    selected=selection_closure(index,scope)
    query_ref={'kind':'character_index','branch':branch,'entity':eid,'record_hash':record['hash'],'index_hash':index['hash'],
               'query_hash':digest({dimension:sorted(values) if values is not None else None for dimension,values in scope.items()})}
    if len(selected)==len(items):return copy.deepcopy(record),[source_ref(branch,eid,record),query_ref],{}
    view=copy.deepcopy(record);view['data']=section_value(record,'core');refs=[source_ref(branch,eid,record,'core')]
    omitted={};source_material=[view['data']]
    for section in PROFILE_SECTIONS:
        full=section_value(record,section);values=[]
        for number,value in enumerate(full):
            if (section,number) in selected:
                values.append(copy.deepcopy(value));refs.append(source_ref(branch,eid,record,section,number));source_material.append(value)
        destination=view['data']
        parts=section.split('.')
        for part in parts[:-1]:destination=destination[part]
        destination[parts[-1]]=values
        if len(values)<len(full):omitted[section]={'count':len(full)-len(values),'retrieve':'character --entity '+eid+' --section '+section+' --revision '+record['hash']+' [--topic TOPIC / --related-entity ID / --place ID]'}
    view['sources']=referenced_sources(record,source_material)
    view['view']={'schema':'novel-ledger-v2.character-view/1','profile_hash':record['hash'],'index_hash':index['hash'],
                  'scope':{dimension:sorted(values) if values is not None else None for dimension,values in scope.items()},
                  'selected_items':len(selected),'total_items':len(items),
                  'instruction':'这是完整档案的场景视图；省略依据为独立索引，缺少适用维度时保留原条目。补读使用原版本 section/item，不能把本视图当完整历史。'}
    refs.append(query_ref)
    # Choosing the less repetitive representation never removes extra material.
    # A nearly full view can cost more than the original because each included
    # item needs provenance. In that case send the complete version once.
    full_refs=[source_ref(branch,eid,record),query_ref]
    if len(dump([view,refs,omitted]))>=len(dump([record,full_refs,{}])):
        return copy.deepcopy(record),full_refs,{}
    return view,refs,omitted


def context(db,branch,ids,state,plan=None,stage=None):
    profiles={eid:current(db,branch,eid) for eid in ids};selected={};omitted={};refs=[];omitted_profiles={}
    for eid in ids:
        events=state.get('character_developments',{}).get(eid,[])
        record=profiles[eid]
        # Include every growth event not yet reflected in the profile, plus recent context.
        covered=record.get('growth_count',0) if record else 0
        if record and (covered>len(events) or record['growth_hash']!=digest(events[:covered])):covered=0
        start=min(covered,max(0,len(events)-3))
        selected[eid]=events[start:]
        if start:omitted[eid]={'count':start,'retrieve':'character --entity '+eid+' --history (or recall --entity '+eid+')'}
        if record:
            can_scope=plan is not None and stage not in ('character_plan','character_index','chapter_review') and get(db,'contract').get('character_views','full')=='scoped' and valid_index(db,branch,eid,state)
            if can_scope:
                profiles[eid],profile_refs,omission=scoped_profile(db,branch,eid,record,plan,state)
                refs.extend(profile_refs)
                if omission:omitted_profiles[eid]=omission
            else:refs.append(source_ref(branch,eid,record))
        for number,event in enumerate(selected[eid],start):
            refs.append({'kind':'character_development','branch':branch,'entity':eid,'item':number,'value_hash':digest(event)})
    result={'status':'interpretation_and_design_not_canon','profiles':profiles,'validity':{eid:usable(db,branch,eid,state) for eid in ids},'developments':selected,'omitted_developments':omitted,
            'instruction':'先分清小传设计、已有正文依据和本场景意图；成长不是违约，突变须有因果。人物言语还取决于对话对象、所知和当下目的。'}
    if plan is not None and get(db,'contract').get('character_views','full')=='scoped':
        result['source_refs']=refs
        result['omitted_profiles']=omitted_profiles
        result['view_policy']='scoped' if get(db,'contract').get('character_views','full')=='scoped' else 'full'
    return result


def apply_development(state,event,prose,chapter):
    eid=event.get('entity')
    require(person(state,eid) and text(event.get('change')) and text(event.get('cause')),'character_development','成长记录需要人物、具体变化与形成原因')
    evidence(prose,event.get('cause_evidence'),chapter)
    old=state.get('character_developments',{}).get(eid,[])
    require('before' in event and event['before']==(old[-1]['hash'] if old else None),'precondition','人物成长前态不匹配')
    item={k:copy.deepcopy(event[k]) for k in ('change','cause','evidence','cause_evidence')}
    item['before']=event['before'];item['hash']=digest(item)
    state.setdefault('character_developments',{}).setdefault(eid,[]).append(item)


def validate_scenes(plan,state):
    for scene in plan.get('scenes',[]):
        present=scene.get('present',[])
        speakers=scene.get('speakers',[e for e in present if person(state,e)])
        require(isinstance(speakers,list) and len(speakers)==len(set(speakers)) and all(person(state,e) and e in present for e in speakers),'dialogue','speakers 必须是本场景在场人物，静默场景可明确空数组')
        intents=scene.get('dialogue_intents',[])
        require(isinstance(intents,list) and all(isinstance(x,dict) for x in intents),'dialogue','dialogue_intents 为数组')
        require(len(intents)==len(speakers) and {i.get('entity') for i in intents}==set(speakers),'dialogue','每个拟发言者需要独立场景意图')
        for i in intents:
            require(all(text(i.get(k)) for k in ('seeks','withholds','pressure','tactic')),'dialogue','意图需要 seeks/withholds/pressure/tactic，允许明确说明无隐瞒或无额外压力')
            require(not i.get('to') or person(state,i['to']),'dialogue','to 引用已登记对话对象')
            require(strings(i.get('knowledge_topics',[])),'dialogue','knowledge_topics 为需核对的话题，不等于人物已知')


def copy_for_revision(db,parent,branch):
    if not enabled(db):return
    state=replay(db,parent)
    for eid in state['entities']:
        record=current(db,parent,eid)
        if record:put(db,key(branch,eid),record)  # Source hashes and growth replay determine validity on the new branch.


def resolve(state,value):
    matches=[eid for eid,row in state['entities'].items() if person(state,eid) and value in [eid,row['name']]+row.get('aliases',[])]
    require(len(matches)==1,'character','人物 ID/姓名/别名不能唯一定位');return matches[0]


def history(db,branch,eid):
    lineage=[];cursor=branch
    while cursor:
        lineage.append(cursor);row=db.execute('SELECT parent FROM branches WHERE id=?',(cursor,)).fetchone();cursor=row[0] if row else None
    placeholders=','.join('?' for _ in lineage);versions=[]
    for row in db.execute(f"SELECT id,branch,receipt FROM tasks WHERE branch IN ({placeholders}) AND stage='character_plan' AND state='done' ORDER BY rowid",lineage):
        for p in json.loads(row['receipt'])['data']['profiles']:
            if p['entity']==eid:versions.append({'task_id':row['id'],'branch':row['branch'],'data':p})
    state=replay(db,branch)
    return {'current':current(db,branch,eid),'valid':usable(db,branch,eid,state),'versions':versions,'developments':state.get('character_developments',{}).get(eid,[])}
