"""Batch-review chunk granularity: coverage unchanged, cost adjustable."""
import json
import tempfile
import unittest
from pathlib import Path
from support import *
from novel_core import engine
from novel_core.store import Error, dump, digest, put, transaction


class ReviewChunkTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        initialize(self.path,contract());self.db=connect(self.path)
        put(self.db,'pilot_approved',True)
    def tearDown(self): self.db.close();self.temp.cleanup()

    def _commit_book(self,n=10):
        state=None
        from novel_core.ledger import seed, append_events
        state=seed(get(self.db,'contract'))
        text='周行在档案室核对记录，宋禾在门房登记来访。'
        events=[{'op':'clock','before':0,'after':n,'evidence':ev(text,n)}]
        data={'volume':'v1','summary':f'第{n}章例行核查。','events':events,'prose_hash':digest(text),
              'facts_review':{'verdict':'pass'},'reader_review':{'verdict':'pass'}}
        with transaction(self.db):
            for i in range(1,n+1):
                self.db.execute('INSERT OR REPLACE INTO chapters VALUES (?,?,?,?,?)',('main',i,text,dump(data|{'summary':f'第{i}章例行核查。'}),digest(text)))
                self.db.execute('INSERT OR REPLACE INTO plans VALUES (?,?,?)',('main',i,dump(plan(i))))
            self.db.execute('INSERT OR REPLACE INTO plans VALUES (?,?,?)',('main',n+1,dump(plan(n+1))))
            for i in range(1,n+1):
                chapter_events=[{'op':'clock','before':i-1,'after':i,'evidence':{'chapter':i,'start':0,'quote':text}}]
                append_events(self.db,'main',i,text,chapter_events,state)

    def test_default_chunk_is_single_chapter(self):
        self._commit_book()
        t=engine.next_task(self.path,self.db) if False else engine.next_task(self.db,self.path)
        self.assertEqual(t['stage'],'batch_review')
        payload=json.loads(rpath(t, 'input_path').read_text())['payload']
        self.assertEqual((payload['first'],payload['through']),(1,1))

    def test_chunk_of_three_covers_and_advances(self):
        self._commit_book()
        with transaction(self.db): put(self.db,'review_chunk',3)
        t=engine.next_task(self.db,self.path)
        self.assertEqual(t['stage'],'batch_review')
        payload=json.loads(rpath(t, 'input_path').read_text())['payload']
        self.assertEqual((payload['first'],payload['through']),(1,3))
        self.assertIn('【第1章】',payload['prose']);self.assertIn('【第3章】',payload['prose'])
        # 模拟前两个 chunk 已审:游标推进到 7..9,再 10 单独成块
        with transaction(self.db):
            self.db.execute("UPDATE tasks SET state='cancelled' WHERE state='pending'")
            for end in (3,6):
                self.db.execute('INSERT INTO reviews VALUES (?,?,?,?,?)',
                                ('main','batch_chunk',end,engine.review_hash(self.db,'main',end-2,end),
                                 dump({'verdict':'pass','first':end-2,'through':end})))
        t=engine.next_task(self.db,self.path)
        payload=json.loads(rpath(t, 'input_path').read_text())['payload']
        self.assertEqual((payload['first'],payload['through']),(7,9))
        with transaction(self.db):
            self.db.execute("UPDATE tasks SET state='cancelled' WHERE state='pending'")
            self.db.execute('INSERT INTO reviews VALUES (?,?,?,?,?)',
                            ('main','batch_chunk',9,engine.review_hash(self.db,'main',7,9),
                             dump({'verdict':'pass','first':7,'through':9})))
        t=engine.next_task(self.db,self.path)
        payload=json.loads(rpath(t, 'input_path').read_text())['payload']
        self.assertEqual((payload['first'],payload['through']),(10,10))

    def test_config_bounds(self):
        import novel
        for bad in ('0','6'):
            with self.assertRaises(Error) as exc:
                novel.dispatch(novel.parser().parse_args(['config','--project',str(self.path),'--key','review_chunk','--value',bad]))
            self.assertEqual(exc.exception.code,'config')
        out=novel.dispatch(novel.parser().parse_args(['config','--project',str(self.path),'--key','review_chunk','--value','3']))
        self.assertEqual(out['review_chunk'],3)


if __name__=='__main__':unittest.main()
