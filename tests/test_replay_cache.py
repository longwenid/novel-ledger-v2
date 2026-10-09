"""Replay cache: append-only keying, isolation, and no masking of tampering."""
import json
import tempfile
import unittest
from pathlib import Path
from support import *
from novel_core import engine
from novel_core.ledger import replay
from novel_core.memory import pack
from novel_core.store import digest


class ReplayCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        initialize(self.path,contract());self.db=connect(self.path)
        self._commit(3)
    def tearDown(self): self.db.close();self.temp.cleanup()

    def _commit(self,n):
        from novel_core.ledger import append_events
        state=replay(self.db,'main')
        with transaction(self.db):
            base=engine.head(self.db,'main')
            for i in range(base+1,base+n+1):
                text=f'第{i}章，周行与宋禾核对了账目细节。'
                events=[{'op':'clock','before':i-1,'after':i,'evidence':{'chapter':i,'start':0,'quote':text}}]
                data={'volume':'v1','summary':f'第{i}章核查账目。','events':events,'prose_hash':digest(text),
                      'facts_review':{'verdict':'pass'},'reader_review':{'verdict':'pass'}}
                self.db.execute('INSERT OR REPLACE INTO chapters VALUES (?,?,?,?,?)',('main',i,text,dump(data),digest(text)))
                self.db.execute('INSERT OR REPLACE INTO plans VALUES (?,?,?)',('main',i,dump(plan(i))))
                append_events(self.db,'main',i,text,events,state)

    def test_cached_replay_matches_full_verification(self):
        first=replay(self.db,'main')                              # miss: verifies, populates
        self.assertEqual(digest(first),digest(replay(self.db,'main')))          # hit: same state
        self.assertEqual(digest(first),digest(replay(self.db,'main',verify=True)))
        mutated=first;mutated['clock']=999                        # callers must not see cache
        self.assertNotEqual(mutated['clock'],replay(self.db,'main')['clock'])

    def test_cache_invalidates_on_append_only_growth(self):
        before=digest(replay(self.db,'main'))
        self._commit(2)
        self.assertNotEqual(before,digest(replay(self.db,'main')))
        self.assertEqual(digest(replay(self.db,'main')),digest(replay(self.db,'main',verify=True)))

    def test_audit_detects_chain_tampering_despite_cache(self):
        replay(self.db,'main')                                    # warm the cache
        row=self.db.execute("SELECT chapter,idx,data FROM events WHERE branch='main' ORDER BY chapter,idx LIMIT 1").fetchone()
        tampered=json.dumps({**json.loads(row['data']),'after':99})
        self.db.execute("UPDATE events SET data=? WHERE branch='main' AND chapter=? AND idx=?",(tampered,row['chapter'],row['idx']))
        with self.assertRaises(Exception) as exc:
            engine.audit(self.db)
        self.assertEqual(getattr(exc.exception,'code',None),'event_chain')

    def test_pack_labels_derived_materials(self):
        data=pack(self.db,'main',4,'draft',{})
        summaries=[s for s in data['selected'] if s['kind']=='recent_summary']
        self.assertTrue(summaries)
        self.assertTrue(all(s.get('derived_view') for s in summaries))
        self.assertIn('派生视图',data['guide'])  # 事实边界并入 guide 单节

if __name__=='__main__':unittest.main()
