"""Read-only recovery positions and unfinished revision intent, without dispatch."""
import json
from .store import get, head


def revision_context(db, branch):
    chain = []
    seen = set()
    while branch and branch not in seen:
        seen.add(branch)
        row = db.execute('SELECT * FROM branches WHERE id=?', (branch,)).fetchone()
        if not row or row['status'] != 'candidate':break
        chain.append({key: row[key] for key in ('id', 'parent', 'start', 'through', 'reason')})
        branch = row['parent']
    if not chain:return None
    return {'chain': list(reversed(chain)),
            'instruction': '这是尚未合并的修订链；父级结构、篇幅或剧情目标仍须核对，后加的文风修订不自动替代它们。'
                           '原因说明保留原意，不作为程序解析出的硬章数配额；以作者约定、正文和设计逐项判断。'
                           '修订 through 是旧稿传播范围，不是卷末或作者目标完成的证明；超出本范围的长期目标须在有效设计和后续计划中承接。'}


def position(db, branch):
    row = db.execute('SELECT status,start,through FROM branches WHERE id=?', (branch,)).fetchone()
    number = head(db, branch)
    chapter = db.execute('SELECT data FROM chapters WHERE branch=? AND number=?', (branch, number)).fetchone()
    data = json.loads(chapter['data']) if chapter else {}
    return {'branch': branch, 'mode': 'revision' if row and row['status'] == 'candidate' else 'writing',
            'committed': number, 'volume': data.get('volume'),
            'revision_range': [row['start'], row['through']] if row and row['status'] == 'candidate' else None}


def snapshot(db, pending=None):
    branch = get(db, 'work_branch')
    work = position(db, branch)
    published = position(db, get(db, 'active_branch'))
    revision_boundary = (not pending and work['mode'] == 'revision'
                         and work['committed'] >= work['revision_range'][1])
    number = pending['chapter'] if pending else work['committed'] if revision_boundary else work['committed'] + 1
    plan = db.execute('SELECT data FROM plans WHERE branch=? AND number=?', (branch, number)).fetchone()
    data = json.loads(plan['data']) if plan else {}
    result = {'work': work, 'published': published,
              'target': {'chapter': number, 'volume': data.get('volume'),
                         'stage': pending['stage'] if pending else None,
                         'source': 'pending_task' if pending else 'revision_boundary' if revision_boundary else 'plan_only' if plan else 'not_dispatched',
                         'volume_end_marked': bool(data.get('volume_end'))},
              'boundary': '恢复沿用工作分支及 pending；正式稿与候选稿分别汇报。目标计划和卷末标记不证明已收卷；卷次与下一阶段以 advance 的真实任务为准。'}
    context = revision_context(db, branch)
    if context:result['revision_context'] = context
    return result
