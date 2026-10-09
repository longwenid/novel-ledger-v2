"""Rebuildable local indexes; canonical rows remain the only source of truth.

Triggers record source mutations inside the same transaction. Per-row source
hashes and generations are provenance, not a replacement for ledger audit.
"""
from __future__ import annotations
import json
import contextlib
from .store import digest, dump, require

PROJECTION_VERSION = 2


def ensure_schema(db):
    """Install projections without executescript or an implicit transaction commit."""
    db.create_function('py_casefold_contains', 2,
                       lambda text, query: int(str(query).casefold() in str(text).casefold()),
                       deterministic=True)
    tables = [
        'CREATE TABLE IF NOT EXISTS local_generations(branch TEXT PRIMARY KEY,event_generation INTEGER NOT NULL DEFAULT 0,invalid_generation INTEGER NOT NULL DEFAULT 0,chapter_generation INTEGER NOT NULL DEFAULT 0,plan_generation INTEGER NOT NULL DEFAULT 0)',
        'CREATE TABLE IF NOT EXISTS local_event_dirty(branch TEXT,chapter INTEGER,idx INTEGER,PRIMARY KEY(branch,chapter,idx))',
        'CREATE TABLE IF NOT EXISTS local_event_rows(branch TEXT,chapter INTEGER,idx INTEGER,source_hash TEXT,event_hash TEXT,search_data TEXT,PRIMARY KEY(branch,chapter,idx))',
        'CREATE TABLE IF NOT EXISTS local_event_entities(branch TEXT,chapter INTEGER,idx INTEGER,entity TEXT,PRIMARY KEY(branch,chapter,idx,entity))',
        'CREATE INDEX IF NOT EXISTS local_entity_lookup ON local_event_entities(branch,entity,chapter,idx)',
        'CREATE TABLE IF NOT EXISTS local_event_topics(branch TEXT,chapter INTEGER,idx INTEGER,topic TEXT,PRIMARY KEY(branch,chapter,idx,topic))',
        'CREATE INDEX IF NOT EXISTS local_topic_lookup ON local_event_topics(branch,topic,chapter,idx)',
        'CREATE TABLE IF NOT EXISTS local_event_facts(branch TEXT,chapter INTEGER,idx INTEGER,fact TEXT,PRIMARY KEY(branch,chapter,idx,fact))',
        'CREATE INDEX IF NOT EXISTS local_fact_lookup ON local_event_facts(branch,fact,chapter,idx)',
        'CREATE TABLE IF NOT EXISTS local_projection_versions(branch TEXT,kind TEXT,generation INTEGER,version INTEGER,PRIMARY KEY(branch,kind))',
        'CREATE TABLE IF NOT EXISTS local_state_snapshots(branch TEXT PRIMARY KEY,version TEXT,contract_hash TEXT,event_generation INTEGER,invalid_generation INTEGER,chapter INTEGER,idx INTEGER,tail_hash TEXT,source_hash TEXT,state TEXT,checksum TEXT)',
        'CREATE TABLE IF NOT EXISTS local_budget_dirty(branch TEXT,chapter INTEGER,PRIMARY KEY(branch,chapter))',
        'CREATE TABLE IF NOT EXISTS local_task_metrics(id TEXT PRIMARY KEY,input_chars INTEGER,receipt_chars INTEGER,preparation_chars INTEGER)',
        'CREATE TABLE IF NOT EXISTS local_usage_summary(id INTEGER PRIMARY KEY CHECK(id=1),tasks INTEGER,inputs INTEGER,receipts INTEGER,preparation INTEGER)',
        'CREATE TABLE IF NOT EXISTS local_chapter_counts(branch TEXT,chapter INTEGER,source_hash TEXT,plan_hash TEXT,volume TEXT,han INTEGER,closed INTEGER,PRIMARY KEY(branch,chapter))',
    ]
    existed = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='local_generations'").fetchone()
    for sql in tables:
        db.execute(sql)
    triggers = {}
    for table in ('events','chapters','plans'):
        for op in ('INSERT','UPDATE','DELETE'):
            name = f'local_{table}_{op.lower()}'
            owners = ['NEW'] if op == 'INSERT' else ['OLD'] if op == 'DELETE' else ['OLD','NEW']
            statements = []
            for owner in owners:
                branch = f'{owner}.branch'
                statements.append(f'INSERT OR IGNORE INTO local_generations(branch) VALUES ({branch});')
                if table == 'events':
                    invalid = '1' if op != 'INSERT' else f'CASE WHEN EXISTS(SELECT 1 FROM events WHERE branch={branch} AND (chapter>NEW.chapter OR (chapter=NEW.chapter AND idx>NEW.idx))) THEN 1 ELSE 0 END'
                    statements.append(f'UPDATE local_generations SET event_generation=event_generation+1,invalid_generation=invalid_generation+{invalid} WHERE branch={branch};')
                    statements.append(f'INSERT OR IGNORE INTO local_event_dirty VALUES ({branch},{owner}.chapter,{owner}.idx);')
                elif table == 'chapters':
                    invalid = '1' if op == 'DELETE' else '(CASE WHEN OLD.prose IS NOT NEW.prose OR OLD.hash IS NOT NEW.hash OR OLD.branch IS NOT NEW.branch OR OLD.number IS NOT NEW.number THEN 1 ELSE 0 END)' if op == 'UPDATE' else '0'
                    statements.append(f'UPDATE local_generations SET chapter_generation=chapter_generation+1,invalid_generation=invalid_generation+{invalid} WHERE branch={branch};')
                    statements.append(f'INSERT OR IGNORE INTO local_budget_dirty VALUES ({branch},{owner}.number);')
                else:
                    statements.append(f'UPDATE local_generations SET plan_generation=plan_generation+1 WHERE branch={branch};')
                    statements.append(f'INSERT OR IGNORE INTO local_budget_dirty VALUES ({branch},{owner}.number);')
            triggers[name] = f'CREATE TRIGGER IF NOT EXISTS {name} AFTER {op} ON {table} BEGIN ' + ' '.join(statements) + ' END'
    # REPLACE deletes do not run DELETE triggers with SQLite's default
    # recursive_triggers=OFF. Detect canonical coordinate replacement before
    # it disappears, and use unique task metric rows to avoid double counting.
    for table, coordinate in (('events','chapter'),('chapters','number')):
        extra = ' AND idx=NEW.idx' if table=='events' else ''
        name = f'local_{table}_replace'
        triggers[name] = f"CREATE TRIGGER IF NOT EXISTS {name} BEFORE INSERT ON {table} WHEN EXISTS(SELECT 1 FROM {table} WHERE branch=NEW.branch AND {coordinate}=NEW.{coordinate}{extra}) BEGIN INSERT OR IGNORE INTO local_generations(branch) VALUES (NEW.branch); UPDATE local_generations SET invalid_generation=invalid_generation+1 WHERE branch=NEW.branch; END"
    stage_new = "NEW.stage IN ('context_scan','context_reduce')"
    length_new = '(LENGTH(NEW.input)+1)'
    receipt_new = '(CASE WHEN NEW.receipt IS NULL THEN 0 ELSE LENGTH(NEW.receipt)+1 END)'
    subtract = "UPDATE local_usage_summary SET inputs=inputs-COALESCE((SELECT input_chars FROM local_task_metrics WHERE id={id}),0),receipts=receipts-COALESCE((SELECT receipt_chars FROM local_task_metrics WHERE id={id}),0),preparation=preparation-COALESCE((SELECT preparation_chars FROM local_task_metrics WHERE id={id}),0){count} WHERE id=1;"
    add = "UPDATE local_usage_summary SET inputs=inputs+(SELECT input_chars FROM local_task_metrics WHERE id=NEW.id),receipts=receipts+(SELECT receipt_chars FROM local_task_metrics WHERE id=NEW.id),preparation=preparation+(SELECT preparation_chars FROM local_task_metrics WHERE id=NEW.id){count} WHERE id=1;"
    insert_metric = f"INSERT OR REPLACE INTO local_task_metrics VALUES (NEW.id,{length_new},{receipt_new},CASE WHEN {stage_new} THEN {length_new} ELSE 0 END);"
    triggers['local_tasks_insert'] = 'CREATE TRIGGER IF NOT EXISTS local_tasks_insert AFTER INSERT ON tasks BEGIN '+subtract.format(id='NEW.id',count=',tasks=tasks-(SELECT COUNT(*) FROM local_task_metrics WHERE id=NEW.id)')+insert_metric+add.format(count=',tasks=tasks+1')+' END'
    triggers['local_tasks_delete'] = 'CREATE TRIGGER IF NOT EXISTS local_tasks_delete AFTER DELETE ON tasks BEGIN '+subtract.format(id='OLD.id',count=',tasks=tasks-(SELECT COUNT(*) FROM local_task_metrics WHERE id=OLD.id)')+'DELETE FROM local_task_metrics WHERE id=OLD.id; END'
    changed_metric = f"UPDATE local_task_metrics SET id=NEW.id,input_chars=CASE WHEN OLD.input IS NEW.input THEN input_chars ELSE {length_new} END,receipt_chars=CASE WHEN OLD.receipt IS NEW.receipt THEN receipt_chars ELSE {receipt_new} END,preparation_chars=CASE WHEN OLD.input IS NEW.input AND OLD.stage IS NEW.stage THEN preparation_chars ELSE CASE WHEN {stage_new} THEN {length_new} ELSE 0 END END WHERE id=OLD.id;"
    triggers['local_tasks_update'] = 'CREATE TRIGGER IF NOT EXISTS local_tasks_update AFTER UPDATE OF id,input,receipt,stage ON tasks WHEN OLD.id IS NOT NEW.id OR OLD.input IS NOT NEW.input OR OLD.receipt IS NOT NEW.receipt OR OLD.stage IS NOT NEW.stage BEGIN '+subtract.format(id='OLD.id',count='')+changed_metric+add.format(count='')+' END'
    for table, kind in (('local_event_rows','events'),('local_event_entities','events'),('local_event_topics','events'),('local_chapter_counts','budget')):
        for op in ('INSERT','UPDATE','DELETE'):
            name = f'{table}_corruption_{op.lower()}'
            owners = ['NEW'] if op=='INSERT' else ['OLD'] if op=='DELETE' else ['OLD','NEW']
            statements = ' '.join(f"DELETE FROM local_projection_versions WHERE branch={owner}.branch AND kind='{kind}';" for owner in owners)
            triggers[name] = f'CREATE TRIGGER IF NOT EXISTS {name} AFTER {op} ON {table} BEGIN {statements} END'
    installed = {r['name']:r['sql'] for r in db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'")}
    def trigger_sql(sql):
        return ' '.join(sql.replace(' IF NOT EXISTS','').split())
    changed = {name for name,sql in triggers.items() if name in installed and trigger_sql(sql)!=trigger_sql(installed[name])}
    missing = (set(triggers) - set(installed)) | changed
    for name in changed:
        db.execute(f'DROP TRIGGER {name}')
    for sql in triggers.values():
        db.execute(sql)
    if existed and (missing - {'local_tasks_insert','local_tasks_delete','local_tasks_update'}):
        # A legacy or interrupted setup cannot claim to have observed all writes.
        db.execute('UPDATE local_generations SET invalid_generation=invalid_generation+1,event_generation=event_generation+1,chapter_generation=chapter_generation+1,plan_generation=plan_generation+1')
        db.execute('DELETE FROM local_projection_versions')
    if not db.execute('SELECT 1 FROM local_usage_summary WHERE id=1').fetchone() or missing.intersection({'local_tasks_insert','local_tasks_delete','local_tasks_update'}):
        db.execute('DELETE FROM local_usage_summary')
        db.execute('DELETE FROM local_task_metrics')
        db.execute("INSERT INTO local_task_metrics SELECT id,LENGTH(input)+1,CASE WHEN receipt IS NULL THEN 0 ELSE LENGTH(receipt)+1 END,CASE WHEN stage IN ('context_scan','context_reduce') THEN LENGTH(input)+1 ELSE 0 END FROM tasks")
        db.execute("INSERT INTO local_usage_summary SELECT 1,COUNT(*),COALESCE(SUM(LENGTH(input)+1),0),COALESCE(SUM(CASE WHEN receipt IS NULL THEN 0 ELSE LENGTH(receipt)+1 END),0),COALESCE(SUM(CASE WHEN stage IN ('context_scan','context_reduce') THEN LENGTH(input)+1 ELSE 0 END),0) FROM tasks")
    return {'version': PROJECTION_VERSION, 'rebuildable': True}


def generations(db, branch):
    row = db.execute('SELECT * FROM local_generations WHERE branch=?', (branch,)).fetchone()
    return dict(row) if row else {'branch':branch,'event_generation':0,'invalid_generation':0,'chapter_generation':0,'plan_generation':0}


def event_references(event):
    from .spatial import event_refs
    refs = [event.get('entity'), event.get('from'), event.get('to')] + event.get('refs', []) + event_refs(event)
    if isinstance(event.get('entity'), dict):
        refs.append(event['entity']['id'])
    return {r for r in refs if isinstance(r, str)}


def event_topics(event):
    values = [event.get(k) for k in ('topic','id','asset','key')] + event.get('topics', [])
    return {value for value in values if isinstance(value,str)}


def event_facts(event):
    """Ledger coordinates an event asserts, for revision impact lookups.

    state facts and knowledge topics use the same `entity:key` shape recall
    shows; relation events are indexed in both directions so a dyad query
    from either endpoint hits."""
    op = event.get('op')
    out = set()
    if op == 'state' and isinstance(event.get('entity'), str) and isinstance(event.get('key'), str):
        out.add(event['entity'] + ':' + event['key'])
    elif op == 'relation' and all(isinstance(event.get(k), str) and event[k] for k in ('from','to','key')):
        out.add(event['from'] + '>' + event['to'] + ':' + event['key'])
        out.add(event['to'] + '>' + event['from'] + ':' + event['key'])
    elif op == 'knowledge' and isinstance(event.get('entity'), str) and isinstance(event.get('topic'), str):
        out.add(event['entity'] + ':' + event['topic'])
    return out


def _replace_event(db, branch, chapter, idx):
    for table in ('local_event_rows','local_event_entities','local_event_topics','local_event_facts'):
        db.execute(f'DELETE FROM {table} WHERE branch=? AND chapter=? AND idx=?', (branch, chapter, idx))
    row = db.execute('SELECT data,hash FROM events WHERE branch=? AND chapter=? AND idx=?', (branch,chapter,idx)).fetchone()
    if not row:
        return
    event = json.loads(row['data'])
    db.execute('INSERT INTO local_event_rows VALUES (?,?,?,?,?,?)', (branch,chapter,idx,digest(row['data']),row['hash'],dump(event)))
    db.executemany('INSERT INTO local_event_entities VALUES (?,?,?,?)', [(branch,chapter,idx,r) for r in sorted(event_references(event))])
    db.executemany('INSERT INTO local_event_topics VALUES (?,?,?,?)', [(branch,chapter,idx,r) for r in sorted(event_topics(event))])
    db.executemany('INSERT INTO local_event_facts VALUES (?,?,?,?)', [(branch,chapter,idx,r) for r in sorted(event_facts(event))])


def _sync_events(db, branch):
    generation = generations(db,branch)['event_generation']
    old = db.execute("SELECT generation,version FROM local_projection_versions WHERE branch=? AND kind='events'", (branch,)).fetchone()
    if old and old['generation']==generation and old['version']==PROJECTION_VERSION:
        return
    if not old or old['version']!=PROJECTION_VERSION:
        for table in ('local_event_rows','local_event_entities','local_event_topics','local_event_facts'):
            db.execute(f'DELETE FROM {table} WHERE branch=?', (branch,))
        coords = db.execute('SELECT chapter,idx FROM events WHERE branch=? ORDER BY chapter,idx', (branch,)).fetchall()
    else:
        coords = db.execute('SELECT chapter,idx FROM local_event_dirty WHERE branch=? ORDER BY chapter,idx', (branch,)).fetchall()
    for row in coords:
        _replace_event(db,branch,row['chapter'],row['idx'])
    db.execute('DELETE FROM local_event_dirty WHERE branch=?', (branch,))
    db.execute("INSERT OR REPLACE INTO local_projection_versions VALUES (?,'events',?,?)", (branch,generation,PROJECTION_VERSION))


def impacted_events(db, branch, fact):
    """Committed events asserting a fact coordinate, in chapter order.

    Exact-match lookup on the inverted fact index; relation coordinates were
    indexed in both directions, so querying `to>from:key` finds the event that
    wrote `from>to:key`. Coordinates are the keys recall --state displays."""
    sync_events(db,branch)
    rows = db.execute('SELECT f.chapter,f.idx,r.search_data FROM local_event_facts f '
                      'JOIN local_event_rows r ON r.branch=f.branch AND r.chapter=f.chapter AND r.idx=f.idx '
                      'WHERE f.branch=? AND f.fact=? ORDER BY f.chapter,f.idx', (branch,fact)).fetchall()
    return [{'chapter':r['chapter'],'idx':r['idx'],'event':json.loads(r['search_data'])} for r in rows]


def recall_events(db, branch, query='', entity=None, topic=None, start=1, end=None, offset=0, limit=20, newest=False):
    """Exact legacy filters/order, indexed count/page; caller resolves entity aliases."""
    require(0 < limit <= 100 and offset >= 0, 'pagination', 'limit 必须为 1..100，offset 非负')
    if not end:
        from .store import head
        end = head(db,branch)
    sync_events(db,branch)
    clauses = ['r.branch=?','r.chapter>=?','r.chapter<=?']
    args = [branch,start,end]
    from_clause = 'local_event_rows r'
    # Start from the selective inverted index rather than examining every
    # event for EXISTS. Each reference row is unique for this exact selector;
    # a shared event still occurs once. Other selectors retain EXISTS.
    if entity or topic:
        kind, field, value = ('entities','entity',entity) if entity else ('topics','topic',topic)
        index = 'local_entity_lookup' if entity else 'local_topic_lookup'
        from_clause = f'local_event_{kind} m INDEXED BY {index} CROSS JOIN local_event_rows r ON r.branch=m.branch AND r.chapter=m.chapter AND r.idx=m.idx'
        clauses.extend(['m.branch=?',f'm.{field}=?'])
        args.extend([branch,value])
    for kind, value in (('entities',entity),('topics',topic)):
        if value and not ((kind=='entities' and entity) or (kind=='topics' and not entity)):
            field = 'entity' if kind=='entities' else 'topic'
            clauses.append(f'EXISTS(SELECT 1 FROM local_event_{kind} x WHERE x.branch=r.branch AND x.chapter=r.chapter AND x.idx=r.idx AND x.{field}=?)')
            args.append(value)
    if query:
        clauses.append('py_casefold_contains(r.search_data,?)')
        args.append(query)
    where = ' AND '.join(clauses)
    total = db.execute('SELECT COUNT(*) FROM '+from_clause+' WHERE '+where,args).fetchone()[0]
    order = 'r.chapter DESC,r.idx DESC' if newest else 'r.chapter,r.idx'
    records=[]
    for row in db.execute('SELECT r.*,e.data,e.hash FROM '+from_clause+' JOIN events e ON e.branch=r.branch AND e.chapter=r.chapter AND e.idx=r.idx WHERE '+where+' ORDER BY '+order+' LIMIT ? OFFSET ?', args+[limit,offset]):
        require(row['source_hash']==digest(row['data']) and row['event_hash']==row['hash'], 'projection_source', '事件索引来源版本不符，请重建投影；不能把旧索引当成事实')
        records.append({'chapter':row['chapter'],'index':row['idx'],'event':json.loads(row['data']),'event_hash':row['hash']})
    return {'records':records,'total':total,'offset':offset,'next_offset':offset+limit if offset+limit<total else None,'absence_is_not_proof':True}


def _chapter_counts(db, branch):
    """Incremental chapter+plan source projection, with no author quota changes."""
    from .budget import han_count
    gen = generations(db,branch)
    generation = dump([gen['chapter_generation'],gen['plan_generation']])
    old = db.execute("SELECT generation,version FROM local_projection_versions WHERE branch=? AND kind='budget'", (branch,)).fetchone()
    # No chapters means no entries and no persistence needed for an empty book.
    if not db.execute('SELECT 1 FROM chapters WHERE branch=? LIMIT 1',(branch,)).fetchone():
        return []
    if not old or old['generation']!=generation or old['version']!=PROJECTION_VERSION:
        if not old or old['version']!=PROJECTION_VERSION:
            db.execute('DELETE FROM local_chapter_counts WHERE branch=?',(branch,))
            coords = db.execute('SELECT number AS chapter FROM chapters WHERE branch=?',(branch,)).fetchall()
        else:
            coords = db.execute('SELECT chapter FROM local_budget_dirty WHERE branch=?',(branch,)).fetchall()
        for coord in coords:
            number=coord['chapter']
            db.execute('DELETE FROM local_chapter_counts WHERE branch=? AND chapter=?',(branch,number))
            row=db.execute('SELECT c.prose,c.data,c.hash,p.data AS plan FROM chapters c JOIN plans p ON p.branch=c.branch AND p.number=c.number WHERE c.branch=? AND c.number=?',(branch,number)).fetchone()
            if not row:continue
            data=json.loads(row['data']);plan=json.loads(row['plan'])
            # Count the actual changed source once; metadata is a convenience,
            # not authority when it disagrees with prose.
            count=han_count(row['prose'])
            db.execute('INSERT INTO local_chapter_counts VALUES (?,?,?,?,?,?,?)',(branch,number,digest([row['prose'],row['data'],row['hash']]),digest(row['plan']),data['volume'],count,int(bool(plan.get('volume_end')))))
        db.execute('DELETE FROM local_budget_dirty WHERE branch=?',(branch,))
        db.execute("INSERT OR REPLACE INTO local_projection_versions VALUES (?,'budget',?,?)",(branch,generation,PROJECTION_VERSION))
    return [dict(r) for r in db.execute('SELECT volume,SUM(han) AS han,MAX(closed) AS closed FROM local_chapter_counts WHERE branch=? GROUP BY volume',(branch,))]


@contextlib.contextmanager
def local_transaction(db):
    """Serialize projection refresh; never commit a surrounding canon txn."""
    outer = db.in_transaction
    db.execute('SAVEPOINT local_projection_refresh' if outer else 'BEGIN IMMEDIATE')
    try:
        yield
        db.execute('RELEASE local_projection_refresh' if outer else 'COMMIT')
    except BaseException:
        if outer:
            db.execute('ROLLBACK TO local_projection_refresh')
            db.execute('RELEASE local_projection_refresh')
        else:db.execute('ROLLBACK')
        raise


def sync_events(db, branch):
    with local_transaction(db):
        _sync_events(db,branch)


def chapter_counts(db, branch):
    with local_transaction(db):
        return _chapter_counts(db,branch)


def usage_summary(db):
    row = db.execute('SELECT tasks,inputs,receipts,preparation FROM local_usage_summary WHERE id=1').fetchone()
    return dict(row)
