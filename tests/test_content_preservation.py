"""Content is retained without arbitrary character-length rejection or slicing."""
import json
import tempfile
import unittest
from pathlib import Path
from support import *
from novel_core.store import validate_style


class ContentPreservationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        initialize(self.path,contract());self.db=connect(self.path)

    def tearDown(self):
        self.db.close();self.temp.cleanup()

    def task(self):return engine.next_task(self.db,self.path)

    def test_author_style_name_and_samples_are_preserved_in_full(self):
        style={'voice':'叙事声音的必要特征。'*20,'traits':['动作推动叙事'],
               'sample':'完整开场样本。'*500,'sample_close':'完整结尾样本。'*300}
        self.assertEqual(validate_style(style),style)

    def test_pilot_anchor_selects_complete_paragraphs(self):
        opening='开场段落保持完整。'*100
        closing='结尾段落保留完整。'*100
        prose='第一章 雨夜\n\n'+opening+'\n\n'+closing
        respond(self.db,self.path,self.task())
        respond(self.db,self.path,self.task(),prose=prose)
        respond(self.db,self.path,self.task())
        engine.approve(self.db)
        anchor=get(self.db,'style_anchor')
        self.assertEqual(anchor['sample'],opening)
        self.assertEqual(anchor['sample_close'],closing)
        self.assertEqual(self.db.execute("SELECT prose FROM chapters WHERE branch='main' AND number=1").fetchone()[0],prose)

    def test_summary_and_review_memory_are_preserved_beyond_old_limits(self):
        respond(self.db,self.path,self.task(),ending=True)
        prose='周行把完整凭证交给宋禾，两人承担追索的代价。'
        respond(self.db,self.path,self.task(),prose=prose)
        task=self.task();work=envelope(task)
        summary='必要的选择、代价、关系和因果依据。'*150
        data=review(work['payload']['facts_checks'],prose,1)
        data.update(events=[],summary=summary)
        submit_review(self.db,task,data)
        chapter=json.loads(self.db.execute("SELECT data FROM chapters WHERE branch='main' AND number=1").fetchone()[0])
        self.assertEqual(chapter['summary'],summary)
        engine.approve(self.db)
        memory='必须保留的伏笔、人物选择和完整因果证据。'*200
        for _ in range(10):
            task=self.task();work=envelope(task)
            scope=work['payload']['scope']
            if scope.endswith('_chunk'):
                respond(self.db,self.path,task)
                continue
            data=review(work['payload']['checks'],prose,1)
            data['memory']={'text':memory,'sources':[ev(prose,1)]}
            engine.submit(self.db,result(task,data))
            saved=json.loads(self.db.execute('SELECT data FROM memories WHERE branch=? AND scope=? AND through=1',
                                            ('main',scope)).fetchone()[0])
            self.assertEqual(saved['text'],memory)
            break
        else:self.fail('没有抵达整体复核记忆提交阶段')

    def test_review_reason_and_evidence_have_no_length_quota(self):
        prose='每一项关键事实都需要完整上下文。'*100
        data=review(['coverage'],prose,1)
        data['checks'][0]['reason']='完整理由保留必要因果。'*100
        engine.validate_review(data,['coverage'],{1:prose})


if __name__=='__main__':unittest.main()
