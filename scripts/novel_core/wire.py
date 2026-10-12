"""Lossless evidence references and small, recoverable transport views."""
import copy
from pathlib import Path
from .store import require, Batch, Error

EVIDENCE_INPUT='quote_optional_start/1'
EVIDENCE_RULE='证据填 chapter/quote；start 可选，程序批量定位唯一原文。歧义或排错才用 evidence，不逐条计算坐标。'


def evidence_contract(chapter=1, quote='原文'):
    return {'protocol':EVIDENCE_INPUT,'example':{'chapter':chapter,'quote':quote},'rule':EVIDENCE_RULE}


def expand_receipt(db,task,work,data):
    """Resolve transport references without creating any editorial judgment."""
    out=expand_evidence(data,task['stage'])
    reader=out.get('reader')
    if not isinstance(reader,dict) or 'first_read_ref' not in reader:return out
    from . import reviewflow
    require(task['stage']=='chapter_review' and reviewflow.enabled(work),
            'first_read_ref','首读引用只用于有持久门禁的 chapter_review')
    require('first_read' not in reader,'first_read_conflict','首读全文与引用只能选一种')
    saved=reviewflow.record(db,task['id'])
    require(saved is not None,'first_read_required','先执行 first-read 冻结本任务真实首读')
    require(isinstance(reader['first_read_ref'],str) and reader['first_read_ref']==saved['hash']
            and saved['receipt']['input_hash']==task['input_hash']
            and saved['receipt']['prose_hash']==work['payload']['prose_hash'],
            'first_read_version','首读引用须对应本任务、输入和正文的已冻结记录')
    out=copy.deepcopy(out);reader=out['reader'];reader.pop('first_read_ref')
    reader['first_read']=copy.deepcopy(saved['receipt']['first_read'])
    return out


def expand_evidence(data,stage):
    """Expand only protocol proof slots, never arbitrary narrative fact values."""
    if 'evidence_catalog' not in data:return data
    require(stage in ('draft','repair','redesign','chapter_review','batch_review','volume_review','revision_review','book_review'),
            'evidence_catalog','此阶段不使用证据目录')
    catalog=data['evidence_catalog']
    require(isinstance(catalog,dict),'evidence_catalog','evidence_catalog 须为编号到完整原文证据的对象')
    for name,proof in catalog.items():
        require(isinstance(name,str) and name.strip() and isinstance(proof,dict)
                and {'chapter','quote'} <= set(proof) <= {'chapter','start','quote'} and type(proof['chapter']) is int
                and ('start' not in proof or (type(proof['start']) is int and proof['start']>=0))
                and isinstance(proof['quote'],str) and proof['quote'].strip(),
                'evidence_catalog','证据编号须定义 chapter/quote，可选非负整数 start，不嵌套引用',{'ref':name})
    out=copy.deepcopy(data);out.pop('evidence_catalog')
    for path, value in proof_slots(out):
        if 'ref' not in value:continue
        require(set(value)=={'ref'} and isinstance(value['ref'],str) and value['ref'] in catalog,
                'evidence_catalog','证据引用须为 {ref:已定义编号}，不能混入坐标或覆盖原文',
                {'ref':value.get('ref'),'field':path})
        expanded=copy.deepcopy(catalog[value['ref']]);value.clear();value.update(expanded)
    return out


def proof_slots(data):
    """Registered proof slots only; narrative values are never traversed."""
    def refs(value, path):
        if isinstance(value, list):
            for index, item in enumerate(value):
                yield from refs(item, path + '/' + str(index))
        elif isinstance(value, dict):
            yield path, value
    def receipt(value, path):
        if not isinstance(value, dict):return
        for field in ('checks', 'findings', 'obligations'):
            rows=value.get(field, [])
            for index, row in enumerate(rows if isinstance(rows, list) else []):
                if not isinstance(row, dict):continue
                prefix=f'{path}/{field}/{index}'
                if 'evidence' in row:yield from refs(row['evidence'], prefix+'/evidence')
                challenge=row.get('challenge')
                if isinstance(challenge, dict) and 'evidence' in challenge:
                    yield from refs(challenge['evidence'], prefix+'/challenge/evidence')
        memory=value.get('memory')
        if isinstance(memory, dict):yield from refs(memory.get('sources', []), path+'/memory/sources')
    yield from receipt(data, 'data')
    for category in ('facts', 'reader'):yield from receipt(data.get(category), 'data/'+category)
    for path, events in (('data/candidate_events', data.get('candidate_events', [])),
                         ('data/events', data.get('events', [])),
                         ('data/facts/events', (data.get('facts') or {}).get('events', []) if isinstance(data.get('facts'), dict) else [])):
        for index, event in enumerate(events if isinstance(events, list) else []):
            if not isinstance(event, dict):continue
            for field in ('evidence', 'cause_evidence', 'change_evidence'):
                if field in event:yield from refs(event[field], f'{path}/{index}/{field}')


def anchor_proofs(data, sources, skip_obligations=False):
    """Anchor a receipt against its exact composed/frozen source versions.

    A per-receipt cache avoids scanning the same quote for every check. Explicit
    valid coordinates retain their meaning, including repeated occurrences.
    Domain validators still execute independently after normalization.
    """
    from .ledger import evidence
    batch=Batch(); cache={}
    for path, ref in proof_slots(data):
        # reviewflow owns obligation shape/coverage and aggregates their proof
        # defects together with structural errors, in the established order.
        if skip_obligations and '/obligations/' in path:continue
        chapter=ref.get('chapter')
        try:
            require(type(chapter) is int and chapter in sources, 'evidence', '证据章号不在本任务冻结来源内')
            require(isinstance(ref.get('quote'), str), 'evidence', '缺少正文证据原文')
            # Avoid unhashable malformed start values before touching the cache.
            key=(chapter, ref['quote'], repr(ref.get('start')))
            if key not in cache:
                probe=dict(ref); evidence(sources[chapter], probe, chapter); cache[key]=probe
            ref.update(cache[key])
        except Error as error:
            batch.capture(error, field=path)
    batch.raise_if_any()
    return data


def compact_response(project,value):
    """A named structural view. Full tasks remain in task.json, not truncated."""
    if not isinstance(value,dict):return value
    out=copy.deepcopy(value)
    verbose={'volume_budget','usage','recovery','review_verification','context_chars','db_version',
             'skill_version','writer_batch','chapter_batch_size','chapter_batch','batch_through'}
    def status(item):return {k:v for k,v in item.items() if k not in verbose}
    if out.get('schema')=='novel-ledger-v2.status/1' or 'work_committed' in out:out=status(out)
    if isinstance(out.get('status'),dict):out['status']=status(out['status'])
    if isinstance(out.get('transition'),dict):out['transition']=status(out['transition'])
    task=out.get('task')
    if isinstance(task,dict) and 'input_path' in task:
        fields=('schema','task_id','stage','chapter','branch','input_hash','project','input_path','output_path',
                'task_path','host_dispatch_path','delivery_phase','state','chapter_session','review_session','preparation_session','execution_policy')
        short={k:task[k] for k in fields if k in task}
        path=task.get('task_path') or str(Path(task['input_path']).with_name('task.json'))
        short['task_path']=path
        short['worker_prompt']='读取 '+str(Path(project).resolve()/path)+'，按文件内 worker_prompt 和 commands 执行当前绑定阶段或连续会话；返回最后已提交任务 ID。'
        out['task']=short
    settled=out.get('settlement')
    if isinstance(settled,dict) and 'steps' in settled:
        settled['step_count']=len(settled.pop('steps'))
    correction=out.get('numeric_correction')
    if isinstance(correction,dict) and 'patches' in correction:
        correction['patch_count']=len(correction.pop('patches'))
    if out.get('schema')=='novel-ledger-v2.handoff/1':out['controller_path']='work/controller.json'
    out['response_view']='compact/1'
    return out
