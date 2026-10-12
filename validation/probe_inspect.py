"""Inspect a probe project's state: pending task, batch, logs."""
import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
project = Path(sys.argv[1] if len(sys.argv) > 1 else
               r'C:\Users\longwen\.zcode\skills\novel-ledger-v2\.probe-tmp\delivery-probe\book')
db = sqlite3.connect(project / 'novel.sqlite3')
db.row_factory = sqlite3.Row
print('status:', db.execute("SELECT value FROM meta WHERE key='status'").fetchone()[0])
print('pause_reason:', db.execute("SELECT value FROM meta WHERE key='pause_reason'").fetchone()[0])
print('work_branch:', db.execute("SELECT value FROM meta WHERE key='work_branch'").fetchone()[0])
print('batch:', db.execute("SELECT value FROM meta WHERE key='chapter_batch:main'").fetchone())
print('batch_through:', db.execute("SELECT value FROM meta WHERE key='batch_through'").fetchone())
print('\ntasks:')
for row in db.execute('SELECT id,stage,chapter,state FROM tasks ORDER BY rowid DESC LIMIT 12'):
    print('  ', dict(row))
print('\ndrafts:', [r[0] for r in db.execute('SELECT number FROM drafts')])
print('\nplans:', [r[0] for r in db.execute('SELECT number FROM plans ORDER BY number')])
print('\nlast logs:')
for row in db.execute('SELECT action,data FROM logs ORDER BY id DESC LIMIT 12'):
    print('  ', row['action'], str(row['data'])[:220])
