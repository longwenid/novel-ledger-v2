"""Task-scoped, provenance-bearing work packs and historical recall."""
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
    from .projections import recall_events
    return recall_events(db,branch,query,entity,topic,start,end,offset,limit,newest)


DISCIPLINE = {
    'review': 'checks 结果只能 pass/fix;审稿以充分核验为先:quote 精确定位必要语境,reason 说明事实/因果与阅读影响,不设字数限制;避免重复抄录与自辩,必要证据不得省略;pass 时 findings 必须为 [],first_read 如实记录首读体验,章摘要覆盖选择、后果和未决压力;每项证据 {chapter,start,quote} 以任务 branch 字段所指分支的库存正文为坐标系实测且唯一命中(分段视图中的【第N章】章头只是分隔,不属于正文,quote 不要包含它;export/html 默认输出 active 分支,与工作分支可能不同,须显式 --branch),不适用须 not_applicable+具体理由',
    'events': 'before 照抄账本当前值(首次登记为 null);clock.before 恒为包内 state.clock 当前值(首章为 0),不是 null;move 必须引用 continuity 中已登记的 links ID,不确定先 recall;resource 只登记已入账资产,from_before/to_before 照账本,未登记道具不补流水',
    'hooks': '钩子只为正文已写明的重大承诺登记(约定定调或读者可指认的明确伏笔),不为章末效果硬造,无钩可留就不留;延期上限 2 次且每次须 reason;closed 不可重开;due 为最迟章号,到期章必须兑现/关闭/再延',
    'clock': '证据须引用含时间词(日/夜/晨/时…)的正文语句;计划 scenes 的 D+N 是日程锚',
    'patches': 'start 为完整基稿的字符位置,old 必须与该处原文精确一致',
    'plan': '分层计划:每章需 ending_shape(收束形态"类别:说明",类别: 悬念/闭环/交接/爆发/静收/反转/余波);卷纲有 sequence 时 sequence_refs 为 1 起的节拍序号数组。近期章纲用 execution 说明场景展开依据及已约定篇幅，用 rhythm 说明本章作用、压力变化原因、呼吸落点与承接；不固定事件数、松紧曲线或章末类别轮换。未设通常篇幅时按场景估计本章 target_han 并解释依据，不由全书总量机械均分。',
}


def _pin_evidence(ref):
    """Keep exact source ranges and hashes, with quotes available on demand."""
    if isinstance(ref, dict):
        out={k:ref[k] for k in ('chapter','start') if k in ref}
        if isinstance(ref.get('quote'),str) and type(ref.get('start')) is int:
            out.update(end=ref['start']+len(ref['quote']),source_hash=digest(ref['quote']))
        return out
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


def pack(db, branch, chapter, stage, payload):
    contract = get(db, 'contract')
    from . import contextflow
    bounded = contextflow.enabled(db)
    planrow = db.execute('SELECT data FROM plans WHERE branch=? AND number=?', (branch, chapter)).fetchone()
    plan = json.loads(planrow[0]) if planrow else {}
    base = {'schema': 'novel-ledger-v2.pack/1', 'stage': stage, 'chapter': chapter, 'branch': branch,
            'contract': contract, 'plan': plan, 'payload': payload,
            'discipline': dict(DISCIPLINE),
            'retrieval': {'command': 'recall', 'branch': branch, 'absence_is_not_proof': True,
                          'fact_boundary': '事实只能来自有效正文与作者约定；recent_summary/memory 为派生视图，与正文冲突时以 recall 重放的账本为准'},
            'selected': [], 'omitted': [],
            'context_policy': '按任务、实体、话题、依赖章组织完整材料；字符仅用于观察，不作拒绝、截断或压缩门槛。'}
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
        # Observe reviewed prose volume without turning it into a reading quota.
        chars = db.execute('SELECT COALESCE(SUM(LENGTH(prose)),0) FROM chapters WHERE branch=? AND number BETWEEN ? AND ?',
                           (branch, payload['first'], payload['through'])).fetchone()[0]
        base['review_prose_chars'] = int(chars)
    elif stage in ('facts','reader') and isinstance(payload.get('prose'),str):
        # reader 首读后再核对约定；长度只用于观测，必要补读不受额度限制。
        base['review_prose_chars'] = len(payload['prose'])
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
        if stage in ('character_plan','character_index'):refs.update(payload['entities'])
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
        for scene in plan.get('scenes', []):
            for intent in scene.get('dialogue_intents', []):
                topics.update(intent.get('knowledge_topics', []))
        relation_view = {k:v for k,v in state.get('relations',{}).items()
                         if k.split('>',1)[0] in refs or k.split('>',1)[1].rsplit(':',1)[0] in refs} if state.get('relations') else {}
        base['state'] = {
            'clock': state['clock'],
            **({'clock_threads': state['clock_threads']} if state.get('clock_threads') else {}),
            'entities': {k:v for k,v in state['entities'].items() if k in refs},
            'facts': {k:v for k,v in state['facts'].items() if k.split(':')[0] in refs},
            'knowledge': {k:v for k,v in state['knowledge'].items() if k.split(':')[0] in refs and (not topics or k.split(':',1)[1] in topics)},
            'resources': {k:v for k,v in state['resources'].items() if json.loads(k)[0] in refs},
            'hooks': {k:v for k,v in state['hooks'].items() if k in plan.get('hooks', []) or (v['status']=='open' and v['due'] <= chapter+5)},
            **({'relations': relation_view} if relation_view else {})}
        if topics:
            omitted=sum(k.split(':',1)[0] in refs and k.split(':',1)[1] not in topics for k in state['knowledge'])
            if omitted:
                base['omitted'].append({'kind':'knowledge_topics','count':omitted,
                    'selected_topics':sorted(topics),'retrieve':'recall --state --entity ID --topic TOPIC',
                    'boundary':'其他认知仍在账本；写作或审稿涉及新话题时先核对，不能据本包缺项判定人物未知。'})
        if characters.enabled(db) and stage in ('character_plan','character_index','draft','repair','redesign','facts'):
            ids=sorted(e for e in refs if characters.person(state,e))
            base['character_context']=characters.context(db,branch,ids,state,plan=plan,stage=stage)
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
        if get(db,'style_only_branch')==branch and stage in ('draft','repair','redesign'):
            # Style-only rewrites keep the original prose as their content baseline;
            # the full world/volume designs are dead weight in every pack.
            world=design.current(db,branch,'world')
            base['design_context']={'status':'style_revision',
                'world_overview':(world or {}).get('data',{}).get('overview',''),
                'note':'文风修订：内容以 original_prose 为准，不得改变事实、事件与因果'}
        else:
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
        # Keep new scene-development instructions once in the task input;
        # the canonical complete outline remains in the database.
        moved=[key for key in ('execution','rhythm') if key in plan]
        if moved:
            base['plan']={key:value for key,value in plan.items() if key not in moved}
            base['plan_section_refs']={key:'writer_brief.'+key for key in moved}
    anchor = get(db,'style_anchor')
    if anchor and stage in ('draft','repair','redesign','reader'):
        base['style_anchor']={'voice':anchor.get('voice',''),'traits':anchor.get('traits',[]),
                              'sample':anchor.get('sample',''),'sample_close':anchor.get('sample_close',''),
                              'usage':'声线参照：按 traits 保持叙事口吻、句式节奏与用词域的连续性；voice_match 审稿依此核对，不逐句模仿样例内容。'}
    candidates = []
    if stage in ('plan','draft','repair','redesign'):
        # Adjacent outline intentions help vary rhythm without loading a book's
        # outline or prose. Committed prose remains the factual source.
        for row in db.execute('SELECT p.number,p.data,c.hash AS prose_hash FROM plans p '
                              'LEFT JOIN chapters c ON c.branch=p.branch AND c.number=p.number '
                              'WHERE p.branch=? AND p.number<? ORDER BY p.number DESC LIMIT 3',(branch,chapter)):
            previous=json.loads(row['data'])
            candidates.append({'kind':'recent_rhythm','chapter':row['number'],
                'plan_hash':digest(previous),'prose_hash':row['prose_hash'],
                'written':row['prose_hash'] is not None,
                'outline':{k:previous[k] for k in ('goal','conflict','change','ending_shape','rhythm') if k in previous},
                'boundary':'这是邻章设计意图；已写章的实际效果用 recall --chapter N 核对，不能据章末类别推断节奏质量。'})
    if stage!='reader' and not stage.endswith('_review'):
        for row in db.execute('SELECT number,data FROM chapters WHERE branch=? AND number<? ORDER BY number DESC LIMIT 3', (branch,chapter)):
            data = json.loads(row['data'])
            candidates.append({'kind':'recent_summary','chapter':row['number'],'text':data['summary'], 'source_chapters':[row['number']], 'derived_view':True})
        for row in db.execute('SELECT * FROM memories WHERE branch=? ORDER BY through DESC LIMIT 2', (branch,)):
            candidates.append({'kind':'memory','scope':row['scope'],'through':row['through'],'content':json.loads(row['data']), 'derived_view':True})
        # Source-version identity deduplicates events shared by several actors.
        # Latest records are a navigational view; older canon stays queryable.
        seen=set();omitted_by_entity={}
        for entity in plan.get('entities', []):
            page=recall(db,branch,entity=entity,limit=20,newest=True)
            for record in page['records']:
                key=(record['chapter'],record['index'],record['event_hash'])
                if key not in seen:
                    candidates.append({'kind':'history',**record});seen.add(key)
            missing=page['total']-len(page['records'])
            if missing:omitted_by_entity[entity]=missing
        if omitted_by_entity:
            base['omitted'].append({'kind':'history','by_entity':omitted_by_entity,
                                   'retrieve':'recall with entity/topic/chapter; omissions are not evidence of absence'})
    base['selected'].extend(candidates)
    if bounded:
        identity=digest({'branch':branch,'chapter':chapter,'stage':stage,'payload':payload,'plan':plan})
        base['context_identity']=identity
        extra=get(db,'context_extra:'+identity)
        if extra:
            base['selected'].append({'kind':'requested_material','rounds':extra['rounds'],'materials':extra['materials']})
            base['context_questions']=[{'request':m['request'],'reason':m['reason']} for m in extra['materials']]
        return contextflow.fit(db,base)
    return contextflow.stamp(db,base)
