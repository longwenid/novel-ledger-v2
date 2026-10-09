import tempfile
import unittest
from pathlib import Path
from support import *
import quality_eval as q
from novel_core.store import digest,Error,write_json


class LiteraryEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        (self.path/'source.txt').write_text('原始甲项。原始乙项。',encoding='utf-8')
        self.manifest={'chapters':[{'chapter':1,'path':'source.txt','volume':'v1'}],
                       'cases':[{'label':'control','expectations':[{'criterion':'pacing','expected':'pass'}]},
                                {'label':'changed','edits':[{'chapter':1,'start':0,'old':'原始甲项。','new':'修改甲项。','criterion':'causality'}]}]}
        write_json(self.path/'manifest.json',self.manifest)
        self.routes=q.prepare(self.path/'manifest.json',self.path/'eval')['cases']
    def tearDown(self):self.temp.cleanup()
    def review(self,index,find=False):
        work=q.load(self.routes[index]['input']);prose=work['chapters'][0]['prose'];evidence=[{'chapter':1,'start':0,'quote':prose}]
        result={'case_id':work['case_id'],'input_hash':digest(work),'first_read':{'observed':'测试定位能力，不代表文学判断'},
                'checks':[{'id':c,'result':'fix' if find and c=='causality' else 'pass','reason':'检查规范测试','evidence':evidence} for c in q.CRITERIA],
                'findings':[{'criterion':'causality','severity':'major','reason':'用于已标注修改定位测试','evidence':evidence}] if find else []}
        write_json(self.routes[index]['output'],result);return work,result
    def test_blind_package_has_no_expected_variants_or_gold_and_keeps_full_source(self):
        work=q.load(self.routes[1]['input'])
        self.assertEqual(work['chapters'][0]['prose'],'修改甲项。原始乙项。')
        self.assertNotIn('label',work);self.assertNotIn('edits',work);self.assertNotIn('expectations',work)
        self.assertEqual((self.path/'source.txt').read_text(),'原始甲项。原始乙项。')
    def test_seeded_detection_and_unknown_control_are_not_conflated(self):
        self.review(0,True);self.review(1,True);score=q.score(self.path/'eval')
        self.assertEqual(score['seeded']['true_positive'],1);self.assertEqual(score['seeded']['false_negative'],0)
        self.assertEqual(score['controls']['alerts_pending_adjudication'],1)
        self.assertEqual(score['adjudicated']['pending_expectations'],1);self.assertIsNone(score['adjudicated']['precision']);self.assertIsNone(score['billed_tokens'])
    def test_invalid_quote_or_changed_work_cannot_score(self):
        self.review(0);work,review=self.review(1,True)
        review['findings'][0]['evidence']=[{'chapter':1,'start':0,'quote':'不存在'}]
        write_json(self.routes[1]['output'],review)
        with self.assertRaises(Error):q.score(self.path/'eval')
        work['chapters'][0]['prose']='改了冻结来源';write_json(self.routes[1]['input'],work)
        with self.assertRaises(Error):q.score(self.path/'eval')
    def test_missed_seed_is_counted_without_inventing_false_positive_gold(self):
        self.review(0);self.review(1);score=q.score(self.path/'eval')
        self.assertEqual(score['seeded']['false_negative'],1);self.assertEqual(score['seeded']['recall'],0)
        self.assertIsNone(score['adjudicated']['false_positive'])
    def test_outputs_cannot_be_created_inside_skill_and_batches_cannot_be_overwritten(self):
        with self.assertRaises(Error):q.prepare(self.path/'manifest.json',q.ROOT/'evaluation')
        with self.assertRaises(Error):q.prepare(self.path/'manifest.json',self.path/'eval')


if __name__=='__main__':unittest.main()
