"""One independent long-review execution across unchanged canonical scopes."""
import json
from .store import digest, get, head, require
from .recovery import revision_context

PROTOCOL = 'review-run/1'
STAGES = ('batch_review', 'volume_review', 'revision_review', 'book_review')


def fingerprint(db, branch):
    through = head(db, branch)
    versions = []
    for kind in ('character', 'character-index', 'design'):
        prefix = kind + ':' + branch + ':'
        versions.extend((r['key'], digest(json.loads(r['value']))) for r in db.execute(
            'SELECT key,value FROM meta WHERE substr(key,1,?)=? ORDER BY key', (len(prefix), prefix)))
    tail = db.execute('SELECT hash FROM events WHERE branch=? ORDER BY chapter DESC,idx DESC LIMIT 1', (branch,)).fetchone()
    # Receipt/memory/preparation writes are excluded: they are the work of this
    # run. Canonical inputs, review partition and author targets stay fixed.
    return digest({'branch': branch, 'through': through, 'contract': get(db, 'contract'),
        'style_anchor': get(db, 'style_anchor'), 'revision': revision_context(db, branch),
        'review_chunk': get(db, 'review_chunk', 1), 'batch_through': get(db, 'batch_through', 0),
        'chapters': [tuple(r) for r in db.execute('SELECT number,hash FROM chapters WHERE branch=? ORDER BY number', (branch,))],
        'plans': [tuple(r) for r in db.execute('SELECT number,data FROM plans WHERE branch=? AND number<=? ORDER BY number', (branch, through))],
        'event_tail': tail['hash'] if tail else None, 'versions': versions})


def session(db, branch, stage):
    if stage not in STAGES:return None
    return {'protocol': PROTOCOL, 'id': fingerprint(db, branch),
            'branch': branch, 'through': head(db, branch), 'role': 'reviewer'}


def matches(db, token):
    return (isinstance(token, dict) and token.get('protocol') == PROTOCOL
            and token.get('role') == 'reviewer' and token.get('branch') == get(db, 'work_branch')
            and token.get('through') == head(db, token['branch'])
            and token.get('id') == fingerprint(db, token['branch']))


def allowed(work, token):
    return (work.get('review_session') == token
            and work.get('stage') in STAGES + ('context_scan', 'context_reduce'))


def continue_session(db, project, task):
    from . import engine
    token = json.loads(task['input']).get('review_session')
    require(token and token.get('protocol') == PROTOCOL, 'review_session', '只有绑定独立复审会话的新任务可以连续处理')
    following = engine.next_task(db, project, after=task['id'], brief=True, review_session=token)
    result = {'schema': 'novel-ledger-v2.review-run/1', 'accepted': True,
              'task_id': task['id'], 'session': token}
    if following.get('task_id'):result.update(action='continue_review', task=following)
    else:result.update(action=following.get('stage', following.get('status')), transition=following,
                      boundary='返回最后提交 ID；主控处理返修、合并、完本或其他阶段，新 Worker 重新读取必要资料。')
    return result
