import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from support import *
from novel_core.runner import run, validate_config
from novel_core.store import Error

class RunnerTests(unittest.TestCase):
    def test_command_driver_retries_with_feedback_and_fences(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);initialize(root,contract());db=connect(root)
            worker=root/'worker.py'
            worker.write_text('''import json,os,pathlib
p=pathlib.Path(os.environ['NOVEL_TASK_FILE']);t=json.loads(p.read_text());f=json.loads(pathlib.Path(os.environ['NOVEL_FEEDBACK_FILE']).read_text())
if t['attempt']==0: raise SystemExit(3)
assert f['previous_error']['code']=='driver_exit'
plan={'number':1,'goal':'找到收据','conflict':'失去证人','change':'决定自己作证','volume':'v1','entities':['hero']}
r={'schema':'novel-ledger-v2.result/1','task_id':t['task_id'],'input_hash':t['input_hash'],'lease_owner':t['lease_owner'],'data':{'chapters':[plan]}}
pathlib.Path(os.environ['NOVEL_RESULT_FILE']).write_text(json.dumps(r,ensure_ascii=False))
''')
            try:
                result=run(db,root,{'argv':[sys.executable,str(worker)],'timeout':10,'max_retries':1},1)
                self.assertEqual(result['tasks_completed'],1)
                self.assertIsNone(get(db,'runner'))
                self.assertEqual(engine.next_task(db,root)['stage'],'draft')
            finally:db.close()
    def test_driver_invalid_no_shell(self):
        with self.assertRaises(Error):validate_config({'argv':'echo foo'})
        with self.assertRaises(Error):validate_config({'argv':['python','{secret}']})

if __name__=='__main__':unittest.main()

class RecoveryTests(unittest.TestCase):
    def test_failed_driver_releases_task_for_manual_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);initialize(root,contract());db=connect(root)
            try:
                run(db,root,{'argv':[sys.executable,'-c','raise SystemExit(2)'],'max_retries':0},1)
                self.assertEqual(get(db,'status'),'paused')
                self.assertIsNone(db.execute("SELECT lease_owner FROM tasks WHERE state='pending'").fetchone()[0])
                engine.resume(db);t=engine.next_task(db,root);respond(db,root,t)
                self.assertEqual(engine.next_task(db,root)['stage'],'draft')
            finally:db.close()
    def test_orphan_worker_blocks_claim(self):
        from novel_core.runner import claim
        import subprocess
        child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])
        try:
            with tempfile.TemporaryDirectory() as temp:
                root=Path(temp);initialize(root,contract());db=connect(root)
                try:
                    with transaction(db):put(db,'runner',{'owner':'old','pid':99999999,'worker_pid':child.pid,'until':0})
                    with self.assertRaises(Error):claim(db,'new',60)
                finally:db.close()
        finally:child.terminate();child.wait()
