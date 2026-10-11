"""Deterministic event settlement shared by preflight, drafts and review."""
import copy
import json
from .store import Batch, Error, digest, require
from .ledger import apply, replay


def semantic(value):
    """Exclude only known ledger provenance, never arbitrary fact values."""
    result=copy.deepcopy(value)
    fields={'knowledge':('evidence',),'hooks':('seed','last'),
            'anchors':('source','last_change','change_evidence'),
            'places':('source',),'links':('source','last_change'),
            'placements':('source',)}
    for collection,proofs in fields.items():
        for record in result.get(collection,{}).values():
            for field in proofs:record.pop(field,None)
    # Development hashes are actual downstream preconditions. Keep them even
    # when their proof moves: preserving a suffix must remain conservative.
    return result


def state_hash(state):
    return digest(semantic(state))


def changes(before, after):
    return [{'key': k, 'before': before.get(k), 'after': after.get(k)}
            for k in sorted(set(before) | set(after)) if before.get(k) != after.get(k)]


def report(before, after, steps):
    return {'schema': 'novel-ledger-v2.settlement/1', 'status': 'computed_not_semantic_pass',
            'before_state_hash': state_hash(before), 'after_state_hash': state_hash(after),
            'resources': changes(before['resources'], after['resources']),
            'clock': {'before': before['clock'], 'after': after['clock']},
            'clock_threads':changes(before['clock_threads'],after['clock_threads']), 'steps': steps}


def evaluate(db, branch, chapter, prose, events, candidate=False, initial_state=None):
    from . import engine
    from .batchflow import candidate_state
    require(isinstance(events, list), 'candidate_events' if candidate else 'facts', '事件须为数组')
    before = initial_state if initial_state is not None else (candidate_state(db, branch, chapter - 1) if candidate else replay(db, branch))
    state = copy.deepcopy(before)
    steps = []
    # One rejection answers every event: each retry costs a full-context model
    # call. Failed events skip (their op checks run before mutating state).
    # Only order-dependent failures are flagged cascade — they may merely
    # reflect an earlier skipped event; evidence and shape checks are absolute.
    ORDER_DEPENDENT = frozenset(('precondition', 'time_conflict', 'time_schedule', 'overdue_hook', 'event_accounting'))
    batch = Batch()
    broken = False
    for index, event in enumerate(events):
        context = {'event_index': index, 'chapter': chapter}
        try:
            require(isinstance(event, dict), 'event_fields', '事件须为对象')
            if event.get('op') == 'clock':
                ref=event.get('evidence')
                require(isinstance(ref,dict) and isinstance(ref.get('quote'),str),'evidence','clock 须提供对象形式的正文证据与原文引句')
                quote = ref['quote']
                require(any(w in quote for w in engine.TIME_TOKENS + engine.TIME_CHARS),
                        'time_evidence', 'clock 证据须引用正文时间语句', {'quote': quote})
            resources = dict(state['resources']); clock = state['clock'];threads=dict(state['clock_threads'])
            apply(state, event, prose, chapter)
        except KeyError as error:
            batch.add('event_fields', '事件缺少字段；按 event_contract_path 补齐，不查源码猜格式',
                      **context,
                      **{'op': event.get('op') if isinstance(event, dict) else None,
                         'missing': error.args[0]})
            broken = True
        except Error as error:
            batch.capture(error, **context,
                          **({'cascade': True} if broken and error.code in ORDER_DEPENDENT else {}),
                          **{'recover': '按本任务 state、commands.state 与事件契约修正正式 result，再运行 commands.preflight；两轮仍失败交回主控'})
            broken = True
        else:
            delta = changes(resources, state['resources'])
            thread_delta=changes(threads,state['clock_threads'])
            if delta or clock != state['clock'] or thread_delta:
                steps.append({'event_index': index, 'op': event['op'], 'resources': delta,
                              'clock': {'before': clock, 'after': state['clock']},'clock_threads':thread_delta})
    batch.raise_if_any()
    engine.validate_hook_deadlines(state, chapter)
    row = db.execute('SELECT data FROM plans WHERE branch=? AND number=?', (branch, chapter)).fetchone()
    plan = json.loads(row[0]) if row else {}
    if 'day_span' in plan:
        lo = plan['day_anchor'] + plan['day_span'][0]
        hi = plan['day_anchor'] + plan['day_span'][1] + 1
        require(lo <= state['clock'] <= hi, 'time_schedule', '章末时钟不在计划日程内；核对正文时间与本章计划',
                {'chapter': chapter, 'current': state['clock'], 'expected_range': [lo, hi],
                 'before': before['clock'], 'day_anchor': plan['day_anchor'], 'day_span': plan['day_span']})
    return state, report(before, state, steps)
