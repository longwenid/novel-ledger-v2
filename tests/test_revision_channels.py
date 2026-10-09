"""Style-only revision and pilot-reset channels; design errors self-answer."""
import json

import pytest

from tests.support import contract, ev

from novel_core import design, engine, ledger
from novel_core.memory import DISCIPLINE
from novel_core.store import Error, connect, digest, dump, get, initialize, put


def setup_project(tmp_path, extra=None):
    project = tmp_path/'p'
    c = contract()
    if extra:
        c.update(extra)
    initialize(project, c)
    return project, connect(project)


def commit_events(db, chapter, prose, events):
    state = ledger.replay(db, 'main')
    ledger.append_events(db, 'main', chapter, prose, events, state)
    stored = {'events': events, 'prose_hash': digest(prose), 'summary': '概要。'}
    db.execute('INSERT OR REPLACE INTO chapters VALUES (?,?,?,?,?)',
               ('main', chapter, prose, dump(stored), digest(prose)))


def pilot_events():
    return [{'op': 'relation', 'from': 'hero', 'to': 'ally', 'key': 'trust',
             'before': None, 'after': '托付后背', 'evidence': ev(PILOT_PROSE, 1)}]


PILOT_PROSE = '周行把后背交给了宋禾。'


def seed_designs(db, prose_hash=None):
    world = {'active': True, 'sources': ({'1': prose_hash} if prose_hash else {}),
             'data': {'overview': 'x'}, 'hash': 'whash'}
    put(db, design.key('main', 'world'), world)
    volume = {'active': True, 'sources': {}, 'data': {'volume': 'v1'}, 'hash': 'v1hash', 'world_hash': 'whash'}
    put(db, design.key('main', 'v1'), volume)


def add_pilot_plan(db, branch='main'):
    plan = {'number': 1, 'volume': 'v1', 'entities': ['hero', 'ally'],
            'goal': '周行向宋禾交底', 'conflict': '交底可能暴露自己', 'change': '宋禾第一次知道内情'}
    db.execute("INSERT INTO plans VALUES (?,1,?)", (branch, dump(plan)))


def test_design_unknown_field_error_lists_keys(tmp_path):
    _, db = setup_project(tmp_path, {'planning_mode': 'layered'})
    with pytest.raises(Error) as ei:
        design.save(db, 'main', 'world', 1, {'overview': 'x', 'bogus_field': 1}, 't')
    message = str(ei.value)
    assert 'bogus_field' in message and 'overview' in message


def test_style_only_revision_keeps_designs_and_plans(tmp_path):
    project, db = setup_project(tmp_path, {'planning_mode': 'layered'})
    commit_events(db, 1, PILOT_PROSE, pilot_events())
    add_pilot_plan(db)
    seed_designs(db)

    result = engine.begin_revision(db, 1, '形容词太密', style_only=True)
    assert result['style_only'] is True and '文风修订' in result['boundary']
    branch = result['branch']
    assert get(db, design.key(branch, 'world'))['active'] is True
    assert get(db, design.key(branch, 'v1'))['active'] is True
    assert db.execute('SELECT COUNT(*) FROM plans WHERE branch=?', (branch,)).fetchone()[0] == 1
    task = engine.next_task(db, str(project))
    assert task['stage'] == 'draft' and task['chapter'] == 1


def test_plain_revision_still_invalidates_designs_citing_rewritten_prose(tmp_path):
    project, db = setup_project(tmp_path, {'planning_mode': 'layered'})
    commit_events(db, 1, PILOT_PROSE, pilot_events())
    add_pilot_plan(db)
    seed_designs(db, prose_hash=digest(PILOT_PROSE))

    result = engine.begin_revision(db, 1, '改因果')
    assert 'style_only' not in result
    branch = result['branch']
    assert get(db, design.key(branch, 'world'))['active'] is False
    assert db.execute('SELECT COUNT(*) FROM plans WHERE branch=?', (branch,)).fetchone()[0] == 0
    task = engine.next_task(db, str(project))
    assert task['stage'] == 'world_plan'


def test_pilot_reset_rewrites_in_place(tmp_path):
    project, db = setup_project(tmp_path, {'planning_mode': 'layered'})
    commit_events(db, 1, PILOT_PROSE, pilot_events())
    add_pilot_plan(db)
    seed_designs(db)

    status = engine.pilot_reset(db, '形容词尽量不要；章末无必要不加钩子')
    assert status['status'] == 'ready'
    assert db.execute('SELECT COUNT(*) FROM chapters WHERE branch=?', ('main',)).fetchone()[0] == 0
    assert db.execute('SELECT COUNT(*) FROM events WHERE branch=?', ('main',)).fetchone()[0] == 0
    assert get(db, design.key('main', 'world'))['active'] is True
    assert db.execute('SELECT COUNT(*) FROM plans WHERE branch=?', ('main',)).fetchone()[0] == 1
    task = engine.next_task(db, str(project))
    assert task['stage'] == 'draft' and task['chapter'] == 1
    assert get(db, 'work_branch') == 'main'

    put(db, 'pilot_approved', True)
    with pytest.raises(Error):
        engine.pilot_reset(db, '定调后又想改文风')


def test_discipline_carries_clock_hint():
    assert 'clock.before' in DISCIPLINE['events'] and 'null' in DISCIPLINE['events']


def test_writer_brief_discourages_default_hooks():
    brief = design.writer_brief({'number': 1, 'goal': 'g', 'conflict': 'c', 'change': 'x',
                                 'ending_shape': '静收:余波落在决定上'})
    assert '非必要不添加' in brief['instruction']
    assert brief['dramatic_task']['ending_shape'] == '静收:余波落在决定上'


def test_style_only_pack_slims_design_context(tmp_path):
    from novel_core.memory import pack
    project, db = setup_project(tmp_path, {'planning_mode': 'layered'})
    commit_events(db, 1, PILOT_PROSE, pilot_events())
    add_pilot_plan(db)
    seed_designs(db)
    result = engine.begin_revision(db, 1, '文风太碎', style_only=True)
    assert get(db, 'style_only_branch') == result['branch']

    work = pack(db, result['branch'], 1, 'draft', {})
    slim = work['design_context']
    assert slim['status'] == 'style_revision'
    assert slim['world_overview'] == 'x'
    assert len(json.dumps(slim, ensure_ascii=False)) < 500

    normal = pack(db, 'main', 1, 'draft', {})
    assert normal['design_context']['world']['data']['overview'] == 'x'
    assert 'choice_log' not in json.dumps(normal['design_context'])


def test_review_context_hash_is_version_stable(tmp_path):
    from novel_core import store
    _, db = setup_project(tmp_path)
    first = engine.chapter_review_context_hash(db, 'main', 1)
    saved = store.VERSION
    store.VERSION = saved + '-upgraded'
    try:
        again = engine.chapter_review_context_hash(db, 'main', 1)
    finally:
        store.VERSION = saved
    assert first == again


def test_pack_carries_stage_primer(tmp_path):
    from novel_core.memory import pack
    _, db = setup_project(tmp_path)
    for stage, fragment in (('draft', 'prose'), ('facts', 'verdict'), ('reader', 'first_read'),
                            ('plan', 'ending_shape'), ('volume_plan', 'choice_log'),
                            ('batch_review', 'findings')):
        work = pack(db, 'main', 1, stage, {'prose': 'x', 'summary': 's', 'entities': ['hero']} if stage.startswith('character') else {'prose': 'x', 'summary': 's'})
        primer = work.get('stage_primer') or ''
        assert fragment in primer, stage
        assert 'stage_primer' in work['self_contained']
