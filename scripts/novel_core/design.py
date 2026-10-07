"""Layered prospective design; never a second source of canonical facts."""
import copy
import json
from .store import get, put, require, digest, head, log
from .ledger import replay


def enabled(db):
    return get(db,'contract').get('planning_mode')=='layered'


def key(branch,scope):
    return 'design:'+branch+':'+scope


def current(db,branch,scope):
    return get(db,key(branch,scope))


def usable(db,branch,scope):
    record=current(db,branch,scope)
    if not record or not record['active']:return False
    for number,expected in record['sources'].items():
        row=db.execute('SELECT hash FROM chapters WHERE branch=? AND number=?',(branch,int(number))).fetchone()
        if not row or row[0]!=expected:return False
    if scope!='world':
        world=current(db,branch,'world')
        if not usable(db,branch,'world') or record['world_hash']!=world['hash']:return False
    refs=record['data'].get('canon_refs',[])
    if refs:
        state=replay(db,branch)
        for ref in refs:
            name=ref['entity']+':'+ref['key']
            if name not in state['facts'] or state['facts'][name]!=ref['value']:return False
    return True


def volume_for(db,branch,chapter):
    row=db.execute('SELECT data FROM plans WHERE branch=? AND number=?',(branch,chapter)).fetchone()
    if row:return json.loads(row[0])['volume']
    volumes=get(db,'contract')['volumes']
    prior=db.execute('SELECT data FROM plans WHERE branch=? AND number=?',(branch,chapter-1)).fetchone()
    if not prior:return volumes[0]['id']
    plan=json.loads(prior[0]);ids=[v['id'] for v in volumes]
    index=ids.index(plan['volume'])+int(bool(plan.get('volume_end')))
    require(index<len(ids),'design_volume','已到末卷末尾，须核对终局标记而非生成不存在的新卷')
    return ids[index]


def job(db,branch,scope,chapter):
    previous=current(db,branch,scope)
    return ('world_plan' if scope=='world' else 'volume_plan',chapter,
            {'design_scope':scope,'previous_design':previous,
             'instruction':'设计尚未发生的内容，不修改作者约定或正式事实。全书保持因果与核心地理；本卷细化常驻场所和必要行动条件。按 references/design-and-brief.md 返回 data。'})


def text(value):return isinstance(value,str) and bool(value.strip())


def strings(value,nonempty=False):
    return isinstance(value,list) and (bool(value) or not nonempty) and all(text(v) for v in value)


# 章末收束形态的受控类别。真实项目里反复出现的缺陷不是"章末写得差",而是多章
# 连用同一种收束模具(如连续以同一物件静收);类别受控才能机械校验轮换。
CLOSING_SHAPES=('钩子','悬念','闭环','交接','爆发','静收','反转','余波')


def closing_category(value):
    """ending_shape 形如 '类别:说明';返回类别,非法定期返回 None。"""
    if not text(value):return None
    for sep in (':','：'):
        if sep in value:
            category=value.split(sep,1)[0].strip()
            return category if category in CLOSING_SHAPES else None
    return None


SPINE_LAYERS=('main','subplot','timeline','tension')


def validate_spine(spine):
    """Four-layer book spine at act granularity: main line, subplot, timeline,
    tension curve. Act-level touchpoints stay coarse; per-chapter order remains
    the plan layer's job."""
    require(isinstance(spine,dict) and set(spine)==set(SPINE_LAYERS),'design',
            'event_spine 需要 main/subplot/timeline/tension 四层')
    ids=set()
    for layer in SPINE_LAYERS:
        items=spine[layer]
        require(isinstance(items,list) and items and all(isinstance(i,dict) for i in items),f'design',
                f'脊柱 {layer} 层为非空触点对象数组')
        for item in items:
            require(isinstance(item,dict) and all(text(item.get(k)) for k in ('id','span','event')),'design',
                    '脊柱触点需要 id/span/event')
            require(item['id'].startswith('sp:'),'design','脊柱触点 ID 使用 sp: 前缀')
            require(item['id'] not in ids,'design',f"脊柱触点 ID 重复：{item['id']}")
            ids.add(item['id'])
    return ids


def spine_ids(db,branch):
    """IDs of the active world spine, or None when the design predates spines."""
    world=current(db,branch,'world')
    if not world or not world.get('active') or 'event_spine' not in world['data']:
        return None
    return {item['id'] for layer in SPINE_LAYERS for item in world['data']['event_spine'][layer]}


def locations(items,state):
    require(isinstance(items,list),'design','locations 必须为数组')
    ids=[]
    for item in items:
        require(isinstance(item,dict) and set(item)<= {'id','name','purpose','layout','constraints','canon_place'},'design','场所设计字段无效')
        require(all(text(item.get(k)) for k in ('id','name','purpose')),'design','场所设计需要 id/name/purpose')
        require(item['id'].startswith('design:'),'design','候选场所 ID 使用 design: 前缀，与事实实体分开')
        require(strings(item.get('layout',[])) and strings(item.get('constraints',[])),'design','布局与限制为字符串数组，未定内容可以留空')
        if item.get('canon_place') is not None:require(item['canon_place'] in state.get('places',{}),'design','canon_place 须引用已有物理地点')
        ids.append(item['id'])
    require(len(ids)==len(set(ids)),'design','候选场所 ID 重复')
    return set(ids)


def choices(entries):
    """Adjudication trail for volume-outline reasoning: the direction and
    pivotal-beat options that were presented, which was chosen, and why.
    Unattended runs self-adjudicate within the hatched outline and log it here."""
    require(isinstance(entries,list) and entries,'design','choice_log 至少记录一次裁决')
    for entry in entries:
        require(isinstance(entry,dict) and entry.get('kind') in ('direction','beat'),'design','choice_log 条目 kind 为 direction/beat')
        require(entry.get('mode') in ('interactive','unattended'),'design','choice_log mode 为 interactive/unattended')
        if entry['kind']=='beat':
            require(type(entry.get('beat')) is int and entry['beat']>=1,'design','beat 裁决需要 1 起的序号')
        require(text(entry.get('chosen')) and text(entry.get('reason')),'design','choice_log 需要 chosen 与 reason')
        candidates=entry.get('candidates')
        require(isinstance(candidates,list) and len(candidates)>=2,'design','方向候选至少两个')
        require(any(isinstance(c,dict) and c.get('wild') is True for c in candidates),'design','候选须含至少一个 wild 脑爆项')
        for c in candidates:
            require(isinstance(c,dict) and all(text(c.get(k)) for k in ('direction','payoff','cost','risk')),'design','候选需要 direction/payoff/cost/risk')


def save(db,branch,scope,chapter,data,task_id):
    require(enabled(db),'design','本书未启用分层设计')
    state=replay(db,branch)
    common={'dependencies','canon_refs','open_questions'}
    if scope=='world':
        allowed=common|{'overview','causal_spine','event_spine','factions','geography','core_locations','ending_dependencies'}
        require(set(data)<=allowed,'design','全书设计包含未知字段；不能提交正文事件或覆盖作者约定')
        require(text(data.get('overview')) and strings(data.get('causal_spine'),True) and strings(data.get('ending_dependencies'),True),'design','全书设计需要概述、主要因果节点和终局依赖')
        validate_spine(data.get('event_spine'))
        geo=data.get('geography')
        require(isinstance(geo,dict) and set(geo)=={'regions','connections'} and strings(geo['regions'],True) and strings(geo['connections']),'design','geography 需要 regions/connections 数组')
        factions=data.get('factions')
        require(isinstance(factions,list) and all(isinstance(f,dict) and all(text(f.get(k)) for k in ('name','drive','limits')) for f in factions),'design','势力需要 name/drive/limits，可无组织势力')
        require(data.get('core_locations'),'design','先设计至少一个全书核心场所')
        locations(data['core_locations'],state)
        world_hash=None
    else:
        allowed=common|{'volume','purpose','carries_in','climax','handoff','location_ids','new_locations','sequence','action_constraints','ending_setup','choice_log'}
        require(set(data)<=allowed,'design','分卷设计包含未知字段')
        require(usable(db,branch,'world'),'design','先完成有效全书设计')
        require(data.get('volume')==scope and text(data.get('purpose')),'design','分卷用途或 ID 不匹配任务')
        # Volume outline: how this volume is entered, peaked, and handed on —
        # the signed part of the volume proposal, checked before any chapter of it is planned.
        require(text(data.get('carries_in')) and text(data.get('climax')) and text(data.get('handoff')),'design',
                '卷纲需要 carries_in（承接上卷）、climax（卷高潮/结尾方向）、handoff（下卷交接；末卷写如何收束到终局）')
        require(strings(data.get('sequence'),True) and strings(data.get('action_constraints')) and strings(data.get('ending_setup')),'design','本卷需要因果顺序、行动约束及终局铺设数组')
        choices(data.get('choice_log'))
        world=current(db,branch,'world');world_hash=world['hash']
        known={i['id'] for i in world['data']['core_locations']}
        new=locations(data.get('new_locations',[]),state)
        require(not known.intersection(new),'design','本卷新地点不能覆盖全书候选地点')
        require(strings(data.get('location_ids')) and set(data['location_ids'])<=known|new,'design','本卷地点须引用全书地点或本卷新地点')
    require(strings(data.get('open_questions',[])),'design','open_questions 为字符串数组')
    dependencies=data.get('dependencies',[])
    require(isinstance(dependencies,list) and all(type(n) is int and 1<=n<chapter for n in dependencies),'design','依赖须是已提交历史章')
    sources={}
    for n in dependencies:
        row=db.execute('SELECT hash FROM chapters WHERE branch=? AND number=?',(branch,n)).fetchone()
        require(row,'design','设计依赖章节不存在');sources[str(n)]=row[0]
    refs=data.get('canon_refs',[])
    require(isinstance(refs,list),'design','canon_refs 为事实引用数组')
    for ref in refs:
        require(isinstance(ref,dict) and text(ref.get('entity')) and text(ref.get('key')) and 'value' in ref,'design','canon_refs 需要 entity/key/value')
        name=ref['entity']+':'+ref['key']
        require(name in state['facts'] and state['facts'][name]==ref['value'],'design_canon_conflict','设计引用与当前事实不符，不能用预设覆盖正文')
    # A replaced design invalidates future chapter plans, not canonical prose.
    if scope=='world':
        for volume in get(db,'contract')['volumes']:invalidate(db,branch,volume['id'],'全书设计版本更新')
        db.execute('DELETE FROM plans WHERE branch=? AND number>?',(branch,head(db,branch)))
    else:
        for row in db.execute('SELECT number,data FROM plans WHERE branch=? AND number>?',(branch,head(db,branch))).fetchall():
            if json.loads(row['data'])['volume']==scope:db.execute('DELETE FROM plans WHERE branch=? AND number=?',(branch,row['number']))
    previous=current(db,branch,scope)
    record={'scope':scope,'revision':previous['revision']+1 if previous else 1,'active':True,'status':'proposal_not_canon',
            'data':copy.deepcopy(data),'sources':sources,'world_hash':world_hash,'task_id':task_id,'based_on_chapter':head(db,branch)}
    record['hash']=digest({k:v for k,v in record.items() if k!='active'})
    put(db,key(branch,scope),record)
    log(db,'design_submit',{'branch':branch,'scope':scope,'revision':record['revision'],'hash':record['hash']})


def invalidate(db,branch,scope,reason):
    scopes=['world']+[v['id'] for v in get(db,'contract')['volumes']] if scope=='world' else [scope]
    for name in scopes:
        record=current(db,branch,name)
        if record:
            record['active']=False;record['invalidation_reason']=reason;put(db,key(branch,name),record)
    log(db,'design_invalidate',{'branch':branch,'scope':scope,'reason':reason})


def copy_for_revision(db,parent,branch,start):
    if not enabled(db):return
    ids=[v['id'] for v in get(db,'contract')['volumes']]
    row=db.execute('SELECT data FROM plans WHERE branch=? AND number=?',(parent,start)).fetchone()
    affected=ids.index(json.loads(row[0])['volume'])
    for scope in ['world']+ids:
        record=current(db,parent,scope)
        if not record:continue
        if any(int(n)>=start for n in record['sources']) or (scope!='world' and ids.index(scope)>=affected):
            record['active']=False;record['invalidation_reason']='正文候选修订，需要重审受影响设计'
        put(db,key(branch,scope),record)
    db.execute('DELETE FROM plans WHERE branch=? AND number>=?',(branch,start))


def history(db,branch,scope):
    rows=[];lineage=[];cursor=branch
    while cursor:
        lineage.append(cursor)
        parent=db.execute('SELECT parent FROM branches WHERE id=?',(cursor,)).fetchone()
        cursor=parent[0] if parent else None
    placeholders=','.join('?' for _ in lineage)
    for row in db.execute(f"SELECT id,branch,stage,receipt FROM tasks WHERE branch IN ({placeholders}) AND stage IN ('world_plan','volume_plan') AND state='done' ORDER BY rowid",lineage):
        receipt=json.loads(row['receipt']);data=receipt['data']
        if ('world' if row['stage']=='world_plan' else data['volume'])==scope:
            rows.append({'task_id':row['id'],'branch':row['branch'],'data':data})
    return {'current':current(db,branch,scope),'versions':rows,'status':'proposal_not_canon'}


def context(db,branch,volume=None):
    out={'status':'proposal_not_canon','world':current(db,branch,'world')}
    if volume:out['volume']=current(db,branch,volume)
    return out


def validate_brief(plan,state,known_locations,volume=None):
    require(text(plan.get('reader_experience')),'brief','分层模式需要本章 reader_experience')
    # 章计划对卷纲的方向对齐在这里把关:每章声明推进的节拍与本章收束形态。
    refs=plan.get('sequence_refs')
    sequence=(volume or {}).get('sequence') or []
    if sequence:
        require(isinstance(refs,list) and refs and all(type(n) is int and 1<=n<=len(sequence) for n in refs),'brief',
                f'sequence_refs 须为 1..{len(sequence)} 的节拍序号数组,声明本章推进本卷 sequence 的哪些节拍')
    shape=plan.get('ending_shape')
    require(text(shape),'brief','分层模式需要本章收束形态 ending_shape,格式"类别:说明"')
    require(closing_category(shape) is not None,'brief',
            f'ending_shape 须以收束类别开头({"、".join(CLOSING_SHAPES)}),后接具体说明')
    choices=plan.get('character_choices')
    require(isinstance(choices,list) and choices,'brief','本章需要人物选择 character_choices')
    for c in choices:
        require(isinstance(c,dict) and c.get('entity') in state['entities'] and all(text(c.get(k)) for k in ('desire','pressure','choice','cost')),'brief','人物选择需要 entity/desire/pressure/choice/cost')
    require(isinstance(plan.get('scenes'),list) and plan['scenes'],'brief','每章至少一个场景提案，数量与顺序不固定')
    for scene in plan['scenes']:
        require(all(text(scene.get(k)) for k in ('intent','conflict','turn')),'brief','场景提案需要 intent/conflict/turn')
        require(not scene.get('design_location') or scene['design_location'] in known_locations,'brief','场景候选地点不属于本卷设计')
        require(isinstance(scene.get('props',[]),list) and all(e in state['entities'] for e in scene.get('props',[])),'brief','关键物品 props 使用已登记实体 ID，新物品先在正文中呈现')
        require(all(strings(scene.get(k,[])) for k in ('blocking','perceptual_limits')),'brief','站位建议和感知限制为字符串数组')
    require(strings(plan.get('expression_space',[])),'brief','expression_space 为表达空间数组')


def writer_brief(plan):
    return {'schema':'novel-ledger-v2.writer-brief/1','chapter':plan['number'],
            'dramatic_task':{'reader_experience':plan.get('reader_experience','结合本书创作约定'), 'goal':plan['goal'],'conflict':plan['conflict'],
                             'necessary_change':plan['change'],'ending_shape':plan.get('ending_shape',''),'character_choices':plan.get('character_choices',[])},
            'scene_proposals':plan.get('scenes',[]),
            'continuity_sources':{'current_state':'state','layout':'continuity','historical_evidence':'selected','prospective_world':'design_context','character_profiles':'character_context'},
            'expression_space':plan.get('expression_space',[]),
            'instruction':'场景提案、人物选择和必要变化是创作方向，未发生前不是事实。结合当前状态写完整小说正文；可调整场景顺序、动作与对话，新细节须在事实审稿中提取。不要把任务书写成正文或替自己审稿。'}


def render_brief(work):
    brief=work['writer_brief'];state=work.get('state',{});continuity=work.get('continuity',{})
    names={k:v['name'] for k,v in state.get('entities',{}).items()}
    names.update({p['id']:p['display_name'] for p in continuity.get('places',[])})
    def name(e):return names.get(e,e)
    def display(value):
        if isinstance(value,list):return '；'.join(name(v) if isinstance(v,str) else json.dumps(v,ensure_ascii=False) for v in value) or '未指定'
        return name(value) if isinstance(value,str) else json.dumps(value,ensure_ascii=False)
    lines=['# 第 '+str(work['chapter'])+' 章场景任务书','',brief['instruction'],'','## 创作任务']
    task=brief['dramatic_task']
    for key_,label in [('reader_experience','读者体验'),('goal','目标'),('conflict','冲突'),('necessary_change','必要变化'),('ending_shape','收束形态')]:lines+=['',label+'：'+(task[key_] if task.get(key_) else '未指定')]
    for c in task['character_choices']:lines+=['',f"人物 {name(c['entity'])}：想要 {c['desire']}；压力 {c['pressure']}；选择 {c['choice']}；代价 {c['cost']}"]
    profiles=work.get('character_context',{}).get('profiles',{})
    if profiles:
        lines+=['','## 人物小传与表达依据（设计和解读，非新增事实）']
        for eid,record in profiles.items():
            if not record:continue
            p=record['data'];bio=p['biography'];voice=p['voice']
            lines+=['',name(eid)+'：'+bio['public_role']+'；诉求：'+bio['desire'],'','表达：'+voice['register']+'；'+display(voice['tactics'])]
            if voice.get('stress_shift'):lines+=['','压力下：'+voice['stress_shift']]
            for mode in voice.get('relationship_modes',[]):lines+=['','面对 '+name(mode['audience'])+'：'+mode['approach']]
    lines+=['','## 当前连续性（已确立）']
    for entity,record in continuity.get('placements',{}).items():
        position=record['position']
        location='由 '+name(position['holder'])+' 携带/容纳' if 'holder' in position else '位于 '+name(position['place'])
        lines+=['',name(entity)+'：'+location]
    for key_,record in state.get('knowledge',{}).items():
        actor,topic=key_.split(':',1)
        lines+=['',name(actor)+' 对“'+topic+'”的认知：'+record['stance']+' / '+record['claim']+'；来源：'+str(record['source'])]
    if not continuity.get('placements') and not state.get('knowledge'):lines+=['','本输入未选入站位/认知记录；不表示人物无位置或不知道，按需检索原文。']
    lines+=['','## 场景提案（可调整，尚未发生）']
    labels={'place':'地点','label':'候选地点','design_location':'关联设计','present':'拟在场人物','intent':'意图','conflict':'冲突','turn':'变化','time_hint':'时间衔接','props':'关键物件','blocking':'站位与动作建议','perceptual_limits':'感知限制'}
    for i,scene in enumerate(brief['scene_proposals'],1):
        lines+=['',f"### 场景 {i}：{name(scene.get('place') or scene.get('label'))}"]
        for k in labels:
            if k in scene:lines+=['',labels[k]+'：'+display(scene[k])]
        for intent in scene.get('dialogue_intents',[]):
            lines+=['',name(intent['entity'])+' 本场景诉求：'+intent['seeks']+'；保留：'+intent['withholds']+'；压力：'+intent['pressure']+'；策略：'+intent['tactic']]
    lines+=['','## 表达空间']+['\n- '+s for s in brief['expression_space']]
    if work.get('context_materials'):
        lines+=['','## 已准备的历史材料','','历史材料已分批审读；必须结合 input.json 的 context_notes 与 context_materials 来源索引。缺少原始字段不表示没有该事实，疑点用 material 定向核对；提炼不能新增事实。']
    lines+=['','## 材料与事实边界','','本文件由同任务 input.json 派生，是 writer_brief 的替代阅读视图；已读完整 input.json 时不重复加载。其余输入中的约定、当前状态、历史证据与来源结论仍须核对，未来设计不能覆盖已有事实。补读通过 recall/material，额度不足按 context_more 分批准备。','']
    return '\n'.join(lines)
