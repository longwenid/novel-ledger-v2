"""Advisory volume allocations; never a chapter gate or a replacement for prose."""
import json
import re
from .store import get, require


def han_count(prose):
    return len(re.findall(r'[\u3400-\u4dbf\u4e00-\u9fff]', prose))


def validate(contract):
    volumes = contract['volumes']
    if not any('word_budget' in v for v in volumes):
        return
    require(all(type(v.get('word_budget')) is int and v['word_budget'] > 0 for v in volumes),
            'volume_budget', '启用分卷预算时每卷均需正整数 word_budget（汉字）；不能只填部分卷')
    total = sum(v['word_budget'] for v in volumes)
    low, high = contract['word_range']
    require(low <= total <= high, 'volume_budget', '各卷参考预算之和须在全书约定区间内')


def overview(db, branch):
    contract = get(db, 'contract')
    if not any('word_budget' in v for v in contract['volumes']):
        return {'status': 'not_configured', 'branch': branch}
    rows = {v['id']: {'volume': v['id'], 'baseline_han': v['word_budget'],
                     'written_han': 0, 'closed': False} for v in contract['volumes']}
    # New chapters store a derived count; legacy chapters are counted only when needed.
    for chapter in db.execute('SELECT c.number,c.data,p.data AS plan FROM chapters c '
                              'JOIN plans p ON p.branch=c.branch AND p.number=c.number '
                              'WHERE c.branch=? ORDER BY c.number', (branch,)):
        data = json.loads(chapter['data']); plan = json.loads(chapter['plan'])
        count = data.get('han_characters')
        if count is None:
            prose = db.execute('SELECT prose FROM chapters WHERE branch=? AND number=?',
                               (branch, chapter['number'])).fetchone()[0]
            count = han_count(prose)
        row = rows[data['volume']]
        row['written_han'] += count
        row['closed'] = row['closed'] or bool(plan.get('volume_end'))
    total = sum(r['baseline_han'] for r in rows.values())
    written = sum(r['written_han'] for r in rows.values())
    pool = max(0, total - written)
    active = [r for r in rows.values() if not r['closed']]
    weights = [max(0, r['baseline_han'] - r['written_han']) for r in active]
    if active and not sum(weights):
        weights = [r['baseline_han'] for r in active]
    denominator = sum(weights)
    shares = [pool * w // denominator for w in weights] if denominator else []
    # Largest remainder keeps the suggested allocation exact using integers only.
    if denominator:
        order = sorted(range(len(weights)), key=lambda i: -(pool * weights[i] % denominator))
        for i in order[:pool - sum(shares)]:
            shares[i] += 1
    for row in rows.values():
        row['suggested_remaining_han'] = 0
        row['deviation_han'] = row['written_han'] - row['baseline_han']
    for row, share in zip(active, shares):
        row['suggested_remaining_han'] = share
    low, high = contract['word_range']
    return {'status': 'advisory_not_quota', 'branch': branch, 'unit': 'han_characters',
            'baseline_total_han': total, 'written_han': written,
            'remaining_to_range_han': [max(0, low-written), max(0, high-written)],
            'over_upper_bound': written > high,
            'baseline_exhausted_with_open_volumes': bool(active) and written >= total,
            'unallocated_han': pool if not active else 0,
            'volumes': list(rows.values())}
