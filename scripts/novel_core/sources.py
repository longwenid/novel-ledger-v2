"""Immutable material versions and reusable complete semantic-unit indexes.

These tables are derived caches. They never replace the original snapshots or
promote preparation notes to canon. DDL deliberately uses individual statements
so an enclosing chapter transaction is never committed by schema creation.
"""
import copy
import json
from .store import digest, dump, require

INDEX_VERSION = 'semantic-records/3'
CACHE_VERSION = 'purpose-preparation/3'


def ensure_schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS material_source_versions ('
               'source_id TEXT NOT NULL, version TEXT NOT NULL, field TEXT NOT NULL, '
               'text TEXT NOT NULL, units TEXT NOT NULL, index_version TEXT NOT NULL, '
               'PRIMARY KEY(source_id,version))')
    if 'units_hash' not in {row[1] for row in db.execute('PRAGMA table_info(material_source_versions)')}:
        db.execute('ALTER TABLE material_source_versions ADD COLUMN units_hash TEXT')
    db.execute('CREATE TABLE IF NOT EXISTS material_source_units ('
               'source_id TEXT NOT NULL, unit_id TEXT NOT NULL, unit_version TEXT NOT NULL, '
               'semantic_text TEXT NOT NULL, value_json TEXT NOT NULL, '
               'PRIMARY KEY(source_id,unit_id,unit_version))')
    db.execute('CREATE TABLE IF NOT EXISTS material_preparation_cache ('
               'cache_key TEXT PRIMARY KEY, purpose_hash TEXT NOT NULL, '
               'dependency_signature TEXT NOT NULL, payload TEXT NOT NULL, '
               'cache_version TEXT NOT NULL)')


def source_id(branch, field):
    """A logical source identity, independent of bundle order or byte offsets."""
    return 'material:' + digest({'branch': branch, 'field': field})


def _at(value, path):
    for key in path:
        value = value[key]
    return value


def _identity_path(value, path):
    """Prefer record keys to list positions; append-only anonymous lists use slots."""
    out = []
    current = value
    for key in path:
        child = current[key]
        if isinstance(key, int) and isinstance(child, dict):
            if child.get('id') is not None:
                identity = {'id': child['id']}
            elif child.get('chapter') is not None and child.get('index') is not None:
                identity = {'chapter': child['chapter'], 'index': child['index']}
            elif child.get('number') is not None:
                identity = {'number': child['number']}
            elif child.get('kind') is not None and child.get('chapter') is not None:
                identity = {'kind': child['kind'], 'chapter': child['chapter']}
            else:
                identity = {'slot': key}
            out.append({'record': identity})
        else:
            out.append(key)
        current = child
    return out


def register_document(db, identity, field, text, units_factory, value=None):
    """Register a whole source version, reusing unchanged record material.

    ``units_factory`` locates full records. It is never invoked for an existing
    immutable document version. A changed document needs one structural walk;
    unchanged units reuse their already indexed complete value, even when a
    new record shifts their source coordinates.
    """
    ensure_schema(db)
    version = digest(text)
    row = db.execute('SELECT * FROM material_source_versions WHERE source_id=? AND version=?',
                     (identity, version)).fetchone()
    if row and row['index_version'] == INDEX_VERSION and row['units_hash']==digest(row['units']):
        require(digest(row['text']) == version, 'context_integrity', '来源版本快照损坏')
        units = json.loads(row['units'])
        return {'source_id': identity, 'version': version, 'text': row['text'], 'units': units,
                'index_stats': {'version_hit': True, 'structural_walks': 0,
                                'new_units': 0, 'reused_units': len(units)}}
    value = json.loads(text) if value is None else value
    units = copy.deepcopy(units_factory(text, field, value=value))
    new = reused = 0
    occurrences = {}
    for unit in units:
        semantic = text[unit['semantic_start']:unit['semantic_end']]
        identity_path = _identity_path(value, unit['path'])
        identity_key = dump(identity_path)
        occurrence = occurrences.get(identity_key, 0)
        occurrences[identity_key] = occurrence + 1
        # Repeated declared record IDs retain separate complete units.
        uid = digest({'source_id': identity, 'path': identity_path, 'occurrence': occurrence})
        uv = digest(semantic)
        old = db.execute('SELECT semantic_text FROM material_source_units '
                         'WHERE source_id=? AND unit_id=? AND unit_version=?',
                         (identity, uid, uv)).fetchone()
        if old:
            require(old['semantic_text'] == semantic, 'context_integrity', '语义单元版本内容不匹配')
            reused += 1
        else:
            # The producer normally already has decoded JSON. Persist only a
            # new unit's complete value, rather than decoding all old records.
            db.execute('INSERT INTO material_source_units VALUES (?,?,?,?,?)',
                       (identity, uid, uv, semantic, dump(_at(value, unit['path']))))
            new += 1
        unit.update(source_id=identity, unit_id=uid, unit_version=uv)
    units_json=dump(units)
    db.execute('INSERT OR REPLACE INTO material_source_versions(source_id,version,field,text,units,index_version,units_hash) VALUES (?,?,?,?,?,?,?)',
               (identity, version, field, text, units_json, INDEX_VERSION,digest(units_json)))
    return {'source_id': identity, 'version': version, 'text': text, 'units': units,
            'index_stats': {'version_hit': False, 'structural_walks': 1,
                            'new_units': new, 'reused_units': reused}}


def read_document(db, identity, version):
    ensure_schema(db)
    row = db.execute('SELECT * FROM material_source_versions WHERE source_id=? AND version=?',
                     (identity, version)).fetchone()
    require(row, 'context_source', '不可变来源版本不存在')
    require(digest(row['text']) == version, 'context_integrity', '来源版本快照损坏')
    require(row['units_hash']==digest(row['units']),'context_integrity','来源目录版本损坏，重新登记完整来源才能使用')
    return {'source_id': identity, 'version': version, 'field': row['field'],
            'text': row['text'], 'units': json.loads(row['units'])}


def source_units(db, identity, version):
    """The complete directory for one immutable version; no recency slicing."""
    return read_document(db, identity, version)['units']


def read_unit(db, identity, unit_id, unit_version):
    ensure_schema(db)
    row=db.execute('SELECT semantic_text,value_json FROM material_source_units '
                   'WHERE source_id=? AND unit_id=? AND unit_version=?',
                   (identity,unit_id,unit_version)).fetchone()
    require(row,'context_source','完整语义单元版本不存在')
    require(digest(row['semantic_text'])==unit_version,'context_integrity','语义单元版本内容损坏')
    # A map-member unit also binds its key. Decode its complete value, rather
    # than trusting a separately persisted derived JSON value.
    try:
        value=json.loads(row['semantic_text'])
    except ValueError:
        member=json.loads('{'+row['semantic_text']+'}')
        require(len(member)==1,'context_integrity','语义单元不是完整值或单一记录')
        value=next(iter(member.values()))
    require(dump(value)==row['value_json'],'context_integrity','语义单元派生值与不可变来源不一致')
    return {'source_id':identity,'unit_id':unit_id,'unit_version':unit_version,
            'semantic_text':row['semantic_text'],'value':value}


def cache_get(db, key, purpose_hash, dependency_signature):
    ensure_schema(db)
    row = db.execute('SELECT * FROM material_preparation_cache WHERE cache_key=?', (key,)).fetchone()
    if not row or row['cache_version'] != CACHE_VERSION or row['purpose_hash'] != purpose_hash \
            or row['dependency_signature'] != dependency_signature:
        return None
    stored=json.loads(row['payload'])
    require(isinstance(stored,dict) and stored.get('hash')==digest(stored.get('value')),
            'context_integrity','准备缓存内容损坏')
    return stored['value']


def cache_put(db, key, purpose_hash, dependency_signature, payload):
    ensure_schema(db)
    db.execute('INSERT OR REPLACE INTO material_preparation_cache VALUES (?,?,?,?,?)',
               (key, purpose_hash, dependency_signature,
                dump({'value':payload,'hash':digest(payload)}), CACHE_VERSION))
