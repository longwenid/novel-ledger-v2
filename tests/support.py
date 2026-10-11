import copy
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from novel_core.store import initialize,connect,get,put,transaction,dump
from novel_core import engine


def fixture_first_read():
    # Engineering fixture only: it makes no claim about literary quality.
    return {'desire':'交收据','obstacle':'不信','change':'认可','read_on_reason':'后果'}


def fixture_seal(task):
    from novel_core import reviewflow
    db=connect(task['project'])
    try:
        if not get(db,reviewflow.key(task['task_id'])):
            receipt={'task_id':task['task_id'],'input_hash':task['input_hash'],
                     'prose_hash':task['first_read_hash'],'first_read':fixture_first_read()}
            reviewflow.seal(db,task['project'],task['task_id'],receipt)
    finally:db.close()


def fixture_obligations(work,category,prose,n):
    return [{'id':row['id'],'result':'pass','reason':'工程夹具，不代表文学质量验证。',
             'evidence':[ev(prose,n)],'challenge':{'disposition':'satisfied',
             'reason':'合成正文无此工程约束冲突。','evidence':ev(prose,n)}}
            for row in work.get('review_obligations',[]) if row['category']==category]


def contract():
    return {'chapter_batch_size':1,'context_mode':'legacy','review_chunk':1,'title':'账本测试','reader_promise':'选择有代价的追索','protagonist_drive':'查清旧案','world_rules':['不能无代价制造资源'],
            'ending':'主角交付证据','arcs':['由独行变为信任伙伴'],'volumes':[{'id':'v1','purpose':'追索并兑现'}], 'word_range':[1,100000],
            'entities':[{'id':'hero','name':'周行','kind':'person','aliases':['老周']},{'id':'ally','name':'宋禾','kind':'person'}],
            'initial_resources':[{'holder':'hero','asset':'cash','unit':'文','quantity':'100'}]}


def ev(text,n=1,quote=None):
    quote=quote or text
    return {'chapter':n,'quote':quote,'start':text.index(quote)}


def rpath(task, key='input_path'):
    """Envelope routes are project-relative; join with the task's project base."""
    if key=='input_path' and task.get('delivery_phase')=='first_read':fixture_seal(task)
    p=Path(task[key])
    return p if p.is_absolute() else Path(task['project'])/p


def envelope(task):
    """Read the complete envelope the way a worker does.

    `input.json` is an index: fields above the delivery threshold live in
    per-type files next to it (`input_files`), and the index says which
    material is required reading. A caller that wants the whole object must put
    those fields back, exactly as the worker-facing rule describes.
    """
    work=json.loads(rpath(task).read_text(encoding='utf-8'))
    base=rpath(task).parent
    for field,pointer in (work.get('input_files') or {}).items():
        work[field]=json.loads((Path(task['project'])/pointer['path']).read_text(encoding='utf-8'))
    return work


def pack_prose(base, pack, key='prose'):
    """Read delivered narrative prose from its content-addressed file."""
    return (Path(base)/pack['payload'][key+'_path']).read_text(encoding='utf-8')


def restore_material(project, work, field):
    """Read an addressed field back, the way a worker does.

    `shared_materials[field]` is either one file or a base part plus pointered
    parts; the pointer=[] part is the base object and the rest are filled back
    along their paths, which together equal the field's complete value.
    """
    material = work['shared_materials'][field]
    parts = material.get('parts')
    if not parts:
        return read_material(project, material)
    base = None
    for part in parts:
        value = read_material(project, part)
        if part['pointer'] == []:
            base = value
    assert base is not None, f'{field} 缺少 pointer=[] 的基础分片'
    for part in parts:
        if part['pointer'] == []:
            continue
        cursor = base
        for key in part['pointer'][:-1]:
            cursor = cursor[key]
        cursor[part['pointer'][-1]] = read_material(project, part)
    return base


def read_material(project, material):
    return json.loads((Path(project)/material['path']).read_text(encoding='utf-8'))


def result(task,data,**extra):
    data=copy.deepcopy(data)
    if task.get('stage')=='chapter_review' and isinstance(data,dict):
        work=json.loads(rpath(task).read_text(encoding='utf-8'));prose=pack_prose(task['project'],work)
        for category in ('facts','reader'):
            if isinstance(data.get(category),dict):
                data[category].setdefault('obligations',fixture_obligations(work,category,prose,task['chapter']))
    return {'schema':'novel-ledger-v2.result/1','task_id':task['task_id'],'input_hash':task['input_hash'],'data':data,**extra}


def plan(n,ending=False):
    return {'number':n,'goal':'交出凭证','conflict':'对方不信','change':'让证人站出来','volume':'v1','entities':['hero','ally'], 'volume_end':ending,'book_end':ending}


def review(checks,prose,n=1):
    return {'verdict':'pass','checks':[{'id':k,'result':'pass','reason':'这是测试审稿回执，不是文学质量证据。','evidence':[ev(prose,n)]} for k in checks], 'findings':[]}


def combined_review(work,prose,n=1,events=None,summary='周行交付收据，宋禾确认。',**extra):
    facts=review(work['payload']['facts_checks'],prose,n)
    facts.update(events=events or [],summary=summary,**extra)
    reader=review(work['payload']['reader_checks'],prose,n)
    reader['first_read']=fixture_first_read()
    return {'facts':facts,'reader':reader}


def submit_review(db,task,data):
    """Test convenience: customize one category in a complete combined receipt."""
    if task['stage']=='chapter_review' and 'facts' not in data and 'reader' not in data:
        work=json.loads(rpath(task).read_text(encoding='utf-8'))
        prose=pack_prose(task['project'],work)
        receipt=combined_review(work,prose,task['chapter'])
        checks={check['id'] for check in data.get('checks',[])}
        category='reader' if 'first_read' in data or checks & set(work['payload']['reader_checks']) else 'facts'
        receipt[category]=data
        receipt[category].setdefault('obligations',fixture_obligations(work,category,prose,task['chapter']))
        data=receipt
    return engine.submit(db,result(task,data))


def respond(db,project,task,prose=None,events=None,ending=False):
    stage=task['stage']; n=task['chapter']
    pack=json.loads(rpath(task).read_text(encoding='utf-8'))
    if stage=='plan': data={'chapters':[plan(n,ending)]}
    elif stage in ('draft','repair','redesign'):
        text=prose or f'第{n}章，周行把收据交给宋禾。她看了许久，终于按下指印。'
        data={'prose':text,'repair_note':'修正具体矛盾','design_change':'改为人物主动做决定'}
    elif stage=='chapter_review':
        text=pack_prose(project,pack)
        data=combined_review(pack,text,n,events)
    else:
        payload=pack['payload']; first=payload['first'];through=payload['through']
        source=db.execute('SELECT prose FROM chapters WHERE branch=? AND number=?',(task['branch'],first)).fetchone()[0]
        data=review(payload['checks'],source,first)
        data['memory']={'text':'人物交出凭证并承担代价。','sources':[ev(source,first)]}
    return engine.submit(db,result(task,data))


def drive(db,project,count=1):
    for _ in range(300):
        task=engine.next_task(db,project)
        if task.get('stage')=='await_author': engine.approve(db);continue
        if task.get('stage')=='revision_promoted':continue
        if not task.get('task_id'):return task
        respond(db,project,task,ending=task['chapter']==count)
    raise AssertionError('state machine did not stop')
