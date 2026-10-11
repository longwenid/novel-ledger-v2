"""Measure the per-task delivered payload of a realistic book and audit
chapter/character-table coverage.

This is a *diagnostic ruler*, not a behavioural test: it seeds a disposable
project, walks the real engine until every stage has been materialised once,
and reports the byte cost each worker would have to read plus which character
profiles actually reach a chapter-specific task.

Usage: python validation/delivery_probe.py [--out report.json] [--chapters N] [--keep]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from novel_core import characters, design, engine  # noqa: E402
from novel_core.ledger import append_events, replay, seed  # noqa: E402
from novel_core.store import (connect, digest, dump, get, head,  # noqa: E402
                              initialize, put, transaction)

PERSONS = 26
PLACES = 8


def contract():
    entities = [{'id': 'hero', 'name': '周行', 'kind': 'person', 'aliases': ['老周']}]
    entities += [{'id': f'p{i:02d}', 'name': f'人物{i:02d}', 'kind': 'person'} for i in range(PERSONS - 1)]
    entities += [{'id': f'loc{i:02d}', 'name': f'场所{i:02d}', 'kind': 'place'} for i in range(PLACES)]
    entities += [{'id': 'receipt', 'name': '收据', 'kind': 'item'}]
    return {
        'title': '交付成本探针', 'reader_promise': '选择有代价的追索',
        'protagonist_drive': '查清旧案', 'world_rules': ['不能无代价制造资源'],
        'ending': '主角交付证据', 'arcs': ['由独行变为信任伙伴'],
        'volumes': [{'id': 'v1', 'purpose': '追索并兑现'}, {'id': 'v2', 'purpose': '结清旧账'}],
        'word_range': [100000, 3000000], 'chapter_target_han': 3000,
        'planning_mode': 'layered', 'character_mode': 'profiled',
        'character_views': 'scoped', 'context_mode': 'bounded', 'chapter_batch_size': 3,
        'entities': entities,
        'initial_resources': [{'holder': 'hero', 'asset': 'cash', 'unit': '文', 'quantity': '100'}],
    }


def profile(eid, tier='supporting'):
    """A realistically-sized profile: the example asset is ~1.4K chars."""
    big = '她在旧案里反复核对过交接顺序，知道每一步该留下什么凭证。' * 3
    data = {'entity': eid, 'tier': tier,
            'biography': {'public_role': f'{eid} 的社会身份与职责边界', 'desire': '把这件事查清并保住退路',
                          'experiences': [{'text': big, 'status': 'design'}]},
            'voice': {'register': '具体、克制，先问可核对的经过；' + big,
                      'tactics': ['先还原交接顺序', '用具体条件争取合作'],
                      'relationship_modes': [{'audience': 'hero', 'approach': '给对方拒绝的余地'}]},
            'examples': [], 'canon_refs': []}
    if tier == 'major':
        data['biography'].update(fear='凭错误判断把无辜的人推到责任前面',
                                values=['可核实的事实', '不把代价转嫁给同伴'],
                                boundary='不让证人签自己没有看懂的材料',
                                contradiction='希望别人信任自己，却习惯把风险独自藏起来')
        data['voice'].update(rhythm='平时先短问再补条件，迟疑时会修正自己的措辞',
                             stress_shift='越担心判断出错，问题越细')
        data['arc'] = {'direction': '从独自控制风险到尊重同伴的选择',
                       'not_yet_earned': ['未经冲突不能突然信任所有人']}
    return data


def plan_data(n, volume='v1'):
    speaker = f'p{(n - 1) % (PERSONS - 1):02d}'
    scene = {'place': f'loc{n % PLACES:02d}', 'present': ['hero', speaker],
             'intent': '核对接手顺序', 'conflict': '对方不给凭证', 'turn': '对方提出交换条件',
             'speakers': ['hero', speaker],
             'dialogue_intents': [{'entity': eid, 'seeks': '拿到凭证', 'withholds': '自己的怀疑',
                                   'pressure': '时间不够', 'tactic': '先给条件'} for eid in ('hero', speaker)],
             'props': ['receipt']}
    return {'number': n, 'goal': '交出凭证', 'conflict': '对方不信', 'change': '让证人站出来',
            'volume': volume, 'entities': ['hero', speaker],
            'scenes': [scene],
            'reader_experience': '看着主角用自己的代价换取一处可核对的进展',
            'ending_shape': '闭环:这一章的交换有了明确结果',
            'character_choices': [{'entity': 'hero', 'desire': '拿到凭证', 'pressure': '时间不够',
                                   'choice': '答应交换', 'cost': '欠下一笔人情'}],
            'sequence_refs': [1], 'expression_space': ['少用形容词'],
            'execution': {'target_han': 3000, 'expansion_units': [
                {'id': 'u1', 'scene': 1, 'focus': '交接现场', 'development': '把每一步行动写清',
                 'expected_change': '凭证到手', 'weight': 1}]},
            'rhythm': {'chapter_role': '推进追索', 'carries_in': '上一章欠下的人情',
                       'handoff': '凭证指向新的场所',
                       'pressure_curve': [{'scene': 1, 'movement': '压力上升', 'cause': '对方不给凭证'}],
                       'breathing_points': [{'scene': 1, 'placement': '交接后的沉默',
                                             'function': '人物重新权衡代价'}]}}


def place_events(prose, n):
    """Register the physical places so a plan can reference a canonical place ID."""
    return [{'op': 'place', 'entity': f'loc{i:02d}', 'parent': None,
             'evidence': {'chapter': n, 'start': 0, 'quote': prose[:8]}} for i in range(PLACES)]


def seed_chapter(db, branch, n, volume='v1', events=None):
    text = f'第{n}章，周行把收据交给宋禾。' + '她看了许久，终于按下指印。' * 8
    events = (events or []) + (place_events(text, n) if n == 1 else [])
    data = {'volume': volume, 'summary': '周行交付收据，宋禾确认。', 'events': events,
            'prose_hash': digest(text), 'facts_review': {'verdict': 'pass'},
            'reader_review': {'verdict': 'pass'}}
    db.execute('INSERT INTO chapters VALUES (?,?,?,?,?)', (branch, n, text, dump(data), digest(text)))
    db.execute('INSERT INTO plans VALUES (?,?,?)', (branch, n, dump(plan_data(n, volume))))
    state = seed(get(db, 'contract')) if n == 1 else replay(db, branch, n - 1)
    append_events(db, branch, n, text, events, state)


def seed_world_data():
    return {'overview': '灯铺旧案与救灾物资去向。' * 6,
            'causal_spine': ['欠据引出账目矛盾', '证人推翻旧口径', '查仓取得公开证据'],
            'event_spine': {layer: [{'id': f'sp:{layer}1', 'span': 'ch1-10', 'event': '旧案线索出现'}]
                            for layer in ('main', 'subplot', 'timeline', 'tension')},
            'geography': {'regions': ['北岸旧城'], 'connections': ['渡口连接两岸']},
            'factions': [{'name': '仓吏房', 'drive': '维持调拨口径', 'limits': '不能伪造收据'}],
            'core_locations': [{'id': f'design:l{i}', 'name': f'设计场所{i}', 'purpose': '交割地点',
                                'layout': ['临河'], 'constraints': ['只容两人']} for i in range(6)],
            'ending_dependencies': ['公开证据需要证人作证'],
            'dependencies': [], 'canon_refs': [], 'open_questions': []}


def seed_volume_data(scope):
    return {'volume': scope, 'purpose': '从欠据发现账与水位矛盾。' * 4,
            'carries_in': '师父留下的欠据', 'climax': '取得查仓证据', 'handoff': '证据指向第二卷',
            'location_ids': ['design:l0'], 'new_locations': [],
            'sequence': ['发现欠据矛盾', '取得证人', '查仓', '公开证据'],
            'action_constraints': ['调拨须有印记'], 'ending_setup': ['旧账结算'],
            'choice_log': [{'kind': 'direction', 'mode': 'unattended',
                            'candidates': [{'direction': '查仓', 'payoff': '证据', 'cost': '暴露自己',
                                            'risk': '被调离'},
                                           {'direction': '私下换证', 'payoff': '快', 'cost': '失信',
                                            'risk': '被反咬', 'wild': True}],
                            'chosen': '查仓', 'reason': '收益与代价都落在主角身上'}],
            'dependencies': [], 'canon_refs': [], 'open_questions': []}


def seed_world(db, branch):
    design.save(db, branch, 'world', 1, seed_world_data(), 'probe-world')
    design.save(db, branch, 'v1', 1, seed_volume_data('v1'), 'probe-volume')


def seed_profiles(db, branch):
    for index in range(PERSONS):
        eid = 'hero' if index == 0 else f'p{index - 1:02d}'
        characters.save(db, branch, 1, {'profiles': [profile(eid, 'major' if index == 0 else 'supporting')]},
                        [eid], 'probe-profile-' + eid)


# --- measurement -----------------------------------------------------------

def measure_envelope(project, task):
    """Bytes a worker must read: the index, required slices and required shards."""
    base = Path(project).resolve()
    files = []
    for route_key in ('input_path', 'brief_path', 'event_contract_path', 'first_read_template_path'):
        route = task.get(route_key)
        if not route:
            continue
        path = Path(route)
        path = path if path.is_absolute() else base / route
        if path.is_file():
            files.append({'route': route_key, 'bytes': path.stat().st_size})
    work = {}
    index_bytes = 0
    input_path = base / task['input_path']
    if input_path.is_file():
        index_bytes = input_path.stat().st_size
        work = json.loads(input_path.read_text(encoding='utf-8'))
    slices = work.get('input_files') or {}
    required = sum((base / row['path']).stat().st_size for row in work.get('must_read') or []
                   if row.get('path'))
    optional = sum((base / row['path']).stat().st_size for row in work.get('on_demand') or []
                   if row.get('path'))
    for field, entry in (work.get('shared_materials') or {}).items():
        for part in entry.get('parts') or [entry]:
            size = (base / part['path']).stat().st_size
            if part.get('needed'):
                required += size
            else:
                optional += size
    shared_bytes = required + optional
    return {'files': files, 'input_bytes': index_bytes,
            'index_bytes': index_bytes,
            'slice_bytes': sum(row['bytes'] for row in slices.values()),
            'required_shard_bytes': required,
            'optional_shard_bytes': optional,
            'shared_bytes': shared_bytes,
            'slices': {field: row['bytes'] for field, row in slices.items()},
            'required_read': index_bytes + required,
            'total_if_read_all': index_bytes + sum(row['bytes'] for row in slices.values()) + shared_bytes}



def character_reach(db, task, work):
    """Which profiles does this task actually put in front of the worker?"""
    ctx = work.get('character_context') or {}
    profiles = ctx.get('profiles') or {}
    state = replay(db, task['branch'])
    plan_row = db.execute('SELECT data FROM plans WHERE branch=? AND number=?',
                          (task['branch'], task['chapter'])).fetchone()
    plan = json.loads(plan_row[0]) if plan_row else {}
    participants = characters.participants(plan, state) if plan else []
    delivered = sorted(k for k, v in profiles.items() if v)
    return {'stage': task['stage'], 'chapter': task['chapter'],
            'delivered_profiles': delivered, 'delivered_count': len(delivered),
            'participants': participants,
            'irrelevant': sorted(set(delivered) - set(participants)),
            'missing_for_participants': sorted(set(participants) - set(delivered))}


# --- driver: advance the real state machine so every stage gets materialised ---

PROSE_UNIT = '她把收据按在柜台上，等他先把经手人的名字写完。' * 3


def chapter_prose(n, variant=0):
    tail = ('她数过三遍，才把经手人的名字写完。' * 3 +
            '掌柜把旧账翻到第七页，指出两处经手人印记不同。' * variant) if variant else ''
    return f'第{n}章。' + PROSE_UNIT * 55 + tail


def quote(text):
    return text[:12]


def obligations(work, category, text, n):
    rows = []
    for row in work.get('review_obligations', []):
        if row['category'] != category:
            continue
        ref = {'chapter': n, 'start': 0, 'quote': quote(text)}
        rows.append({'id': row['id'], 'result': 'pass', 'reason': '本章未触及该约定的反面，按原文核过。',
                     'evidence': [dict(ref)],
                     'challenge': {'disposition': 'satisfied',
                                   'reason': '最可能违约的语段仍是行动与后果，未出现相反表达。',
                                   'evidence': dict(ref)}})
    return rows


def checks(ids, text, n):
    return [{'id': key, 'result': 'pass', 'reason': '按原文逐项核过。',
             'evidence': [{'chapter': n, 'start': 0, 'quote': quote(text)}]} for key in ids]


def make_receipt(db, project, task, visit=1):
    """Synthetic worker receipt; the probe measures delivery, not literary quality."""
    stage = task['stage']
    work = {}
    input_path = Path(task['project']) / task['input_path']
    if input_path.is_file():
        work = json.loads(input_path.read_text(encoding='utf-8'))
    payload = work.get('payload', {})
    n = task['chapter']
    if stage == 'plan':
        first = payload['window'][0]
        data = {'chapters': [plan_data(k) for k in range(first, first + 5)]}
    elif stage == 'world_plan':
        data = seed_world_data()
    elif stage == 'volume_plan':
        data = seed_volume_data(payload.get('design_scope', 'v1'))
    elif stage == 'character_plan':
        data = {'profiles': [profile(eid, 'supporting') for eid in payload['entities']]}
    elif stage == 'character_index':
        data = {'indexes': []}
        for eid in payload['entities']:
            record = characters.current(db, task['branch'], eid)
            items = characters.indexed_items(record)
            data['indexes'].append({'entity': eid, 'profile_hash': record['hash'],
                                    'core_hash': digest(characters.section_value(record, 'core')),
                                    'coverage': 'complete', 'core_depends_on': [],
                                    'items': [{'section': section, 'item': item, 'value_hash': digest(value),
                                               'classification': 'global',
                                               'reason': '核心动机与声音处处相关，不做场景省略。'}
                                              for (section, item), value in items.items()]})
    elif stage in ('draft', 'repair', 'redesign'):
        text = chapter_prose(n, max(1, visit) if stage != 'draft' else 0)
        data = {'prose': text, 'summary': f'第{n}章：周行交付收据，宋禾确认。'}
        if work.get('chapter_session', {}).get('role') == 'writer' or payload.get('candidate_events_snapshot') is not None:
            data.update(evidence_catalog={}, self_check='核过选择、阻力、后果与重复表达；本章无账本变化。')
            if payload.get('candidate_events_snapshot') is not None:
                data['reuse_candidate_events'] = True
            else:
                data['candidate_events'] = []
        if stage == 'repair':
            data['repair_note'] = '把交接步骤写实，去掉形容词。'
        if stage == 'redesign':
            data['design_change'] = '改为人物主动做出选择并承担代价。'
    elif stage == 'chapter_review':
        first = {'desire': '想知道凭证能不能兑现', 'obstacle': '对方不肯先给凭证',
                 'change': '两人用交换条件替代信任', 'read_on_reason': '后果落在主角身上'}
        if task.get('delivery_phase') == 'first_read':
            from novel_core.reviewflow import seal
            body = {'task_id': task['task_id'], 'input_hash': task['input_hash'],
                    'prose_hash': task.get('first_read_hash'), 'first_read': first}
            header = task.get('first_read_template_path')
            if header:
                path = Path(task['project']) / header
                if path.is_file():
                    template = json.loads(path.read_text(encoding='utf-8'))
                    if 'first_reads' in template:
                        body.pop('first_read')
                        body['first_reads'] = [{'chapter': r['chapter'], 'prose_hash': r['prose_hash'],
                                                'first_read': dict(first)} for r in template['first_reads']]
            seal(db, task['project'], task['task_id'], body)
            return None
        text = (Path(task['project']) / payload['prose_path']).read_text(encoding='utf-8')
        data = {'facts': {'verdict': 'pass', 'checks': checks(payload['facts_checks'], text, n),
                          'obligations': obligations(work, 'facts', text, n), 'events': [],
                          'summary': f'第{n}章：周行交付收据，宋禾确认。'},
                'reader': {'verdict': 'pass', 'checks': checks(payload['reader_checks'], text, n),
                           'obligations': obligations(work, 'reader', text, n), 'first_read': first,
                           'findings': []},
                'findings': []}
    elif stage in ('batch_review', 'volume_review', 'revision_review', 'book_review'):
        first = payload.get('first', 1)
        source = db.execute('SELECT prose FROM chapters WHERE branch=? AND number=?',
                            (task['branch'], first)).fetchone()
        text = source[0] if source else ''
        scope = payload.get('scope', '')
        data = {'verdict': 'pass', 'checks': checks(payload['checks'], text, first),
                'evidence_catalog': {}, 'findings': []}
        if not scope.endswith('_chunk'):
            data['memory'] = {'text': '周行交付收据并承担代价。',
                              'sources': [{'chapter': first, 'start': 0, 'quote': quote(text)}]}
    elif stage == 'context_scan':
        pages = payload.get('pages') or [payload]
        # The probe declines to compact: every page is kept verbatim, which the
        # engine supports as a lossless disposition.
        data = {'cannot_compact': True, 'reason': '探针逐页保留全部来源，不压缩。'}
    elif stage == 'context_reduce':
        data = {'cannot_compact': True, 'reason': '探针保留全部来源，不压缩。'}
    else:
        return None
    return {'schema': 'novel-ledger-v2.result/1', 'task_id': task['task_id'],
            'input_hash': task['input_hash'], 'data': data}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', default=str(ROOT / 'validation' / 'delivery-probe.json'))
    parser.add_argument('--chapters', type=int, default=14)
    parser.add_argument('--keep', action='store_true')
    args = parser.parse_args()

    scratch = ROOT / '.probe-tmp'
    scratch.mkdir(exist_ok=True)
    # The workspace sandbox grants this directory tree at creation time; nested
    # mkdtemp() output is not writable, so the probe uses one fixed location.
    work_dir = scratch / 'delivery-probe'
    project = work_dir / 'book'
    if work_dir.exists():
        shutil.rmtree(work_dir)
    project.mkdir(parents=True)
    initialize(project, contract())
    db = connect(project)
    report = {'project': str(project), 'scale': {'persons': PERSONS, 'places': PLACES},
              'stages': [], 'notes': []}
    try:
        with transaction(db):
            put(db, 'pilot_approved', True)
            for n in range(1, args.chapters + 1):
                seed_chapter(db, 'main', n)
        seed_world(db, 'main')
        seed_profiles(db, 'main')
        report['book'] = {'committed': head(db, 'main')}

        seen, visits = set(), {}
        for _ in range(160):
            task = engine.next_task(db, project, brief=True)
            stage = task.get('stage')
            if not task.get('task_id'):
                report['notes'].append({'stop': 'no_task', 'stage': stage, 'seen': sorted(seen)})
                break
            visits[stage] = visits.get(stage, 0) + 1
            if visits[stage] > 6:
                report['notes'].append({'stop': 'repeat', 'stage': stage, 'task_id': task.get('task_id'),
                                        'visits': visits, 'seen': sorted(seen)})
                break
            seen.add(stage)
            if stage == 'await_author':
                report['stages'].append({'stage': stage, 'chapter': task.get('chapter'), 'input_bytes': 0})
                engine.approve(db)
                continue
            input_file = Path(task['project']) / task['input_path']
            work = json.loads(input_file.read_text(encoding='utf-8')) if input_file.is_file() else {}
            field_bytes = sorted(((len(json.dumps(value, ensure_ascii=False)), key)
                                  for key, value in work.items()), reverse=True)
            row = {'stage': stage, 'chapter': task.get('chapter'),
                   'delivery_phase': task.get('delivery_phase'),
                   'input_bytes': input_file.stat().st_size if input_file.is_file() else 0,
                   'top_fields': [{'field': key, 'bytes': size} for size, key in field_bytes[:8]],
                   'payload_fields': sorted(((len(json.dumps(value, ensure_ascii=False)), key)
                                             for key, value in (work.get('payload') or {}).items()),
                                            reverse=True)[:6],
                   'envelope': measure_envelope(project, task),
                   'visit': visits[stage]}
            if stage in ('draft', 'repair', 'redesign', 'chapter_review', 'plan',
                         'character_plan', 'character_index'):
                row['characters'] = character_reach(db, task, work)
            report['stages'].append(row)
            receipt = make_receipt(db, project, task, visits[stage])
            if receipt is None:
                continue
            try:
                engine.submit(db, receipt, brief=True)
            except Exception as error:
                report['notes'].append({'stage': stage, 'chapter': task.get('chapter'),
                                        'submit_failed': str(error),
                                        'details': getattr(error, 'details', None)})
                break

        # Chapter/character-table coverage across the whole book: for every
        # planned chapter, are all participants backed by a usable profile?
        coverage = []
        for row in db.execute('SELECT number,data FROM plans WHERE branch=? ORDER BY number', ('main',)):
            plan = json.loads(row['data'])
            state = replay(db, 'main', row['number'] - 1)
            parts = characters.participants(plan, state)
            missing = [e for e in parts if not characters.usable(db, 'main', e, state)]
            if missing:
                coverage.append({'chapter': row['number'], 'missing_profiles': missing})
        report['character_coverage_gaps'] = coverage
        used = set()
        for row in db.execute('SELECT number,data FROM plans WHERE branch=?', ('main',)):
            used.update(characters.participants(json.loads(row['data']), replay(db, 'main', 0)))
        report['profiles_never_in_a_plan'] = sorted(
            e for e in (['hero'] + [f'p{i:02d}' for i in range(PERSONS - 1)])
            if characters.current(db, 'main', e) and e not in used)
    finally:
        db.close()
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'out': args.out,
                          'stages': [(s['stage'], s.get('chapter'), s['input_bytes']) for s in report['stages']],
                          'coverage_gaps': len(report.get('character_coverage_gaps', []))},
                         ensure_ascii=False))
        if not args.keep:
            shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == '__main__':
    main()
