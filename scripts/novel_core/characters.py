"""Versioned character interpretations with source-bound growth, not new world facts."""
import copy
import json
from .store import get,put,require,digest,log,head
from .ledger import replay,evidence


def enabled(db):return get(db,'contract').get('character_mode')=='profiled'
def text(v):return isinstance(v,str) and bool(v.strip())
def strings(v):return isinstance(v,list) and all(text(x) for x in v)
def person(state,eid):return eid in state['entities'] and state['entities'][eid]['kind'] in ('person','character')
def key(branch,eid):return 'character:'+branch+':'+eid
def current(db,branch,eid):return get(db,key(branch,eid))
def growth_hash(state,eid):return digest(state.get('character_developments',{}).get(eid,[]))


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
        require(isinstance(e,dict) and set(e)<= {'text','status','evidence'} and text(e.get('text')) and e.get('status') in ('design','established'),'character','经历必须明确为设计或已确立')
        if e['status']=='established':source(e.get('evidence'))
        else:require('evidence' not in e,'character','未发生经历不能伪装成已有证据')
    modes=voice.get('relationship_modes',[])
    require(isinstance(modes,list) and all(isinstance(m,dict) and text(m.get('audience')) and text(m.get('approach')) for m in modes),'character','关系语气需要 audience/approach')
    require(strings(voice.get('avoid',[])),'character','avoid 为写法提醒数组，不设置禁词配额')
    examples=data.get('examples',[])
    require(isinstance(examples,list),'character','examples 为已有正文片段数组')
    for example in examples:
        require(isinstance(example,dict) and example.get('speaker')==data['entity'] and text(example.get('note')),'character','代表性对白须标同一人物 speaker 及语境 note')
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
        record={'data':copy.deepcopy(profile),'revision':old['revision']+1 if old else 1,'active':True,'sources':sources,
                'growth_hash':growth_hash(state,eid),'growth_count':len(state.get('character_developments',{}).get(eid,[])),'task_id':task_id,'status':'interpretation_and_design_not_canon','based_on_chapter':head(db,branch)}
        record['hash']=digest(record);put(db,key(branch,eid),record)
        log(db,'character_submit',{'branch':branch,'entity':eid,'revision':record['revision'],'hash':record['hash']})


def context(db,branch,ids,state):
    profiles={eid:current(db,branch,eid) for eid in ids};selected={};omitted={}
    for eid in ids:
        events=state.get('character_developments',{}).get(eid,[])
        record=profiles[eid]
        # Include every growth event not yet reflected in the profile, plus recent context.
        covered=record.get('growth_count',0) if record else 0
        if record and (covered>len(events) or record['growth_hash']!=digest(events[:covered])):covered=0
        start=min(covered,max(0,len(events)-3))
        selected[eid]=events[start:]
        if start:omitted[eid]={'count':start,'retrieve':'character --entity '+eid+' --history (or recall --entity '+eid+')'}
    return {'status':'interpretation_and_design_not_canon','profiles':profiles,'validity':{eid:usable(db,branch,eid,state) for eid in ids},'developments':selected,'omitted_developments':omitted,
            'instruction':'先分清小传设计、已有正文依据和本场景意图；成长不是违约，突变须有因果。人物言语还取决于对话对象、所知和当下目的。'}


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
