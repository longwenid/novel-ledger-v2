import copy
import json
import tempfile
import unittest
from pathlib import Path
from support import *
from novel_core.patches import apply_patch,read_slice,save_source
from novel_core.store import Error,digest,head


class PatchUnitTests(unittest.TestCase):
    def test_candidate_reads_keep_long_paragraph_and_explicit_range(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp);initialize(path,contract());db=connect(path)
            try:
                paragraph='前提不能丢失。'+('有完整因果的长段。'*7000)+'结尾的决定。\n'
                prose=paragraph+'第二段的后果。'
                hashed=save_source(db,prose)
                task={'id':'candidate-test','input':dump({'stage':'repair','payload':{'base_hash':hashed}})}
                page=read_slice(db,task)
                self.assertEqual(page['text'],paragraph)
                self.assertEqual(page['end'],len(paragraph))
                whole=read_slice(db,task,0,len(prose))
                self.assertEqual(whole['text'],prose)
                self.assertEqual(whole['base_hash'],hashed)
            finally:db.close()

    def test_unicode_multiple_patches_share_original_offsets(self):
        text='第一段🙂。\n第二段。\n第三段。'
        data={'base_hash':digest(text),'patches':[{'start':text.index('第三'),'old':'第三','new':'末尾'},{'start':0,'old':'第一','new':'开头增添几字'}]}
        self.assertEqual(apply_patch(text,data),'开头增添几字段🙂。\n第二段。\n末尾段。')
    def test_duplicate_passage_changes_only_exact_occurrence(self):
        text='他说好。\n他说好。';start=text.rindex('他说')
        data={'base_hash':digest(text),'patches':[{'start':start,'old':'他说好。','new':'他摇了摇头。'}]}
        self.assertEqual(apply_patch(text,data),'他说好。\n他摇了摇头。')
    def test_stale_anchor_overlap_and_noop_rejected(self):
        text='甲乙丙丁'
        cases=[{'base_hash':'wrong','patches':[{'start':0,'old':'甲','new':'新'}]},
               {'base_hash':digest(text),'patches':[{'start':1,'old':'甲','new':'新'}]},
               {'base_hash':digest(text),'patches':[{'start':0,'old':'甲乙','new':'新'},{'start':1,'old':'乙','new':'字'}]},
               {'base_hash':digest(text),'patches':[{'start':0,'old':'甲','new':'甲'}]}]
        for data in cases:
            with self.subTest(data=data),self.assertRaises(Error):apply_patch(text,data)
    def test_deletion_allowed_but_not_empty_chapter_or_ambiguous_insert(self):
        text='保留。删除。'
        self.assertEqual(apply_patch(text,{'base_hash':digest(text),'patches':[{'start':3,'old':'删除。','new':''}]}),'保留。')
        for old,new in [(text,''),('','新增')]:
            with self.assertRaises(Error):apply_patch(text,{'base_hash':digest(text),'patches':[{'start':0,'old':old,'new':new}]})


class PatchPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name);initialize(self.path,contract());self.db=connect(self.path)
        self.text='\n\n'.join(f'第{i}段：周行核对收据，宋禾检查封条，没有遗漏。' for i in range(80))
        self.target='第40段：周行核对收据，宋禾检查封条，没有遗漏。'
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def task(self):return engine.next_task(self.db,self.path)
    def start(self):
        respond(self.db,self.path,self.task());respond(self.db,self.path,self.task(),prose=self.text)
        t=self.task();w=envelope(t);data=review(w['payload']['facts_checks'],self.text)
        data['verdict']='fix';data['repair_scope']='local';data['checks'][0]['result']='fix'
        data['findings']=[{'severity':'quality','impact':'这段动作重复，削弱人物分工','goal':'让宋禾指出封条不同','evidence':ev(self.text,quote=self.target)}]
        submit_review(self.db,t,data);return self.task()
    def patch(self,t,old=None,new='第40段：周行核对收据，宋禾指着重新粘过的封条，等他先开口。'):
        w=envelope(t);old=old or self.target
        return result(t,{'base_hash':w['payload'].get('base_hash',digest(self.text)),'patches':[{'start':self.text.index(old),'old':old,'new':new}],'repair_note':'只改本段分工和动作'})
    def test_local_packet_omits_whole_chapter_and_preserves_same_task(self):
        t=self.start();w=envelope(t);payload=w['payload']
        self.assertNotIn('prose',payload);self.assertGreater(payload['omitted_chars'],len(self.text)*0.8)
        self.assertTrue(any(self.target in x['text'] for x in payload['excerpts']))
        self.assertEqual(t,self.task())
        row=self.db.execute('SELECT * FROM tasks WHERE id=?',(t['task_id'],)).fetchone()
        view=read_slice(self.db,row,0,100);self.assertEqual(view['text'],self.text[:100])
        self.assertEqual(view['base_hash'],digest(self.text))
    def test_patch_composes_without_canon_change_and_reaudits_full_text(self):
        t=self.start();data=self.patch(t);engine.submit(self.db,data)
        self.assertTrue(engine.submit(self.db,data)['idempotent']);self.assertEqual(head(self.db,'main'),0)
        next_=self.task();w=envelope(next_)
        self.assertEqual(next_['stage'],'chapter_review');self.assertEqual(pack_prose(self.path, w),self.text.replace(self.target,data['data']['patches'][0]['new']))
        self.assertNotIn('facts_review',engine.load_draft(self.db,'main',1))
        respond(self.db,self.path,next_);self.assertEqual(head(self.db,'main'),1)
        self.assertTrue(engine.audit(self.db)['ok'])
        row=self.db.execute('SELECT * FROM tasks WHERE id=?',(t['task_id'],)).fetchone()
        self.assertEqual(read_slice(self.db,row,0,len(self.text))['text'],self.text) # Old source remains immutable.
    def test_wrong_base_and_outside_scope_leave_task_and_draft_unchanged(self):
        t=self.start();data=self.patch(t);data['data']['base_hash']='wrong'
        with self.assertRaises(Error):engine.submit(self.db,data)
        first=self.text.split('\n')[0]
        with self.assertRaises(Error):engine.submit(self.db,self.patch(t,old=first,new='修改章首'))
        self.assertEqual(self.task()['task_id'],t['task_id']);self.assertEqual(engine.load_draft(self.db,'main',1)['prose'],self.text)
    def test_expansion_is_bounded_and_does_not_reset_failure_count(self):
        t=self.start();attempts=engine.load_draft(self.db,'main',1)['attempts']
        engine.submit(self.db,result(t,{'expand_context':True,'reason':'需要核对章首的交接条件'}))
        next_=self.task();w=envelope(next_);self.assertEqual(pack_prose(self.path, w),self.text)
        self.assertEqual(engine.load_draft(self.db,'main',1)['attempts'],attempts)
        with self.assertRaises(Error):engine.submit(self.db,result(next_,{'expand_context':True,'reason':'再次扩大'}))
        self.assertEqual(head(self.db,'main'),0)
    def test_whole_prose_and_mixed_modes_not_accepted_as_local_patch(self):
        t=self.start()
        with self.assertRaises(Error):engine.submit(self.db,result(t,{'prose':self.text+'新句','repair_note':'修改'}))
        mixed=self.patch(t);mixed['data']['prose']='不能混用'
        with self.assertRaises(Error):engine.submit(self.db,mixed)
    def test_new_draft_cannot_patch_nonexistent_base(self):
        respond(self.db,self.path,self.task());t=self.task()
        with self.assertRaises(Error):engine.submit(self.db,self.patch(t))
    def test_revision_can_reuse_original_without_echoing_it(self):
        # The author's voice is already part of both original review inputs.
        put(self.db,'style_anchor',{'source':'author','traits':['克制'],'sample':'先核凭证再开口。'})
        respond(self.db,self.path,self.task());respond(self.db,self.path,self.task(),prose=self.text)
        respond(self.db,self.path,self.task());engine.approve(self.db)
        with transaction(self.db):revision=engine.begin_revision(self.db,1,'复核后续连续性')
        t=self.task();w=envelope(t)
        self.assertEqual(t['stage'],'draft');self.assertEqual(w['payload']['base_hash'],digest(self.text))
        outcome=engine.submit(self.db,result(t,{'base_hash':digest(self.text),'reuse_original':True}))
        # Reuse requires unchanged upstream prose and valid parent reviews.
        self.assertNotIn('fast_committed',outcome)
        self.assertNotIn(self.text,(outcome.get('receipt') or ''))
        self.assertEqual(head(self.db,'main'),1)
        self.assertEqual(head(self.db,revision['branch']),0)
        review_task=self.task();self.assertEqual(review_task['stage'],'chapter_review')
        respond(self.db,self.path,review_task)
        self.assertEqual(head(self.db,revision['branch']),1)
    def test_candidate_revision_accepts_patch_with_original_branch_untouched(self):
        respond(self.db,self.path,self.task());respond(self.db,self.path,self.task(),prose=self.text)
        respond(self.db,self.path,self.task());engine.approve(self.db)
        with transaction(self.db):revision=engine.begin_revision(self.db,1,'修正一段动作')
        task=self.task();engine.submit(self.db,self.patch(task));respond(self.db,self.path,self.task())
        main=self.db.execute('SELECT prose FROM chapters WHERE branch=? AND number=1',('main',)).fetchone()[0]
        new=self.db.execute('SELECT prose FROM chapters WHERE branch=? AND number=1',(revision['branch'],)).fetchone()[0]
        self.assertEqual(main,self.text);self.assertNotEqual(new,self.text)

if __name__=='__main__':unittest.main()
