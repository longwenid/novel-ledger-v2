"""Sidecar provenance for material actually delivered to a task.

Delivery is observable; comprehension and a complete semantic read-set are not.
These records never relax the existing review/revision acceptance guards.
"""
from __future__ import annotations
import json
import time
from .store import digest, dump, get, require


def ensure_schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS task_reads(id INTEGER PRIMARY KEY,consumer_task TEXT NOT NULL,target_task TEXT NOT NULL,kind TEXT NOT NULL,query TEXT NOT NULL,value_hash TEXT NOT NULL,sources TEXT NOT NULL,watch TEXT NOT NULL,delivered_at REAL NOT NULL,UNIQUE(consumer_task,target_task,kind,query,value_hash))')
    db.execute('CREATE INDEX IF NOT EXISTS task_reads_consumer ON task_reads(consumer_task,id)')


def source_refs(value):
    refs=[]
    def visit(item):
        if isinstance(item,dict):
            if isinstance(item.get('source_ref'),dict):refs.append(item['source_ref'])
            if isinstance(item.get('source_refs'),list):refs.extend(r for r in item['source_refs'] if isinstance(r,dict))
            for key,child in item.items():
                if key not in ('source_ref','source_refs'):visit(child)
        elif isinstance(item,list):
            for child in item:visit(child)
    visit(value)
    unique={dump(ref):ref for ref in refs}
    return [unique[key] for key in sorted(unique)]


def _watch(db,query,target_task):
    """Version witnesses are conservative, including negative query scopes."""
    command=query.get('command',query.get('operation'))
    branch=query.get('branch') or get(db,'work_branch')
    watch={}
    if command in ('initial','candidate','material'):
        row=db.execute('SELECT input_hash,input FROM tasks WHERE id=?',(target_task,)).fetchone()
        if row:watch['frozen_task']={'task_id':target_task,'input_hash':row['input_hash'],'stored_input_hash':digest(row['input'])}
    if command=='recall' and query.get('contract'):
        watch['contract']=digest(get(db,'contract'))
    elif command=='recall' and query.get('chapter') is not None:
        row=db.execute('SELECT hash,prose FROM chapters WHERE branch=? AND number=?',(branch,query['chapter'])).fetchone()
        watch['chapter']={'branch':branch,'number':query['chapter'],'hash':row['hash'] if row else None,'prose_hash':digest(row['prose']) if row else None}
    elif command in ('recall','map'):
        row=db.execute('SELECT event_generation,invalid_generation,chapter_generation FROM local_generations WHERE branch=?',(branch,)).fetchone()
        watch['query_scope']={'branch':branch,'generations':list(row) if row else [0,0,0],
                              'contract_hash':digest(get(db,'contract')),
                              'policy':'any relevant domain mutation invalidates this witness; empty results are scoped observations'}
    elif command=='character':
        # Current interpretations can change without an event. A historical
        # version-addressed read remains available, but may no longer be current.
        from .characters import current,resolve
        from .ledger import replay
        entity=resolve(replay(db,branch),query['entity'])
        from .characters import index_key,growth_hash
        record=current(db,branch,entity)
        source_versions=[]
        for number in (record or {}).get('sources',{}):
            row=db.execute('SELECT hash,prose FROM chapters WHERE branch=? AND number=?',(branch,int(number))).fetchone()
            source_versions.append([number,row['hash'] if row else None,digest(row['prose']) if row else None])
        state=replay(db,branch)
        watch['character']={'branch':branch,'entity':entity,'record_hash':digest(record),
                            'index_hash':digest(get(db,index_key(branch,entity,record['hash']))) if record else None,
                            'source_versions':source_versions,'growth_hash':growth_hash(state,entity),
                            'canon_values':[[ref['entity'],ref['key'],state['facts'].get(ref['entity']+':'+ref['key'])] for ref in (record or {}).get('data',{}).get('canon_refs',[])]}
    elif command=='design':
        from .design import current
        record=current(db,branch,query.get('scope','world'))
        generation=db.execute('SELECT event_generation,invalid_generation,chapter_generation FROM local_generations WHERE branch=?',(branch,)).fetchone()
        watch['design']={'branch':branch,'scope':query.get('scope','world'),'record_hash':digest(record),
                         'world_hash':digest(current(db,branch,'world')),'contract_hash':digest(get(db,'contract')),
                         'source_domain_generations':list(generation) if generation else [0,0,0]}
    return watch


def record_read(db,consumer_task,query,value,target_task=None):
    if not consumer_task:return None
    row=db.execute('SELECT id FROM tasks WHERE id=?',(consumer_task,)).fetchone()
    require(row,'task','材料读依赖的消费任务不存在')
    target_task=target_task or consumer_task
    query=dict(query)
    query.setdefault('branch',get(db,'work_branch'))
    kind='initial' if query.get('command')=='initial' else 'supplemental'
    refs=source_refs(value)
    if isinstance(value,dict) and value.get('format')=='json_text_fragment':
        refs.append({'kind':'delivered_page','content_hash':value['content_hash'],
                     'start':value['start'],'end':value['end'],'total_chars':value['total_chars']})
    if query.get('command')=='recall' and isinstance(value,dict):
        for event in value.get('records',[]):
            refs.append({'kind':'event','branch':query['branch'],'chapter':event['chapter'],
                         'index':event['index'],'event_hash':event['event_hash'],'value_hash':digest(event['event'])})
    # Certify complete chapter bodies only when the returned object actually
    # contains them. A summary, quote, catalogue or partial page is insufficient.
    if query.get('command')=='recall' and query.get('chapter') is not None and isinstance(value,dict) and isinstance(value.get('prose'),str):
        refs.append({'kind':'chapter_full','branch':query['branch'],'chapter':value['number'],
                     'chapter_hash':value['hash'],'prose_hash':digest(value['prose'])})
    if kind=='initial':
        # Whole records already carry their versions in the frozen input. Keep
        # their navigational provenance here, without re-injecting a duplicate
        # catalogue into ordinary full-profile model packs.
        from .characters import source_ref
        for entity,record in value.get('character_context',{}).get('profiles',{}).items():
            if record and not record.get('view'):
                refs.append(source_ref(query['branch'],entity,record))
        for field in ('contract','plan'):
            if field in value:refs.append({'kind':field,'branch':query['branch'],'chapter':query.get('chapter'),'value_hash':digest(value[field])})
        for scope,record in value.get('design_context',{}).items():
            if isinstance(record,dict) and 'hash' in record:
                refs.append({'kind':'design','branch':query['branch'],'scope':scope,'record_hash':record['hash'],'value_hash':digest(record.get('data'))})
        for item in value.get('selected',[]):
            if 'event_hash' in item and 'event' in item:
                refs.append({'kind':'event','branch':query['branch'],'chapter':item['chapter'],
                             'index':item['index'],'event_hash':item['event_hash'],'value_hash':digest(item['event'])})
            if item.get('kind')=='required_chapter' and isinstance(item.get('prose_hash'),str):
                # Prose is delivered as a content-addressed file whose bytes
                # equal the stored chapter prose; the hash certifies the body.
                refs.append({'kind':'chapter_full','branch':query['branch'],'chapter':item['chapter'],
                             'chapter_hash':item['hash'],'prose_hash':item['prose_hash']})
    watch=_watch(db,query,target_task)
    value_hash=digest(value)
    db.execute('INSERT OR IGNORE INTO task_reads(consumer_task,target_task,kind,query,value_hash,sources,watch,delivered_at) VALUES (?,?,?,?,?,?,?,?)',
               (consumer_task,target_task,kind,dump(query),value_hash,dump(refs),dump(watch),time.time()))
    return value_hash


def record_initial(db,task_id,work):
    return record_read(db,task_id,{'command':'initial','branch':work.get('branch'),'stage':work.get('stage'),'chapter':work.get('chapter')},work,task_id)


def read_set(db,task_id):
    require(db.execute('SELECT 1 FROM tasks WHERE id=?',(task_id,)).fetchone(),'task','任务不存在')
    entries=[]
    for row in db.execute('SELECT * FROM task_reads WHERE consumer_task=? ORDER BY id',(task_id,)):
        entries.append({'target_task':row['target_task'],'kind':row['kind'],'query':json.loads(row['query']),
                        'value_hash':row['value_hash'],'sources':json.loads(row['sources']),
                        'watch':json.loads(row['watch'])})
    return {'task_id':task_id,'coverage':'delivered_only' if entries else 'unknown_legacy',
            'complete_semantic_read_set':False,'entries':entries,'signature':digest(entries),
            'reuse_authority':False}


def inspect(db,task_id):
    result=read_set(db,task_id)
    for entry in result['entries']:
        entry['versions_unchanged']=entry['watch']==_watch(db,entry['query'],entry['target_task'])
    result['versions_unchanged']=all(e['versions_unchanged'] for e in result['entries']) if result['entries'] else None
    result['boundary']='仅证明工具返回了这些版本与查询范围；未证明理解、穷尽因果或可跨 scope 复用审稿。原审稿签名与上游正文门禁仍生效。'
    return result


def read_chapters(db,task_id,branch=None):
    """Full, still-current chapter versions delivered through observable tools."""
    result={}
    for entry in read_set(db,task_id)['entries']:
        for source in entry['sources']:
            if source.get('kind')!='chapter_full':continue
            if branch is not None and source['branch']!=branch:continue
            row=db.execute('SELECT hash,prose FROM chapters WHERE branch=? AND number=?',
                           (source['branch'],source['chapter'])).fetchone()
            if row and row['hash']==source['chapter_hash'] and digest(row['prose'])==source['prose_hash']:
                result[str(source['chapter'])]=row['hash']
    return result
