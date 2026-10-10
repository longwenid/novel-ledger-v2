"""A killed writer must not leave a canon chapter without its events or receipt."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from support import *

class AtomicCommitTests(unittest.TestCase):
    def test_process_death_between_prose_and_events_rolls_back(self):
        with tempfile.TemporaryDirectory() as temp:
            project=Path(temp);initialize(project,contract());db=connect(project)
            try:
                for _ in range(2):respond(db,project,engine.next_task(db,project))
                task=engine.next_task(db,project)
                self.assertEqual(task['stage'],'chapter_review')
                taskfile=project/'pending-task.json';taskfile.write_text(json.dumps(task), encoding='utf-8')
                code='''import os,sys,json
sys.path.insert(0,sys.argv[1])
from support import *
project=Path(sys.argv[2]);db=connect(project);task=json.loads(Path(sys.argv[3]).read_text(encoding='utf-8'))
engine.append_events=lambda *args,**kwargs: os._exit(17)
respond(db,project,task)
'''
                proc=subprocess.run([sys.executable,'-c',code,str(Path(__file__).resolve().parent),str(project),str(taskfile)],capture_output=True,text=True,encoding='utf-8')
                self.assertEqual(proc.returncode,17,proc.stderr)
                self.assertEqual(engine.head(db,'main'),0)
                self.assertEqual(engine.next_task(db,project)['task_id'],task['task_id'])
                respond(db,project,task)
                self.assertEqual(engine.head(db,'main'),1)
                self.assertTrue(engine.audit(db)['ok'])
            finally:db.close()

if __name__=='__main__':unittest.main()
