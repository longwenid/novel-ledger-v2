"""Host-neutral worker handoff, result skeletons and scoped evidence location."""
import copy
import json
import os
import shlex
from pathlib import Path
from .store import require, get, put, write_json, digest, transaction
from . import reviewflow

# Field names, not executable examples or invented events. The source of before
# values is the task's state or a tracked recall, never a historical receipt.
EVENT_FIELDS = {
    'entity': ['entity:{id,name,kind,aliases?}'], 'alias': ['entity', 'alias'],
    'state': ['entity', 'key', 'before', 'after'],
    'relation': ['from', 'to', 'key', 'before', 'after'],
    'knowledge': ['entity', 'topic', 'before:旧claim或null', 'claim', 'stance:knows|believes|suspects|disproved', 'source'],
    'resource': ['asset', 'unit', 'from', 'to', 'quantity:十进制字符串', 'reason', 'from_before:非null来源时必填', 'to_before:非null去向时必填'],
    'hook': ['id', 'before', 'status:open|paid|closed', 'text:首次必填', 'due:首次必填', 'reason:延期或关闭必填'],
    'clock': ['before:非负整数', 'after:非负整数', 'thread?:支线时钟'],
    'anchor': ['entity', 'key', 'value', 'policy?:fixed|eventful'],
    'anchor_assert': ['entity', 'key', 'value'],
    'anchor_change': ['entity', 'key', 'before', 'after', 'reason', 'change_evidence'],
    'place': ['entity', 'parent', 'attributes?'],
    'place_change': ['entity', 'before', 'after', 'reason', 'change_evidence'],
    'link': ['id', 'a', 'b', 'kind:path|sight|sound', 'directed?:bool', 'open?:bool', 'min_ticks?:int', 'requires?:{实体:属性:值}', 'label?'],
    'link_change': ['id', 'before', 'after', 'reason'],
    'placement': ['entity', 'before:旧position或null', 'after:{place:ID}或{holder:ID}'],
    'move': ['entity', 'from', 'to', 'route:地点ID数组', 'links:连接ID数组', 'depart:int', 'arrive:int'],
    'perceive': ['observer', 'target', 'channel:sight|sound', 'from', 'to', 'link:不同地点时必填'],
    'character_development': ['entity', 'before:旧成长hash或null', 'change', 'cause', 'cause_evidence'],
}


def check_rows(ids):
    return [{'id': key, 'result': '', 'reason': '', 'evidence': []} for key in ids]


# Material at or above this size ships as an addressable file instead of being
# inlined in the envelope. The number exists to keep a one-shot input.json
# inside a single host read; it routes delivery, it never caps content.
INLINE_MATERIAL_CHARS = 4096


def shared_delivery(project,work):
    """Exact-address delivery for every task, not only role sessions.

    The role-session gate assumed sharing only pays off when one worker reads
    the same material twice ("no cross-worker read exemption"). Measured
    against a real book, that assumption is what broke: a ONE-SHOT envelope —
    the common dispatch shape — reached 60–72K characters, of which
    `design_context` and `character_context` were 44–51%. That exceeds the
    host's single-read cap, so the worker read the envelope in two or three
    pages and every page stayed in context for the rest of the run. Across 16
    workers the same file was read repeatedly for 412K characters (28% of all
    reads), and carried context inflated 10–59x.

    Addressable delivery is not primarily about reuse; it is about the worker
    reading once, and reading only what it needs.
    """
    view=copy.deepcopy(work);materials={}
    extracted=[]          # set whenever anything ships as a file; see the tail
    def shared(value):
        extracted.append(1)
        hashed=digest(value)
        # 16-hex names follow the proven prose/ convention: workers transcribe
        # them reliably, while 64-hex paths caused failed reads plus
        # glob-recovery loops (sess-3a04: 47 failed reads, 2/3 of worker tool
        # calls). Mismatched bytes heal in place, as they always did; a real
        # 64-bit prefix collision (~1e-11 for thousands of parts) stays visible
        # through the full-digest hash field. write_json emits CRLF on Windows
        # text mode, so compare LF-normalized.
        expected=(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode('utf-8')
        rel='work/shared/'+hashed[:16]+'.json';path=Path(project).resolve()/rel
        stored=path.read_bytes().replace(b'\r\n',b'\n') if path.exists() else None
        if stored!=expected:write_json(path,value)
        return {'path':rel,'hash':hashed[:12],'encoding':'json'}
    def shardable(item):
        # Debris-sized entries cost more as files (manifest row + failed-read
        # risk) than inline inside the base part; only real records shard.
        return len(json.dumps(item,ensure_ascii=False))>=1024
    fields=('contract','style_anchor','character_context','design_context','guide','state','continuity')
    if work.get('review_session'):fields+=('revision_context','volume_budget')
    session=bool(work.get('chapter_session') or work.get('review_session'))
    for field in fields:
        if field not in view:continue
        size=len(json.dumps(view[field],ensure_ascii=False))
        # Lossless routing, never a cap. The two dispatch shapes want opposite
        # things: inside a role session the worker reads several tasks, so any
        # sizeable snapshot pays to address out; a one-shot worker reads once
        # and cannot reuse anything, so only the block materials that push the
        # envelope past a single host read are worth the extra file. Contract,
        # style anchor, guide, state and continuity stay inline either way —
        # they are the readings a worker must not miss, and they are small.
        if session:
            if field in ('state','continuity') and size<2048:continue
        elif field not in ('design_context','character_context') or size<INLINE_MATERIAL_CHARS:
            continue
        value=view.pop(field);materials[field]=shared(value)
        if field in ('character_context','design_context','state','continuity') and isinstance(value,dict):
            rest=copy.deepcopy(value);parts=[]
            if field=='character_context':
                for collection in ('profiles','developments'):
                    entries=rest.get(collection)
                    if isinstance(entries,dict):
                        big={k:v for k,v in entries.items() if shardable(v)}
                        rest[collection]={k:v for k,v in entries.items() if k not in big}
                        for key,item in big.items():parts.append({'pointer':[collection,key],**shared(item)})
            elif field=='design_context':
                for key in ('world','volume'):
                    if isinstance(rest.get(key),dict) and shardable(rest[key]):
                        parts.append({'pointer':[key],**shared(rest.pop(key))})
            else:
                # Whole named collections keep arbitrary nested fact values
                # intact; changing the clock does not resend every claim/map.
                for key in list(rest):
                    if isinstance(rest[key],dict) and rest[key] and shardable(rest[key]):
                        parts.append({'pointer':[key],**shared(rest.pop(key))})
            if parts:materials[field]['parts']=[{'pointer':[],**shared(rest)},*parts]
    # Individual historical events remain byte-complete and independently
    # versioned; a sliding chapter window does not force rereading old events.
    for index,record in enumerate(view.get('selected',[])):
        if record.get('kind')=='history':
            view['selected'][index]={k:record[k] for k in ('kind','chapter','index','event_hash') if k in record}
            view['selected'][index]['material']=shared(record)
    for record in view.get('candidate_ledger',{}).get('prefix',[]):
        record['events_material']=shared(record.pop('events'))
    # Only announce the addressable view when something actually shipped that
    # way. Adding an empty manifest would change every envelope, including
    # frozen ones whose input must stay byte-identical across re-materialisation.
    if materials or extracted:
        view['shared_materials']=materials
        view['shared_read_rule']='完整资料按版本引用。shared_materials 有 parts 时完整读取各 part，pointer=[] 为基础对象，其余按 pointer 路径填回，等价于该字段完整文件；二者选一，不重复读。selected.material 与 candidate_ledger.prefix.events_material 中被本章事件、证据或承接引用的必须读取；其余可先以摘要兜底，疑点用 commands 定向补读。同一 Worker 只复用自己已读且哈希未变的文件；新 Worker或上下文不足重新读，复用不代表审稿通过。'
    return view


def template(task, work, saved=None, db=None):
    data = {}
    if task['stage'] == 'plan':
        # Skeleton mirrors the validators for THIS project's mode flags; the
        # same-source guarantee is locked by tests filling the skeleton and
        # passing validate_plan. Optional bundles (rhythm, major_turn,
        # dependencies, spare_options) stay opt-in and are not scaffolded.
        from . import design, characters
        layered = db is not None and design.enabled(db)
        profiled = db is not None and characters.enabled(db)
        contract = (get(db, 'contract') if db is not None else None) or {}
        volumes = [v['id'] for v in contract.get('volumes', [])]
        volume = volumes[0] if volumes else ''
        sequence = []
        if layered:
            try:
                volume = design.volume_for(db, task['branch'], task['chapter']) or volume
                current_volume = design.current(db, task['branch'], volume)
                sequence = (current_volume or {}).get('data', {}).get('sequence') or []
            except Exception:
                pass
        scene = {'label': '<新场所短名；已登记场所改用 place 字段写场所ID>', 'present': ['<在场实体ID>'],
                 'intent': '<场景意图>', 'conflict': '<场景阻力>', 'turn': '<场景转折>',
                 'time_hint': '<时序提示；跨日推进写 D+1 形式，单日可省略本字段>'}
        if profiled:
            scene['speakers'] = ['<拟发言人物ID，静默场景改为空数组>']
            scene['dialogue_intents'] = [{'entity': '<人物ID>', 'seeks': '', 'withholds': '', 'pressure': '', 'tactic': ''}]
        chapter = {'number': task['chapter'], 'goal': '<本章目标>', 'conflict': '<本章阻力>', 'change': '<本章变化>',
                   'volume': volume, 'entities': ['<已登记实体ID>'], 'scenes': [scene],
                   'execution': {'target_han': contract.get('chapter_target_han', 3000), 'expansion_units': [
                       {'id': 'u1', 'scene': 1, 'focus': '<展开什么>', 'development': '<怎样展开>',
                        'expected_change': '<落地变化>', 'weight': 1}]}}
        if layered:
            chapter.update(reader_experience='<本章读者体验>', ending_shape='<收束类别>:<具体说明>',
                           character_choices=[{'entity': '<实体ID>', 'desire': '<想要什么>', 'pressure': '<承受什么>',
                                               'choice': '<做出什么选择>', 'cost': '<付出什么代价>'}],
                           sequence_refs=[1] if sequence else [], expression_space=[])
            # Mirrors validate_chapter_execution's rhythm contract; workers who
            # want rhythm without this scaffold invented shapes and died on
            # chapter_rhythm rejections (session e354).
            chapter['rhythm'] = {'chapter_role': '<本章在卷中的作用>', 'carries_in': '<承接的前章后果>',
                                 'handoff': '<交给下一章的推进>',
                                 'pressure_curve': [{'scene': 1, 'movement': '<压力走向>', 'cause': '<动因>'}],
                                 'breathing_points': [{'scene': 1, 'placement': '<呼吸落点>',
                                                       'function': '<落点承担的人物/认知/后果>'}]}
        data = {'chapters': [chapter]}
    elif task['stage'] == 'volume_plan':
        # Mirrors design.save's volume branch: same-source guarantee, one beat
        # and one direction entry so both adjudication shapes are visible.
        known_locations = []
        if db is not None:
            try:
                from . import design as _design
                world = _design.current(db, task['branch'], 'world')
                known_locations = [i['id'] for i in (world or {}).get('data', {}).get('core_locations', [])]
            except Exception:
                known_locations = []
        candidate = {'direction': '<候选方向>', 'payoff': '<收益>', 'cost': '<代价>', 'risk': '<风险>', 'wild': False}
        entry = {'kind': 'direction', 'mode': 'unattended',
                 'candidates': [candidate, {**candidate, 'direction': '<对照脑暴方向>', 'wild': True}],
                 'chosen': '<选中候选的 direction 原文>', 'reason': '<为何优于其他候选>'}
        location = {'id': 'design:<本卷新场所ID>', 'name': '<场所名>', 'purpose': '<用途与戏剧功能>',
                    'layout': ['<布局要点>'], 'constraints': ['<行动限制>']}
        data = {'volume': (work.get('payload') or {}).get('design_scope') or '<本卷ID>',
                'purpose': '<本卷用途>', 'carries_in': '<承接上卷的后果>', 'climax': '<卷高潮与结尾方向>',
                'handoff': '<下卷交接；末卷写如何收束到终局>',
                'location_ids': known_locations[:1] or ['<全书核心场所 design:ID>'],
                'new_locations': [location],
                'sequence': ['<本卷因果节拍，按发生顺序>'], 'action_constraints': ['<本卷行动约束>'],
                'ending_setup': ['<终局铺设>'],
                'choice_log': [entry, {'kind': 'beat', 'beat': 1, 'mode': 'unattended',
                                       'candidates': [dict(candidate), {**candidate, 'direction': '<关键节拍对照脑暴>', 'wild': True}],
                                       'chosen': '<选中节拍方向>', 'reason': '<节拍裁决依据>'}]}
    elif task['stage'] == 'chapter_review':
        data['evidence_catalog']={}
        for category in ('facts', 'reader'):
            data[category] = {'verdict': '', 'checks': check_rows(work['payload'][category+'_checks']), 'findings': []}
            if reviewflow.enabled(work):
                hint = {'chapter': task['chapter'], 'start': 0,
                        'quote': '<替换为本章原文引句；约定对象本章未出现也须引任一触及句并判 pass>'}
                data[category]['obligations'] = [
                    {'id': row['id'], 'result': 'pass|fix', 'reason': '', 'evidence': [dict(hint)],
                     'challenge': {'disposition': 'satisfied|violated', 'reason': '', 'evidence': dict(hint)}}
                    for row in work['review_obligations'] if row['category'] == category]
        data['facts'].update(events=[], summary='')
        if saved:data['reader']['first_read_ref']=saved['hash']
        else:data['reader']['first_read']={k: '' for k in reviewflow.FIRST_FIELDS}
    elif task['stage'] == 'context_scan':
        p = work['payload']; pages = p.get('pages', [p])
        coverage = [{k: page[k] for k in ('document', 'document_hash', 'start', 'end')} for page in pages]
        data = {'coverage': coverage if 'pages' in p else coverage[0], 'notes': [], 'omission_reason': ''}
    elif task['stage'] == 'context_reduce':
        data = {'notes': [], 'accounting': [{'input_id': n['id'], 'disposition': '', 'reason': ''} for n in work['payload']['inputs']]}
    elif task['stage'] in ('batch_review','volume_review','revision_review','book_review'):
        data = {'evidence_catalog':{},'verdict':'','checks':check_rows(work['payload']['checks']),'findings':[]}
        if not work['payload']['scope'].endswith('_chunk'):data['memory']={'text':'','sources':[]}
    elif task['stage'] in ('draft', 'redesign'):
        data = {'prose': '', 'summary': ''}
        if task['stage'] == 'redesign': data['design_change'] = ''
    elif task['stage'] == 'repair':
        data = {'base_hash':work['payload']['base_hash'], 'patches':[], 'repair_note':''}
    if work.get('chapter_session',{}).get('role')=='writer' and task['stage'] in ('draft','repair','redesign'):
        data.update(evidence_catalog={},candidate_events=[],summary='',self_check='')
        if 'candidate_events_snapshot' in work['payload']:
            data.pop('candidate_events');data['reuse_candidate_events']=True
    return {'schema': 'novel-ledger-v2.result/1', 'task_id': task['id'], 'input_hash': task['input_hash'], 'data': data}


def delivery(db, project, task, work, envelope):
    base = Path(project).resolve(); root = base / Path(envelope['output_path']).parent
    saved = reviewflow.record(db, task['id']) if reviewflow.enabled(work) else None
    gated = reviewflow.enabled(work) and saved is None
    cli = str(Path(__file__).resolve().parents[1] / 'novel.py')
    # Commands must be copy-paste executable: python3 does not exist on this
    # Windows host and forced workers to hand-rewrite commands, breaking the
    # verbatim-commands discipline the envelopes are built around.
    def command(name, *args):
        return shlex.join(['python' if os.name == 'nt' else 'python3', cli, name, '--project', str(base), *map(str, args)])
    envelope['worker_prompt'] = ('本信封与 task.json 自含执行所需：宿主强制加载 skill 时不冲突，以本信封为准，不通读 references；只在字段说明不足或报错时查本文件引用的对应指南。'
                                 '只完成当前任务，读取 '+str(root/'task.json')+'。路径相对 PROJECT=' + str(base)
        + '。以 guide 和 result.template.json 为契约；不可复制旧审稿回执或查库猜协议。'
        + '必要补读用任务内 commands；空模板不代表 pass。成功提交后只返回 task_id/stage/状态，结束，不 next。')
    streaming=work.get('preparation_session',{}).get('protocol')=='same-bundle/1'
    commands = {'submit': command('submit', '--result', base/envelope['output_path'], '--brief','--compact'),
                'evidence': command('evidence', '--task', task['id'], '--quote', '替换为精确原文')}
    if streaming:
        commands['submit']+=' --continue-preparation'
        envelope['preparation_session']=copy.deepcopy(work['preparation_session'])
        envelope['worker_prompt']=('本信封与 task.json 自含执行所需：宿主强制加载 skill 时不冲突，以本信封为准，不通读 references。'
            '只处理当前 preparation_session.bundle 材料批次，读取 '+str(root/'task.json')
            +'。PROJECT='+str(base)+'。按 guide 逐页核验全部来源并写模板，执行 commands.submit；'
            'action=continue_preparation 时直接使用响应 task 继续同一批次，不重复派工或重读已交付来源。'
            '其余 action 或上下文不足时结束本次执行，返回最后已提交 task_id、bundle 与状态。'
            '不能进入执笔、审稿或不同批次，不调用 next/advance；取舍和来源仍逐页核验，不能套用旧回执。')
    if work.get('chapter_session'):
        envelope['chapter_session']=copy.deepcopy(work['chapter_session'])
        commands['submit']=command('submit','--result',base/envelope['output_path'],'--brief','--compact','--continue-chapter-batch')
        role=work['chapter_session']['role']
        envelope['worker_prompt']=('本信封与 task.json 自含执行所需：宿主强制加载 skill 时不冲突，以本信封为准，不通读 references。'
            '只完成 chapter_session 绑定的当前批次与 '+role+' 角色，读取 '+str(root/'task.json')
            +'。PROJECT='+str(base)+'。逐章读取最新输入和模板，按 commands.submit 提交；action=continue_chapter_batch 时直接处理响应 task。'
            '其余 action 或上下文不足时结束，返回最后已提交 task_id、批次与状态，不 next/advance，不跨角色或批次。'
            '执笔逐章保留正文、candidate_events 和具体 self_check；候选事件仅用于承接，不自审入库。'
            '资料按 shared_read_rule 读取；有 parts 时逐份读取完整资料，同一 Worker 只复用自己已读且未变的文件。'
            '独立审稿：信封带 first_read_files 时只读整批正文，读到每章即记录首读，随后一次冻结；'
            '后续沿用已冻结首读，不读执笔自评，逐章举证。')
    if work.get('review_session'):
        envelope['review_session']=copy.deepcopy(work['review_session'])
        commands['submit']=command('submit','--result',base/envelope['output_path'],'--brief','--compact','--continue-review')
        envelope['worker_prompt']=('本信封与 task.json 自含执行所需：宿主强制加载 skill 时不冲突，以本信封为准，不通读 references。'
            '只完成 review_session 绑定的独立复审，读取 '+str(root/'task.json')
            +'。PROJECT='+str(base)+'。按每次返回任务的 scope/first/through 与检查项举证并分别提交，不套用旧 pass。'
            'action=continue_review 时直接处理响应 task，包括本次复审材料准备；其余 action 或上下文不足时结束，返回最后已提交 task_id。'
            '同一执行已读且哈希未变的正文和 shared_materials 可复用阅读，跨卷审/修订审仍各自核对目标；疑点补读原文。'
            '新 Worker 重新读取必要完整资料，不沿用别人的已读状态。不能执笔、返修、合并、完本或调用 next/advance。')
    if task['stage'] in ('draft', 'repair', 'redesign'):
        commands['measure'] = command('measure', '--result', base/envelope['output_path'])
        if work.get('chapter_session'):
            envelope['event_contract_path']=str((root/'events.json').relative_to(base))
            write_json(root/'events.json',{'operations':EVENT_FIELDS,'before_source':'本任务 state（含前章候选事件）；候选提案须正文举证，不替正式审稿。证据定位优先用 commands.evidence；若写辅助脚本组装回执，脚本读写任何文件必须显式 encoding="utf-8"（Windows 控制台默认编码会损坏中文引文）。'})
            commands['state']=command('recall','--task',task['id'],'--state','--entity','替换为实体ID')
    if gated:
        envelope.update(delivery_phase='first_read', first_read_result_path=str(root.relative_to(base)/'first_read.json'))
        first = {'task_id': task['id'], 'input_hash': task['input_hash'],
                 'prose_hash': work['payload']['prose_hash']}
        if envelope.get('first_read_files'):
            # The engine derives the top-level first_read from the current
            # chapter's entry; workers fill the array exactly once.
            first['first_reads']=[{'chapter':r['chapter'],'prose_hash':r['prose_hash'],
                'first_read':{k:'' for k in reviewflow.FIRST_FIELDS}} for r in envelope['first_read_files']]
        else:
            first['first_read']={k: '' for k in reviewflow.FIRST_FIELDS}
        write_json(root/'first_read.template.json', first)
        envelope['first_read_template_path'] = str((root/'first_read.template.json').relative_to(base))
        commands['first_read'] = command('first-read', '--task', task['id'], '--result', root/'first_read.json','--compact')
        envelope['worker_prompt'] += '先以目标读者的身份真实读完 first_read_files（存在时为整批）或 first_read_path 再记录四项体验——回执是你的阅读事实，不是表格作业；填写 first_read.template.json 到 first_read_result_path，执行 commands.first_read；成功后读响应 task.task_path 取得已解锁完整信封、input_path 与正式模板。'
    else:
        envelope['delivery_phase'] = 'review' if reviewflow.enabled(work) else 'task'
        if task['stage'] in ('draft','repair','redesign','chapter_review'):
            commands['preflight']=command('preflight','--result',base/envelope['output_path'],'--compact')
        if work['payload'].get('base_hash') or task['stage']=='chapter_review':
            commands['patch']=command('patch','--task',task['id'],'--old','替换为完整原文','--new','替换为修改文字')
        from .recovery import revision_context
        revision = revision_context(db, task['branch'])
        if revision:
            # Also restore intent for an old frozen input, without changing its
            # identity or leaking author goals through the blind first-read gate.
            envelope['revision_context'] = revision
            envelope['worker_prompt'] += '核对信封 revision_context 的全部未完成父子修订目标，后加文风目标不撤销父级结构目标。'
        envelope['template_path'] = str((root/'result.template.json').relative_to(base))
        write_json(root/'result.template.json', template(task, work, saved, db=db))
        envelope['worker_prompt'] += '读取 input_path，以 guide 执行；写 output_path 后直接执行 commands.submit。响应 task 有 task_path 时读该文件取得完整信封。'
        if task['stage'] in ('draft', 'repair', 'redesign'):
            envelope['worker_prompt'] += '程序校验合成全文及篇幅；需在提交前查看计数时可用 commands.measure。'
        if task['stage'] == 'chapter_review':
            envelope['event_contract_path'] = str((root/'events.json').relative_to(base))
            write_json(root/'events.json', {'evidence': {'chapter': task['chapter'], 'start': 'evidence 命令定位', 'quote': '完整原文'},
                'operations': EVENT_FIELDS, 'before_source': '包内 state；缺项用 recall --state --entity ID --topic TOPIC。准备结论与旧回执不作前态。证据定位优先用 commands.evidence（返回精确 chapter/start/quote）；若写辅助脚本组装回执，脚本读写任何文件必须显式 encoding="utf-8"（Windows 控制台默认编码会损坏中文引文）。',
                'references': [str(Path(cli).parents[1]/'references'/name) for name in ('protocol.md','spatial-continuity.md','characters.md')]})
            commands['state'] = command('recall', '--task', task['id'], '--state', '--entity', '替换为实体ID')
        if saved:
            envelope['first_read_receipt_hash']=saved['hash']
            envelope['first_read_receipt_path']=str((root/'frozen_first_read.json').relative_to(base))
            write_json(root/'frozen_first_read.json',saved['receipt'])
            envelope['worker_prompt']+='若当前上下文未保留自己已读的本章冻结首读，读取 first_read_receipt_path 恢复原体验；不据补读资料改写。'
        if work['payload'].get('numeric_correction'):
            envelope['numeric_correction']=copy.deepcopy(work['payload']['numeric_correction'])
            envelope['worker_prompt']+='明确数值笔误可按 numeric_correction 修正，针对合成稿完成两类全部检查后同次提交；账本效果改变或其他缺陷仍判 fix。'
    envelope['commands'] = commands
    return envelope


def locate(db, task_id, quote, chapter=None, document=None):
    """Unique, exact evidence restricted to this task's delivered sources."""
    require(isinstance(quote, str) and quote.strip(), 'evidence', '引文不能为空')
    task = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
    require(task and task['state'] == 'pending', 'task', '证据定位只用于当前 pending')
    work = json.loads(task['input']); p = work['payload']; candidates = []
    if task['stage'] == 'context_scan':
        for page in p.get('pages', [p]):
            if document is not None and page['document'] != document: continue
            text = get(db, 'candidate_text:' + page['prose_hash'])
            require(isinstance(text, str) and digest(text) == page['prose_hash'], 'prose_store', '页文本校验失败')
            candidates.append(({'document': page['document']}, text, page['start']))
    else:
        refs = p.get('prose_files', [])
        if p.get('prose_hash'): refs = [{'chapter': task['chapter'], 'prose_hash': p['prose_hash']}]
        for ref in refs:
            if chapter is not None and ref['chapter'] != chapter: continue
            text = get(db, 'candidate_text:' + ref['prose_hash'])
            require(isinstance(text, str) and digest(text) == ref['prose_hash'], 'prose_store', '正文校验失败')
            candidates.append(({'chapter': ref['chapter']}, text, 0))
    matches = []
    for scope, text, offset in candidates:
        start = text.find(quote)
        while start != -1:
            matches.append({**scope, 'start': offset + start, 'quote': quote})
            start = text.find(quote, start + 1)
    if len(matches) != 1:
        details = {'recover': '引文未在交付来源中找到；逐字核对（含标点）后重试'}
        if matches:
            details = {'matches': [{k: v for k, v in m.items() if k != 'quote'} for m in matches[:4]],
                       'match_count': len(matches),
                       'recover': '多处命中：加长 --quote 纳入语境使其唯一，或用 --chapter/--document 限定范围'}
        elif candidates:
            from .ledger import closest_fragment
            closest = closest_fragment(candidates[0][1], quote)
            if closest:
                details = {'closest': closest,
                           'recover': '--quote 与正文不一致：把 closest.quote 原样作为 --quote 重试'}
        require(False, 'evidence', '引文缺失或不唯一，请补足语境或指定 chapter/document', details)
    return matches[0]


def advance(db, project, after=None):
    """Controller handoff: verify actual completion and obtain one next task.

    This does not launch a host worker or attest that a host session has ended.
    Receipt validation, stale-context refresh and author gates stay in next_task.
    """
    from . import engine
    following = engine.next_task(db, project, after=after, brief=True)
    snapshot = engine.status(db, brief=True)
    checkpoint(db, project)
    result = {'schema':'novel-ledger-v2.handoff/1','after_task':after,'status':snapshot,
              'boundary':'主控须先等待宿主 Worker 结束；此结果只核对真实提交，不证明新旧宿主会话空闲。'}
    if following.get('task_id'):
        result.update(action='task_ready',task=following)
    else:
        action=snapshot['status'] if snapshot['status'] in ('paused','completed') else following.get('stage') or following.get('status')
        result.update(action=action,transition=following)
    return result


def continue_preparation(db, project, task):
    """Return only the next page in this frozen batch to the same worker."""
    from . import engine, contextflow
    work=json.loads(task['input']);session=work.get('preparation_session',{})
    require(task['stage'] in ('context_scan','context_reduce')
            and session.get('protocol')==contextflow.SESSION_PROTOCOL
            and session.get('bundle')==work['payload'].get('bundle'),
            'preparation_session','只有明确绑定同一材料批次的新准备任务可以连续交接')
    following=engine.next_task(db,project,after=task['id'],brief=True,preparation_bundle=session['bundle'])
    if following.get('task_id'):
        return {'schema':'novel-ledger-v2.preparation/1','action':'continue_preparation',
                'accepted':True,'task_id':task['id'],'bundle':session['bundle'],'task':following}
    return {'schema':'novel-ledger-v2.preparation/1','action':following.get('stage',following.get('status')),
            'accepted':True,'task_id':task['id'],'bundle':session['bundle'],'transition':following,
            'boundary':'结束本次材料执行，向主控返回最后已提交任务 ID；不进入写作或审稿。'}


def checkpoint(db, project, goal=None, writer_session=None, handled=None):
    """Persist intent separately from the derived pending/approval snapshot."""
    from .engine import status
    base = Path(project).resolve()
    with transaction(db):
        prior = get(db, 'controller_intent', {})
        if goal is not None:
            require(isinstance(goal, str) and goal.strip(), 'checkpoint', '目标须是作者已授权的范围')
            prior['authorized_goal'] = goal
        branch = get(db, 'work_branch')
        pending = db.execute("SELECT chapter FROM tasks WHERE state='pending'").fetchone()
        plan = db.execute('SELECT data FROM plans WHERE branch=? AND number=?', (branch,pending['chapter'])).fetchone() if pending else None
        volume = json.loads(plan['data']).get('volume') if plan else None
        if prior.get('writer_branch') != branch or (volume and prior.get('writer_volume') and prior['writer_volume'] != volume):
            for k in ('writer_session_id','handled_chapters','writer_branch','writer_volume'): prior.pop(k, None)
        if writer_session is not None:
            require(writer_session.strip(), 'checkpoint', '执笔会话 ID 不能为空')
            if prior.get('writer_session_id') != writer_session:prior.pop('handled_chapters',None)
            prior['writer_session_id'] = writer_session
            prior['writer_branch'] = branch
            if volume:prior['writer_volume'] = volume
        if handled is not None:
            require(all(type(n) is int and n > 0 for n in handled), 'checkpoint', '章号须为正整数')
            require(prior.get('writer_session_id'), 'checkpoint', '章号集合须绑定已记录的执笔会话')
            prior['handled_chapters'] = sorted(set(handled))
        put(db, 'controller_intent', prior)
        snapshot = status(db, brief=True)
    result = {'schema': 'novel-ledger-v2.controller/1', 'root': str(Path(__file__).resolve().parents[2]),
              'project': str(base), **prior, **snapshot,
              'authorization_recorded': bool(prior.get('authorized_goal')),
              'next_action': '主控先核实旧 Worker 已结束，再 advance 恢复；交接时带 --after 已完成任务ID，不能重复派工',
              'boundary': '快照不证明宿主会话存活；作者授权目标只能由主控记录，程序不推断批准或完本授权。'}
    write_json(base/'work/controller.json', result)
    return result
