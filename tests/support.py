import copy
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from novel_core.store import initialize,connect,get,put,transaction,dump
from novel_core import engine


def contract():
    return {'context_mode':'legacy','title':'账本测试','reader_promise':'选择有代价的追索','protagonist_drive':'查清旧案','world_rules':['不能无代价制造资源'],
            'ending':'主角交付证据','arcs':['由独行变为信任伙伴'],'volumes':[{'id':'v1','purpose':'追索并兑现'}], 'word_range':[1,100000],
            'entities':[{'id':'hero','name':'周行','kind':'person','aliases':['老周']},{'id':'ally','name':'宋禾','kind':'person'}],
            'initial_resources':[{'holder':'hero','asset':'cash','unit':'文','quantity':'100'}]}


def ev(text,n=1,quote=None):
    quote=quote or text
    return {'chapter':n,'quote':quote,'start':text.index(quote)}


def rpath(task, key='input_path'):
    """Envelope routes are project-relative; join with the task's project base."""
    p=Path(task[key])
    return p if p.is_absolute() else Path(task['project'])/p


def pack_prose(base, pack, key='prose'):
    """Read delivered narrative prose from its content-addressed file."""
    return (Path(base)/pack['payload'][key+'_path']).read_text(encoding='utf-8')


def result(task,data,**extra):
    return {'schema':'novel-ledger-v2.result/1','task_id':task['task_id'],'input_hash':task['input_hash'],'data':data,**extra}


def plan(n,ending=False):
    return {'number':n,'goal':'交出凭证','conflict':'对方不信','change':'让证人站出来','volume':'v1','entities':['hero','ally'], 'volume_end':ending,'book_end':ending}


def review(checks,prose,n=1):
    return {'verdict':'pass','checks':[{'id':k,'result':'pass','reason':'这是测试审稿回执，不是文学质量证据。','evidence':[ev(prose,n)]} for k in checks], 'findings':[]}


def respond(db,project,task,prose=None,events=None,ending=False):
    stage=task['stage']; n=task['chapter']
    pack=json.loads(rpath(task).read_text(encoding='utf-8'))
    if stage=='plan': data={'chapters':[plan(n,ending)]}
    elif stage in ('draft','repair','redesign'):
        text=prose or f'第{n}章，周行把收据交给宋禾。她看了许久，终于按下指印。'
        data={'prose':text,'repair_note':'修正具体矛盾','design_change':'改为人物主动做决定'}
    elif stage=='facts':
        text=pack_prose(project,pack);data=review(pack['payload']['checks'],text,n)
        data.update(events=events or [],summary='周行交付收据，宋禾确认。')
    elif stage=='reader':
        text=pack_prose(project,pack);data=review(pack['payload']['checks'],text,n)
        data['first_read']={'desire':'交收据','obstacle':'不信','change':'认可','read_on_reason':'后果'}
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
