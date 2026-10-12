"""Task-scoped, provenance-bearing work packs and historical recall."""
import json
from .store import dump, get, head, require, digest
from .ledger import replay
from .patches import save_source
from .spatial import spatial_view, address_signals
from .wire import EVIDENCE_RULE, evidence_contract
from .schedule import contract as time_contract

# Narrative prose travels as content-addressed files next to the pack; the pack
# carries path+hash and SQLite keeps the authoritative bytes for re-materialization.
PROSE_DIR = 'work/prose'


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
    'review': 'checks 结果只能 pass/fix;审稿以充分核验为先:quote 精确定位必要语境,reason 说明事实/因果与阅读影响,不设字数限制;避免重复抄录与自辩,必要证据不得省略;pass 时 findings 必须为 [];'+EVIDENCE_RULE+'证据使用任务 branch 字段所指分支的库存正文，不适用须 not_applicable+具体理由',
    'events': 'before 照抄账本当前值(首次登记为 null);clock.before 恒为包内 state.clock 当前值(首章为 0),不是 null;move 必须引用 continuity 中已登记的 links ID,不确定先 recall;resource 只登记已入账资产,from_before/to_before 照账本,未登记道具不补流水',
    'hooks': '钩子只为正文已写明的重大承诺登记(约定定调或读者可指认的明确伏笔),不为章末效果硬造,无钩可留就不留;延期上限 2 次且每次须 reason;closed 不可重开;due 为最迟章号,到期章必须兑现/关闭/再延',
    'clock': '时间引句须解释实际经过时间与倒计时；有时间词只证明引句类型，不证明天数。新 time/timeline 按声明 basis 核对，旧冻结 D+N 才相对本章首时钟。time_schedule 冲突须据正文改判 fix，不能照允许范围改 clock 凑数。',
    'patches': 'start 为完整基稿的字符位置,old 必须与该处原文精确一致',
    'plan': '分层计划:每章需 ending_shape(收束形态"类别:说明",类别: 闭环/交接/爆发/静收/反转/余波;章尾不加悬念钩子,收束落在行动、抉择或后果的自然完成上);卷纲有 sequence 时 sequence_refs 为 1 起的节拍序号数组。近期章纲用 execution 说明场景展开依据及已约定篇幅，用 rhythm 说明本章作用、压力变化原因、呼吸落点与承接；不固定事件数、松紧曲线或章末类别轮换。未设通常篇幅时按场景估计本章 target_han 并解释依据，不由全书总量机械均分。',
}

# Per-stage discipline routing: a stage only carries the rules governing what
# it actually submits; everything else is noise diluting the real contract.
STAGE_DISCIPLINE = {
    'draft': (), 'repair': ('patches',), 'redesign': ('plan',),
    'chapter_review': ('events', 'hooks', 'clock', 'review'),
    'plan': ('plan',), 'world_plan': (), 'volume_plan': (),
    'character_plan': (), 'character_index': (),
}

# Plan fields the writer brief restates; the draft-family pack view drops them
# so the same creative fact is never loaded twice. The stored plan keeps all.
WRITER_BRIEF_FIELDS = frozenset({'reader_experience', 'goal', 'conflict', 'change',
                                 'ending_shape', 'character_choices', 'scenes',
                                 'expression_space', 'execution', 'rhythm'})


def stage_discipline(stage):
    if stage in STAGE_DISCIPLINE:
        return STAGE_DISCIPLINE[stage]
    if stage.endswith('_review'):
        return ('review',)
    return ()

# Self-contained per-stage submit contracts so workers skip reference reads.
STAGE_PRIMERS = {
    'context_scan': 'data={coverage,notes:[{text,evidence:[{document,start,quote}]}],omission_reason?:无相关结论的具体理由}。'
                    'payload.pages 存在时完整阅读各页，coverage 为每页 document/document_hash/start/end 对象数组；单页时 coverage 为该对象。'
                     '页文件等于文档[start:end]，units.local_start/local_end/local_semantic_start/local_semantic_end 是页内坐标，evidence.start 是文档绝对坐标。'
                    '片段含 JSON 分隔符，未必是独立 JSON；不要对整页盲目 json.loads，证据用 commands.evidence 定位。'
                    '无法安全提炼交 cannot_compact:true+reason，保留全部原页，不查库、不借旧回执套结论。',
    'context_reduce': 'data={notes:[{text,evidence:[{document,start,quote}]}],accounting:[{input_id,disposition:kept|duplicate|irrelevant,reason,duplicate_of?:输出note下标}]}。'
                      '逐项登记全部 payload.inputs；kept/duplicate 的完整因果证据须整体继承到对应输出。'
                      'cannot_compact:true+reason 保留全部节点；不得新增未读来源。',
    'draft': 'data={prose:完整正文非空字符串可含章标题,summary:本章摘要覆盖选择/后果/未决压力,reads?:["实体:键",...]}。'
             'reads 声明本章正文实际依赖的账本坐标，包括用「那笔本钱」这类指代引用的值——名字和数值扫荡看不到指代，只有你的声明能表达它。'
             '历史修订据此判断后文是否受影响：声明过的键一旦被改，这一章就会重做而不是被携带。漏声明不会报错，只会让后文保守地全部重做。'
             '执笔不替自己签发审稿,不提交 events 或 findings。短章目标见包内 chapter_length,目标不能擅改。',
    'repair': '局部返修交 data={base_hash,patches:[{start,old,new}],repair_note};old 须与基稿该处精确一致且 start 按完整基稿定位。整章重写才交 prose+repair_note,必须实际改文。reuse_original 仅限历史修订 draft，不用于 repair/redesign。新增依赖可附 reads。',
    'redesign': 'data 同 draft 的 prose/summary（可带 reads），另必交 design_change 说明设计改变，须产生实际变化的正文;可附 plan_update 仅调当章 goal/conflict/change/entities/topics/hooks/dependencies/scenes/reader_experience/character_choices/expression_space/execution/rhythm,不可改卷序、终局标记或篇幅目标。',
    'facts': '另必交 events:[{op,evidence:{chapter,quote},...}] 和非空 summary（覆盖选择/后果/未决压力）。'+EVIDENCE_RULE+'事件类型和字段按 references/protocol.md，返修去向按 prior_events_note；前态照抄 state，不重报 auto_carry 续算事件。',
    'reader': '另必交 first_read={desire,obstacle,change,read_on_reason}（四项均非空），保留读取 input 前记录的首读体验；档案不能替正文辩护。',
    'world_plan': 'data 只含引擎允许字段(报错会列出收到键与允许键);不能提交正文事件或覆盖作者约定;event_spine 四层 main/subplot/timeline/tension,触点 id 用 sp: 前缀。',
    'volume_plan': 'data 必含 volume/purpose/carries_in/climax/handoff/sequence/action_constraints/ending_setup/choice_log;choice_log 每条 kind=direction|beat、mode、candidates≥2 且含 wild:true、chosen、reason。',
    'plan': 'data={chapters:[...]} 每批 1~10 章,章号连续;每章必含 number/goal/conflict/change/volume/entities/ending_shape(类别:说明,类别见 guide 纪律),sequence_refs 对齐卷纲节拍,历史依赖只能引用已提交章。',
    'character_plan': 'data={profiles:[{entity,tier,biography:{public_role,desire,experiences:[{text,status:design|established}]},voice:{register,tactics:[非空字符串],relationship_modes:[{audience,approach}],avoid:[字符串]}}]}。tier=major|supporting|walk_on；major 还需 biography.fear/values（非空数组）/boundary/contradiction、voice.rhythm/stress_shift、arc={direction,not_yet_earned:[字符串]}；已确立经历须 evidence={chapter,start,quote}，设计经历不附 evidence；更新须 change_reason，引擎自管字段不要提交。',
    'character_index': '按 instruction 对每条档案建立适用范围与因果依赖索引;relevance 只是提案,穷尽且可解释才写 scoped,不确定写 unknown;sources 原章用 recall --chapter 完整补读。',
}


def review_contract(checks_path='payload.checks'):
    """The shared receipt schema, applied separately to each set of checks."""
    ref=evidence_contract()['example']
    example={'verdict':'pass','checks':[{'id':'CHECK_ID','result':'pass',
              'reason':'具体核验理由','evidence':[ref]}],'findings':[]}
    finding={'impact':'具体阅读影响','goal':'可执行修订目标','severity':'quality','evidence':ref}
    return ('data 示例='+dump(example)+'。CHECK_ID 替换为 '+checks_path+' 中的 ID，逐项提交完整 checks，'
            +EVIDENCE_RULE+'每项 result=pass|fix，reason 非空。'
            '可选项确不适用时 evidence=[] 并给非空 not_applicable，核心项仍须证据。'
            '需返工时 verdict=fix，相关检查 result=fix，findings 至少一项：'+dump(finding)+'；'
            'severity=blocker|quality|note，整体结论须与检查及发现一致。')


def stage_primer(stage):
    if stage == 'chapter_review':
        return ('data={facts:{...},reader:{...}}，以下共同契约分别用于两个子回执，不能只给总 verdict。'
                'facts.checks 对应 payload.facts_checks，reader.checks 对应 payload.reader_checks。'
                '先读 task.json 的 first_read_path 正文，记录 reader.first_read，再读 input.json 核对事实、约定和人物；'
                '不读执笔自评，不把同一审稿者的两类检查称为两名独立盲审。'
                +review_contract('对应的 payload.facts_checks 或 payload.reader_checks')+
                'facts 子回执：'+STAGE_PRIMERS['facts']+
                'reader 子回执：'+STAGE_PRIMERS['reader']+
                '两类都通过且事件验证成功才入库；任一 fix 先返修；审稿者不执笔下一章，候选连写仅由绑定 writer 角色的任务推进。')
    if stage in ('facts','reader') or stage.endswith('_review'):
        primer=review_contract()
        if stage in STAGE_PRIMERS:return primer+STAGE_PRIMERS[stage]
        return primer+('正文按 payload.prose_files 每章一个文件交付，证据坐标以对应章文件为准。'
                      'payload.scope 不以 _chunk 结尾的整体复核，pass 时另必交 '
                      'memory={text:非空提炼记忆,sources:[{chapter,start,quote}]}，来源须在审稿范围内；'
                      '也可交非空字符串 memory，来源由分段回执绑定。整体复核按 coverage_receipts 和 selected 核对，'
                      '没有 prose_files 时按疑点 recall --chapter 补读。')
    if stage in STAGE_PRIMERS:
        return STAGE_PRIMERS[stage]
    return None


def prose_ref(db, text):
    """Content-addressed file reference; SQLite keeps the authoritative bytes."""
    hashed = save_source(db, text)
    return {'prose_path': PROSE_DIR + '/' + hashed[:16] + '.txt', 'prose_hash': hashed}


def attach_prose(db, work):
    """Replace embedded narrative prose with content-addressed file references.

    The pack keeps path+hash; engine.task_files materializes the files from the
    prose store before the worker sees the task. Facts/reader/repair prose keeps
    its exact stored bytes, so {chapter,start,quote} coordinates are unchanged.
    """
    payload = work.get('payload', {})
    for key in ('prose', 'original_prose'):
        text = payload.get(key)
        if isinstance(text, str) and text.strip():
            ref = prose_ref(db, text)
            payload.pop(key)
            payload[key + '_path'] = ref['prose_path']
            payload[key + '_hash'] = ref['prose_hash']
    chapters = payload.pop('prose_chapters', None)
    if chapters:
        files = []
        for number in chapters:
            row = db.execute('SELECT prose FROM chapters WHERE branch=? AND number=?', (work['branch'], number)).fetchone()
            require(row, 'context_source', '审稿正文不存在：%d' % number)
            files.append({'chapter': number, **prose_ref(db, row[0])})
        payload['prose_files'] = files
    for item in work.get('selected', []):
        if item.get('kind') == 'required_chapter' and isinstance(item.get('text'), str):
            item.update(prose_ref(db, item.pop('text')))
    return work


def guide(stage, instruction=None):
    """One ordered instruction section: submit contract, task note, relevant discipline, boundaries."""
    lines = []
    primer = stage_primer(stage)
    if primer:
        lines.append('[提交契约] ' + primer)
    if instruction:
        lines.append('[任务] ' + instruction)
    for key in stage_discipline(stage):
        lines.append('[纪律] ' + DISCIPLINE[key])
    lines.append('[事实边界] 事实只能来自有效正文与作者约定；selected 中的 recent_summary/memory 为派生视图，与正文冲突时以 recall 重放的账本为准。')
    if stage=='plan':lines.append('[时间坐标] '+time_contract()['rule'])
    helpers='信封提供时用 commands.patch 定位补丁、commands.preflight 只读验证完整回执；' if stage in ('plan','draft','repair','redesign','chapter_review') else ''
    lines.append('[纠错] '+helpers+'回执/result 文件用 Python 脚本 UTF-8 无 BOM 整体写入，不在命令行内联 JSON（弯引号与转义易错，PowerShell 写文件会带 BOM）。'
                 '错误定位按 message/details 修正，details.issues 一次列出全部不合格项，逐项修完再重提，不逐条试探。结算以前后值和 settlement 为准，不从自评推算余额；不读 scripts 源码、不自建校验脚本。同一任务修正两轮仍失败：保留现场，返回最后已提交任务ID与错误原文，由主控 advance 接续。')
    lines.append('[观测] 协议字符仅观测，不是配额；缺项可用 recall/material 定向补读，未选入不表示不存在。')
    return '\n'.join(lines)


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
    require(stage not in ('facts','reader'),'unsupported_review','只支持 chapter_review 综合审稿任务')
    contract = get(db, 'contract')
    from . import contextflow
    bounded = contextflow.enabled(db)
    planrow = db.execute('SELECT data FROM plans WHERE branch=? AND number=?', (branch, chapter)).fetchone()
    plan = json.loads(planrow[0]) if planrow else {}
    base = {'schema': 'novel-ledger-v2.pack/1', 'stage': stage, 'chapter': chapter, 'branch': branch,
            'contract': contract, 'plan': plan, 'payload': payload,
            'retrieval': {'command': 'recall', 'branch': branch, 'absence_is_not_proof': True},
            'selected': [], 'omitted': []}
    from .recovery import revision_context
    revision = revision_context(db, branch)
    if revision:base['revision_context'] = revision
    from .reviewrun import session
    review_session = session(db, branch, stage)
    if review_session:base['review_session'] = review_session
    if stage.endswith('_review') and stage != 'chapter_review':
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
    elif stage == 'chapter_review' and isinstance(payload.get('prose'),str):
        # reader 首读后再核对约定；长度只用于观测，必要补读不受额度限制。
        base['review_prose_chars'] = len(payload['prose'])
    if bounded and stage.endswith('_review') and not payload.get('scope','').endswith('_chunk'):
        scope=payload.get('scope')
        if scope and 'first' in payload and 'through' in payload:
            base['synthesis_materials']=[{'chapter':r['through'],'input_hash':r['input_hash'],'review':slim_review(json.loads(r['data']))}
                for r in db.execute('SELECT through,input_hash,data FROM reviews WHERE branch=? AND scope=? AND through BETWEEN ? AND ? ORDER BY through',
                                    (branch,scope+'_chunk',payload['first'],payload['through']))]
    # The combined reviewer reads envelope first_read_path before this pack.
    if stage in ('world_plan','volume_plan','plan','batch_review','volume_review','book_review','revision_review'):
        from .budget import overview
        base['volume_budget'] = overview(db, branch)
    from . import batchflow
    chapter_session=batchflow.session(db,branch,stage)
    if chapter_session:base['chapter_session']=chapter_session
    if chapter_session and chapter_session['role']=='reviewer':
        from .reviewflow import batch_key
        base['batch_first_read_sealed']=bool(get(db,batch_key(chapter_session['batch'])))
    if stage == 'chapter_review' or not stage.endswith('_review'):
        state = batchflow.candidate_state(db,branch,chapter-1) if chapter_session and chapter_session['role']=='writer' else replay(db, branch)
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
        if stage == 'chapter_review':base['address_signals']=address_signals(state,payload.get('prose',''))
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
        if characters.enabled(db) and stage in ('character_plan','character_index','draft','repair','redesign','chapter_review'):
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
                if isinstance(record,dict):
                    required_sources.update((record.get('source_branch',branch),int(n)) for n in record.get('sources',{}))
            present={(v.get('branch',branch),v['chapter']) for v in base['selected'] if v['kind']=='required_chapter'}
            for origin,n in sorted(required_sources-present):
                row=db.execute('SELECT prose,hash FROM chapters WHERE branch=? AND number=?',(origin,n)).fetchone()
                # Invalidated designs can cite chapters removed by a candidate revision.
                if row:base['selected'].append({'kind':'required_chapter','chapter':n,'hash':row['hash'],'text':row['prose'],
                                               **({'branch':origin,'source_note':'设计证据原文；当前事实按任务分支 recall 核验'} if origin!=branch else {})})
                else:base['omitted'].append({'kind':'invalidated_design_source','branch':origin,'chapter':n,'reason':'not present in source branch; re-evaluate proposal'})
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
    if anchor and stage in ('draft','repair','redesign','chapter_review'):
        base['style_anchor']={'voice':anchor.get('voice',''),'traits':anchor.get('traits',[]),
                              'sample':anchor.get('sample',''),'sample_close':anchor.get('sample_close',''),
                              'usage':'声线参照：按 traits 保持叙事口吻、句式节奏与用词域的连续性；voice_match 审稿依此核对，不逐句模仿样例内容。'}
    if chapter_session and chapter_session['role']=='writer':
        base['candidate_ledger']={'status':'unreviewed','canonical_through':head(db,branch),
            'prefix':batchflow.prefix_material(db,branch,chapter),
            'instruction':'state/continuity 已叠加本批前章候选事件；只作承接前态，不是正式事实。逐章提交候选事件与具体自查，不能自审入库。'}
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
    if stage == 'chapter_review' or not stage.endswith('_review'):
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
    # One ordered instruction section; the plan view drops everything the
    # writer brief already carries so workers read each fact exactly once.
    base['guide'] = guide(stage, base.pop('instruction', None))
    if stage in ('draft', 'repair', 'redesign'):
        base['guide'] += '\n[身份] 以职业连载作者的手艺执笔，判断落在人物与场景里。'
    if 'writer_brief' in base:
        base['plan'] = {k: v for k, v in base['plan'].items() if k not in WRITER_BRIEF_FIELDS}
    if chapter_session and chapter_session['role']=='writer':
        base['guide']+='\n'+'\n'.join(DISCIPLINE[k] for k in ('events','hooks','clock'))+'\n[候选账本] 正文整章一次写入 output_path，不用多次小步编辑拼装；篇幅门槛是精确线：shortfall_han 必须为 0（汉字数 ≥ target_han 才 ready），没有九成达标的区间，初写按 target 上浮约百分之五落笔可避免反复补写；measure 仅在确需提前计数时用。data 必含 candidate_events（按正文举证，无变化填 []）、summary、self_check 非空具体自查。返修模板有 reuse_candidate_events=true 时可续算证据句语境未变的冻结事件，语境改变则完整重报 candidate_events，不混用。候选事件不替审稿，不写正式账本；前态用本任务 state 与 candidate_ledger，补读用 commands 的 --task。'
    attach_prose(db, base)
    if stage == 'chapter_review':
        from .reviewflow import PROTOCOL, obligations
        base['review_protocol'] = PROTOCOL
        base['review_obligations'] = obligations(contract, base.get('style_anchor'))
        base['guide'] += ('\n[审稿门禁] 首读由 first-read 持久冻结；正式 reader.first_read_ref 按模板引用本任务首读哈希，程序恢复原记录，亦可原样交 first_read 全文，二者不混用。'
                          'facts/reader.obligations 按 review_obligations 本类别 ID 穷尽回应；'
                          '每条含 result(pass|fix)/reason 与 evidence 数组 [{chapter,start,quote}]，challenge 为单对象 {disposition:satisfied|violated,reason,evidence:{chapter,start,quote}}；'
                          '约定对象本章未触及时判 pass：reason 说明对象未出现，evidence 引任一正文句作未触及佐证。'
                          'challenge 选最可能违反该约定的原文并解释为何可保留或必须返修；violated 必须 fix，'
                          '对应 world_rules/voice_match/language 检查同步 fix，并给标有 obligation_id 的 quality/blocker findings。'
                          '技术名词、字数、格式通过不证明语义质量；不读旧审稿判词来补写首读或替自己判断。'
                          'facts.events 由你从正文独立重报（数值前值照抄本任务 state），无需也无法对齐执笔者表述——入库后引擎自动重排后章承接。'
                          '事件字段见 task.json 的 event_contract_path，证据位置用 commands.evidence。')
    if stage=='chapter_review' and payload.get('numeric_correction'):
        base['guide']+='\n[数值定点修正] '+payload['numeric_correction']['instruction']+' 用 commands.patch 取得 Python 字符位置；正式检查、义务与事件证据均引用合成稿的坐标。commands.preflight 验证两类全额检查、原首读、精确补丁与结算不变，失败用普通 fix，不重写首读。'
    if stage.endswith('_review') or (chapter_session and chapter_session['role']=='writer'):
        base['guide']+='\n[证据复用] data.evidence_catalog 可把精确 chapter/start/quote 一次登记为编号；检查、义务、发现、事件的证据位置用 {ref:编号} 引用，同一引文不重复输出。也可直接写完整证据。各项判断与理由仍独立填写，不生成总 pass 或套用理由。'
    if bounded:
        identity=digest({'branch':branch,'chapter':chapter,'stage':stage,'payload':payload,'plan':plan})
        base['context_identity']=identity
        extra=get(db,'context_extra:'+identity)
        if extra:
            base['selected'].append({'kind':'requested_material','rounds':extra['rounds'],'materials':extra['materials']})
            base['context_questions']=[{'request':m['request'],'reason':m['reason']} for m in extra['materials']]
        return contextflow.fit(db,base)
    return base
