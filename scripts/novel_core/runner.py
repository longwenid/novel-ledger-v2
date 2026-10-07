"""Optional vendor-neutral argv driver. No shell, SDK, account or model defaults."""
from __future__ import annotations
import json
import os
import signal
import sys
import subprocess
import time
import uuid
from pathlib import Path
from .store import Error, require, get, put, transaction, log, write_json
from .engine import next_task, submit, status


def process_alive(pid):
    if not pid:
        return False
    if os.name == 'nt':
        # os.kill(pid, 0) is not a liveness probe on Windows: signal 0 maps to
        # CTRL_C_EVENT and fails on any pid that is not a console process-group id.
        import ctypes
        SYNCHRONIZE = 0x00100000
        WAIT_TIMEOUT = 0x102
        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(SYNCHRONIZE, False, pid)
        if not handle:
            return False
        try:
            return kernel.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid,0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # Zombie processes cannot write results. ps is read-only, never a shell command.
    if os.name=='posix':
        info=subprocess.run(['ps','-p',str(pid),'-o','stat='],capture_output=True,text=True)
        if info.returncode==0 and info.stdout.strip().startswith('Z'):
            return False
    return True


def worker_alive(pid):
    if not pid:
        return False
    if process_alive(pid):
        return True
    if os.name!='posix':
        return False
    rows=subprocess.run(['ps','-axo','pgid=,stat='],capture_output=True,text=True)
    if rows.returncode!=0:
        try:
            os.killpg(pid,0)
            return True
        except ProcessLookupError:
            return False
    return any(len(parts)>=2 and parts[0]==str(pid) and not parts[1].startswith('Z')
               for line in rows.stdout.splitlines() for parts in [line.split()])


def claim(db,owner,ttl):
    with transaction(db):
        current=get(db,'runner')
        if current and current['until']>time.time() and current['owner']!=owner:
            raise Error('runner_busy','已有运行器持有租约')
        # A dead controller may leave its detached worker alive. Never start a duplicate.
        if current and current['owner']!=owner:
            require(not process_alive(current.get('pid')), 'runner_busy', '旧运行器仍然存在')
            require(not worker_alive(current.get('worker_pid')), 'orphan_worker', '旧模型进程仍在运行；等待其退出或确认终止后再恢复，不派发第二份任务')
        put(db,'runner',{'owner':owner,'until':time.time()+ttl,'pid':os.getpid()})


def stop_process(proc):
    if os.name=='posix':
        # A process group can outlive its leader. Always retire remaining descendants.
        if worker_alive(proc.pid):
            try:
                os.killpg(proc.pid,signal.SIGTERM)
            except ProcessLookupError:
                pass
            deadline=time.monotonic()+5
            while worker_alive(proc.pid) and time.monotonic()<deadline:
                time.sleep(0.05)
            if worker_alive(proc.pid):
                try:
                    os.killpg(proc.pid,signal.SIGKILL)
                except ProcessLookupError:
                    pass
    elif proc.poll() is None:
        proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def validate_config(config):
    argv=config.get('argv')
    require(isinstance(argv,list) and argv and all(isinstance(s,str) and s for s in argv),'driver','argv 必须是非空字符串数组')
    allowed={'{task_file}','{result_file}','{project}','{feedback_file}'}
    import re
    for arg in argv:
        require(all(p in allowed for p in re.findall(r'\{[^{}]+\}',arg)),'driver','未知 argv 占位符')
    require(config.get('session_mode','serial') in ('serial','fresh'),'driver','session_mode 只能 serial/fresh')
    require(type(config.get('timeout',1800)) is int and config.get('timeout',1800)>0,'driver','timeout 必须为正整秒')
    require(type(config.get('max_retries',2)) is int and 0<=config.get('max_retries',2)<=5,'driver','max_retries 为 0..5')
    return config


def run(db,project,config,max_tasks=0):
    config=validate_config(config)
    require(type(max_tasks) is int and max_tasks>=0,'driver','max_tasks 不能为负数')
    owner=uuid.uuid4().hex
    claim(db,owner,60)
    completed=0
    proc=None
    try:
        with transaction(db):
            put(db,'session_mode',config.get('session_mode','serial'))
        while not max_tasks or completed<max_tasks:
            task=next_task(db,project)
            if task.get('stage')=='revision_promoted':
                continue
            if not task.get('task_id'):
                return task
            envelope=Path(task['output_path'])
            task_root=(envelope if envelope.is_absolute() else Path(project).resolve()/envelope).parent
            last_error=None
            for attempt in range(config.get('max_retries',2)+1):
                from .contextflow import session_checkpoint,charge_input,charge_failed_attempt
                work=json.loads((task_root/'input.json').read_text(encoding='utf-8'))
                checkpoint=session_checkpoint(db,work,task['task_id'],replay_input=attempt>0)
                if checkpoint:return checkpoint
                if attempt:
                    with transaction(db):charge_input(db,task['task_id'],work,repeat=True)
                if get(db,'status')=='paused':
                    return status(db)
                timeout=config.get('timeout',1800)
                # Every attempt has a new fencing token. Outputs from prior workers cannot submit.
                token=uuid.uuid4().hex
                attempt_root=task_root/('attempt-'+token)
                attempt_root.mkdir(parents=True,exist_ok=True)
                result_file=attempt_root/'result.json'
                feedback=attempt_root/'feedback.json'
                task_file=attempt_root/'task.json'
                with transaction(db):
                    db.execute("UPDATE tasks SET lease_owner=?,lease_until=? WHERE id=? AND state='pending'",(token,time.time()+timeout+15,task['task_id']))
                task.update(lease_owner=token,attempt=attempt,feedback_path=str(feedback),output_path=str(result_file))
                write_json(task_file,task)
                write_json(feedback,{'attempt':attempt,'previous_error':last_error,'instruction':'根据错误修正产物；临时基础设施失败才原任务重试。只写 result_file，不直接调用 submit。'})
                if result_file.exists():
                    result_file.rename(result_file.with_name(f'result.attempt-{attempt-1}.{uuid.uuid4().hex[:6]}.json'))
                mapping={'{task_file}':str(task_file),'{result_file}':str(result_file),'{project}':str(Path(project).resolve()),'{feedback_file}':str(feedback)}
                argv=[]
                for arg in config['argv']:
                    for key,value in mapping.items():
                        arg=arg.replace(key,value)
                    argv.append(arg)
                env=dict(os.environ,NOVEL_TASK_FILE=str(task_file),NOVEL_RESULT_FILE=str(result_file),NOVEL_FEEDBACK_FILE=str(feedback),NOVEL_LEASE_OWNER=token)
                before_submit=None
                observed_chars=0
                try:
                    with result_file.with_name(f'driver-{attempt}.log').open('wb') as output:
                        launch=attempt_root/'launch.json'
                        write_json(launch,{'argv':argv,'parent_pid':os.getpid()})
                        proc=subprocess.Popen([sys.executable,str(Path(__file__).with_name('driver_bootstrap.py')),str(launch)],cwd=str(Path(project).resolve()),env=env,stdout=output,stderr=subprocess.STDOUT,start_new_session=(os.name=='posix'))
                        with transaction(db):
                            put(db,'runner',{'owner':owner,'until':time.time()+60,'pid':os.getpid(),'worker_pid':proc.pid})
                        launch.with_suffix('.go').touch()
                        deadline=time.monotonic()+timeout
                        beat=0
                        while proc.poll() is None:
                            if time.monotonic()>=deadline:
                                raise Error('driver_timeout','模型调用超时，已终止后才重试')
                            if get(db,'status')=='paused':
                                stop_process(proc)
                                return status(db)
                            if time.monotonic()>=beat:
                                with transaction(db):
                                    put(db,'runner',{'owner':owner,'until':time.time()+60,'pid':os.getpid(),'worker_pid':proc.pid if proc else None})
                                beat=time.monotonic()+10
                            time.sleep(0.2)
                        require(proc.returncode==0,'driver_exit',f'驱动返回非零退出码 {proc.returncode}')
                    stop_process(proc)
                    require(result_file.exists(),'driver_result','模型未写出结果文件')
                    raw=result_file.read_text(encoding='utf-8');observed_chars=len(raw)
                    result=json.loads(raw)
                    before_submit=get(db,'context_session_used',0)
                    submit(db,result)
                    last_error=None
                    break
                except (Error,OSError,ValueError,KeyError,TypeError) as exc:
                    if proc:
                        stop_process(proc)
                    last_error={'code':getattr(exc,'code',type(exc).__name__),'message':str(exc)}
                    with transaction(db):
                        if before_submit is None or get(db,'context_session_used',0)==before_submit:
                            charge_failed_attempt(db,observed_chars)
                        log(db,'driver_retry',{'task':task['task_id'],'attempt':attempt,'error':last_error})
                    # Wait is interruptible by a user pause; no duplicate workers.
                    until=time.monotonic()+min(2**attempt,8)
                    while time.monotonic()<until:
                        if get(db,'status')=='paused':
                            return status(db)
                        time.sleep(0.2)
            if last_error:
                with transaction(db):
                    put(db,'status','paused')
                    put(db,'pause_reason',{'code':'driver_exhausted','error':last_error})
                return status(db)
            completed+=1
        return {'tasks_completed':completed,**status(db)}
    except Error as exc:
        with transaction(db):
            put(db,'status','paused')
            put(db,'pause_reason',{'code':exc.code,'message':str(exc),'details':exc.details})
        return status(db)
    finally:
        if proc:
            stop_process(proc)
        with transaction(db):
            current=get(db,'runner')
            if current and current['owner']==owner:
                put(db,'runner',None)
            db.execute("UPDATE tasks SET lease_owner=NULL,lease_until=NULL WHERE state='pending' AND lease_owner IS NOT NULL")
