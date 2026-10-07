"""Bounded, provenance-bearing work packs and paginated historical recall."""
import json
from .store import dump, get, head, require, Error, digest
from .ledger import replay
from .spatial import event_refs, spatial_view, address_signals


def recall(db, branch, query='', entity=None, topic=None, start=1, end=None, offset=0, limit=20, newest=False):
    require(0 < limit <= 100 and offset >= 0, 'pagination', 'limit 必须为 1..100，offset 非负')
    state = replay(db, branch)
    if entity:
        matches = [eid for eid, row in state['entities'].items() if entity in [eid, row['name']] + row.get('aliases', [])]
        require(len(matches) == 1, 'entity', '人物或别名无法唯一解析')
        entity = matches[0]
    hits = []
    order='chapter DESC,idx DESC' if newest else 'chapter,idx'
    for row in db.execute('SELECT * FROM events WHERE branch=? AND chapter>=? AND chapter<=? ORDER BY '+order, (branch, start, end or head(db, branch))):
        event = json.loads(row['data'])
        refs = [event.get('entity'), event.get('from'), event.get('to')] + event.get('refs', []) + event_refs(event)
        if isinstance(event.get('entity'), dict):
            refs.append(event['entity']['id'])
        if entity and entity not in refs:
            continue
        if topic and topic not in (event.get('topic'), event.get('id'), event.get('asset'), event.get('key')) and topic not in event.get('topics', []):
            continue
        if query and query.casefold() not in dump(event).casefold():
            continue
        hits.append({'chapter': row['chapter'], 'index': row['idx'], 'event': event, 'event_hash': row['hash']})
    return {'records': hits[offset:offset+limit], 'total': len(hits), 'offset': offset, 'next_offset': offset+limit if offset+limit < len(hits) else None, 'absence_is_not_proof': True}


DISCIPLINE = {
    'review': 'checks 结果只能 pass/fix;每项证据 {chapter,start,quote} 以任务 branch 字段所指分支的库存正文为坐标系实测且唯一命中(分段视图中的【第N章】章头只是分隔,不属于正文,quote 不要包含它;export/html 默认输出 active 分支,与工作分支可能不同,须显式 --branch),不适用须 not_applicable+具体理由',
    'events': 'before 照抄账本当前值(首次登记为 null);move 必须引用 continuity 中已登记的 links ID,不确定先 recall;resource 只登记已入账资产,from_before/to_before 照账本,未登记道具不补流水',
    'hooks': '钩子只为正文已写明的重大承诺登记(约定定调或读者可指认的明确伏笔),不为章末效果硬造,无钩可留就不留;延期上限 2 次且每次须 reason;closed 不可重开;due 为最迟章号,到期章必须兑现/关闭/再延',
    'clock': '证据须引用含时间词(日/夜/晨/时…)的正文语句;计划 scenes 的 D+N 是日程锚',
    'patches': 'start 为完整基稿的字符位置,old 必须与该处原文精确一致',
    'plan': '分层计划:每章需 ending_shape(收束形态"类别:说明",类别: 悬念/闭环/交接/爆发/静收/反转/余波)且类别不与近三章重复;卷纲有 sequence 时 sequence_refs 为 1 起的节拍序号数组,声明本章推进哪些节拍',
}


def _pin_evidence(ref):
    """Keep a locatable pointer {chapter,start}; drop the bulky quote text."""
    if isinstance(ref, dict):
        return {k: ref[k] for k in ('chapter', 'start') if k in ref}
    if isinstance(ref, list):
        return [_pin_evidence(e) for e in ref]
    return ref


def slim_review(review):
    """Synthesis reads verdicts, check outcomes and findings; per-item evidence
    quotes dominate the bytes and are re-derivable from stored prose via recall."""
    out = {k: review[k] for k in ('scope', 'first', 'through', 'verdict') if k in review}
    for key in ('checks', 'findings'):
        rows = []
        for r in review.get(key, []):
            row = dict(r)
            if 'evidence' in row:
                row['evidence'] = _pin_evidence(row['evidence'])
            rows.append(row)
        out[key] = rows
    return out


def reading_allowance(prose_chars):
    """核验类任务的补读配额按其负责核验的正文体量声明,不随工作包体积浮动:
    1.5 倍覆盖分页与序列化开销,4096 覆盖约定/状态等定向核对。"""
    return prose_chars * 3 // 2 + 4096


def pack(db, branch, chapter, stage, payload):
    contract = get(db, 'contract')
    from . import contextflow
    bounded = contextflow.enabled(db)
    budget = contextflow.limits(db)['input'] if bounded else get(db, 'context_chars')
    planrow = db.execute('SELECT data FROM plans WHERE branch=? AND number=?', (branch, chapter)).fetchone()
    plan = json.loads(planrow[0]) if planrow else {}
    base = {'schema': 'novel-ledger-v2.pack/1', 'stage': stage, 'chapter': chapter, 'branch': branch,
            'contract': contract, 'plan': plan, 'payload': payload,
            'discipline': dict(DISCIPLINE),
            'retrieval': {'command': 'recall', 'branch': branch, 'absence_is_not_proof': True,
                          'fact_boundary': '事实只能来自有效正文与作者约定；recent_summary/memory 为派生视图，与正文冲突时以 recall 重放的账本为准'},
            'selected': [], 'omitted': [],
            'context_anchors': {'unit': 'unicode_characters_not_tokens', 'package_chars': budget}}
    if stage.endswith('_review'):
        # The anchor-chapter plan names the range end, not the chapters under
        # review, and misleads reviewers; chunk reviewers instead get every
        # reviewed chapter's own plan, whole-range reviewers work from chunk
        # verdicts and recall.
        base.pop('plan')
        if str(payload.get('scope', '')).endswith('_chunk') and 'first' in payload and 'through' in payload:
            base['plans'] = {str(r['number']): json.loads(r['data']) for r in
                             db.execute('SELECT number,data FROM plans WHERE branch=? AND number BETWEEN ? AND ? ORDER BY number',
                                        (branch, payload['first'], payload['through']))}
    if stage.endswith('_review') and 'first' in payload and 'through' in payload:
        # Reviewers must read the prose under review via bounded recall, so the
        # reading allowance is sized to that prose — not to the deliberately
        # slimmed package, whose bulk is not what gets re-read.
        chars = db.execute('SELECT COALESCE(SUM(LENGTH(prose)),0) FROM chapters WHERE branch=? AND number BETWEEN ? AND ?',
                           (branch, payload['first'], payload['through'])).fetchone()[0]
        base['retrieval_allowance'] = reading_allowance(int(chars))
    elif stage in ('facts','reader') and isinstance(payload.get('prose'),str):
        # facts/reader 的包刻意只含本章正文——reader 还移除 contract、按指引
        # recall --contract 补读约定;配额若照包体积算,约定的核对通路就被卡死。
        base['retrieval_allowance'] = reading_allowance(len(payload['prose']))
    if bounded and stage.endswith('_review') and not payload.get('scope','').endswith('_chunk'):
        scope=payload.get('scope')
        if scope and 'first' in payload and 'through' in payload:
            base['synthesis_materials']=[{'chapter':r['through'],'input_hash':r['input_hash'],'review':slim_review(json.loads(r['data']))}
                for r in db.execute('SELECT through,input_hash,data FROM reviews WHERE branch=? AND scope=? AND through BETWEEN ? AND ? ORDER BY through',
                                    (branch,scope+'_chunk',payload['first'],payload['through']))]
    # Reader first pass deliberately has no plans, contract or writer explanation.
    if stage in ('world_plan','volume_plan','plan','batch_review','volume_review','book_review','revision_review'):
        from .budget import overview
        base['volume_budget'] = overview(db, branch)
    if stage == 'reader':
        base.pop('contract')
        base.pop('plan')
        base['discipline']={'review':DISCIPLINE['review']}
        base['instruction'] = '先只读 payload.prose 完成 first_read；随后用 recall --contract 核对创作约定。若检查项含 dialogue_identity，首读完成后可用 character 查询有关人物档案与来源；不能用档案设想替正文辩护。不要读取执笔自评。'
    elif not stage.endswith('_review'):
        state = replay(db, branch)
        refs = set(plan.get('entities', []))
        from . import characters
        if stage=='character_plan':refs.update(payload['entities'])
        if characters.enabled(db):refs.update(characters.participants(plan,state))
        scene_places={scene['place'] for scene in plan.get('scenes',[]) if scene.get('place') in state.get('places',{})}
        for scene in plan.get('scenes',[]):
            refs.update(scene.get('present',[]));refs.update(scene.get('props',[]))
        refs.update(c['entity'] for c in plan.get('character_choices',[]) if isinstance(c,dict) and 'entity' in c)
        source_text=payload.get('prose','') or '\n'.join(e['text'] for e in payload.get('excerpts',[]))
        # Relevant prose-only discoveries must still recall canonical places if the plan omitted them.
        for eid,item in state['entities'].items():
            if any(name in source_text for name in [item['name']]+item.get('aliases',[])):
                refs.add(eid)
                if eid in state.get('places',{}):scene_places.add(eid)
        if state.get('anchors') or state.get('places'):
            base['continuity']=spatial_view(state,scene_places,refs)
        if stage=='facts':base['address_signals']=address_signals(state,payload.get('prose',''))
        topics = set(plan.get('topics', []))
        base['state'] = {
            'clock': state['clock'],
            **({'clock_threads': state['clock_threads']} if state.get('clock_threads') else {}),
            'entities': {k:v for k,v in state['entities'].items() if k in refs},
            'facts': {k:v for k,v in state['facts'].items() if k.split(':')[0] in refs},
            'knowledge': {k:v for k,v in state['knowledge'].items() if k.split(':')[0] in refs and (characters.enabled(db) or not topics or k.split(':',1)[1] in topics)},
            'resources': {k:v for k,v in state['resources'].items() if json.loads(k)[0] in refs},
            'hooks': {k:v for k,v in state['hooks'].items() if k in plan.get('hooks', []) or (v['status']=='open' and v['due'] <= chapter+5)}}
        if characters.enabled(db) and stage in ('character_plan','draft','repair','redesign','facts'):
            ids=sorted(e for e in refs if characters.person(state,e))
            base['character_context']=characters.context(db,branch,ids,state)
        for n in plan.get('dependencies', []):
            row = db.execute('SELECT prose,hash,data FROM chapters WHERE branch=? AND number=?', (branch,n)).fetchone()
            require(row, 'dependency', f'依赖章节不存在：{n}')
            if stage in ('draft','repair','redesign'):
                # Writers need the full source text; reviewers and profile updates
                # work from state + summaries + targeted recall instead.
                base['selected'].append({'kind':'required_chapter', 'chapter':n, 'hash':row['hash'], 'text':row['prose']})
            else:
                data=json.loads(row['data'])
                base['selected'].append({'kind':'dependent_chapter', 'chapter':n, 'hash':row['hash'],
                                         'summary':data.get('summary',''),
                                         'retrieve':'recall --chapter %d 分页读原文;未选入不表示不存在'%n})
    from . import design
    if stage in ('world_plan','volume_plan','plan','draft','repair','redesign') and design.enabled(db) and not (stage=='repair' and payload.get('repair_scope')=='local'):
        volume=payload.get('design_scope') if stage=='volume_plan' else plan.get('volume')
        if stage=='plan':volume=design.volume_for(db,branch,chapter)
        base['design_context']=design.context(db,branch,volume)
        required_sources=set()
        for record in base['design_context'].values():
            if isinstance(record,dict):required_sources.update(int(n) for n in record.get('sources',{}))
        present={v['chapter'] for v in base['selected'] if v['kind']=='required_chapter'}
        for n in sorted(required_sources-present):
            row=db.execute('SELECT prose,hash FROM chapters WHERE branch=? AND number=?',(branch,n)).fetchone()
            # Invalidated designs can cite chapters removed by a candidate revision.
            if row:base['selected'].append({'kind':'required_chapter','chapter':n,'hash':row['hash'],'text':row['prose']})
            else:base['omitted'].append({'kind':'invalidated_design_source','chapter':n,'reason':'not present in current branch; re-evaluate proposal'})
    if stage=='volume_plan' and design.enabled(db):
        # 开新卷时把前卷设计的交接面交给设计者;并明示设计是意图,carries_in
        # 要按账本里实际写成的前卷对齐,不能照抄原计划。
        order=[v['id'] for v in contract['volumes']]
        scope=payload.get('design_scope')
        if scope in order and order.index(scope)>0:
            prior=design.current(db,branch,order[order.index(scope)-1])
            if prior:
                data=prior['data']
                base['prior_volume']={'volume':order[order.index(scope)-1],
                    'carries_out':{'climax':data.get('climax',''),'handoff':data.get('handoff',''),'ending_setup':data.get('ending_setup',[])},
                    'note':'前卷设计是意图,不是事实;前卷实际写成的内容以账本 recall 为准,carries_in 须与实际对齐。'}
    if stage in ('draft','repair','redesign') and plan:
        base['writer_brief']=design.writer_brief(plan)
    anchor = get(db,'style_anchor')
    if anchor and stage in ('draft','repair','redesign','reader'):
        base['style_anchor']={'voice':anchor.get('voice',''),'traits':anchor.get('traits',[]),
                              'sample':anchor.get('sample',''),'sample_close':anchor.get('sample_close',''),
                              'usage':'声线参照：按 traits 保持叙事口吻、句式节奏与用词域的连续性；voice_match 审稿依此核对，不逐句模仿样例内容。'}
    if not bounded:
        require(len(dump(base)) <= budget, 'context_overflow', '必需材料超过工作包预算；提高 context_chars 或缩小本次场景/复核范围，不得静默截断')
    candidates = []
    if stage!='reader' and not stage.endswith('_review'):
        for row in db.execute('SELECT number,data FROM chapters WHERE branch=? AND number<? ORDER BY number DESC LIMIT 3', (branch,chapter)):
            data = json.loads(row['data'])
            candidates.append({'kind':'recent_summary','chapter':row['number'],'text':data['summary'], 'source_chapters':[row['number']], 'derived_view':True})
        for row in db.execute('SELECT * FROM memories WHERE branch=? ORDER BY through DESC LIMIT 2', (branch,)):
            candidates.append({'kind':'memory','scope':row['scope'],'through':row['through'],'content':json.loads(row['data']), 'derived_view':True})
        # Blanket history dumps fill the pack without improving continuity; a
        # newest-first bounded slice plus omitted accounting keeps retrieval targeted.
        history_budget = 6000
        spent = 0
        per_entity = {e: 0 for e in plan.get('entities', [])}
        omitted_history = 0
        for entity in plan.get('entities', []):
            for record in recall(db,branch,entity=entity,limit=20,newest=True)['records']:
                item = {'kind':'history', **record}
                cost = len(dump(item))
                if per_entity[entity] >= 1 and spent + cost > history_budget:
                    omitted_history += 1
                    continue
                candidates.append(item); spent += cost; per_entity[entity] += 1
        if omitted_history:
            base['omitted'].append({'kind':'history','count':omitted_history,'retrieve':'recall with entity/topic/chapter'})
    for candidate in candidates:
        base['selected'].append(candidate)
        if len(dump(base)) + 512 > budget:
            base['selected'].pop()
            # Record a count and retrievable category, not a growing omitted-ID list.
            kind = candidate['kind']
            found = next((o for o in base['omitted'] if o['kind']==kind), None)
            if found:
                found['count'] += 1
            else:
                base['omitted'].append({'kind':kind,'count':1,'retrieve':'recall with entity/topic/chapter'})
    if bounded:
        identity=digest({'branch':branch,'chapter':chapter,'stage':stage,'payload':payload,'plan':plan})
        base['context_identity']=identity
        extra=get(db,'context_extra:'+identity)
        if extra:
            base['selected'].append({'kind':'requested_material','rounds':extra['rounds'],'materials':extra['materials']})
            base['context_questions']=[{'request':m['request'],'reason':m['reason']} for m in extra['materials']]
        try:return contextflow.fit(db,base)
        except Error as exc:
            if exc.code!='context_capacity':raise
            raise Error(exc.code,str(exc),{'input_limit_chars':budget,'material_chars':{k:len(dump(v)) for k,v in base.items()},'retry_without_change':False,'action':'缩小不可拆分的当前任务，或按宿主真实可用窗口调整预算；不得截断作者约束'}) from exc
    base['chars'] = len(dump(base)) + 32
    require(len(dump(base)) <= budget, 'context_overflow', '工作包元数据超过预算')
    return base
