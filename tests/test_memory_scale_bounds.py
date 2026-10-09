"""Targeted knowledge selection and historical pagination at growing scale."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from support import contract, plan, ev
from novel_core.store import initialize, connect, transaction, dump, digest, get
from novel_core.ledger import seed, append_events
from novel_core.memory import pack, recall


class MemoryScaleBoundsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)
        self.contract = contract()
        self.contract.update(context_mode='bounded', character_mode='profiled')
        initialize(self.project, self.contract)
        self.db = connect(self.project)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def history(self, count):
        prose = '周行核对了旧账，仍误信钥匙在东门。'
        events = [
            {'op': 'knowledge', 'entity': 'hero', 'topic': f'archive{i}',
             'before': None, 'stance': 'knows', 'claim': f'旧账{i}已核对',
             'source': '合成工程夹具', 'evidence': ev(prose)}
            for i in range(count)
        ]
        events.append({'op': 'knowledge', 'entity': 'hero', 'topic': 'key',
                       'before': None, 'stance': 'believes',
                       'claim': '钥匙在东门', 'source': '合成工程夹具',
                       'evidence': ev(prose)})
        data = {'volume': 'v1', 'summary': '核对旧账与误信。', 'events': events,
                'prose_hash': digest(prose), 'facts_review': {'verdict': 'pass'},
                'reader_review': {'verdict': 'pass'}}
        with transaction(self.db):
            self.db.execute('INSERT INTO chapters VALUES (?,?,?,?,?)',
                            ('main', 1, prose, dump(data), digest(prose)))
            append_events(self.db, 'main', 1, prose, events, seed(self.contract))
        return events

    def test_profiled_person_respects_explicit_topics_with_large_history(self):
        self.history(1000)
        p = plan(2)
        p['topics'] = ['key']
        self.db.execute('INSERT INTO plans VALUES (?,?,?)', ('main', 2, dump(p)))
        work = pack(self.db, 'main', 2, 'draft', {})
        self.assertEqual(work['stage'], 'draft')
        self.assertEqual(set(work['state']['knowledge']), {'hero:key'})
        self.assertEqual(work['state']['knowledge']['hero:key']['stance'], 'believes')
        self.assertEqual(next(o for o in work['omitted'] if o['kind']=='knowledge_topics')['count'],1000)
        # The full canonical history remains available through targeted recall.
        self.assertEqual(recall(self.db, 'main', entity='hero', topic='archive0')['total'], 1)

    def test_unspecified_topics_preserve_all_person_knowledge(self):
        self.history(30)
        self.db.execute('INSERT INTO plans VALUES (?,?,?)', ('main', 2, dump(plan(2))))
        work = pack(self.db, 'main', 2, 'draft', {})
        if work['stage']=='context_scan':
            bundle=get(self.db,'context_bundle:'+work['payload']['bundle'])
            state=json.loads(next(d['text'] for d in bundle['documents'] if d['field']=='state'))
        else:state=work['state']
        self.assertEqual(len(state['knowledge']), 31)
        self.assertIn('hero:key', state['knowledge'])

    def test_dialogue_knowledge_topics_are_included_alongside_plan_topics(self):
        self.history(30)
        p=plan(2);p['topics']=['key']
        p['scenes']=[{'present':['hero'],'dialogue_intents':[{'entity':'hero','knowledge_topics':['archive0']}]}]
        self.db.execute('INSERT INTO plans VALUES (?,?,?)',('main',2,dump(p)))
        work=pack(self.db,'main',2,'draft',{})
        self.assertEqual(set(work['state']['knowledge']),{'hero:key','hero:archive0'})

    def test_paginated_recall_retains_only_requested_records(self):
        self.history(500)
        real_loads = json.loads
        live = {'current': 0, 'peak': 0}

        class TrackedEvent(dict):
            def __init__(self, value):
                super().__init__(value)
                live['current'] += 1
                live['peak'] = max(live['peak'], live['current'])

            def __del__(self):
                live['current'] -= 1

        def tracked_loads(value, *args, **kwargs):
            parsed = real_loads(value, *args, **kwargs)
            return TrackedEvent(parsed) if isinstance(parsed, dict) and parsed.get('op') == 'knowledge' else parsed

        # Isolate query allocation from canonical-state replay allocation.
        with patch('novel_core.memory.replay', return_value=seed(self.contract)), \
                patch('novel_core.memory.json.loads', side_effect=tracked_loads):
            page = recall(self.db, 'main', entity='老周', offset=200, limit=20)
        self.assertEqual(page['total'], 501)
        self.assertEqual(page['next_offset'], 220)
        self.assertEqual([r['index'] for r in page['records']], list(range(200, 220)))
        self.assertLessEqual(live['peak'], 22)
        newest = recall(self.db, 'main', topic='key', newest=True)
        self.assertEqual(newest['records'][0]['index'], 500)
        last = recall(self.db, 'main', entity='hero', offset=500, limit=20)
        self.assertEqual(len(last['records']), 1)
        self.assertIsNone(last['next_offset'])
        absent = recall(self.db, 'main', entity='hero', offset=501, limit=20)
        self.assertEqual(absent['records'], [])
        self.assertEqual(absent['total'], 501)
        self.assertIsNone(absent['next_offset'])


if __name__ == '__main__':
    unittest.main()
