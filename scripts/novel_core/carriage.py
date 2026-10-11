"""Dependency-derived revision scope: carry provably unaffected chapters.

A revision branch used to redo the whole positional suffix (start..through):
every downstream chapter was re-drafted and re-reviewed even when the fix
never touched it (session e354 redid ch2-7 for two ch2/ch3 findings).

Carry admits a chapter only when the revision actually changed ledger
state upstream AND three independent detectors pass, all engine-side and
deterministic:
  0. non-empty delta — an empty delta means the fix is expression-level or
     event-less; unregistered free-text reads are unanalysable there
     (test_revision_scope encodes this), so the worker-mediated fast-commit
     path stays authoritative and carry never fires;
  1. dependency delta — the changed state keys must be disjoint from the
     chapter's read-set (plan entities + entities named in prose, the same
     read-set review_context_hash binds);
  2. surface sweep — the old/new values of changed keys (and their entity
     names) must not appear in the chapter's prose, catching free-text
     references no structured key can see;
  3. replay — the chapter's own events must re-apply against the new branch
     prefix (preconditions, clock chain, hook deadlines), and the parent's
     stored review verdicts must actually have passed.

Carried chapters keep their parent review verdicts (marked carried_from);
the chunked revision reviews and the range revision_review still cover the
whole suffix and remain the net that catches anything the detectors missed.
"""
import copy
import json
from .store import digest, dump, get, head, log, transaction
from .ledger import append_events, replay
from .settlement import semantic

# Collections whose change cannot be entity-scoped: any edit there blocks
# carry (v1 stays conservative; spatial facts ripple in ways the read-set
# does not encode).
UNSCOPABLE = frozenset(('entities', 'anchors', 'places', 'links', 'placements',
                        'hooks', 'developments', 'relations'))


def _entity_of(collection, key):
    """Owning entity ids of a state key, or None when unscopable."""
    if collection in ('facts', 'knowledge'):
        return {key.split(':', 1)[0]} if ':' in key else None
    if collection == 'resources':
        try:
            parsed = json.loads(key)
            return {parsed[0]} if isinstance(parsed, list) and parsed else None
        except ValueError:
            return None
    return None


def state_delta(before_state, after_state):
    """Semantic key-level delta between two replayed states."""
    before, after = semantic(before_state), semantic(after_state)
    delta = {}
    for collection in set(before) | set(after):
        old, new = before.get(collection) or {}, after.get(collection) or {}
        if not isinstance(old, dict) or not isinstance(new, dict):
            continue
        for key in set(old) | set(new):
            if old.get(key) != new.get(key):
                delta.setdefault(collection, {})[key] = (old.get(key), new.get(key))
    return delta


def _delta_entities(delta):
    ids = set()
    for collection, entries in delta.items():
        if collection in UNSCOPABLE:
            return None  # signal: unscopable change, deny carry
        for key in entries:
            owners = _entity_of(collection, key)
            if owners is None:
                return None
            ids |= owners
    return ids


def _read_set(db, branch, parent, n, prose, entities_index):
    """Same read-set review_context_hash binds: plan entities plus every
    entity named or aliased in the chapter prose. The branch's regenerated
    plan wins when present; the parent's is the carried chapter's origin."""
    ids = set()
    plan_row = db.execute('SELECT data FROM plans WHERE branch=? AND number=?', (branch, n)).fetchone()
    if plan_row is None:
        plan_row = db.execute('SELECT data FROM plans WHERE branch=? AND number=?', (parent, n)).fetchone()
    if plan_row:
        plan = json.loads(plan_row[0])
        ids.update(plan.get('entities', []))
        ids.update(choice.get('entity') for choice in plan.get('character_choices', []))
        for scene in plan.get('scenes', []):
            ids.update(scene.get('present', []))
            ids.update(scene.get('speakers', []))
            ids.update(scene.get('props', []))
            for intent in scene.get('dialogue_intents', []):
                if isinstance(intent, dict):
                    ids.add(intent.get('entity'))
                    if intent.get('to'):
                        ids.add(intent['to'])
    ids.discard(None)
    for eid, entity in entities_index.items():
        if any(name in prose for name in [entity.get('name', '')] + entity.get('aliases', [])):
            ids.add(eid)
    return ids


def _sweep_forms(delta, entities_index):
    forms = set()
    for collection, entries in delta.items():
        for key, (old, new) in entries.items():
            owners = _entity_of(collection, key) or set()
            for eid in owners:
                entity = entities_index.get(eid)
                if entity:
                    forms.update({entity.get('name', ''), *entity.get('aliases', [])})
            for value in (old, new):
                text = value if isinstance(value, str) else dump(value) if value is not None else ''
                if len(text) >= 2:
                    forms.add(text)
    return {f for f in forms if len(f) >= 2}


def carried_chapters(db, branch):
    rows = db.execute('SELECT number,data FROM chapters WHERE branch=? ORDER BY number', (branch,)).fetchall()
    out = []
    for row in rows:
        try:
            data = json.loads(row['data'])
        except ValueError:
            continue
        if isinstance(data, dict) and data.get('carried_from'):
            out.append(row['number'])
    return out


def _carry_one(db, branch, parent, n, chapter_row):
    prose = chapter_row['prose']
    data = json.loads(chapter_row['data'])
    events = data.get('events')
    # A carried chapter must carry a finished review too: committed chapters
    # store both receipts; guard anyway so a malformed row cannot slip through.
    if not isinstance(events, list) or not isinstance(data.get('facts_review'), dict) or not isinstance(data.get('reader_review'), dict):
        return None
    parent_state = replay(db, parent, through=n - 1)
    branch_state = replay(db, branch)
    delta = state_delta(parent_state, branch_state)
    entities_index = branch_state.get('entities', {})
    if not delta:
        return None  # expression-level or event-less fix: unregistered reads unanalysable
    for verdict in (data.get('facts_review', {}).get('verdict'), data.get('reader_review', {}).get('verdict')):
        if verdict != 'pass':
            return None  # the verdict being carried must actually have passed
    changed = _delta_entities(delta)
    if changed is None:
        return None
    if changed & _read_set(db, branch, parent, n, prose, entities_index):
        return None
    forms = _sweep_forms(delta, entities_index)
    if forms and any(form in prose for form in forms):
        return None
    stored = copy.deepcopy(data)
    stored['carried_from'] = parent
    stored['carried_delta'] = bool(delta)
    try:
        with transaction(db):
            db.execute('INSERT INTO chapters VALUES (?,?,?,?,?)', (branch, n, prose, dump(stored), digest(prose)))
            append_events(db, branch, n, prose, events, branch_state)
            from .engine import validate_hook_deadlines
            validate_hook_deadlines(branch_state, n)
            db.execute('INSERT OR IGNORE INTO plans SELECT ?,number,data FROM plans WHERE branch=? AND number=?', (branch, parent, n))
            log(db, 'revision_carry', {'branch': branch, 'chapter': n, 'from': parent,
                                       'delta_touched': bool(delta)})
    except Exception:
        return None  # replay or uniqueness refused the carry: normal dispatch follows
    return n


def fast_forward(db, branch):
    """Carry the provably-unaffected suffix; returns chapters carried now."""
    rev = db.execute('SELECT * FROM branches WHERE id=?', (branch,)).fetchone()
    if not rev or rev['status'] != 'candidate':
        return []
    if db.execute("SELECT 1 FROM tasks WHERE state='pending'").fetchone() or get(db, 'status') != 'ready':
        return []
    if head(db, branch) < rev['start']:
        # Nothing in the revision range has landed yet: an empty delta here
        # means "the fix has not happened", not "the chapter is unaffected".
        return []
    parent = rev['parent']
    from .engine import load_draft
    carried = []
    while True:
        n = head(db, branch) + 1
        if n > rev['through']:
            break
        if load_draft(db, branch, n):
            break  # a worker already holds this chapter; normal flow continues
        chapter = db.execute('SELECT * FROM chapters WHERE branch=? AND number=?', (parent, n)).fetchone()
        if chapter is None or _carry_one(db, branch, parent, n, chapter) is None:
            break
        carried.append(n)
    return carried
