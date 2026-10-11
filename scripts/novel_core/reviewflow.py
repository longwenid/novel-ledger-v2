"""Versioned review delivery and immutable first impressions.

This proves protocol order and evidence coverage, not human comprehension or
the truth of an editorial judgment. Old frozen tasks retain their old contract.
"""
import copy
import json
import time
from .store import Error, Batch, get, put, digest, require, transaction, log
from .ledger import evidence

PROTOCOL = 'sealed-review/1'
FIRST_FIELDS = ('desire', 'obstacle', 'change', 'read_on_reason')


def enabled(work):
    return work.get('review_protocol') == PROTOCOL


def key(task_id):
    return 'review_first_read:' + task_id


def record(db, task_id):
    saved = get(db, key(task_id))
    if saved is not None:
        body = saved.get('receipt') if isinstance(saved, dict) else None
        require(isinstance(body, dict) and body.get('task_id') == task_id
                and saved.get('hash') == digest(body), 'first_read_integrity', '持久首读记录损坏，不能解锁材料或继续入库')
    return saved


def obligations(contract, anchor=None):
    rows = [{'id': 'language', 'category': 'reader',
             'rule': '删除非必要修饰；用动作、事实、对白和后果表达。技术名词不能证明文风合规。'}]
    for category, prefix, rules in (
        ('facts', 'world', contract.get('world_rules', [])),
        ('reader', 'style', (anchor or contract.get('style') or {}).get('traits', [])),
    ):
        for index, rule in enumerate(rules):
            rows.append({'id': prefix + ':' + str(index), 'category': category,
                         'rule': copy.deepcopy(rule)})
    return rows


def batch_key(ident):return 'chapter_batch_first_read:'+ident


def prime_batch_record(db,task,work):
    """Bind a genuinely frozen batch first impression to a later chapter task."""
    session=work.get('chapter_session',{})
    if session.get('role')!='reviewer' or record(db,task['id']):return
    saved=get(db,batch_key(session['batch']))
    if not saved:return
    require(saved['hash']==digest(saved['reads']),'first_read_integrity','批次首读记录损坏')
    row=next((r for r in saved['reads'] if r['chapter']==task['chapter']),None)
    require(row and row['prose_hash']==work['payload']['prose_hash'],'first_read_version','批内正文已变化，须新独立审稿批次')
    body={'task_id':task['id'],'input_hash':task['input_hash'],
          'prose_hash':row['prose_hash'],'first_read':copy.deepcopy(row['first_read'])}
    put(db,key(task['id']),{'receipt':body,'hash':digest(body),'batch_first_read':session['batch']})
    log(db,'first_read_bound_from_batch',{'task':task['id'],'batch':session['batch'],'chapter':task['chapter']})


def seal(db, project, task_id, receipt):
    """Freeze a first-read receipt before materializing the full input."""
    from . import engine
    with transaction(db):
        task = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
        require(task and task['state'] == 'pending' and task['stage'] == 'chapter_review'
                and task['branch'] == get(db, 'work_branch') and get(db, 'status') == 'ready',
                'first_read_task', '首读只用于当前有效的待审稿任务')
        work = json.loads(task['input'])
        require(enabled(work), 'first_read_protocol', '旧任务沿用其冻结协议，不补造首读记录')
        signature = engine.task_review_context_hash(db, task, work)
        require(signature == work.get('review_context_hash'), 'review_context_stale', '审稿上下文已变化，请重新 next')
        require(isinstance(receipt, dict) and receipt.get('task_id') == task_id
                and receipt.get('input_hash') == task['input_hash']
                and receipt.get('prose_hash') == work['payload']['prose_hash'],
                'first_read_version', '首读须绑定本次 task_id/input_hash/prose_hash')
        session=work.get('chapter_session',{})
        previous_batch=get(db,batch_key(session['batch'])) if session.get('role')=='reviewer' else None
        if previous_batch and 'first_reads' in receipt:
            require(previous_batch['hash']==digest(previous_batch['reads']),'first_read_integrity','批次首读记录损坏')
            require(receipt['first_reads']==previous_batch['reads'],'first_read_conflict','整批首读已冻结，不可在补读后重写')
        if session.get('role')=='reviewer' and get(db,batch_key(session['batch'])) is None:
            from .batchflow import review_sources
            expected={r['chapter']:r['prose_hash'] for r in review_sources(db,work)}
            reads=receipt.get('first_reads')
            require(isinstance(reads,list) and all(isinstance(r,dict) and type(r.get('chapter')) is int for r in reads)
                    and len(reads)==len(expected) and {r.get('chapter') for r in reads}==set(expected),
                    'batch_first_read','须先只读整批正文，逐章冻结首读后才能取得正式材料')
            for row in reads:
                impression=row.get('first_read')
                require(row.get('prose_hash')==expected[row['chapter']] and isinstance(impression,dict)
                        and set(impression)==set(FIRST_FIELDS)
                        and all(isinstance(impression[k],str) and impression[k].strip() for k in FIRST_FIELDS),
                        'batch_first_read','各章首读须绑定正文版本并填齐四项具体体验')
            put(db,batch_key(session['batch']),{'reads':copy.deepcopy(reads),'hash':digest(reads)})
            log(db,'chapter_batch_first_read_sealed',{'batch':session['batch'],'chapters':sorted(expected)})
        # Single source of truth: with a reads array the top-level entry for
        # the current chapter is derived by the engine; declaring a divergent
        # copy is the only failure, and the error shows both texts.
        reads = receipt.get('first_reads')
        if isinstance(reads, list):
            match = next((r for r in reads if isinstance(r, dict) and r.get('chapter') == task['chapter']), None)
            if isinstance(match, dict) and isinstance(match.get('first_read'), dict):
                declared = receipt.get('first_read')
                def _peek(impression):
                    return {k: (v[:40] if isinstance(v, str) else v) for k, v in impression.items()}
                require(declared is None or declared == match['first_read'], 'first_read_conflict',
                        '顶层 first_read 与 first_reads 中本章条目不一致；只填 first_reads 一处，顶层由程序生成本章条目',
                        {'chapter': task['chapter'], 'entry': _peek(match['first_read']),
                         'declared': _peek(declared) if isinstance(declared, dict) else declared})
                receipt['first_read'] = copy.deepcopy(match['first_read'])
        first = receipt.get('first_read')
        require(isinstance(first, dict) and set(first) == set(FIRST_FIELDS)
                and all(isinstance(first[k], str) and first[k].strip() for k in FIRST_FIELDS),
                'first_read', '首读需要 desire/obstacle/change/read_on_reason 四项非空体验')
        body = {k: copy.deepcopy(receipt[k]) for k in ('task_id', 'input_hash', 'prose_hash', 'first_read')}
        saved = record(db, task_id)
        if saved:
            require(saved['receipt'] == body, 'first_read_conflict', '首读已冻结，不可在补读后改写；保留疑点到正式检查')
        else:
            saved = {'receipt': body, 'hash': digest(body), 'sealed_at': time.time()}
            put(db, key(task_id), saved)
            log(db, 'first_read_sealed', {'task': task_id, 'hash': saved['hash']})
    envelope = engine.task_files(db, project, dict(task))
    return {'accepted': True, 'task_id': task_id, 'first_read_hash': saved['hash'],
            'input_path': envelope['input_path'], 'template_path': envelope['template_path'], 'task': envelope}


def require_material_access(db):
    task = db.execute("SELECT id,input FROM tasks WHERE state='pending'").fetchone()
    if task and enabled(json.loads(task['input'])):
        require(record(db, task['id']) is not None, 'first_read_required',
                '先只读正文并执行 first-read，之后才可补读约定、账本或历史')


def validate(db, task, work, facts, reader, prose, first_read_prose=None):
    if not enabled(work):
        return
    saved = record(db, task['id'])
    require(saved is not None, 'first_read_required', '缺少持久首读记录；先执行 first-read')
    require(saved['receipt']['input_hash'] == task['input_hash']
            and saved['receipt']['prose_hash'] == digest(prose if first_read_prose is None else first_read_prose), 'first_read_version', '首读版本失效')
    first = reader.get('first_read')
    require(isinstance(first, dict) and all(isinstance(first.get(k), str) and first[k].strip() for k in FIRST_FIELDS),
            'first_read', '首读需要四项非空文字体验')
    require(reader.get('first_read') == saved['receipt']['first_read'],
            'first_read_conflict', '正式回执必须保留冻结首读，不得用约定或旧评价重新解释首读')
    batch = Batch()
    for category, receipt in (('facts', facts), ('reader', reader)):
        expected = [r for r in work['review_obligations'] if r['category'] == category]
        rule_by_id = {r['id']: r.get('rule', '') for r in expected}
        rows = receipt.get('obligations')
        ids = {r.get('id') for r in rows} if isinstance(rows, list) else None
        require(isinstance(rows, list) and all(isinstance(r, dict) and isinstance(r.get('id'),str) for r in rows)
                and len(rows) == len(expected) and ids == {r['id'] for r in expected},
                'review_obligations', '须逐条核对本类别全部作者约定，不能用总评替代',
                {'category': category, 'expected': [r['id'] for r in expected], 'received': sorted(ids) if ids else None})
        checks = receipt.get('checks') if isinstance(receipt.get('checks'), list) else []
        findings = receipt.get('findings') if isinstance(receipt.get('findings'), list) else []
        for row in rows:
            oid = {'category': category, 'obligation_id': row.get('id'), 'rule': rule_by_id.get(row.get('id'), '')}
            batch.require(row.get('result') in ('pass', 'fix') and isinstance(row.get('reason'), str)
                    and row['reason'].strip(), 'review_obligations', '每条约定须有具体判断与理由', **oid)
            refs = row.get('evidence')
            batch.require(isinstance(refs, list) and bool(refs), 'review_obligations',
                    '每条约定须有正文证据；约定对象本章未触及时判 pass：reason 说明对象未出现，evidence 引任一正文句作未触及佐证', **oid)
            for ref in refs if isinstance(refs, list) else []:
                try:
                    evidence(prose, ref, task['chapter'])
                except Error as error:
                    batch.capture(error, **oid)
            challenge = row.get('challenge')
            batch.require(isinstance(challenge, dict) and challenge.get('disposition') in ('satisfied', 'violated')
                    and isinstance(challenge.get('reason'), str) and challenge['reason'].strip(),
                    'review_challenge', '每条约定须核对最可能违约的正文语段：challenge 含 disposition/reason 与正文证据',
                    **{'category': category, 'obligation_id': row.get('id')})
            if isinstance(challenge, dict):
                try:
                    evidence(prose, challenge.get('evidence'), task['chapter'])
                except Error as error:
                    batch.capture(error, category=category, obligation_id=row.get('id'), field='challenge.evidence')
            if batch.require(isinstance(row.get('result'), str) and isinstance(challenge, dict)
                    and (row['result'] == 'fix') == (challenge.get('disposition') == 'violated'),
                    'review_challenge', '违约证据与检查结论不一致',
                    **{'category': category, 'obligation_id': row.get('id')}) \
                    and row.get('result') == 'fix':
                check_id = 'world_rules' if row['id'].startswith('world:') else (
                    'voice_match' if row['id'].startswith('style:') and 'voice_match' in work['payload']['reader_checks'] else 'language')
                batch.require(any(c.get('id') == check_id and c.get('result') == 'fix' for c in checks if isinstance(c,dict)),
                        'review_obligations', '违约须同步到对应 world_rules/voice_match/language 检查', {**oid, 'check_id': check_id})
                batch.require(any(f.get('obligation_id') == row['id'] and f.get('severity') in ('blocker','quality')
                            for f in findings if isinstance(f,dict)),
                        'review_obligations', '违约须有标记 obligation_id 的实质返修发现', {**oid, 'check_id': check_id})
        batch.require(not any(isinstance(r,dict) and r.get('result') == 'fix' for r in rows) or receipt.get('verdict') == 'fix',
                'review_obligations', '约定违约必须返修，不能总评通过', {'category': category})
    batch.raise_if_any()
