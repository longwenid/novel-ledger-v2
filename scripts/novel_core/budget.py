"""Author length intentions and advisory volume allocations, never prose caps."""
import re
from .store import get, require


def han_count(prose):
    return len(re.findall(r'[\u3400-\u4dbf\u4e00-\u9fff]', prose))


def validate_chapter_target(value):
    require(type(value) is int and value > 0, 'chapter_target',
            'chapter_target_han / execution.target_han 为作者显式约定的正整数汉字目标；不是上下文字符限额')


def chapter_length(contract, plan, prose=None):
    """Observe an explicitly declared point target; never infer one from book size."""
    execution=plan.get('execution',{})
    require(isinstance(execution,dict),'chapter_execution','execution 必须为对象')
    target=execution.get('target_han',contract.get('chapter_target_han'))
    if target is None:return None
    validate_chapter_target(target)
    result={'unit':'han_characters','target_han':target,
            'source':'chapter_execution' if 'target_han' in execution else 'author_default',
            'policy':'development_target_not_content_cap',
            'quality_note':'篇幅达标只代表数量；仍须核对有效动作、信息、选择、关系及后果，重复解释和新增无因果事件不能用于凑数。'}
    if prose is not None:
        actual=han_count(prose)
        result.update(actual_han=actual,shortfall_han=max(0,target-actual),
                      status='needs_development' if actual<target else 'ready_for_semantic_review')
    else:result['status']='planned'
    return result


def validate(contract):
    if 'chapter_target_han' in contract:validate_chapter_target(contract['chapter_target_han'])
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
    from .projections import chapter_counts
    for chapter in chapter_counts(db,branch):
        row = rows[chapter['volume']]
        row['written_han'] += chapter['han']
        row['closed'] = bool(chapter['closed'])
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
