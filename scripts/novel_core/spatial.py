"""Evidence-bound anchors, sparse place graphs and scene blocking.

A map is a partial graph, never proof that unregistered space does not exist.
All changes still enter through the chapter event transaction.
"""
from __future__ import annotations
import copy
from .store import Error, require

PLACE_BINDINGS = ('office_place','function_place')
SPATIAL_OPS = {'anchor', 'anchor_change', 'anchor_assert', 'place', 'link', 'link_change', 'placement', 'move', 'perceive', 'place_change'}


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def fact_key(entity, key):
    return entity + ':' + key


def source(event):
    return copy.deepcopy(event.get('evidence') or {'kind':'author_contract'})


def anchor(state, event):
    entity, key = event.get('entity'), event.get('key')
    require(entity in state['entities'] and nonempty(key), 'anchor', '稳定事实须引用已登记实体和属性')
    require(not (entity in state.get('placements',{}) and key in ('location','location_id','holder')), 'protected_placement', '已有站位只能通过 placement/move 管理，不能另建稳定位置')
    name = fact_key(entity, key)
    anchors = state.setdefault('anchors', {})
    policy = event.get('policy', 'fixed')
    require(policy in ('fixed', 'eventful') and 'value' in event, 'anchor', '稳定事实须有 value，policy 为 fixed/eventful')
    existing = anchors.get(name)
    if existing:
        require(state['facts'][name] == event['value'] and existing['policy'] == policy, 'anchor_conflict', f'{entity}.{key} 已锁定为 {state["facts"][name]!r}，不能改写成 {event["value"]!r}')
        return
    require(name not in state['facts'] or state['facts'][name] == event['value'], 'anchor_conflict', '已有状态与要锁定的事实不一致，请先核对历史')
    state['facts'][name] = copy.deepcopy(event['value'])
    anchors[name] = {'policy':policy, 'source':source(event), 'last_change':None}


def ancestors(state, place):
    out=[]
    while place is not None:
        require(place in state.get('places', {}), 'place', f'场所未登记：{place}')
        require(place not in out, 'place_cycle', '场所包含关系不能成环')
        out.append(place)
        place=state['facts'][fact_key(place,'parent')]
    return out


def address(state, place):
    chain=ancestors(state,place)
    fields={}
    for node in reversed(chain):
        own={k.split(':',1)[1]:v for k,v in state['facts'].items() if k.startswith(node+':') and k.split(':',1)[1] in ('floor','room_number','address','facing')}
        # A room number belongs to its own node, not its children by inference.
        if node!=place:own.pop('room_number',None)
        fields.update(own)
    return {'place':place,'chain':list(reversed(chain)),**fields}


def add_place(state, event):
    eid=event.get('entity');parent=event.get('parent')
    require(eid in state['entities'] and state['entities'][eid]['kind']=='place', 'place', '场所必须先登记 kind=place 的稳定实体')
    require(eid not in state.get('places',{}), 'place_duplicate', '场所已存在，不能另登记一份覆盖布局')
    require(parent is None or parent in state.get('places',{}), 'place', '先登记父场所，再登记内部区域')
    attrs=event.get('attributes',{})
    require(isinstance(attrs,dict) and 'parent' not in attrs, 'place','attributes 必须为对象，parent 单独声明')
    for key in ('floor','room_number'):
        if key in attrs:require(nonempty(attrs[key]), 'place', '楼层与房号用原样字符串；不从 101 推断楼层')
    if parent:
        inherited=address(state,parent).get('floor')
        require('floor' not in attrs or inherited is None or attrs['floor']==inherited,'floor_conflict','子场所楼层与父区域冲突')
    state.setdefault('places',{})[eid]={'source':source(event)}
    for key,value in {'parent':parent,**attrs}.items():
        anchor(state,{'entity':eid,'key':key,'value':value,'policy':'fixed','evidence':event.get('evidence')})
    # Labels can repeat across buildings, but the same floor in one parent cannot have two physical rooms with one number.
    if attrs.get('room_number'):
        for other in state['places']:
            if other==eid:continue
            if state['facts'][fact_key(other,'parent')]==parent:
                a,b=address(state,other),address(state,eid)
                require((a.get('floor'),a.get('room_number')) != (b.get('floor'),b.get('room_number')),'room_duplicate','同一父场所同一楼层房号重复；请引用原场所 ID')

    validate_places(state, bindings=False)


def add_link(state,event):
    ident=event.get('id');a,b=event.get('a'),event.get('b');kind=event.get('kind')
    require(nonempty(ident) and ident not in state.get('links',{}), 'link', '连接 ID 缺失或重复')
    require(a in state.get('places',{}) and b in state.get('places',{}) and a!=b, 'link', '连接两端必须是不同的已登记场所')
    require(kind in ('path','sight','sound'),'link','kind 只能 path/sight/sound，通行不自动意味着可见或可听')
    require(type(event.get('directed',False)) is bool and type(event.get('open',True)) is bool,'link','directed/open 必须是布尔值')
    duration=event.get('min_ticks')
    require(duration is None or (kind=='path' and type(duration) is int and duration>=0),'link','路径最短耗时为非负整数，单位沿用项目 clock_unit')
    requirements=event.get('requires',{})
    require(isinstance(requirements,dict) and all(isinstance(k,str) and ':' in k and k.split(':')[0] in state['entities'] for k in requirements),'link','requires 必须引用实体:属性，例如 door:open')
    state.setdefault('links',{})[ident]={'a':a,'b':b,'kind':kind,'directed':event.get('directed',False),'open':event.get('open',True),
                                      'min_ticks':duration,'label':event.get('label',''),'requires':copy.deepcopy(requirements),'source':source(event),'last_change':None}


def at(state,entity):
    seen=set()
    while entity in state.get('placements',{}):
        require(entity not in seen,'holder_cycle','携带/容纳关系不能成环')
        seen.add(entity)
        pos=state['placements'][entity]['position']
        if 'place' in pos:return pos['place']
        entity=pos['holder']
    return None


def arrival_bound(state,entity):
    """Preserve the latest established time through nested carriers and handoffs."""
    seen=set();times=[]
    while entity in state.get('placements',{}):
        require(entity not in seen,'holder_cycle','携带/容纳关系不能成环')
        seen.add(entity);record=state['placements'][entity]
        if record.get('last_arrival') is not None:times.append(record['last_arrival'])
        if 'holder' not in record['position']:break
        entity=record['position']['holder']
    return max(times) if times else None


def position_value(state, value):
    require(isinstance(value,dict) and len(value)==1 and set(value)<= {'place','holder'},'placement','位置只能是单一 place 或 holder，不能同时放在桌上又被人拿着')
    if 'place' in value:require(value['place'] in state.get('places',{}),'placement','位置指向未登记场所')
    else:require(value['holder'] in state['entities'] and value['holder'] not in state.get('places',{}),'placement','holder 应是人物、容器或载具实体，场所请用 place')


def place_entity(state,event):
    eid=event.get('entity')
    require(eid in state['entities'] and eid not in state.get('places',{}),'placement','只有可移动实体能放置或携带，房间不能搬到另一个房间')
    after=event.get('after');position_value(state,after)
    current=state.get('placements',{}).get(eid)
    require('before' in event and event['before']==(current['position'] if current else None),'precondition','放置/携带的前态不一致')
    old_place=at(state,eid)
    if not current:
        legacy={k:state['facts'][fact_key(eid,k)] for k in ('location','location_id','holder') if fact_key(eid,k) in state['facts']}
        for key,value in legacy.items():
            if value is None:continue
            if key=='holder':allowed={after.get('holder')}
            else:
                target=after.get('place') or at(state,after.get('holder'))
                chain=ancestors(state,target) if target else []
                allowed={name for place in chain for name in [place,state['entities'][place]['name']]+state['entities'][place].get('aliases',[])}
            require(isinstance(value,str) and value in allowed,'legacy_position_conflict','旧位置/持有者与新站位不同，先核对并修订，不能静默用地图覆盖')
        for key in legacy:
            require(fact_key(eid,key) not in state.get('anchors',{}),'protected_anchor','旧位置被锁定为稳定事实，不能自动转成动态站位')
            state['facts'].pop(fact_key(eid,key))
    new_place=after.get('place') or at(state,after.get('holder'))
    if current and current['position']!=after:
        require(old_place is not None and new_place is not None,'position_unknown','交接前须确定原位置与接收者位置；不能借未定位持有者清空已知位置')
        require(old_place==new_place,'placement_teleport','跨地点变化须用 move 声明路径，不能借交接物品瞬移')
    bounds=[v for v in (arrival_bound(state,eid),arrival_bound(state,after.get('holder'))) if v is not None]
    records=state.setdefault('placements',{})
    records[eid]={'position':copy.deepcopy(after),'source':source(event),'last_arrival':max(bounds) if bounds else None}
    at(state,eid)  # Detect self/nested cycles even if a carrier has no known location.


def conditions_met(state,link):
    return all(key in state['facts'] and state['facts'][key]==value for key,value in link.get('requires',{}).items())


def matching_links(state,a,b,kind):
    return [(key,l) for key,l in state.get('links',{}).items() if l['kind']==kind and
            ((l['a']==a and l['b']==b) or (not l['directed'] and l['a']==b and l['b']==a))]


def move(state,event):
    eid=event.get('entity');record=state.get('placements',{}).get(eid)
    require(record and 'place' in record['position'],'move','先确认主体起点；被携带物体随持有者移动，不能自行瞬移')
    route=event.get('route')
    require(isinstance(route,list) and len(route)>=2 and all(p in state.get('places',{}) for p in route),'move','route 至少包含两个已登记地点')
    require(event.get('from')==record['position']['place']==route[0] and event.get('to')==route[-1],'precondition','移动起终点与当前位置及路径不一致')
    link_ids=event.get('links')
    require(isinstance(link_ids,list) and len(link_ids)==len(route)-1,'move',
            f'每段路径必须点名实际使用的连接 ID(本路径 {len(route)-1} 段,需 {len(route)-1} 个);可用连接见工作包 continuity 或 recall')
    total=0;known=True
    for a,b,ident in zip(route,route[1:],link_ids):
        links=dict(matching_links(state,a,b,'path'))
        known_ids=sorted(links) or '无——先补查或有正文依据地补充地图,不把未知判成不存在'
        require(ident in links,'route_unknown',f'连接 {ident!r} 不在 {a}—{b} 的已知路径中;已登记:{known_ids}')
        link=links[ident]
        require(link['open'] and conditions_met(state,link),'route_closed','所选路径当前不可通行；先演出开门、解锁或另选路线')
        if link['min_ticks'] is None:known=False
        else:total+=link['min_ticks']
    bounds=[arrival_bound(state,eid)]
    for carried in state['placements']:
        cursor=carried;seen=set()
        while cursor in state['placements'] and cursor not in seen:
            seen.add(cursor)
            if cursor==eid:
                bounds.append(arrival_bound(state,carried));break
            cursor=state['placements'][cursor]['position'].get('holder')
    bounds=[v for v in bounds if v is not None]
    previous_arrival=max(bounds) if bounds else None
    depart,arrive=event.get('depart'),event.get('arrive')
    require((depart is None)==(arrive is None),'move','depart/arrive 要么都声明，要么都未知')
    if depart is not None:
        require(type(depart) is int and type(arrive) is int and state['clock']<=depart<=arrive,'move_time','移动时间无效')
        require(previous_arrival is None or depart>=previous_arrival,'move_overlap','同一人物或物体的连续移动时间重叠')
        require(arrive-depart>=total,'travel_time','实际耗时小于路径中已知段的最短耗时')
    state['placements'][eid]={'position':{'place':route[-1]},'source':source(event),'last_arrival':arrive if arrive is not None else previous_arrival,
                              'timing_verified':known and depart is not None}


def apply_spatial(state,event):
    op=event['op']
    if op=='anchor':
        if event.get('key') in PLACE_BINDINGS:require(event.get('value') in state.get('places',{}),'place','功能用途必须绑定已登记物理地点')
        anchor(state,event)
        validate_places(state)
    elif op=='anchor_assert':
        key=fact_key(event['entity'],event['key'])
        require(key in state.get('anchors',{}),'anchor_unknown','本断言缺少已登记的稳定事实，先检索或核实首次设定')
        require('value' in event and state['facts'][key]==event['value'],'anchor_conflict',f'{key} 与既有设定冲突：现值 {state["facts"][key]!r}，正文声称 {event.get("value")!r}')
    elif op=='anchor_change':
        key=fact_key(event['entity'],event['key']);meta=state.get('anchors',{}).get(key)
        require(meta and meta['policy']=='eventful','fixed_anchor','不可无声修改固定事实；原文写错须走历史修订')
        require('before' in event and 'after' in event and event['before']==state['facts'][key],'precondition','稳定事实变更前值不匹配')
        require(nonempty(event.get('reason')) and isinstance(event.get('change_evidence'),dict),'change_cause','须说明搬迁/更名/任免等原因，并引用正文发生的变更')
        if event['key'] in PLACE_BINDINGS:require(event['after'] in state.get('places',{}),'place','办公室迁入地点尚未登记')
        state['facts'][key]=copy.deepcopy(event['after']);meta['last_change']=source(event)
        meta['change_evidence']=copy.deepcopy(event['change_evidence']);meta['reason']=event['reason']
    elif op=='place':add_place(state,event)
    elif op=='place_change':
        eid=event.get('entity');before=event.get('before');after=event.get('after')
        require(eid in state.get('places',{}) and isinstance(before,dict) and isinstance(after,dict) and before and set(before)==set(after),'place_change','场所改造须明确相同属性集合的前后值')
        require('parent' not in before,'place_change','物理房间不直接更换所属建筑；新建/另一个房间应登记新地点')
        require(nonempty(event.get('reason')) and isinstance(event.get('change_evidence'),dict),'change_cause','改造或重编房号须有原因与正文变更证据，单纯换一种描述不算变更')
        for key,value in before.items():
            name=fact_key(eid,key)
            require(name in state.get('anchors',{}) and state['facts'][name]==value,'precondition','改造前的场所属性不匹配')
            if key in ('floor','room_number'):require(nonempty(after[key]),'place_change','楼层和房号须为字符串')
        for key,value in after.items():
            name=fact_key(eid,key);state['facts'][name]=copy.deepcopy(value)
            state['anchors'][name]['last_change']=source(event)
            state['anchors'][name]['reason']=event['reason']
        validate_places(state)
    elif op=='link':add_link(state,event)
    elif op=='link_change':
        link=state.get('links',{}).get(event.get('id'))
        require(link and event.get('before')==link['open'] and type(event.get('after')) is bool,'precondition','连接开闭变化前态不一致')
        require(nonempty(event.get('reason')),'change_cause','开门、封路等变化须有原因和正文证据')
        link['open']=event['after'];link['last_change']=source(event)
    elif op=='placement':place_entity(state,event)
    elif op=='move':move(state,event)
    elif op=='perceive':
        observer=event.get('observer');target=event.get('target');a=at(state,observer);b=at(state,target)
        require(a is not None and b is not None,'position_unknown','感知校验须先确定双方位置；未登记不能当作可见/可听')
        kind=event.get('channel')
        require(kind in ('sight','sound'),'perceive','channel 为 sight/sound')
        require(event.get('from')==a and event.get('to')==b,'position_conflict','观察者/声源位置与当前站位不一致')
        if a!=b:
            links=dict(matching_links(state,a,b,kind))
            require(event.get('link') in links,'perception_unknown','不同地点须有该方向的直接视线/声音连接；不允许借通路或多跳推断')
            require(links[event['link']]['open'] and conditions_met(state,links[event['link']]),'perception_blocked','已登记遮挡使本次看见/听见不成立')
        # Perception is an assertion only, not proof of understanding or new knowledge.


def validate_places(state, bindings=True):
    seen={}
    for eid in state.get('places',{}):
        addr=address(state,eid);parent=state['facts'][fact_key(eid,'parent')]
        own=state['facts'].get(fact_key(eid,'floor'))
        inherited=address(state,parent).get('floor') if parent else None
        require(own is None or inherited is None or own==inherited,'floor_conflict','子场所楼层与父区域冲突')
        if addr.get('room_number'):
            token=(parent,addr.get('floor'),addr['room_number'])
            require(token not in seen,'room_duplicate','改造后出现同父区域、同楼层同房号的冲突')
            seen[token]=eid
    for key in state.get('anchors',{}) if bindings else ():
        if key.split(':',1)[1] in PLACE_BINDINGS:
            require(state['facts'][key] in state.get('places',{}),'place','办公室用途指向未登记物理房间')


def seed_spatial(state,contract):
    if not any(contract.get(k) for k in ('anchors','places','links','placements')):return
    for item in contract.get('anchors',[]):anchor(state,item)
    for item in contract.get('places',[]):add_place(state,item)
    for item in contract.get('links',[]):add_link(state,item)
    for item in contract.get('placements',[]):place_entity(state,dict(item,before=None))
    validate_places(state)


def event_refs(event):
    refs=[]
    for key in ('entity','parent','a','b','observer','target'):
        if isinstance(event.get(key),str):refs.append(event[key])
    for key in ('before','after'):
        value=event.get(key)
        if isinstance(value,dict):refs.extend(v for k,v in value.items() if k in ('place','holder') and isinstance(v,str))
    refs.extend(event.get('route',[]))
    return refs


def resolve_place(state,name):
    candidates=[name] if name in state['entities'] else [eid for eid,row in state['entities'].items() if name in [row['name']]+row.get('aliases',[])]
    matches=set()
    for eid in candidates:
        if eid in state.get('places',{}):matches.add(eid)
        for key in PLACE_BINDINGS:
            place=state['facts'].get(fact_key(eid,key))
            if place in state.get('places',{}):matches.add(place)
    require(len(matches)==1,'place','场所或功能称呼不能唯一定位；请使用完整名称或物理地点 ID')
    return next(iter(matches))


def display_name(state,eid):
    addr=address(state,eid)
    if 'room_number' not in addr:return state['entities'][eid]['name']
    parent=state['facts'][fact_key(eid,'parent')]
    prefix=state['entities'][parent]['name']+' / ' if parent else ''
    floor=str(addr['floor'])+'楼' if 'floor' in addr else ''
    return prefix+floor+str(addr['room_number'])+'室'


def spatial_view(state,places,entities=(),neighbors=True,children_depth=0):
    selected=set(places);required=set(places)
    for entity in entities:
        if entity in state.get('places',{}):selected.add(entity);required.add(entity)
        location=at(state,entity)
        if location:selected.add(location);required.add(location)
        for key in PLACE_BINDINGS:
            office=state['facts'].get(fact_key(entity,key))
            if office in state.get('places',{}):selected.add(office);required.add(office)
    for _ in range(children_depth):
        frontier=set(selected)
        selected.update(eid for eid in state.get('places',{}) if state['facts'].get(fact_key(eid,'parent')) in frontier)
    required.update(selected)
    if neighbors:
        for link in state.get('links',{}).values():
            if link['a'] in required or link['b'] in required:selected.update((link['a'],link['b']))
    for place in list(selected):selected.update(ancestors(state,place))
    bindings={key:state['facts'][key] for key in state.get('anchors',{}) if key.split(':',1)[1] in PLACE_BINDINGS and state['facts'][key] in selected}
    related=set(entities)|selected|{key.split(':')[0] for key in bindings}
    local_links={k:v for k,v in state.get('links',{}).items() if v['a'] in selected and v['b'] in selected}
    conditions={key:state['facts'].get(key) for link in local_links.values() for key in link.get('requires',{})}
    anchors={k:{**v,'value':state['facts'][k]} for k,v in state.get('anchors',{}).items() if k.split(':')[0] in related}
    nodes=[{'id':eid,'name':state['entities'][eid]['name'],'display_name':display_name(state,eid),'address':address(state,eid),'source':state['places'][eid]['source']} for eid in sorted(selected)]
    return {'places':nodes,'anchors':anchors,'function_bindings':bindings,
            'links':{k:{**v,'traversable_or_perceptible':v['open'] and conditions_met(state,v)} for k,v in local_links.items()},
            'conditions':conditions,
            'placements':{k:{**v,'effective_place':at(state,k)} for k,v in state.get('placements',{}).items() if k in related or at(state,k) in required},
            'map_scope':'selected places, ancestors and direct neighbors; partial, not to scale',
            'absence_is_not_proof':True,'unverified':['unregistered routes','unregistered barriers','unmeasured travel time','lighting/weather and human abilities require semantic review']}


def mermaid(view):
    # Fixed internal identifiers, escaped labels: authored names never become Mermaid syntax.
    def escape(s):return str(s).replace('&','&amp;').replace('"','&quot;').replace('<','&lt;').replace('>','&gt;').replace('\n',' ')
    ids={row['id']:'p'+str(i) for i,row in enumerate(view['places'])}
    lines=['flowchart TD']
    for row in view['places']:
        address_=row['address']
        lines.append(f'  {ids[row["id"]]}["{escape(row.get("display_name",row["name"]))}"]')
        parent=address_['chain'][-2] if len(address_['chain'])>1 else None
        if parent in ids:lines.append(f'  {ids[parent]} -. "包含" .-> {ids[row["id"]]}')
    for edge in view['links'].values():
        label=edge['kind']+': '+edge['label']+(' (blocked)' if not edge['traversable_or_perceptible'] else '')
        if edge['min_ticks'] is not None:label+=f' ≥{edge["min_ticks"]} ticks'
        arrow='-->' if edge['directed'] else '<-->'
        lines.append(f'  {ids[edge["a"]]} {arrow}|"{escape(label)}"| {ids[edge["b"]]}')
    return '\n'.join(lines)


def address_signals(state,prose):
    """Conservative lexical hints; dialogue/negation/old dates require editorial judgment."""
    import re
    from .store import digest
    def floor_number(value):
        value=re.sub(r'[第楼层\s]','',str(value))
        if value.isdigit():return str(int(value))
        digits={c:i for i,c in enumerate('零一二三四五六七八九')};digits['两']=2
        if value in digits:return str(digits[value])
        if '十' in value and len(value)<=3:
            a,b=value.split('十',1)
            if (not a or a in digits) and (not b or b in digits):return str((digits[a] if a else 1)*10+(digits[b] if b else 0))
        return value
    targets=[]
    for eid,item in state['entities'].items():
        place=eid if eid in state.get('places',{}) else next((state['facts'].get(fact_key(eid,key)) for key in PLACE_BINDINGS if state['facts'].get(fact_key(eid,key)) in state.get('places',{})),None)
        if place in state.get('places',{}):targets.append((eid,place,[item['name']]+item.get('aliases',[])))
    signals=[]
    for sentence in re.finditer(r'[^。！？\n]+[。！？]?',prose):
        text=sentence.group()
        for eid,place,names in targets:
            if not any(name in text for name in names):continue
            expected=address(state,place)
            floors=[m.group(1) for m in re.finditer(r'(?:第)?([0-9一二三四五六七八九十两]+)\s*[楼层]',text)]
            rooms=[m.group(1) for m in re.finditer(r'(?<![0-9])([0-9]{2,6})\s*(?:号房|房间|室|号)',text)]
            rooms.extend(m.group(1) for m in re.finditer(r'[楼层]\s*(?:的)?\s*([0-9]{2,6})(?![0-9])',text))
            for key,values in (('floor',floors),('room_number',rooms)):
                if key not in expected:continue
                for value in sorted(set(values)):
                    same=floor_number(value)==floor_number(expected[key]) if key=='floor' else value==str(expected[key])
                    if same:continue
                    signal={'entity':eid,'place':place,'key':key,'expected':expected[key],'mentioned':value,'start':sentence.start(),'quote':text,
                            'kind':'possible_address_conflict','warning':'仅是线索；须区分其他地点、旧址、人物误信和真实叙述'}
                    signal['id']=digest(signal)[:16];signals.append(signal)
    return signals


def check_address_responses(signals,data,prose,chapter):
    from .ledger import evidence
    if not signals:return
    rows=data.get('address_responses')
    require(isinstance(rows,list) and all(isinstance(r,dict) for r in rows),'address_unreviewed','存在楼层/房号疑点，须逐项解释或返工，不能无视正文后提交空事件')
    require(len(rows)==len(signals) and {r.get('id') for r in rows}=={s['id'] for s in signals},'address_unreviewed','地址疑点回应缺失或重复')
    for row in rows:
        require(row.get('status') in ('not_conflict','error','uncertain') and nonempty(row.get('reason')),'address_response','疑点需结论及依据')
        evidence(prose,row.get('evidence'),chapter)
        require(data['verdict']=='fix' or row['status']=='not_conflict','address_conflict','尚有地址错误或待核疑点，不能判定事实审查通过')
