"""Exact local projections and cold replay; these do not evaluate prose quality."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from support import contract, initialize, connect, transaction, dump, plan, get, put
from novel_core import projections, ledger, budget
from novel_core.store import Error, digest


class LocalIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        c=contract();c['volumes'][0]['word_budget']=10000
        initialize(self.path,c);self.db=connect(self.path);projections.ensure_schema(self.db)
        ledger._REPLAY_CACHE.clear()

    def tearDown(self):
        self.db.close();self.tmp.cleanup();ledger._REPLAY_CACHE.clear()

    def append(self,n=1):
        state=ledger.replay(self.db,'main')
        first=self.db.execute('SELECT COALESCE(MAX(number),0)+1 FROM chapters').fetchone()[0]
        with transaction(self.db):
            for chapter in range(first,first+n):
                prose=f'第{chapter}日，周行和宋禾对照 Straße 的旧账，记下当日结论。'
                topic=f'旧账:{chapter % 3}'
                old=state['knowledge'].get('hero:'+topic)
                evidence={'chapter':chapter,'start':0,'quote':prose}
                events=[{'op':'clock','before':state['clock'],'after':chapter,'evidence':evidence,
                         'refs':['hero','hero','ally'],'topics':['共同账目']},
                        {'op':'knowledge','entity':'hero','topic':topic,'before':old['claim'] if old else None,
                         'claim':str(chapter),'stance':'knows','source':'当面核对','evidence':evidence}]
                self.db.execute('INSERT INTO chapters VALUES (?,?,?,?,?)',('main',chapter,prose,dump({'volume':'v1','han_characters':budget.han_count(prose)}),digest(prose)))
                self.db.execute('INSERT INTO plans VALUES (?,?,?)',('main',chapter,dump(plan(chapter))))
                ledger.append_events(self.db,'main',chapter,prose,events,state)

    def oracle(self,query='',entity=None,topic=None,start=1,end=999,offset=0,limit=20,newest=False):
        records=[]
        for row in self.db.execute('SELECT * FROM events WHERE branch=? AND chapter BETWEEN ? AND ? ORDER BY '+('chapter DESC,idx DESC' if newest else 'chapter,idx'),('main',start,end)):
            event=json.loads(row['data'])
            if entity and entity not in projections.event_references(event):continue
            if topic and topic not in projections.event_topics(event):continue
            if query and query.casefold() not in dump(event).casefold():continue
            records.append({'chapter':row['chapter'],'index':row['idx'],'event':event,'event_hash':row['hash']})
        total=len(records)
        return {'records':records[offset:offset+limit],'total':total,'offset':offset,'next_offset':offset+limit if offset+limit<total else None,'absence_is_not_proof':True}

    def test_exact_filters_unicode_casefold_order_and_shared_refs(self):
        self.append(12)
        cases=[{}, {'entity':'hero'}, {'entity':'ally','topic':'共同账目'},
               {'query':'STRASSE'}, {'query':'第1'}, {'topic':'旧账:1','query':'旧账'},
               {'entity':'hero','topic':'旧账:2','start':3,'end':10},
               {'entity':'missing'}, {'query':'not present'}]
        for selector in cases:
            for newest in (False,True):
                for offset in (0,2,50):
                    args=dict(selector,newest=newest,offset=offset,limit=3)
                    self.assertEqual(projections.recall_events(self.db,'main',**args),self.oracle(**args))
        # Duplicate references to hero remain a single matching clock event.
        self.assertEqual(projections.recall_events(self.db,'main',entity='hero')['total'],24)

    def test_spatial_and_new_entity_references_are_preserved(self):
        event={'entity':{'id':'new','name':'新人'},'from':'hero','to':'ally','refs':['hero'],
               'parent':'parent','a':'a','b':'b','observer':'watcher','target':'target',
               'before':{'holder':'before_holder'},'after':{'place':'after_place'},'route':['route1']}
        self.assertEqual(projections.event_references(event),{'new','hero','ally','parent','a','b','watcher','target','before_holder','after_place','route1'})

    def test_index_refresh_only_reads_dirty_events_and_pages_parse_only_records(self):
        self.append(50);projections.recall_events(self.db,'main',entity='hero')
        with patch('novel_core.projections.json.loads',wraps=json.loads) as loads:
            page=projections.recall_events(self.db,'main',entity='hero',offset=10,limit=4)
        self.assertEqual(loads.call_count,4)
        self.assertEqual(page['total'],100)
        row=self.db.execute('SELECT * FROM events WHERE chapter=1 AND idx=0').fetchone()
        altered=json.loads(row['data']);altered['topics']=['new_topic']
        self.db.execute('UPDATE events SET data=? WHERE chapter=1 AND idx=0',(dump(altered),))
        with patch('novel_core.projections.json.loads',wraps=json.loads) as loads:
            out=projections.recall_events(self.db,'main',topic='new_topic',limit=1)
        self.assertEqual(loads.call_count,2) # one dirty source + one returned row
        self.assertEqual(out['total'],1)
        indexed=self.db.execute('SELECT source_hash FROM local_event_rows WHERE chapter=1 AND idx=0').fetchone()[0]
        self.assertEqual(indexed,digest(dump(altered)))
        # Recall's projection is not permission to use a modified event chain.
        with self.assertRaises(Error):ledger.replay(self.db,'main')

    def test_topic_and_query_recall_cannot_deliver_tampered_canon(self):
        from novel_core.memory import recall
        self.append(2);ledger.replay(self.db,'main')
        row=self.db.execute('SELECT data FROM events WHERE chapter=1 AND idx=0').fetchone()
        value=json.loads(row[0]);value['after']=999
        self.db.execute('UPDATE events SET data=? WHERE chapter=1 AND idx=0',(dump(value),))
        for selector in ({'topic':'共同账目'},{'query':'Straße'},{}):
            with self.assertRaises(Error) as error:recall(self.db,'main',**selector)
            self.assertEqual(error.exception.code,'event_chain')

    def test_transaction_rollback_restores_projection_and_generation(self):
        self.append(2);before=projections.recall_events(self.db,'main',entity='hero')
        with self.assertRaises(RuntimeError):
            with transaction(self.db):
                self.db.execute('DELETE FROM events WHERE chapter=2')
                self.assertEqual(projections.recall_events(self.db,'main',entity='hero')['total'],2)
                raise RuntimeError('abort')
        self.assertEqual(projections.recall_events(self.db,'main',entity='hero'),before)

    def test_schema_install_is_idempotent_and_does_not_commit_surrounding_txn(self):
        before=self.db.total_changes;projections.ensure_schema(self.db)
        self.assertEqual(before,self.db.total_changes)
        with self.assertRaises(RuntimeError):
            with transaction(self.db):
                put(self.db,'temporary','not committed');projections.ensure_schema(self.db)
                raise RuntimeError('abort')
        self.assertIsNone(get(self.db,'temporary'))

    def test_legacy_projection_rebuild_and_deleted_trigger_invalidate(self):
        self.append(3);projections.recall_events(self.db,'main',entity='hero');ledger.replay(self.db,'main')
        self.db.execute('DROP TRIGGER local_events_update')
        row=self.db.execute('SELECT data FROM events WHERE chapter=1 AND idx=0').fetchone()
        value=json.loads(row[0]);value['topics']=['old_changed']
        self.db.execute('UPDATE events SET data=? WHERE chapter=1 AND idx=0',(dump(value),))
        projections.ensure_schema(self.db)
        self.assertEqual(projections.recall_events(self.db,'main',topic='old_changed')['total'],1)
        ledger._REPLAY_CACHE.clear()
        with self.assertRaises(Error):ledger.replay(self.db,'main')

    def test_cold_snapshot_reads_zero_events_and_pure_append_verifies_suffix(self):
        self.append(8);ledger.replay(self.db,'main')
        self.db.close();self.db=connect(self.path);projections.ensure_schema(self.db);ledger._REPLAY_CACHE.clear()
        with patch('novel_core.ledger.apply',wraps=ledger.apply) as apply:
            state=ledger.replay(self.db,'main')
        self.assertEqual(apply.call_count,0);self.assertEqual(state['clock'],8)
        self.append(1);ledger._REPLAY_CACHE.clear()
        with patch('novel_core.ledger.apply',wraps=ledger.apply) as apply:
            state=ledger.replay(self.db,'main')
        self.assertEqual(apply.call_count,2);self.assertEqual(state['clock'],9)
        with patch('novel_core.ledger.apply',wraps=ledger.apply) as apply:
            verified=ledger.replay(self.db,'main',verify=True)
        self.assertEqual(apply.call_count,18);self.assertEqual(state,verified)

    def test_corrupt_snapshot_falls_back_but_prose_corruption_is_not_hidden(self):
        self.append(3);ledger.replay(self.db,'main');ledger._REPLAY_CACHE.clear()
        self.db.execute("UPDATE local_state_snapshots SET state='{}'")
        with patch('novel_core.ledger.apply',wraps=ledger.apply) as apply:
            state=ledger.replay(self.db,'main')
        self.assertEqual(apply.call_count,6);self.assertEqual(state['clock'],3)
        self.db.execute("UPDATE chapters SET prose=prose||'篡改' WHERE number=1")
        with self.assertRaises(Error) as found:ledger.replay(self.db,'main')
        self.assertEqual(found.exception.code,'event_chain')

    def test_replace_does_not_bypass_canonical_invalidation(self):
        self.append(2);ledger.replay(self.db,'main')
        row=dict(self.db.execute('SELECT * FROM events WHERE chapter=2 AND idx=1').fetchone())
        event=json.loads(row['data']);event['claim']='changed'
        row['data']=dump(event)
        self.db.execute('INSERT OR REPLACE INTO events VALUES (?,?,?,?,?,?)',tuple(row.values()))
        with self.assertRaises(Error):ledger.replay(self.db,'main')

    def test_damaged_event_and_budget_indexes_rebuild_from_sources(self):
        self.append(3);original=projections.recall_events(self.db,'main',entity='hero')
        self.db.execute("DELETE FROM local_event_entities WHERE entity='hero'")
        self.assertEqual(projections.recall_events(self.db,'main',entity='hero'),original)
        total=budget.overview(self.db,'main')['written_han']
        self.db.execute('UPDATE local_chapter_counts SET han=99999')
        self.assertEqual(budget.overview(self.db,'main')['written_han'],total)

    def test_seed_contract_changes_and_branches_do_not_share_snapshot(self):
        self.append(2);original=ledger.replay(self.db,'main')
        c=get(self.db,'contract');c['initial_facts']=[{'entity':'hero','key':'motive','value':'归还'}];put(self.db,'contract',c)
        self.assertEqual(ledger.replay(self.db,'main')['facts']['hero:motive'],'归还')
        with transaction(self.db):
            self.db.execute("INSERT INTO branches VALUES ('candidate','main',1,1,NULL,'test','working')")
            self.db.execute("INSERT INTO chapters SELECT 'candidate',number,prose,data,hash FROM chapters WHERE number=1")
            self.db.execute("INSERT INTO events SELECT 'candidate',chapter,idx,data,prev_hash,hash FROM events WHERE chapter=1")
        self.assertEqual(ledger.replay(self.db,'candidate')['clock'],1)
        self.assertEqual(ledger.replay(self.db,'main')['clock'],original['clock'])

    def test_budget_counts_rebuild_changed_chapter_and_plan_without_touching_contract(self):
        self.append(4);c=get(self.db,'contract');first=budget.overview(self.db,'main')
        with patch('novel_core.projections.json.loads',wraps=json.loads) as loads:
            second=budget.overview(self.db,'main')
        self.assertEqual(loads.call_count,1) # contract only, no chapter/plan material
        self.assertEqual(first,second)
        p=plan(4);p['volume_end']=True
        self.db.execute('UPDATE plans SET data=? WHERE number=4',(dump(p),))
        self.assertTrue(budget.overview(self.db,'main')['volumes'][0]['closed'])
        row=self.db.execute('SELECT data FROM chapters WHERE number=2').fetchone()
        data=json.loads(row[0]);data['han_characters']=99999
        self.db.execute('UPDATE chapters SET data=? WHERE number=2',(dump(data),))
        self.assertEqual(budget.overview(self.db,'main')['written_han'],first['written_han'])
        self.assertEqual(get(self.db,'contract'),c)

    def test_usage_additive_projection_matches_full_sql_after_mutations_rollback(self):
        def exact():
            r=self.db.execute("SELECT COUNT(*) tasks,COALESCE(SUM(LENGTH(input)+1),0) inputs,COALESCE(SUM(CASE WHEN receipt IS NULL THEN 0 ELSE LENGTH(receipt)+1 END),0) receipts,COALESCE(SUM(CASE WHEN stage IN ('context_scan','context_reduce') THEN LENGTH(input)+1 ELSE 0 END),0) preparation FROM tasks").fetchone()
            self.assertEqual(projections.usage_summary(self.db),dict(r))
        self.db.execute('DROP INDEX one_pending')
        for i,stage in enumerate(('draft','context_scan','context_reduce')):
            self.db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?)',(str(i),'main',stage,1,'h','汉😀'*5000,'pending',None,None,None,None))
        exact()
        self.db.execute('INSERT OR REPLACE INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?)',('0','main','draft',1,'h','替换文本','done',None,None,None,None));exact()
        for sql,args in [('UPDATE tasks SET state=? WHERE id=?',('done','0')),
                         ('UPDATE tasks SET receipt=? WHERE id=?',(dump({'prose':'文字'}),'0')),
                         ('UPDATE tasks SET input=?,stage=? WHERE id=?',('更长正文','reader','1')),
                         ('DELETE FROM tasks WHERE id=?',('2',))]:
            self.db.execute(sql,args);exact()
        self.db.execute("UPDATE tasks SET id='renamed' WHERE id='0'");exact()
        self.db.execute("DELETE FROM tasks WHERE id='renamed'");exact()
        before=projections.usage_summary(self.db)
        with self.assertRaises(RuntimeError):
            with transaction(self.db):
                self.db.execute("UPDATE tasks SET receipt='新回执'");exact()
                raise RuntimeError('abort')
        self.assertEqual(projections.usage_summary(self.db),before)


if __name__=='__main__':unittest.main()
