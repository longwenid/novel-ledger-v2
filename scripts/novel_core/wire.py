"""Lossless evidence references and small, recoverable transport views."""
import copy
from pathlib import Path
from .store import require


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
                and set(proof)=={'chapter','start','quote'} and type(proof['chapter']) is int
                and type(proof['start']) is int and proof['start']>=0
                and isinstance(proof['quote'],str) and proof['quote'].strip(),
                'evidence_catalog','每个证据编号须定义完整 chapter/start/quote，不嵌套引用',{'ref':name})
    out=copy.deepcopy(data);out.pop('evidence_catalog')
    def proof(value):
        if isinstance(value,dict) and 'ref' in value:
            require(set(value)=={'ref'} and isinstance(value['ref'],str) and value['ref'] in catalog,
                    'evidence_catalog','证据引用须为 {ref:已定义编号}，不能混入坐标或覆盖原文',{'ref':value.get('ref')})
            return copy.deepcopy(catalog[value['ref']])
        if isinstance(value,list):return [proof(item) for item in value]
        return value
    def receipt(value):
        if not isinstance(value,dict):return
        for field in ('checks','findings','obligations'):
            for row in value.get(field,[]) if isinstance(value.get(field,[]),list) else []:
                if not isinstance(row,dict):continue
                if 'evidence' in row:row['evidence']=proof(row['evidence'])
                challenge=row.get('challenge')
                if isinstance(challenge,dict) and 'evidence' in challenge:challenge['evidence']=proof(challenge['evidence'])
        memory=value.get('memory')
        if isinstance(memory,dict) and 'sources' in memory:memory['sources']=proof(memory['sources'])
    receipt(out)
    for category in ('facts','reader'):receipt(out.get(category))
    lists=[out.get('candidate_events',[])]
    if isinstance(out.get('facts'),dict):lists.append(out['facts'].get('events',[]))
    for events in lists:
        for event in events if isinstance(events,list) else []:
            if not isinstance(event,dict):continue
            for field in ('evidence','cause_evidence','change_evidence'):
                if field in event:event[field]=proof(event[field])
    return out


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
                'task_path','delivery_phase','state','chapter_session','review_session','preparation_session')
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
