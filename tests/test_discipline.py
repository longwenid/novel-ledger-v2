"""Skill-level fixes from ch10 friction: discipline in packs, self-service errors, self-description."""
import json
import tempfile
import unittest
from pathlib import Path
from support import *
from novel_core import engine
from novel_core.ledger import seed, apply
from novel_core.memory import pack
from novel_core.store import Error, VERSION, digest, get, head
from novel_core.spatial import move as apply_move


class DisciplineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        initialize(self.path,contract());self.db=connect(self.path)
    def tearDown(self): self.db.close();self.temp.cleanup()
    def task(self): return engine.next_task(self.db,self.path)

    def test_pack_carries_discipline(self):
        t=self.task()
        work=json.loads(rpath(t, 'input_path').read_text(encoding='utf-8'))
        d=work['discipline']
        self.assertIn('pass/fix',d['review'])
        self.assertIn('links ID',d['events'])
        self.assertIn('上限 2 次',d['hooks'])
        self.assertIn('时间词',d['clock'])

    def test_resource_error_names_holder_and_holdings(self):
        s=seed(get(self.db,'contract'))
        p='周行把三十文交给宋禾。'
        with self.assertRaises(Error) as exc:
            apply(s,{'op':'resource','asset':'铜牌','unit':'枚','quantity':'1','from':'ally','from_before':'1',
                     'reason':'x','evidence':ev(p)},p,1)
        self.assertIn('没有已登记的 铜牌',str(exc.exception))
        with self.assertRaises(Error) as exc:
            apply(s,{'op':'resource','asset':'cash','unit':'文','quantity':'30','from':'hero','from_before':'99',
                     'reason':'x','evidence':ev(p)},p,1)
        self.assertIn('cash=100',str(exc.exception))

    def test_move_error_lists_known_links(self):
        s=seed(get(self.db,'contract'))
        p='周行从酒馆走到码头。'
        apply(s,{'op':'entity','entity':{'id':'tavern','name':'白鸥酒馆','kind':'place'},'evidence':ev(p)},p,1)
        apply(s,{'op':'entity','entity':{'id':'harbor','name':'码头','kind':'place'},'evidence':ev(p)},p,1)
        apply(s,{'op':'place','entity':'tavern','kind':'building','evidence':ev(p)},p,1)
        apply(s,{'op':'place','entity':'harbor','kind':'area','evidence':ev(p)},p,1)
        apply(s,{'op':'link','id':'tavern_harbor','a':'tavern','b':'harbor','kind':'path','evidence':ev(p)},p,1)
        apply(s,{'op':'placement','entity':'hero','before':None,'after':{'place':'tavern'},'evidence':ev(p)},p,1)
        with self.assertRaises(Error) as exc:
            apply_move(s,{'entity':'hero','from':'tavern','to':'harbor','route':['tavern','harbor'],
                          'links':['tavern_street'],'evidence':ev(p)})
        self.assertIn('tavern_harbor',str(exc.exception))
        self.assertIn('tavern_street',str(exc.exception))

    def test_status_and_doctor_are_self_describing(self):
        st=engine.status(self.db)
        self.assertEqual(st['skill_version'],VERSION)
        self.assertEqual(st['db_version'],VERSION)  # fresh init pins the creating version
        import novel
        out=novel.dispatch(novel.parser().parse_args(['doctor','--project',str(self.path)]))
        self.assertTrue(out['skill_root'].endswith('novel-ledger-v2'))
        self.assertEqual(out['skill_version'],VERSION)


if __name__=='__main__':unittest.main()


class DependencyInjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        initialize(self.path,contract());self.db=connect(self.path)
        put(self.db,'pilot_approved',True)
    def tearDown(self): self.db.close();self.temp.cleanup()

    def _commit_sources(self):
        from novel_core.ledger import seed, append_events
        state=seed(get(self.db,'contract'))
        with transaction(self.db):
            for i in (1,2,3):
                text=f'第{i}章，周行与宋禾核对了账目细节。'
                events=[{'op':'clock','before':i-1,'after':i,'evidence':{'chapter':i,'start':0,'quote':text}}]
                data={'volume':'v1','summary':f'第{i}章核查账目。','events':events,'prose_hash':digest(text),
                      'facts_review':{'verdict':'pass'},'reader_review':{'verdict':'pass'}}
                self.db.execute('INSERT OR REPLACE INTO chapters VALUES (?,?,?,?,?)',('main',i,text,dump(data),digest(text)))
                self.db.execute('INSERT OR REPLACE INTO plans VALUES (?,?,?)',('main',i,dump(plan(i))))
                append_events(self.db,'main',i,text,events,state)
            p=plan(4);p['dependencies']=[1,2,3]
            self.db.execute('INSERT OR REPLACE INTO plans VALUES (?,?,?)',('main',4,dump(p)))

    def test_facts_gets_summaries_draft_gets_full_text(self):
        from novel_core.memory import pack
        self._commit_sources()
        facts=pack(self.db,'main',4,'facts',{})
        kinds={s['chapter']:s for s in facts['selected'] if s['kind'] in ('required_chapter','dependent_chapter')}
        self.assertEqual(set(kinds),{1,2,3})
        self.assertTrue(all(k['kind']=='dependent_chapter' for k in kinds.values()))
        self.assertTrue(all('text' not in k and k['summary'] for k in kinds.values()))
        draft=pack(self.db,'main',4,'draft',{})
        kinds={s['chapter']:s for s in draft['selected'] if s['kind']=='required_chapter'}
        self.assertEqual(set(kinds),{1,2,3})
        self.assertTrue(all(len(k['text'])>10 for k in kinds.values()))

    def test_reader_discipline_trimmed_to_review(self):
        from novel_core.memory import pack
        self._commit_sources()
        reader=pack(self.db,'main',4,'reader',{'prose':'第4章正文。'})
        self.assertEqual(set(reader['discipline']),{'review'})
        facts=pack(self.db,'main',4,'facts',{})
        self.assertGreater(len(facts['discipline']),1)


if __name__=='__main__':unittest.main()
