"""Contract amendment without wiping the ledger.

init bakes the whole contract into meta, and every planning receipt quotes its
text — so before any prose exists, renaming the protagonist used to mean
"delete novel.sqlite3, re-init, re-run world_plan..plan". This module turns
that destructive workflow into one guarded command:

- renames (entity name/alias text) propagate into the stored design,
  character and plan texts, with record hashes recomputed, so nothing re-runs;
- structural amendments (entity ids, seed keys, volumes, planning modes)
  invalidate designs and clear plans exactly like replan does, keeping logs,
  tasks and history instead of deleting the database;
- prose guard: once chapters or drafts exist, amendments go through the
  revision flow; silently renaming under live prose would break continuity.

The command shares init's validation gate (store.validate_contract) and runs
inside one transaction: a rejected amendment leaves the ledger untouched.
"""
import json

from .store import get, put, require, digest, log, head, validate_contract


# Seed-shape keys: changing any of these re-derives the initial state, so
# designs and plans built on the old shape must be re-run, not patched.
SEED_KEYS = ('initial_facts', 'initial_relations', 'initial_resources',
             'anchors', 'places', 'links', 'placements')
STRUCTURAL_KEYS = ('planning_mode', 'character_mode', 'context_mode')
DIRECTIONAL_KEYS = ('title', 'reader_promise', 'protagonist_drive', 'world_rules',
                    'ending', 'arcs', 'word_range', 'style')
# config is the runtime governance surface; these contract keys may have been
# set by `config` after init, and a stale contract.json must not silently
# revert them.
CONFIG_MANAGED_KEYS = ('chapter_target_han', 'character_views')


def _entity_shape(entities):
    return sorted((e.get('id'), e.get('kind')) for e in entities or [])


def entity_renames(old, new):
    """Rename pairs (old text, new text) for entities whose id/kind survive.

    The primary name maps directly. Aliases have no stable identity of their
    own, so pairing by raw position misaligns when the author inserts or
    reorders entries (聂远舟[琴师,老聂] → 莱恩[琴师,老莱恩,莱恩] would map
    老聂→琴师). Instead, aliases present in both lists are treated as
    retained; the remaining old-only and new-only entries pair in order, and
    an unpaired surplus is reported so the host can review it. Longer old
    strings replace first so a full name is rewritten before its own alias
    substring can match.
    """
    old_by_id = {e.get('id'): e for e in old.get('entities', []) or []}
    pairs = []
    surplus = []
    for entity in new.get('entities', []) or []:
        previous = old_by_id.get(entity.get('id'))
        if not previous or previous.get('kind') != entity.get('kind'):
            continue
        if previous.get('name') and entity.get('name') and previous['name'] != entity['name']:
            pairs.append((previous['name'], entity['name']))
        old_aliases = previous.get('aliases') or []
        new_aliases = entity.get('aliases') or []
        retained = set(old_aliases) & set(new_aliases)
        old_only = [a for a in old_aliases if a not in retained]
        new_only = [b for b in new_aliases if b not in retained]
        pairs.extend(zip(old_only, new_only))
        surplus.extend(new_only[len(old_only):])
    pairs.sort(key=lambda pair: -len(pair[0]))
    unique = []
    for pair in pairs:
        if pair not in unique:
            unique.append(pair)
    return unique, surplus


def _rewrite(value, pairs):
    if isinstance(value, str):
        for old, new in pairs:
            value = value.replace(old, new)
        return value
    if isinstance(value, list):
        return [_rewrite(item, pairs) for item in value]
    if isinstance(value, dict):
        return {key: _rewrite(item, pairs) for key, item in value.items()}
    return value


def _rehash(record, design_lineage=False):
    """Recompute a stored record's hash. Design records digest without their
    'active' flag (design.save); character profiles and indexes digest the
    whole record (characters.save) — the formulas are not interchangeable."""
    record.pop('hash', None)
    if design_lineage:
        record['hash'] = digest({k: v for k, v in record.items() if k != 'active'})
    else:
        record['hash'] = digest(record)


def _propagate(db, pairs):
    """Rewrite entity names inside stored planning artifacts, keeping hashes
    and cross-references consistent: world record hashes first, then volume
    records' world_hash pointers, character records and chapter plans."""
    counts = {}
    world_hash_map = {}
    rows = db.execute("SELECT key,value FROM meta WHERE key LIKE 'design:%'").fetchall()
    for row in rows:  # pass 1: world scopes define the hash lineage
        record = json.loads(row['value'])
        if record.get('scope') != 'world':
            continue
        old_hash = record.get('hash')
        record['data'] = _rewrite(record['data'], pairs)
        _rehash(record, design_lineage=True)
        if old_hash:
            world_hash_map[old_hash] = record['hash']
        put(db, row['key'], record)
    designs = 0
    for row in rows:  # pass 2: every other scope follows the new world hash
        record = json.loads(row['value'])
        if record.get('scope') == 'world':
            designs += 1
            continue
        if record.get('world_hash') in world_hash_map:
            record['world_hash'] = world_hash_map[record['world_hash']]
        record['data'] = _rewrite(record['data'], pairs)
        _rehash(record, design_lineage=True)
        put(db, row['key'], record)
        designs += 1
    counts['design_records'] = designs

    characters = 0
    for row in db.execute("SELECT key,value FROM meta WHERE key LIKE 'character:%'").fetchall():
        record = json.loads(row['value'])
        if not isinstance(record, dict) or 'data' not in record:
            continue
        record['data'] = _rewrite(record['data'], pairs)
        _rehash(record)
        put(db, row['key'], record)
        characters += 1
    for prefix in ('character-version:', 'character-index:'):
        for row in db.execute("SELECT key,value FROM meta WHERE key LIKE ?", (prefix + '%',)).fetchall():
            record = json.loads(row['value'])
            if not isinstance(record, dict) or 'data' not in record:
                continue
            old_hash = record.get('hash')
            record['data'] = _rewrite(record['data'], pairs)
            _rehash(record)
            if old_hash and old_hash != record['hash']:
                db.execute('DELETE FROM meta WHERE key=?', (row['key'],))
                put(db, row['key'].rsplit(':', 1)[0] + ':' + record['hash'], record)
            else:
                put(db, row['key'], record)
            characters += 1
    counts['character_records'] = characters

    plans = 0
    for row in db.execute('SELECT rowid,data FROM plans').fetchall():
        data = json.loads(row['data'])
        rewritten = _rewrite(data, pairs)
        if rewritten != data:
            db.execute('UPDATE plans SET data=? WHERE rowid=?',
                       (json.dumps(rewritten, ensure_ascii=False, sort_keys=True, separators=(',', ':')), row['rowid']))
            plans += 1
    counts['plans'] = plans
    return counts


def apply(db, new_contract, reason=''):
    """Amend the stored contract in place; caller owns the transaction."""
    require(not db.execute("SELECT 1 FROM tasks WHERE state='pending'").fetchone(),
            'pending', '先完成或取消当前任务，再改订契约')
    prose = db.execute('SELECT count(*) FROM chapters').fetchone()[0]
    drafts = db.execute('SELECT count(*) FROM drafts').fetchone()[0]
    require(prose == 0 and drafts == 0, 'recontract',
            '已有正文或候选稿：改契约走 pilot/revise 修订流程，不能原地改订',
            {'chapters': prose, 'drafts': drafts})
    validate_contract(new_contract)

    old = get(db, 'contract')
    changed = sorted(k for k in set(old) | set(new_contract) if old.get(k) != new_contract.get(k))
    preserved = [k for k in CONFIG_MANAGED_KEYS if k in old and k not in new_contract]
    for key in preserved:
        new_contract = {**new_contract, key: old[key]}
    changed = sorted(set(changed) | set(preserved))
    if not changed:
        return {'changed': [], 'note': '契约未变化，账本保持原样'}

    structural = [k for k in changed if k in STRUCTURAL_KEYS or k in SEED_KEYS]
    if _entity_shape(old.get('entities')) != _entity_shape(new_contract.get('entities')):
        structural.append('entities')
    if [v.get('id') for v in old.get('volumes', [])] != [v.get('id') for v in new_contract.get('volumes', [])]:
        structural.append('volumes')
    renames, alias_surplus = entity_renames(old, new_contract)

    propagated = None
    if renames and not structural:
        # The author's new file may still quote the old name (style samples,
        # fact values); one global rename keeps the seeded state consistent
        # with the propagated receipts.
        new_contract = _rewrite(new_contract, renames)
        propagated = _propagate(db, renames)

    # Invalidation must run before the new contract is stored: design.invalidate
    # walks the contract's volume ids, and the records on disk are keyed by the
    # OLD ids being replaced.
    invalidated = []
    if structural:
        from . import design
        branch = get(db, 'work_branch')
        if design.enabled(db):
            design.invalidate(db, branch, 'world', reason or '契约结构性改订')
        db.execute('DELETE FROM plans WHERE branch=? AND number>?', (branch, head(db, branch)))
        from .batchflow import active, key
        batch = active(db, branch)
        if batch:
            put(db, 'chapter_batch_archive:' + batch['id'], {**batch, 'cancelled_by': 'recontract', 'reason': reason})
            put(db, key(branch), None)
        put(db, 'status', 'ready')
        report_layered = design.enabled(db) or new_contract.get('planning_mode') == 'layered'
        invalidated = ['world_plan', 'volume_plan', 'plan'] if report_layered else ['plan']

    put(db, 'contract', new_contract)
    execution_mode, style_anchor = validate_contract(new_contract)
    if style_anchor is not None:
        put(db, 'style_anchor', style_anchor)
    if 'context_mode' in changed:
        put(db, 'context_mode', new_contract.get('context_mode', 'bounded'))
    if 'review_chunk' in changed:
        put(db, 'review_chunk', new_contract.get('review_chunk', 3))
    if 'preparation_policy' in changed:
        put(db, 'preparation_policy', new_contract.get('preparation_policy', 'adaptive'))

    directional = [k for k in changed if k in DIRECTIONAL_KEYS and k not in ('style',)]
    summary = {'changed': changed, 'renames': [{'from': a, 'to': b} for a, b in renames],
               'structural': structural, 'preserved_config_keys': preserved}
    if alias_surplus:
        summary['unmatched_new_aliases'] = alias_surplus
    if propagated is not None:
        summary['propagated'] = propagated
        summary['note'] = '实体改名已同步到设计/人物/章计划回执并重算哈希，规划无需重跑'
    if invalidated:
        summary['rerun_stages'] = invalidated
        summary['note'] = '结构变更已使设计失效并清理未写计划；advance 会重新派发受影响阶段'
    if directional and not structural:
        summary['advice'] = ('方向性条款（' + '、'.join(directional) + '）已更新；已提交的设计是旧方向下的提案，'
                             '若方向实质变化可 replan 重跑受影响层，小改动可由后续任务直接吸收')
    log(db, 'recontract', {'changed': changed, 'renames': len(renames), 'structural': structural,
                           'reason': reason, 'propagated': propagated})
    return summary
