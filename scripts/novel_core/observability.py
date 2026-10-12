"""Host token observations, separate from the story ledger and its decisions."""
import json
import os
import re
import time
from collections import Counter
from pathlib import Path
from .store import require, dump, digest

PROTOCOL='host-observation/1'
TOKEN_FIELDS=('input_tokens','cache_read_tokens','output_tokens')


def validate(event):
    require(isinstance(event,dict) and event.get('schema')==PROTOCOL,'observation','观测协议无效')
    for key in ('provider','session_id','request_id'):
        require(isinstance(event.get(key),str) and event[key].strip(),'observation','缺少观测身份 '+key)
    for key in TOKEN_FIELDS:
        value=event.get(key)
        require(value is None or (type(value) is int and value>=0),'observation','用量须为非负整数或 null',{'field':key})
    for key in ('role','stage'):
        require(event.get(key) is None or isinstance(event[key],str),'observation','角色和阶段须为字符串或 null',{'field':key})
    require('task_ids' not in event or (isinstance(event['task_ids'],list) and all(isinstance(value,str) for value in event['task_ids'])),
            'observation','task_ids 须为字符串数组，不能将多任务用量强行平均')
    return event


def identity(event):
    return tuple(event[key] for key in ('provider','session_id','request_id'))


def deduplicate(events):
    unique={}
    for event in events:
        validate(event);key=identity(event)
        if key in unique:
            # Imported full/compact transcripts may differ in explanatory text;
            # one request's numeric usage must still agree, or remain unknown.
            prior=unique[key]
            for field in TOKEN_FIELDS:
                a,b=prior.get(field),event.get(field)
                require(a is None or b is None or a==b,'observation_conflict','同一调用出现不同用量，不能累加或静默择一',{'identity':key,'field':field})
                if a is None:prior[field]=b
        else:unique[key]=dict(event)
    return list(unique.values())


def summary(events):
    all_rows=deduplicate(events)
    gaps=[event for event in all_rows if event.get('observation_type')=='coverage_gap']
    rows=[event for event in all_rows if event.get('observation_type')!='coverage_gap']
    totals=Counter();groups={};unknown=0
    for event in rows:
        group=(event.get('role') or 'unknown')+'/'+(event.get('stage') or 'unknown')
        bucket=groups.setdefault(group,{'calls':0,'tokens':Counter(),'unknown_calls':0})
        bucket['calls']+=1
        missing=any(event.get(k) is None for k in TOKEN_FIELDS)
        if missing:unknown+=1;bucket['unknown_calls']+=1
        for key in TOKEN_FIELDS:
            if event.get(key) is not None:totals[key]+=event[key];bucket['tokens'][key]+=event[key]
    return {'schema':PROTOCOL,'calls':len(rows),'usage_complete':unknown==0 and not gaps,'unknown_calls':unknown,
            'missing_sessions':[{'session_id':event['session_id'],'path':event.get('source')} for event in gaps],
            'tokens':{k:totals[k] for k in TOKEN_FIELDS},'known_gross_tokens':sum(totals.values()),
            'gross_tokens':sum(totals.values()) if unknown==0 and not gaps else None,
            'groups':{key:{**value,'tokens':dict(value['tokens'])} for key,value in groups.items()},
            'billing':'Token observations only; monetary cost requires actual host rates.'}


def import_antigravity(path, session_id=None, role='unknown', stage=None):
    source=Path(path).resolve()
    if session_id is None:
        matches=re.findall(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',str(source))
        require(matches,'observation','导入文件无法推导 session_id，请显式传入')
        session_id=matches[-1]
    rows=[]
    for line in source.read_text(encoding='utf-8').splitlines():
        if not line.strip():continue
        record=json.loads(line)
        require(isinstance(record,dict),'observation','会话日志每行须为对象')
        if record.get('type')!='PLANNER_RESPONSE':continue
        step=record.get('step_index')
        require(type(step) is int,'observation','模型调用缺少 step_index')
        calls=record.get('tool_calls',[])
        text=dump(calls)
        task_ids=sorted(set(re.findall(r'--task[ \\"\x27]+([0-9a-f]{32})',text)))
        rows.append({'schema':PROTOCOL,'provider':'antigravity','session_id':session_id,
                     'request_id':str(record.get('request_id') or 'step:'+str(step)),
                     'role':role,'stage':record.get('stage') or stage,'task_ids':task_ids,
                     'stage_source':'record' if record.get('stage') else 'invocation' if stage else 'unknown',
                     **{key:record.get(key) for key in TOKEN_FIELDS},
                     'created_at':record.get('created_at'),'tool_categories':sorted({c.get('name','unknown') for c in calls}),
                     'error_code':record.get('error'),'source':str(source),
                     'input_semantics':'Antigravity input_tokens excludes cache_read_tokens'})
    return deduplicate(rows)


def import_tree(path, session_id=None):
    """Follow only creation results from the requested root, once per child."""
    source=Path(path).resolve();events=import_antigravity(source,session_id,role='controller');visited={events[0]['session_id']} if events else set()
    pending=[source];missing=[]
    while pending:
        log=pending.pop();specs=[]
        for line in log.read_text(encoding='utf-8').splitlines():
            if not line.strip():continue
            record=json.loads(line)
            for call in record.get('tool_calls',[]):
                if call.get('name')!='invoke_subagent':continue
                specs=(call.get('args') or {}).get('Subagents',[])
                if isinstance(specs,str):specs=json.loads(specs)
                if not isinstance(specs,list):specs=[]
            if record.get('type')!='GENERIC':continue
            content=record.get('content','')
            for index,child in enumerate(re.findall(r'"conversationId"\s*:\s*"([0-9a-f-]{36})"',content)):
                if child in visited:continue
                visited.add(child)
                # transcript_full is the canonical export; compact and chunks
                # must never be concatenated to it.
                target=source.parents[3]/child/'.system_generated/logs/transcript_full.jsonl'
                if not target.is_file():missing.append({'session_id':child,'path':str(target)});continue
                spec=specs[index] if index<len(specs) and isinstance(specs[index],dict) else {}
                role=spec.get('Role','unknown')
                role='reviewer' if '审' in role else 'planner' if '规划' in role else 'writer' if '执笔' in role else role
                stages=re.findall(r'work/ch\d+/(\w+)-[0-9a-f]+',spec.get('Prompt',''))
                events.extend(import_antigravity(target,role=role,stage=stages[0] if stages else None));pending.append(target)
    events.extend({'schema':PROTOCOL,'provider':'antigravity','session_id':row['session_id'],
                   'request_id':'coverage-gap','observation_type':'coverage_gap','source':row['path'],
                   **{key:None for key in TOKEN_FIELDS}} for row in missing)
    return events,missing


def append(path, events):
    """A standalone observation stream; it never opens novel.sqlite3."""
    target=Path(path)
    target.parent.mkdir(parents=True,exist_ok=True)
    lock=target.with_name(target.name+'.lock');owned=False;deadline=time.monotonic()+2
    while not owned:
        try:
            descriptor=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            os.close(descriptor);owned=True
        except FileExistsError:
            require(time.monotonic()<deadline,'observation_busy','观测流正被写入；未改故事数据，稍后重试。遗留锁需核实原进程已结束后清理')
            time.sleep(.02)
    try:
        old=[json.loads(line) for line in target.read_text(encoding='utf-8').splitlines() if line.strip()] if target.is_file() else []
        covered={event['session_id'] for event in events if event.get('observation_type')!='coverage_gap'}
        old=[event for event in old if not (event.get('observation_type')=='coverage_gap' and event['session_id'] in covered)]
        combined=deduplicate(old+events)
        temp=target.with_name(target.name+'.'+digest(dump(combined))[:12]+'.tmp')
        try:
            temp.write_text(''.join(dump(event)+'\n' for event in combined),encoding='utf-8')
            temp.replace(target)
        finally:temp.unlink(missing_ok=True)
        return summary(combined)
    finally:lock.unlink()
