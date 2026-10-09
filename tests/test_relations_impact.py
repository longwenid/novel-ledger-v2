"""relation edges in the ledger and the fact-impact revision index."""
import json

import pytest

from tests.support import contract, ev

from novel_core import engine, ledger
from novel_core.store import Error, connect, digest, dump, initialize, put


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
    stored = {'events': events, 'prose_hash': digest(prose)}
    db.execute('INSERT OR REPLACE INTO chapters VALUES (?,?,?,?,?)',
               ('main', chapter, prose, dump(stored), digest(prose)))


def relation(chapter, prose, src='hero', dst='ally', key='trust', before=None, after='并肩'):
    return {'op': 'relation', 'from': src, 'to': dst, 'key': key,
            'before': before, 'after': after, 'evidence': ev(prose, chapter)}


def test_relation_lifecycle_and_cas(tmp_path):
    _, db = setup_project(tmp_path)
    prose2 = '周行把后背交给了宋禾。'
    commit_events(db, 2, prose2, [relation(2, prose2, after='托付后背')])
    state = ledger.replay(db, 'main')
    assert state['relations']['hero>ally:trust'] == '托付后背'

    prose3 = '宋禾没有回头。'
    with pytest.raises(Error):
        commit_events(db, 3, prose3, [relation(3, prose3, before='旧值', after='破裂')])
    commit_events(db, 3, prose3, [relation(3, prose3, before='托付后背', after='破裂')])

    prose4 = '两人形同陌路，只在交接时点头。'
    with pytest.raises(Error):
        commit_events(db, 4, prose4, [relation(4, prose4, src='hero', dst='ghost', after='陌生')])
    with pytest.raises(Error):
        commit_events(db, 4, prose4, [relation(4, prose4, src='hero', dst='hero', after='自省')])
    with pytest.raises(Error):
        commit_events(db, 4, prose4, [relation(4, prose4, key='旧:账', after='清算')])
    state = ledger.replay(db, 'main')
    assert state['relations']['hero>ally:trust'] == '破裂'


def test_initial_relations_seed(tmp_path):
    extra = {'initial_relations': [{'from': 'hero', 'to': 'ally', 'key': 'debt', 'value': '救命之恩'}]}
    _, db = setup_project(tmp_path, extra)
    state = ledger.replay(db, 'main')
    assert state['relations']['hero>ally:debt'] == '救命之恩'
    bad = {'initial_relations': [{'from': 'hero', 'to': 'ally', 'key': '旧:账', 'value': 'x'}]}
    with pytest.raises(Error):
        setup_project(tmp_path/'bad', bad)
    same = {'initial_relations': [{'from': 'hero', 'to': 'hero', 'key': 'self', 'value': 'x'}]}
    with pytest.raises(Error):
        setup_project(tmp_path/'same', same)


def test_impact_state_relation_knowledge_and_plans(tmp_path):
    _, db = setup_project(tmp_path)
    prose2 = '周行对宋禾的态度松动了。'
    commit_events(db, 2, prose2, [{'op': 'state', 'entity': 'hero', 'key': 'trust',
                                   'before': None, 'after': '动摇', 'evidence': ev(prose2, 2)}])
    prose3 = '周行把后背交给了宋禾。'
    commit_events(db, 3, prose3, [relation(3, prose3, after='托付后背')])
    prose4 = '宋禾第一次知道周行在查旧案。'
    commit_events(db, 4, prose4, [{'op': 'knowledge', 'entity': 'ally', 'topic': '旧案',
                                   'before': None, 'claim': '周行在查旧案', 'stance': 'knows',
                                   'source': '周行亲口', 'evidence': ev(prose4, 4)}])
    prose5 = '周行又想起了宋禾的好。'
    commit_events(db, 5, prose5, [{'op': 'state', 'entity': 'hero', 'key': 'trust',
                                   'before': '动摇', 'after': '感激', 'evidence': ev(prose5, 5)}])
    db.execute("INSERT INTO plans VALUES ('main',6,?)", (dump({'entities': ['ally']}),))
    db.execute("INSERT INTO plans VALUES ('main',7,?)", (dump({'entities': ['hero', 'ally']}),))

    impact = engine.fact_impact(db, 'main', 'hero:trust')
    assert impact['chapters'] == [2, 5]

    by_ally = engine.fact_impact(db, 'main', 'ally>hero:trust')
    assert by_ally['chapters'] == [3]

    known = engine.fact_impact(db, 'main', 'ally:旧案')
    assert known['chapters'] == [4]
    assert known['events'][0]['summary'].startswith('ally ~旧案 knows')

    fresh = engine.fact_impact(db, 'main', 'ghost:whereabouts')
    assert fresh['chapters'] == [] and fresh['plans_ahead'] == []
    assert fresh['absence_is_not_proof'] is True


def test_impact_on_clean_branch_is_empty(tmp_path):
    _, db = setup_project(tmp_path)
    report = engine.fact_impact(db, 'main', 'hero:trust')
    assert report == {'schema': 'novel-ledger-v2.impact/1', 'branch': 'main', 'fact': 'hero:trust',
                      'chapters': [], 'events': [], 'plans_ahead': [], 'absence_is_not_proof': True}
