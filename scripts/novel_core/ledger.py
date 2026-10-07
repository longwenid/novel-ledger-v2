"""Evidence-backed state transitions; plans and summaries never mutate facts."""
from __future__ import annotations
import copy
import json
import re
from decimal import Decimal, InvalidOperation, localcontext
from .store import Error, digest, dump, require


def amount(value):
    require(isinstance(value, str), 'amount', '数量必须使用十进制字符串，避免浮点误差')
    try:
        out = Decimal(value)
    except InvalidOperation:
        raise Error('amount', '无效数量')
    require(out.is_finite() and out >= 0, 'amount', '数量必须为非负有限数')
    require(len(out.as_tuple().digits) <= 120 and abs(out.as_tuple().exponent) <= 120, 'amount', '单个数量最多支持 120 位有效数字及 120 位指数')
    return out


def seed(contract):
    state = {'entities': {}, 'facts': {}, 'knowledge': {}, 'resources': {}, 'hooks': {}, 'clock': 0, 'clock_threads': {}}
    for entity in contract.get('entities', []):
        add_entity(state, entity)
    for fact in contract.get('initial_facts', []):
        require(fact.get('entity') in state['entities'] and fact.get('key'), 'initial_fact', '初始状态实体或键缺失')
        state['facts'][fact['entity'] + ':' + fact['key']] = fact['value']
    for item in contract.get('initial_resources', []):
        require(item.get('holder') in state['entities'] and item.get('asset') and item.get('unit'), 'resource', '初始资源缺少归属/资产/单位')
        key = resource_key(item['holder'], item['asset'], item['unit'])
        require(key not in state['resources'], 'resource', '初始资源重复')
        state['resources'][key] = str(amount(item['quantity']))
    from .spatial import seed_spatial
    seed_spatial(state,contract)
    return state


def add_entity(state, entity):
    require(isinstance(entity, dict) and all(isinstance(entity.get(k), str) and entity[k] for k in ('id', 'name', 'kind')), 'entity', '实体需要 id/name/kind')
    eid = entity['id']
    require(':' not in eid and eid not in state['entities'], 'entity', '实体 ID 重复或包含冒号')
    aliases = entity.get('aliases', [])
    require(isinstance(aliases, list) and all(isinstance(a, str) and a for a in aliases), 'alias', 'aliases 必须为字符串数组')
    names = [entity['name']] + aliases
    require(len(set(names)) == len(names), 'alias', '同一实体称呼重复')
    used = {s for e in state['entities'].values() for s in [e['name']] + e.get('aliases', [])}
    require(not used.intersection(names), 'alias_conflict', '称呼已属于其他实体；不能按新名字重复创建同一人物')
    state['entities'][eid] = copy.deepcopy(entity)


def resource_key(holder, asset, unit):
    return dump([holder, asset, unit])


def _unique(prose, quote):
    found = prose.find(quote)
    return found if found != -1 and prose.find(quote, found + 1) == -1 else None


def evidence(prose, item, chapter=None):
    require(isinstance(item, dict) and isinstance(item.get('quote'), str) and item['quote'].strip(), 'evidence', '缺少正文证据')
    quote = item['quote']
    offset = item.get('start')
    if type(offset) is not int or offset < 0 or prose[offset:offset + len(quote)] != quote:
        # Recalibrate drifted offsets (common after patches shift the prose) only
        # when the quote pins down exactly one location; ambiguity stays an error.
        found = _unique(prose, quote)
        if found is None:
            # Chunk-review packs concatenate chapters behind a 【第N章】 display
            # header that is not part of stored prose; a quote lifted from that
            # view only names real text once the header is dropped.
            header = re.match(r'^【[^】]{1,24}】\s*', quote)
            if header:
                stripped = quote[header.end():]
                found = _unique(prose, stripped)
                if found is not None:
                    item['quote'] = quote = stripped
        require(found is not None, 'evidence',
                '引文与正文位置不匹配且无法唯一重定位；请引用正文中唯一出现的原文片段')
        item['start'] = offset = found
    if chapter is not None:
        require(item.get('chapter') == chapter, 'evidence', '证据章号不匹配')
    return True


def apply(state, event, prose, chapter):
    require(isinstance(event, dict), 'event', '事件必须为对象')
    evidence(prose, event.get('evidence'), chapter)
    op = event.get('op')
    from .spatial import SPATIAL_OPS, apply_spatial
    if op=='character_development':
        from .characters import apply_development
        apply_development(state,event,prose,chapter)
    elif op in SPATIAL_OPS:
        if op in ('anchor_change','place_change'):
            evidence(prose,event.get('change_evidence'),chapter)
        apply_spatial(state,event)
    elif op == 'entity':
        add_entity(state, event['entity'])
    elif op == 'alias':
        eid, alias = event['entity'], event['alias']
        require(eid in state['entities'] and isinstance(alias, str) and alias, 'alias', '实体或别名不存在')
        used = {a for e in state['entities'].values() for a in [e['name']] + e.get('aliases', [])}
        require(alias not in used, 'alias_conflict', '别名重复或歧义')
        state['entities'][eid].setdefault('aliases', []).append(alias)
    elif op == 'state':
        eid, key = event['entity'], event['key']
        require(eid in state['entities'] and isinstance(key, str) and key, 'entity', '状态实体或属性不存在')
        full = eid + ':' + key
        require(not (eid in state.get('places',{}) and key in ('floor','room_number','parent','address','facing')), 'protected_place','场所属性须用 anchor/place_change 管理，不能作为普通动态状态改写')
        require(full not in state.get('anchors',{}),'protected_anchor','已锁定属性不能用普通 state 覆盖，请检查是否写错或使用有因果依据的专用变更')
        require(not (eid in state.get('placements',{}) and key in ('location','location_id','holder')), 'protected_placement','已登记站位/持有者请使用 placement/move，不能另建矛盾位置')
        require('before' in event and 'after' in event and state['facts'].get(full) == event['before'], 'precondition', '状态前值不匹配')
        if key == 'alive' and event['before'] is False and event['after'] is True:
            require(bool(event.get('exception_reason')), 'resurrection', '复活须有世界规则依据和正文因果')
        state['facts'][full] = event['after']
    elif op == 'knowledge':
        eid, topic = event['entity'], event['topic']
        require(eid in state['entities'] and isinstance(topic, str) and topic, 'knowledge', '认知实体或话题无效')
        require(event.get('stance') in ('knows', 'believes', 'suspects', 'disproved') and event.get('source') and event.get('claim'), 'knowledge', '认知缺少来源、立场或命题')
        key = eid + ':' + topic
        old = state['knowledge'].get(key)
        require('before' in event and event['before'] == (old['claim'] if old else None), 'precondition', '认知前值不匹配')
        state['knowledge'][key] = {k: event[k] for k in ('claim', 'stance', 'source', 'evidence')}
    elif op == 'resource':
        asset, unit = event['asset'], event['unit']
        src, dst = event.get('from'), event.get('to')
        require(isinstance(asset, str) and asset and isinstance(unit, str) and unit, 'resource', '资产和单位必填')
        require(src != dst and (src is not None or dst is not None), 'resource', '资源来源和去向不能相同')
        require(all(h is None or h in state['entities'] for h in (src, dst)), 'resource', '资源归属实体不存在')
        require(bool(event.get('reason')), 'resource', '资源流动需要原因，外部获得与消耗也必须说明')
        qty = amount(event['quantity'])
        require(qty > 0, 'resource', '资源流水必须大于零')
        for holder, sign, field in ((src, -1, 'from_before'), (dst, 1, 'to_before')):
            if holder is None:
                continue
            key = resource_key(holder, asset, unit)
            if key not in state['resources'] and field in event and amount(event[field]) != 0:
                raise Error('precondition', f'{holder} 名下没有已登记的 {asset}（{unit}）；未入账道具不补记非零流水，先在正文成立取得/清点，或删除该 resource 事件')
            old = amount(state['resources'].get(key, '0'))
            require(field in event and amount(event[field]) == old, 'precondition',
                    f'{holder} 当前 {asset}={old}（{unit}），事件 {field}={event.get(field)!r} 与账本期初不符；以账本为准照抄')
            with localcontext() as ctx:
                ctx.prec = 512
                value = old + sign * qty
            require(value >= 0, 'insufficient_resource', '资源不足，不能重复消费或凭空转移')
            amount(str(value))  # Reject an unrepresentable result before persisting it.
            state['resources'][key] = str(value)
    elif op == 'hook':
        hid = event['id']
        old = state['hooks'].get(hid)
        status = event.get('status')
        require(status in ('open', 'paid', 'closed'), 'hook', '承诺状态无效')
        require(event.get('before') == (old['status'] if old else None), 'precondition', '承诺前态不匹配')
        require(old is not None or status == 'open', 'hook', '未埋设承诺不能直接兑现')
        require(old is None or old['status'] == 'open', 'hook', '已结算承诺不能重复结算或重新打开')
        if not old:
            require(event.get('text') and type(event.get('due')) is int and event['due'] >= chapter, 'hook', '新承诺需要内容和合理的最迟回收章')
        result = copy.deepcopy(old) if old else {'text': event['text'], 'seed': event['evidence'], 'extensions': 0, 'important': event.get('important', True)}
        due = event.get('due', result.get('due'))
        require(type(due) is int and due >= 1, 'hook', '回收窗口必须是正整文章号')
        if old and due > old['due']:
            require(bool(event.get('reason')), 'hook', '延期必须说明因果和新安排')
            result['extensions'] += 1
            require(result['extensions'] <= 2, 'hook_extension',
                    f'承诺 {hid} 已延期 {result["extensions"] - 1}/2 次且本次仍要顺延(due {old["due"]}→{due});上限为 2,须用正文依据将其 paid/closed 并给 reason,或让承诺在 due 前兑现')
        if status == 'closed':
            require(bool(event.get('reason')), 'hook', '取消承诺必须说明正文如何交代')
        result.update(status=status, due=due, last=event['evidence'])
        state['hooks'][hid] = result
    elif op == 'clock':
        thread = event.get('thread')
        if thread:
            # Parallel storylines keep their own calendars; the global clock is
            # the main line and is not moved by thread events.
            require(isinstance(thread, str) and thread.strip(), 'time_conflict', 'thread 为非空字符串')
            threads = state.setdefault('clock_threads', {})
            current = threads.get(thread, state['clock'])
            require(event.get('before') == current and type(event.get('after')) is int and event['after'] >= current,
                    'time_conflict', f'支线时钟 {thread} 前值不匹配或倒退；同步点须显式登记')
            threads[thread] = event['after']
        else:
            require(event.get('before') == state['clock'] and type(event.get('after')) is int and event['after'] >= state['clock'], 'time_conflict', '故事全局时钟不能倒退；倒叙单独标注，不改变当前时钟')
            state['clock'] = event['after']
    else:
        raise Error('event_op', f'未知事件类型：{op}')


_REPLAY_CACHE = {}
_REPLAY_CACHE_LIMIT = 8


def _chain_key(db, branch):
    from .store import get
    last = db.execute('SELECT hash FROM events WHERE branch=? ORDER BY chapter DESC, idx DESC LIMIT 1', (branch,)).fetchone()
    count = db.execute('SELECT COUNT(*) FROM events WHERE branch=?', (branch,)).fetchone()[0]
    # The contract is part of the seed state; include it so distinct projects
    # can never share a cache entry, and appends cannot collide across books.
    return (branch, last[0] if last else 'genesis', count, digest(get(db, 'contract')))


def replay(db, branch, through=None, verify=False):
    """Reconstruct canonical state. Events are hash-chained and append-only,
    so (branch, last hash, event count, contract digest) identifies the state:
    repeated reads (per-entity recall, profiles, html) reuse the verified
    result instead of replaying the whole chain every call. Audit and
    historical views pass verify/through to force full chain verification."""
    if through is not None or verify:
        return _replay(db, branch, through)
    key = _chain_key(db, branch)
    cached = _REPLAY_CACHE.get(key)
    if cached is not None:
        return copy.deepcopy(cached)
    state = _replay(db, branch)
    if len(_REPLAY_CACHE) >= _REPLAY_CACHE_LIMIT:
        _REPLAY_CACHE.clear()
    _REPLAY_CACHE[key] = copy.deepcopy(state)
    return state


def _replay(db, branch, through=None):
    from .store import get
    state = seed(get(db, 'contract'))
    previous = 'genesis'
    sql = 'SELECT e.*,c.prose,c.hash AS prose_hash FROM events e JOIN chapters c ON c.branch=e.branch AND c.number=e.chapter WHERE e.branch=?'
    args = [branch]
    if through is not None:
        sql += ' AND e.chapter<=?'
        args.append(through)
    for row in db.execute(sql + ' ORDER BY e.chapter,e.idx', args):
        require(row['prev_hash'] == previous, 'event_chain', '事件链断裂')
        expected = digest({'prev': previous, 'chapter': row['chapter'], 'idx': row['idx'], 'prose_hash': row['prose_hash'], 'event': json.loads(row['data'])})
        require(expected == row['hash'], 'event_chain', '事件内容或正文版本已改变')
        apply(state, json.loads(row['data']), row['prose'], row['chapter'])
        previous = row['hash']
    return state


def append_events(db, branch, chapter, prose, events, state):
    last = db.execute('SELECT hash FROM events WHERE branch=? ORDER BY chapter DESC,idx DESC LIMIT 1', (branch,)).fetchone()
    previous = last[0] if last else 'genesis'
    for index, item in enumerate(events):
        apply(state, item, prose, chapter)
        hashed = digest({'prev': previous, 'chapter': chapter, 'idx': index, 'prose_hash': digest(prose), 'event': item})
        db.execute('INSERT INTO events VALUES (?,?,?,?,?,?)', (branch, chapter, index, dump(item), previous, hashed))
        previous = hashed
    return state
