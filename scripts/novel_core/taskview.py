"""Lossless, derived reading pages for one frozen task; no domain decisions."""
import hashlib
import json
import re
from pathlib import Path
from .store import digest, dump, require, write_json

PROTOCOL='task-reading/1'
BUNDLE_PROTOCOL='task-reading-bundle/1'
PAGE_BYTES=24000  # Routing threshold only: an indivisible record is never cut.
VERSION_LABEL_BYTES=512  # Small metadata is reread; full hashes stay in the manifest.
ROUTING={'input_files','shared_materials','must_read','on_demand','input_read_rule','shared_read_rule'}


def _path(base, route):
    target=(base/route).resolve()
    require(target.is_relative_to(base),'task_view_path','阅读来源必须在当前项目内')
    return target


def units(project, view, input_path):
    """Collect complete required records plus all optional source addresses."""
    base=Path(project).resolve(); records=[];seen=set();sources={}
    def read(route):
        target=_path(base,route);raw=target.read_bytes()
        sources[route]={'path':route,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}
        return json.loads(raw.decode('utf-8'))
    def add(field,value,origin,pointer=None):
        records.append({'field':field,'value':value,'origin':origin,'pointer':pointer or [],'value_hash':digest(value)})
    def material(field,ref):
        if ref['path'] in seen:return
        seen.add(ref['path']);add(field,read(ref['path']),ref['path'])
    read(input_path)
    for field,value in view.items():
        if field not in ROUTING:add(field,value,input_path,[field])
    # Typed fields that are not marked collections are execution instructions,
    # not optional records. The old cost meter missed those entire fields.
    required_fields={row['field'].split('/',1)[0] for row in view.get('must_read',[])}
    optional_fields={row['field'].split('/',1)[0] for row in view.get('on_demand',[])}
    for field,ref in view.get('input_files',{}).items():
        if field not in optional_fields or field in required_fields:
            material(field,ref)
    for field,entry in view.get('shared_materials',{}).items():
        for part in entry.get('parts') or [entry]:
            # Empty-pointer base records hold clock/status/validity and small
            # entries. They are needed to interpret required sibling records.
            required=part.get('needed',True) or ('parts' in entry and not part.get('pointer'))
            if required:material(field+'/'+str(part.get('pointer',[])),part)
    # Events are addresses embedded in selected/candidate_ledger, including
    # when those fields were themselves sliced out of the index.
    for field in ('selected','candidate_ledger'):
        value=view.get(field)
        if value is None and field in view.get('input_files',{}):value=read(view['input_files'][field]['path'])
        if field=='selected':
            for index,row in enumerate(value if isinstance(value,list) else []):
                if isinstance(row,dict) and row.get('needed',True) and isinstance(row.get('material'),dict):
                    material(f'selected/{index}/material',row['material'])
        elif isinstance(value,dict):
            for row in value.get('prefix',[]):
                if isinstance(row,dict) and isinstance(row.get('events_material'),dict):
                    material('candidate_ledger/'+str(row.get('chapter')),row['events_material'])
    routes=[];optional_seen=set()
    def optional(field,ref):
        signature=(field,ref.get('path'),tuple(ref.get('pointer',[])))
        if signature in optional_seen:return
        optional_seen.add(signature)
        routes.append({'field':field,**{key:ref[key] for key in ('path','pointer','hash') if key in ref}})
    for field,ref in view.get('input_files',{}).items():
        if ref['path'] not in seen:optional(field,ref)
    for field,entry in view.get('shared_materials',{}).items():
        for part in entry.get('parts') or [entry]:
            if part['path'] not in seen:optional(field,part)
    for row in view.get('on_demand',[]):
        if row.get('path') and row['path'] not in seen:optional(row['field'],row)
    add('on_demand_routes',routes,input_path)
    return records,list(sources.values())


def render(project, directory, view, input_path, task_id, input_hash):
    base=Path(project).resolve();root=Path(directory).resolve()
    require(root.is_relative_to(base),'task_view_path','阅读视图只能写入当前项目派生目录')
    records,sources=units(project,view,input_path)
    intro='# 当前任务必读资料\n\n这是完整记录的派生阅读视图；与 input.json 及对应必读分片二选一。'
    intro+='正文按原文件阅读；同一 Worker 可跳过已读且 value_hash 未变的记录，新 Worker 重新读取。\n\n'
    pages=[];body=intro;size=len(body.encode('utf-8'));manifest=[]
    for index,record in enumerate(records):
        # Version labels pay for themselves on substantive records, not on
        # tiny changing metadata. Every full hash remains in the manifest.
        version=('value_hash: '+record['value_hash']+'\n\n') if len(dump(record['value']).encode('utf-8'))>=VERSION_LABEL_BYTES else ''
        text='## '+record['field']+'\n\n'+version+'```json\n'+json.dumps(record['value'],ensure_ascii=False,indent=2,allow_nan=False)+'\n```\n\n'
        count=len(text.encode('utf-8'))
        if manifest and size+count>PAGE_BYTES:
            pages.append(body);body=intro;size=len(body.encode('utf-8'))
        manifest.append({k:v for k,v in record.items() if k!='value'}|{'page':len(pages),'index':index})
        body+=text;size+=count
    pages.append(body)
    routes=[]
    for index,text in enumerate(pages):
        path=root/('reading.md' if index==0 else f'reading-{index+1}.md')
        path.write_bytes(text.encode('utf-8'))
        routes.append({'path':path.relative_to(base).as_posix(),'sha256':hashlib.sha256(text.encode('utf-8')).hexdigest(),
                       'bytes':len(text.encode('utf-8'))})
    value={'schema':PROTOCOL,'task_id':task_id,'input_hash':input_hash,'pages':routes,
           'records':manifest,'sources':sources,'source_read_bytes':sum(row['bytes'] for row in sources)}
    file=root/'reading.manifest.json';write_json(file,value)
    return {'protocol':PROTOCOL,'manifest_path':file.relative_to(base).as_posix(),'pages':routes,
            'required_read_bytes':sum(row['bytes'] for row in routes),
            'source_read_bytes':value['source_read_bytes']}


def verify(project, manifest):
    """Verify derived pages and their sources without treating them as canon."""
    base=Path(project).resolve()
    require(manifest.get('schema')==PROTOCOL,'task_view','阅读视图协议无效')
    for row in manifest['pages']+manifest['sources']:
        raw=_path(base,row['path']).read_bytes()
        require(hashlib.sha256(raw).hexdigest()==row['sha256'],'task_view_integrity','阅读视图或来源文件已变化',{'path':row['path']})
    return True


def read_bundle(project, manifest):
    """One checked call delivers every required record, without a second copy.

    This is an alternative to pages. Optional routes remain complete; no
    semantic record is shortened and first-read isolation stays at the caller.
    """
    verify(project,manifest)
    base=Path(project).resolve();values=[]
    for page in manifest['pages']:
        text=_path(base,page['path']).read_text(encoding='utf-8')
        values.extend(json.loads(value) for value in re.findall(r'```json\n(.*?)\n```',text,re.S))
    records=manifest['records']
    require(len(values)==len(records) and all(digest(value)==record['value_hash'] for value,record in zip(values,records)),
            'task_view_integrity','阅读记录与冻结 manifest 不一致；重建 task-view 后再读')
    return {'schema':BUNDLE_PROTOCOL,'task_id':manifest['task_id'],'input_hash':manifest['input_hash'],
            'record_count':len(records),'complete':True,
            'records':[{'field':record['field'],'value':value,
                        **({'value_hash':record['value_hash']} if len(dump(value).encode('utf-8'))>=VERSION_LABEL_BYTES else {})}
                       for record,value in zip(records,values)]}
