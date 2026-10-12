"""Host-neutral dispatch contract, never a claim that a model was launched."""
from pathlib import Path
from .store import require

PROTOCOL='host-dispatch/1'
WRITER_POLICIES=('batch','chapter')


def dispatch_spec(task, writer_policy='batch'):
    require(writer_policy in WRITER_POLICIES,'host_policy','writer_session_policy 为 batch 或 chapter')
    session=task.get('chapter_session',{})
    policy=task.get('execution_policy',{})
    role=session.get('role') or policy.get('role') or task['stage']
    project=Path(task['project']).resolve()
    return {'protocol':PROTOCOL,'task_id':task['task_id'],'input_hash':task['input_hash'],
            'role':role,'task_path':str(project/task['task_path']),
            'prompt':task['worker_prompt'], 'model_policy':'inherit',
            'history':'fresh_on_dispatch; continuation only within current role and bound session',
            'writer_session_policy':writer_policy,'reviewer_session_policy':policy.get('reviewer_session_policy','batch'),
            'execution_policy':policy,'new_worker_required':policy.get('new_worker_required',False),
            'identity_reporting':'Report the actual host session as receipt.session when available; absence is unverified.',
            'first_read_lifecycle':'Same reviewer continues the same task after first-read unlock; do not dispatch a second reviewer.',
            'write_routes':[task['output_path']]+([task['first_read_result_path']] if task.get('first_read_result_path') else []),
            'runtime_rules':{'source_edits':False,'direct_database_access':False,
                             'receipt_source':'this task template; never another worker receipt'},
            'native_arguments':'Use the active host tool schema; arrays stay arrays when the schema requires arrays.',
            'lifecycle':'dispatch → wait for actual end → advance --after last submitted task_id',
            'launched':False,'permissions_enforced':False,
            'boundary':'This is a dispatch specification. The host must enforce permissions and verify fresh review history.'}
