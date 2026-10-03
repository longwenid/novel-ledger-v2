"""Version-bound text patches. Offsets always refer to the same immutable base."""
import copy
import re
from .store import get,put,digest,require


def save_source(db,prose):
    hashed=digest(prose);name='candidate_text:'+hashed
    old=get(db,name)
    require(old is None or old==prose,'candidate_integrity','候选正文快照与哈希不一致')
    if old is None:put(db,name,prose)
    return hashed


def source(db,work):
    payload=work['payload']
    if payload.get('base_hash'):
        prose=get(db,'candidate_text:'+payload['base_hash'])
        require(isinstance(prose,str) and digest(prose)==payload['base_hash'],'candidate_integrity','候选正文快照缺失或已改变')
        return prose
    # Pre-v1.4 pending tasks still contain their full base directly.
    prose=payload.get('prose') if work['stage']=='repair' else payload.get('original_prose')
    require(isinstance(prose,str) and prose.strip(),'candidate_source','任务没有可复用的候选正文')
    return prose


def excerpts(prose,findings):
    paragraphs=[(m.start(),m.end()) for m in re.finditer(r'[^\n]+(?:\n|$)',prose)]
    spans=[]
    for item in findings:
        if item['severity']=='note':continue
        ref=item['evidence'];start=ref['start'];end=start+len(ref['quote'])
        touched=[i for i,(a,b) in enumerate(paragraphs) if a<end and b>start]
        require(touched,'repair_scope','无法定位局部发现，请按整章上下文修订')
        # Include immediate neighboring paragraphs; do not truncate a long paragraph.
        left=max(0,min(touched)-1);right=min(len(paragraphs)-1,max(touched)+1)
        spans.append((paragraphs[left][0],paragraphs[right][1]))
    merged=[]
    for start,end in sorted(spans):
        if merged and start<=merged[-1][1]:merged[-1]=(merged[-1][0],max(end,merged[-1][1]))
        else:merged.append((start,end))
    require(merged,'repair_scope','局部返修缺少实质问题位置')
    return [{'start':a,'end':b,'text':prose[a:b]} for a,b in merged]


def prepare(db,stage,payload):
    payload=copy.deepcopy(payload)
    if stage=='repair':
        prose=payload['prose'];payload['base_hash']=save_source(db,prose);payload['base_chars']=len(prose)
        payload.setdefault('repair_scope','chapter')
        if payload['repair_scope']=='local':
            payload['excerpts']=excerpts(prose,payload['findings']);payload.pop('prose')
            payload['omitted_chars']=len(prose)-sum(e['end']-e['start'] for e in payload['excerpts'])
        payload['instruction']='优先返回 base_hash + patches + repair_note，只输出替换片段。start 是本次完整基稿的 Python 字符位置，old 必须精确匹配。局部任务需扩大修改范围时返回 expand_context=true 与 reason。补丁合成后仍重新进行事实与读者审稿。'
        payload['retrieval']='candidate --task 当前task_id --start 起始字符 --end 结束字符；省略 end 默认最多4000字符'
    elif stage=='draft' and payload.get('original_prose'):
        payload['base_hash']=save_source(db,payload['original_prose']);payload['base_chars']=len(payload['original_prose'])
        payload['instruction']+=' 可提交 base_hash + patches + repair_note 局部修改；确无需变更时提交 base_hash + reuse_original=true，仍重审事实及正文。'
    return payload


def validate_scope(data):
    if 'repair_scope' in data:
        require(data.get('verdict')=='fix' and data['repair_scope'] in ('local','chapter'),'repair_scope','repair_scope 仅用于 fix，取 local/chapter')


def apply_patch(base,data,allowed=None):
    require(data.get('base_hash')==digest(base),'patch_base','补丁基稿版本不匹配，不能套到新版本或另一章')
    changes=data.get('patches')
    require(isinstance(changes,list) and changes,'patch','patches 必须是非空数组')
    normalized=[]
    for change in changes:
        if isinstance(change,dict) and set(change)=={'start','end','text'}:
            # Structural repair: span-replacement form; old is the anchored slice itself.
            start,end=change.get('start'),change.get('end')
            if type(start) is int and type(end) is int and 0<=start<end<=len(base):
                change={'start':start,'old':base[start:end],'new':change['text']}
        require(isinstance(change,dict) and set(change)=={'start','old','new'},'patch','补丁字段必须恰为 start/old/new；start/end/text 形式会自动换算，非法范围不会')
        start,old,new=change['start'],change['old'],change['new']
        require(type(start) is int and start>=0 and isinstance(old,str) and bool(old) and isinstance(new,str),'patch','start 为非负字符位置，old 为非空原文，new 为替换文本（可空以删除）')
        require('\x00' not in new and new!=old,'patch','替换不能包含 NUL 或原样不变')
        end=start+len(old)
        require(base[start:end]==old,'patch_anchor','补丁原文与指定位置不匹配；重复段落不能只按文本搜索替换')
        if allowed is not None:require(any(start>=e['start'] and end<=e['end'] for e in allowed),'patch_scope','修改超出局部工作范围；先请求 expand_context，不能暗改未提供段落')
        normalized.append((start,end,new))
    normalized.sort()
    for prior,next_ in zip(normalized,normalized[1:]):require(prior[1]<=next_[0],'patch_overlap','补丁范围重叠；所有 start 均以同一基稿为准')
    result=base
    for start,end,new in reversed(normalized):result=result[:start]+new+result[end:]
    require(result.strip() and result!=base,'patch','补丁不能清空整章或整体没有变化')
    return result


def compose(db,stage,work,data,draft):
    choices=[k for k in ('prose','patches','reuse_original') if k in data]
    require(len(choices)==1,'prose_mode','prose/patches/reuse_original 必须且只能选择一种')
    payload=work['payload'];local=stage=='repair' and payload.get('repair_scope')=='local'
    if 'prose' in data:
        require(not local,'patch_required','局部返修只提交 patches；需要整章重写先 expand_context')
        return data['prose']
    require(stage=='repair' or (stage=='draft' and payload.get('original_prose')),'patch_stage','补丁仅用于 repair 或历史修订 draft，redesign 需完整重写')
    base=source(db,work)
    if stage=='repair':require(draft and draft['prose']==base,'patch_base','当前草稿已不再是本任务的补丁基稿')
    if 'reuse_original' in data:
        require(stage=='draft' and data['reuse_original'] is True and data.get('base_hash')==digest(base),'reuse_original','仅历史修订 draft 可按正确版本复用原稿')
        return base
    require(isinstance(data.get('repair_note'),str) and data['repair_note'].strip(),'patch','提交补丁须说明具体修订目的 repair_note')
    return apply_patch(base,data,payload['excerpts'] if local else None)


def read_slice(db,task,start=0,end=None):
    import json
    work=json.loads(task['input']);base=source(db,work)
    require(type(start) is int and 0<=start<=len(base),'candidate_range','start 超出基稿范围')
    end=min(len(base),start+4000) if end is None else end
    require(type(end) is int and start<=end<=len(base) and end-start<=16000,'candidate_range','单次最多读取16000字符，范围须有效')
    return {'task_id':task['id'],'base_hash':digest(base),'start':start,'end':end,'text':base[start:end],
            'total_chars':len(base),'omitted_before':start,'omitted_after':len(base)-end,'status':'candidate_not_canon'}
