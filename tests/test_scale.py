import json
import tempfile
import unittest
from pathlib import Path
from support import *
from novel_core.store import digest, head, chapters, Error
from novel_core.ledger import seed, append_events, replay
from novel_core.memory import pack, recall

class ScaleTests(unittest.TestCase):
    def test_thousand_chapters_replay_recall_bounded_context(self):
        with tempfile.TemporaryDirectory() as temp:
            project=Path(temp);initialize(project,contract());db=connect(project)
            try:
                state=seed(contract())
                with transaction(db):
                    for n in range(1,1001):
                        prose=f'第{n}日，周行核对旧收据，宋禾在账上记下他的去向。'
                        e={'op':'clock','before':n-1,'after':n,'evidence':ev(prose,n)}
                        events=[e]
                        if n==1: events.append({'op':'hook','id':'receipt','before':None,'text':'交出旧收据','status':'open','due':1000,'evidence':ev(prose,n)})
                        if n==1000: events.append({'op':'hook','id':'receipt','before':'open','status':'paid','evidence':ev(prose,n)})
                        data={'events':events,'summary':'核对收据与去向','volume':'v1','prose_hash':digest(prose), 'facts_review':review(engine.FACT_CHECKS,prose,n), 'reader_review':review(engine.READER_CHECKS,prose,n)}
                        db.execute('INSERT INTO chapters VALUES (?,?,?,?,?)',('main',n,prose,dump(data),digest(prose)))
                        db.execute('INSERT INTO plans VALUES (?,?,?)',('main',n,dump(plan(n))))
                        append_events(db,'main',n,prose,events,state)
                    p=plan(1001);p['dependencies']=[1];p['topics']=['receipt'];p['hooks']=['receipt']
                    db.execute('INSERT INTO plans VALUES (?,?,?)',('main',1001,dump(p)))
                recovered=replay(db,'main')
                self.assertEqual(recovered,state);self.assertEqual(recovered['hooks']['receipt']['status'],'paid')
                self.assertTrue(engine.audit(db)['ok'])
                self.assertEqual(recall(db,'main',topic='receipt')['total'],2)
                data=pack(db,'main',1001,'draft',{})
                self.assertFalse(data['context_capacity']['limits_enforced'])
                self.assertEqual(data['state']['hooks']['receipt']['status'],'paid')
                self.assertEqual(data['selected'][0]['chapter'],1)
                self.assertEqual(data['selected'][0]['text'],db.execute('SELECT prose FROM chapters WHERE number=1').fetchone()[0])
                self.assertEqual(recall(db,'main',entity='老周')['total'],0) # Unrelated clock events aren't invented as personal facts.
                self.assertTrue(recall(db,'main',entity='老周')['absence_is_not_proof'])
            finally:db.close()

if __name__=='__main__':unittest.main()
