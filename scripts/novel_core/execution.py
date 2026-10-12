"""Single authority for chapter execution, distinct from planning/review windows."""
import json
import os
import re
import sqlite3
import uuid
from pathlib import Path
from .store import get, put, require, transaction, VERSION, log

PROTOCOL='chapter-execution/1'
SINGLE='single_chapter'
BATCH='chapter_batch'
MODES=(SINGLE,)
DEFAULT_MODE=SINGLE
LEGACY_OPTIONS={'chapter_batch_size':3,'writer_batch':10,'writer_session_policy':'batch'}
SINGLE_OPTIONS={'chapter_batch_size':1,'writer_batch':1,'writer_session_policy':'chapter'}
WRITER_STAGES=('draft','repair','redesign')


def initial_mode(contract):
    # Old contract knobs are input compatibility, never a new runtime mode.
    require(contract.get('execution_mode',SINGLE) in (SINGLE,BATCH),'execution_mode','未知旧执行模式')
    return SINGLE


def finish_upgrade(db):
    """Drain one frozen old task, then archive batches without losing drafts."""
    migration=get(db,'execution_upgrade')
    if not migration or migration['state']=='complete' or db.execute("SELECT 1 FROM tasks WHERE state='pending'").fetchone():
        return
    archived=[]
    for row in db.execute("SELECT key,value FROM meta WHERE key LIKE 'chapter_batch:%'").fetchall():
        batch=json.loads(row['value'])
        if not batch:continue
        put(db,'chapter_batch_archive:'+batch['id'],{**batch,'retired_by':'single_chapter_upgrade'})
        put(db,row['key'],None);archived.append(batch['id'])
    intent=get(db,'controller_intent',{})
    for key in ('writer_session_id','handled_chapters','writer_branch','writer_volume','writer_task_id'):
        intent.pop(key,None)
    put(db,'controller_intent',intent)
    migration.update(state='complete',archived_batches=archived)
    put(db,'execution_upgrade',migration)
    log(db,'execution_upgrade_complete',{'archived_batches':archived,'drafts':'preserved for independent chapter review'})


def upgrade(db,project):
    """Atomic, backed-up v2 upgrade; never rewrite a frozen pending input."""
    with transaction(db):
        old_version=get(db,'version')
        if isinstance(old_version,str) and re.fullmatch(r'\d+\.\d+\.\d+',old_version):
            require(tuple(map(int,old_version.split('.')))<=tuple(map(int,VERSION.split('.'))),
                    'newer_version','项目来自更高版本，不能自动降级')
        migration=get(db,'execution_upgrade')
        if get(db,'version')!=VERSION or get(db,'execution_mode')!=SINGLE:
            previous={'version':get(db,'version'),'mode':get(db,'execution_mode',BATCH),
                      **{key:get(db,key,value) for key,value in LEGACY_OPTIONS.items()}}
            folder=Path(project).resolve()/'work/migrations';folder.mkdir(parents=True,exist_ok=True)
            identifier=uuid.uuid4().hex
            target=folder/('before-'+VERSION+'-'+identifier[:8]+'.sqlite3')
            temporary=target.with_suffix('.tmp')
            try:
                source=sqlite3.connect((Path(project).resolve()/'novel.sqlite3').as_uri()+'?mode=ro',uri=True)
                try:
                    backup=sqlite3.connect(temporary)
                    try:source.backup(backup)
                    finally:backup.close()
                finally:source.close()
                os.chmod(temporary,0o600)
                os.replace(temporary,target)
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
            migration={'version':VERSION,'state':'draining','previous':previous,'backup_path':str(target),
                       'boundary':'current frozen task may finish; all subsequent primary workers are per chapter'}
            put(db,'execution_upgrade',migration)
            put(db,'execution_mode',SINGLE)
            for key,value in SINGLE_OPTIONS.items():put(db,key,value)
            put(db,'version',VERSION)
            log(db,'execution_upgrade_started',migration)
        finish_upgrade(db)


def settings(db):
    # Missing mode means a pre-upgrade project, not permission to reinterpret it.
    mode=get(db,'execution_mode',BATCH)
    require(mode in (SINGLE,BATCH),'execution_mode','未知章节执行模式，不能猜测恢复路径')
    options=SINGLE_OPTIONS if mode==SINGLE else {key:get(db,key,value) for key,value in LEGACY_OPTIONS.items()}
    return {'execution_mode':mode,**options}


def freeze(db,work,task_id):
    config=settings(db);session=work.get('chapter_session',{})
    mode=BATCH if session else config['execution_mode']
    stage=work['stage'];primary=stage in WRITER_STAGES or stage=='chapter_review'
    role='writer' if stage in WRITER_STAGES else 'combined_reviewer' if stage=='chapter_review' else stage
    return {'protocol':PROTOCOL,'mode':mode,'role':role,
            'scope':{'task_id':task_id,'branch':work['branch'],'chapter':work['chapter']},
            'worker_lifetime':'task' if primary and mode==SINGLE else 'bound_session' if session or work.get('review_session') or work.get('preparation_session') else 'task',
            'continuation_allowed':not (primary and mode==SINGLE),
            'new_worker_required':primary and mode==SINGLE,
            'handoff_owner':'controller' if primary and mode==SINGLE else 'bound_session_or_controller',
            'review_order':['prose_first_read','freeze_first_read','facts_and_reader'] if stage=='chapter_review' else [],
            'max_unreviewed_chapters':1 if mode==SINGLE else config['chapter_batch_size'],
            'writer_session_policy':work.get('writer_session_policy',config['writer_session_policy']),
            'reviewer_session_policy':'chapter' if mode==SINGLE else 'batch',
            'host_history_verified':False}


def is_single(work):
    policy=work.get('execution_policy',{})
    return policy.get('protocol')==PROTOCOL and policy.get('mode')==SINGLE


def controller_owned(work):
    return is_single(work) and work.get('stage') in WRITER_STAGES+('chapter_review',)


def bind_session(db, work, session):
    """Reject known cross-task reuse; absent host identity proves nothing."""
    if not controller_owned(work) or session is None:
        return
    if isinstance(session,dict):session=session.get('id')
    require(isinstance(session,str) and session.strip(),'worker_session','宿主会话 ID 须为非空字符串')
    task_id=work['execution_policy']['scope']['task_id']
    prior=db.execute('SELECT task FROM sessions WHERE id=?',(session,)).fetchone()
    require(not prior or prior['task']==task_id,'worker_session',
            '单章执行不能跨任务复用同一宿主会话；首读解锁仅续接同一审稿任务')
    if work['stage']=='chapter_review':
        reviewer=db.execute('SELECT id FROM sessions WHERE task=?',(task_id,)).fetchone()
        require(not reviewer or reviewer['id']==session,'worker_session',
                '首读和综合审稿须由同一已绑定审稿会话完成；原会话无法恢复时取消任务后重新首读')
    db.execute('INSERT OR IGNORE INTO sessions(id,task) VALUES(?,?)',(session,task_id))
