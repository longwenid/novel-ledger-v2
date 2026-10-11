"""Transactional storage. Only this package promotes candidate prose to canon."""
from __future__ import annotations
import contextlib
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

VERSION = '1.39.0'
SCHEMA = 'novel-ledger-v2/2'


def validate_style(style):
    """Author-declared voice anchor: named voice plus traits and prose samples."""
    require(isinstance(style, dict), 'style', 'style 为 {"voice":"...","traits":[...],"sample":"..."} 对象')
    voice = style.get('voice', '')
    require(isinstance(voice, str), 'style', 'voice 为声线名字符串')
    traits = style.get('traits', [])
    require(isinstance(traits, list) and all(isinstance(t, str) and t.strip() for t in traits), 'style', 'traits 为非空字符串数组')
    sample = style.get('sample', '')
    close = style.get('sample_close', '')
    require(isinstance(sample, str) and isinstance(close, str),
            'style', 'sample/sample_close 为完整文风样本字符串')
    require(sample.strip() or traits, 'style', '需要 sample 或 traits 至少其一')
    return {'voice': voice, 'traits': traits, 'sample': sample, 'sample_close': close}

class Error(Exception):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code, self.details = code, details


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256((value if isinstance(value, str) else dump(value)).encode()).hexdigest()


# details may be a zero-arg callable: built only on failure, so success paths stay free.
def require(ok, code, message, details=None):
    if not ok:
        raise Error(code, message, details() if callable(details) else details)


def write_json(path, value, compact=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f'.{os.getpid()}.tmp')
    with temp.open('w', encoding='utf-8') as out:
        out.write((dump(value) if compact else json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)) + '\n')
        out.flush()
        os.fsync(out.fileno())
    os.replace(temp, path)


def connect(project):
    path = Path(project).resolve() / 'novel.sqlite3'
    require(path.exists(), 'not_initialized', f'项目尚未初始化：{path}')
    db = sqlite3.connect(path, timeout=30, isolation_level=None)
    db.row_factory = sqlite3.Row
    try:
        require(get(db, 'schema') == SCHEMA, 'schema', '本版本仅支持新项目数据库 novel-ledger-v2/2，不兼容旧项目')
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA synchronous=FULL')
        ensure_derived_schema(db)
    except BaseException:
        db.close()
        raise
    return db


def ensure_derived_schema(db):
    from . import dependencies, projections, sources
    projections.ensure_schema(db)
    dependencies.ensure_schema(db)
    sources.ensure_schema(db)


@contextlib.contextmanager
def transaction(db, rollback=False):
    db.execute('BEGIN IMMEDIATE')
    try:
        yield
        db.execute('ROLLBACK' if rollback else 'COMMIT')
    except BaseException:
        db.execute('ROLLBACK')
        raise


def get(db, key, default=None):
    row = db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else default


def put(db, key, value):
    db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, dump(value)))


def initialize(project, contract):
    from .ledger import seed
    required = ('title', 'reader_promise', 'protagonist_drive', 'world_rules', 'ending', 'arcs', 'volumes', 'word_range')
    require(isinstance(contract, dict) and all(contract.get(k) for k in required), 'contract', '缺少创作约定必填项')
    band = contract['word_range']
    require(isinstance(band, list) and len(band) == 2 and all(type(v) is int for v in band) and 0 < band[0] <= band[1], 'contract', 'word_range 必须是正整数上下限')
    require(isinstance(contract['volumes'], list) and all(isinstance(v, dict) and v.get('id') and v.get('purpose') for v in contract['volumes']), 'contract', '每卷需要 id 和 purpose')
    require(len({v['id'] for v in contract['volumes']}) == len(contract['volumes']), 'contract', '卷 ID 重复')
    from .budget import validate as validate_budget
    validate_budget(contract)
    require(isinstance(contract.get("entities"), list) and bool(contract["entities"]), "contract", "至少需要一个初始人物实体，才能生成首章计划")
    require(contract.get('planning_mode','legacy') in ('legacy','layered'),'contract','planning_mode 为 layered 或 legacy')
    if contract.get('planning_mode')=='layered':
        require(all(isinstance(v['id'],str) and v['id']!='world' for v in contract['volumes']),'contract','分层模式卷 ID 必须为字符串，world 保留给全书设计')
    require(contract.get('character_mode','legacy') in ('legacy','profiled'),'contract','character_mode 为 profiled 或 legacy')
    require(contract.get('character_views','full') in ('full','scoped'),'contract','character_views 为 full/scoped')
    require(contract.get('context_mode','bounded') in ('bounded','legacy'),'contract','context_mode 为 bounded/legacy')
    require(type(contract.get('chapter_batch_size',3)) is int and 1<=contract.get('chapter_batch_size',3)<=5,'contract','chapter_batch_size 为 1~5')
    require(type(contract.get('writer_batch',10)) is int and 1<=contract.get('writer_batch',10)<=10,'contract','writer_batch 为 1~10 章的执笔会话上限')
    require(type(contract.get('review_chunk',3)) is int and 1<=contract.get('review_chunk',3)<=5,'contract','review_chunk 为 1~5 章的跨章复审粒度')
    style_anchor = validate_style(contract['style']) | {'source': 'author'} if isinstance(contract.get('style'), dict) else None
    seed(contract)
    project = Path(project).resolve()
    project.mkdir(parents=True, exist_ok=True)
    path = project / 'novel.sqlite3'
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    except FileExistsError:
        raise Error('exists', '项目数据库已存在，不覆盖')
    db = sqlite3.connect(path, isolation_level=None)
    db.row_factory = sqlite3.Row
    try:
        db.executescript('''
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=FULL;
        CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE branches(id TEXT PRIMARY KEY, parent TEXT, start INTEGER, through INTEGER, base_hash TEXT, reason TEXT, status TEXT);
        CREATE TABLE chapters(branch TEXT, number INTEGER, prose TEXT, data TEXT, hash TEXT, PRIMARY KEY(branch,number));
        CREATE TABLE drafts(branch TEXT, number INTEGER, data TEXT, PRIMARY KEY(branch,number));
        CREATE TABLE plans(branch TEXT, number INTEGER, data TEXT, PRIMARY KEY(branch,number));
        CREATE TABLE events(branch TEXT, chapter INTEGER, idx INTEGER, data TEXT, prev_hash TEXT, hash TEXT, PRIMARY KEY(branch,chapter,idx));
        CREATE TABLE tasks(id TEXT PRIMARY KEY, branch TEXT, stage TEXT, chapter INTEGER, input_hash TEXT, input TEXT, state TEXT, result_hash TEXT, receipt TEXT, lease_owner TEXT, lease_until REAL);
        CREATE UNIQUE INDEX one_pending ON tasks(state) WHERE state='pending';
        CREATE TABLE reviews(branch TEXT, scope TEXT, through INTEGER, input_hash TEXT, data TEXT, PRIMARY KEY(branch,scope,through));
        CREATE TABLE memories(branch TEXT, scope TEXT, through INTEGER, data TEXT, PRIMARY KEY(branch,scope,through));
        CREATE TABLE sessions(id TEXT PRIMARY KEY, task TEXT);
        CREATE TABLE logs(id INTEGER PRIMARY KEY, created REAL, action TEXT, data TEXT);
        ''')
        with transaction(db):
            for k, v in {'schema': SCHEMA, 'version': VERSION, 'contract': contract, 'active_branch': 'main', 'work_branch': 'main', 'status': 'ready', 'pilot_approved': False, 'context_chars': 48000, 'context_mode': contract.get('context_mode','bounded'), 'writer_batch': contract.get('writer_batch',10), 'chapter_batch_size':contract.get('chapter_batch_size',3), 'review_chunk': contract.get('review_chunk',3), 'seq': 0}.items():
                put(db, k, v)
            if style_anchor is not None:
                put(db, 'style_anchor', style_anchor)
            policy=contract.get('preparation_policy','adaptive')
            require(policy in ('adaptive','semantic'),'config','preparation_policy 为 adaptive/semantic')
            put(db,'preparation_policy',policy)
            db.execute('INSERT INTO branches VALUES (?,?,?,?,?,?,?)', ('main', None, 1, None, None, 'initial', 'active'))
            ensure_derived_schema(db)
        return {'project': str(project), 'schema': SCHEMA, 'status': 'ready'}
    except BaseException:
        db.close()
        # Keep a failed initialization for diagnosis; never delete an existing book.
        raise
    finally:
        db.close()


def log(db, action, data):
    db.execute('INSERT INTO logs(created,action,data) VALUES (?,?,?)', (time.time(), action, dump(data)))


def chapters(db, branch):
    return [dict(row) for row in db.execute('SELECT * FROM chapters WHERE branch=? ORDER BY number', (branch,))]


def head(db, branch):
    return db.execute('SELECT COALESCE(MAX(number),0) FROM chapters WHERE branch=?', (branch,)).fetchone()[0]


def branch_hash(db, branch):
    return digest([(r['number'], r['hash']) for r in chapters(db, branch)])
