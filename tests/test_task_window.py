"""Complete semantic pages and quality-first observations without size gates."""
import json
import tempfile
import unittest
from pathlib import Path
from support import contract, initialize, connect, put, dump, engine, result
from novel import dispatch, parser
from novel_core import contextflow
from novel_core.store import Error


class TaskWindowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        c=contract();c['context_mode']='bounded'
        initialize(self.path,c);self.db=connect(self.path)
    def tearDown(self):self.db.close();self.temp.cleanup()

    def pending(self,work):
        self.db.execute("INSERT INTO tasks(id,branch,stage,chapter,input_hash,input,state) VALUES ('t','main','book_review',1000,'h',?,'pending')",(dump(work),))

    def test_large_escaped_semantic_unit_is_returned_whole(self):
        self.pending({'retrieval_allowance':100000})
        value={'text':'"\\\n'*10000}
        out=contextflow.bounded_read(self.db,value,'t')
        self.assertEqual(out,value)
        self.assertGreater(len(contextflow.wire(out)),48000)

    def test_required_repeated_reads_are_observed_without_character_rejection(self):
        work={'padding':'合'*22000,'retrieval_allowance':4504096}
        self.pending(work)
        for _ in range(100):
            out=contextflow.bounded_read(self.db,{'text':'字'*7000},'t')
            self.assertEqual(out,{'text':'字'*7000})
        self.assertEqual(contextflow.get(self.db,'context_reads:t'),100*len(contextflow.wire(out)))

    def test_large_result_and_loaded_supplements_are_observed(self):
        work={'padding':'合'*10000};self.pending(work)
        put(self.db,'context_reads:t',20000)
        receipt={'data':{'text':'字'*13000}}
        task={'id':'t'}
        contextflow.charge_result(self.db,task,work,receipt)
        put(self.db,'context_reads:t',2400000)
        contextflow.charge_result(self.db,task,work,receipt)
        self.assertEqual(contextflow.get(self.db,'context_attempt_chars:t'),2*len(contextflow.wire(receipt)))

    def test_cli_requires_scoped_history_and_state(self):
        for tail in ([],['--state']):
            with self.assertRaises(Error) as exc:
                dispatch(parser().parse_args(['recall','--project',str(self.path)]+tail))
            self.assertEqual(exc.exception.code,'retrieval_scope')
        scoped=dispatch(parser().parse_args(['recall','--project',str(self.path),'--state','--entity','hero']))
        self.assertEqual(set(scoped['entities']),{'hero'})
        self.assertTrue(scoped['absence_is_not_proof'])

    def test_usage_is_observed_chars_and_never_billing(self):
        task=engine.next_task(self.db,self.path)
        before=contextflow.usage(self.db)
        self.assertEqual(before['tasks'],1)
        raw=self.db.execute('SELECT input FROM tasks WHERE id=?',(task['task_id'],)).fetchone()[0]
        self.assertEqual(before['task_input_chars'],len(raw)+1)
        engine.next_task(self.db,self.path)  # same pending task is not a new load
        contextflow.bounded_read(self.db,{'record':'证据'},task['task_id'])
        after=contextflow.usage(self.db)
        self.assertEqual(after['task_input_chars'],before['task_input_chars'])
        self.assertGreater(after['supplemental_read_chars'],0)
        self.assertIsNone(after['billed_tokens'])
        self.assertIsNone(after['session_handoffs'])
        diagnostic=dispatch(parser().parse_args(['doctor','--project',str(self.path)]))
        self.assertFalse(diagnostic['independent_review_verified'])
        self.assertEqual(engine.status(self.db)['usage'],after)


if __name__=='__main__':unittest.main()
