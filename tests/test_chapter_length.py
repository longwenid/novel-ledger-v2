import json
import tempfile
import unittest
from pathlib import Path

from support import *
from novel_core.budget import chapter_length, han_count
from novel_core.store import Error, head, digest


def developed_plan(target=None):
    p=plan(1)
    p['scenes']=[{'label':'查验凭证的小屋','present':['hero','ally']}]
    p['execution']={'expansion_units':[
        {'id':'testimony','scene':1,'focus':'宋禾是否作证',
         'development':'先核对手印的疑点，再由对话呈现宋禾的顾虑和周行让她自己决定的选择。',
         'expected_change':'宋禾主动提出承担作证风险，周行接受她的条件。'}]}
    if target is not None:p['execution']['target_han']=target
    return p


class ChapterLengthTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        c=contract();c['chapter_target_han']=90
        initialize(self.path,c);self.db=connect(self.path)

    def tearDown(self):
        self.db.close();self.temp.cleanup()

    def task(self):return engine.next_task(self.db,self.path)

    def work(self,t):return json.loads(rpath(t).read_text(encoding='utf-8'))

    def planned(self,p=None):
        t=self.task();engine.submit(self.db,result(t,{'chapters':[p or developed_plan()]}))
        return self.task()

    def test_missing_target_is_compatible_and_book_range_is_not_a_chapter_goal(self):
        c=contract();c['word_range']=[3000000,5000000]
        self.assertIsNone(chapter_length(c,plan(1),'很短的正文。'))
        with transaction(self.db):put(self.db,'contract',c)
        t=self.planned(plan(1));engine.submit(self.db,result(t,{'prose':'周行交出了收据。'}))
        self.assertEqual(self.task()['stage'],'facts')

    def test_target_must_be_an_explicit_positive_integer_without_an_upper_cap(self):
        for invalid in (True,0,-2,3.5,'3000'):
            with self.subTest(value=invalid),tempfile.TemporaryDirectory() as path:
                c=contract();c['chapter_target_han']=invalid
                with self.assertRaises(Error):initialize(path,c)
        self.assertEqual(chapter_length({'chapter_target_han':10**15},{})['target_han'],10**15)

    def test_one_development_unit_is_valid_without_four_or_five_events(self):
        t=self.planned()
        work=self.work(t)
        self.assertEqual(len(work['writer_brief']['execution']['expansion_units']),1)
        self.assertEqual(work['payload']['chapter_length']['target_han'],90)
        text='周行逐项查验收据上的印记，发现纸角曾沾过水。宋禾不肯替别人承担那枚指印的风险，要求先问明证人会被带到哪里。周行没有替她答应，而是将自己的旧记录也放在桌上，说明其中可能牵连到他。她看完这两份凭证后才提出条件，要求当面对质。周行接受了这个条件，二人一同准备次日的问话。'
        self.assertGreaterEqual(han_count(text),90)
        engine.submit(self.db,result(t,{'prose':text}))
        facts=self.task();respond(self.db,self.path,facts,events=[])
        reader=self.task();self.assertIn('substance',self.work(reader)['payload']['checks'])
        respond(self.db,self.path,reader)
        self.assertEqual(head(self.db,'main'),1)
        stored=json.loads(self.db.execute('SELECT data FROM chapters').fetchone()[0])
        self.assertEqual(stored['chapter_length']['actual_han'],han_count(text))
        self.assertEqual(stored['events'],[])

    def test_short_first_draft_is_accepted_preserved_and_patchable(self):
        t=self.planned();short='宋禾没有立刻按手印。'
        receipt=engine.submit(self.db,result(t,{'prose':short,'han_characters':9000}))
        self.assertTrue(receipt['accepted']);self.assertEqual(head(self.db,'main'),0)
        saved=engine.load_draft(self.db,'main',1)
        self.assertEqual(saved['prose'],short)
        self.assertEqual(saved['chapter_length']['actual_han'],han_count(short))
        self.assertEqual(saved['stage'],'repair')
        self.db.close();self.db=connect(self.path)
        repair_task=self.task();work=self.work(repair_task)
        self.assertEqual(pack_prose(self.path, work),short)
        self.assertEqual(work['payload']['chapter_length']['shortfall_han'],90-han_count(short))
        expanded='宋禾没有立刻按手印。她先问清凭证上的名字由谁填写，又把两张纸放在光下比对。周行依她的要求逐项说明来源，没有跳过自己保存凭证的那一晚。她听完后提出亲自见证人的条件，周行发现这会让自己也必须到场，仍然答应。她这才用旧布擦掉桌边的水渍，将需要核验的字迹逐处圈出，准备在对质时一项项追问。'
        engine.submit(self.db,result(repair_task,{'base_hash':work['payload']['base_hash'],
            'patches':[{'start':0,'old':short,'new':expanded}],'repair_note':'保留原稿的犹豫，展开核验、条件和双方选择。'}))
        self.assertEqual(engine.load_draft(self.db,'main',1)['prose'],expanded)
        self.assertEqual(self.task()['stage'],'facts')

    def test_overshoot_is_preserved_and_does_not_trigger_an_upper_length_gate(self):
        t=self.planned()
        text='周行让宋禾自行选择是否作证。\n'+'这是为验证没有上限截断而保留的工程样本段落。'*1000
        engine.submit(self.db,result(t,{'prose':text}))
        self.assertEqual(engine.load_draft(self.db,'main',1)['prose'],text)
        self.assertEqual(self.task()['stage'],'facts')

    def test_deliberate_short_chapter_needs_an_advance_reason(self):
        p=developed_plan(20)
        t=self.task()
        with self.assertRaises(Error):engine.submit(self.db,result(t,{'chapters':[p]}))
        p['execution']['target_reason']='此章仅处理上一章选择的即时后果，留白使下一章当面对质更有压力。'
        engine.submit(self.db,result(t,{'chapters':[p]}))
        self.assertEqual(self.work(self.task())['payload']['chapter_length']['target_han'],20)

    def test_repeated_short_repairs_escalate_design_without_discarding_text(self):
        t=self.planned()
        for text in ('宋禾迟疑。','宋禾迟疑，周行等待。','宋禾迟疑，周行仍在等待回答。'):
            payload={'prose':text}
            if t['stage']=='repair':payload['repair_note']='补足实际发生的反应。'
            engine.submit(self.db,result(t,payload));t=self.task()
        self.assertEqual(t['stage'],'redesign')
        self.assertEqual(get(self.db,'status'),'ready')
        self.assertEqual(engine.load_draft(self.db,'main',1)['length_attempts'],3)
        execution=developed_plan(10)['execution']
        with self.assertRaises(Error) as raised:
            engine.submit(self.db,result(t,{'prose':'宋禾改为主动询问证人的来处。',
                'design_change':'应重设计核验过程。','plan_update':{'execution':execution}}))
        self.assertEqual(raised.exception.code,'chapter_target')
        self.assertEqual(self.task()['task_id'],t['task_id'])

    def test_no_progress_redesign_is_rejected_and_small_additions_have_a_finite_cycle(self):
        t=self.planned();texts=('宋禾迟疑。','宋禾迟疑，周行等待。','宋禾迟疑，周行仍在等待回答。')
        for text in texts:
            data={'prose':text}
            if t['stage']=='repair':data['repair_note']='演出当前人物的反应。'
            engine.submit(self.db,result(t,data));t=self.task()
        self.assertEqual(t['stage'],'redesign')
        with self.assertRaises(Error) as raised:
            engine.submit(self.db,result(t,{'prose':texts[-1],'design_change':'重新考虑场景展开。'}))
        self.assertEqual(raised.exception.code,'redesign')
        self.assertEqual(self.task()['task_id'],t['task_id'])
        changed=texts[-1]+'她仍未回答。'
        accepted=engine.submit(self.db,result(t,{'prose':changed,'design_change':'增加了人物的实际反应，但这次工程样本仍未覆盖目标。'}))
        self.assertTrue(accepted['accepted']);self.assertEqual(accepted['status'],'paused')
        self.assertEqual(accepted['reason']['code'],'chapter_development_exhausted')
        self.assertEqual(accepted['reason']['chapter_length']['actual_han'],han_count(changed))
        self.assertEqual(engine.load_draft(self.db,'main',1)['prose'],changed)
        self.assertEqual(head(self.db,'main'),0)
        self.assertEqual(self.task()['status'],'paused')

    def test_a_new_author_target_receives_its_own_development_cycle(self):
        t=self.planned()
        for text in ('宋禾迟疑。','宋禾迟疑，周行等待。','宋禾迟疑，周行仍在等待回答。'):
            data={'prose':text}
            if t['stage']=='repair':data['repair_note']='补足实际反应。'
            engine.submit(self.db,result(t,data));t=self.task()
        self.assertEqual(t['stage'],'redesign')
        c=get(self.db,'contract');c['chapter_target_han']=120
        with transaction(self.db):put(self.db,'contract',c)
        engine.submit(self.db,result(t,{'prose':'宋禾迟疑，周行仍在等待回答，她又问起凭证来源。',
                                      'design_change':'增加核验凭证来源的阻力。'}))
        saved=engine.load_draft(self.db,'main',1)
        self.assertEqual(saved['length_attempts'],1)
        self.assertEqual(self.task()['stage'],'repair')
        self.assertEqual(get(self.db,'status'),'ready')

    def test_redesign_cannot_remove_the_only_declared_target(self):
        c=get(self.db,'contract');c.pop('chapter_target_han')
        with transaction(self.db):put(self.db,'contract',c)
        t=self.planned(developed_plan(90))
        for text in ('宋禾迟疑。','宋禾迟疑，周行等待。','宋禾迟疑，周行仍在等待回答。'):
            data={'prose':text}
            if t['stage']=='repair':data['repair_note']='补足反应。'
            engine.submit(self.db,result(t,data));t=self.task()
        without_target=developed_plan()['execution']
        with self.assertRaises(Error) as raised:
            engine.submit(self.db,result(t,{'prose':'宋禾迟疑，继而主动询问凭证从何而来。',
                'design_change':'补足查验过程。','plan_update':{'execution':without_target}}))
        self.assertEqual(raised.exception.code,'chapter_target')
        self.assertEqual(engine.chapter_length(self.db,'main',1)['target_han'],90)

    def test_changed_author_target_reopens_a_paused_length_cycle_on_the_first_next(self):
        t=self.planned()
        for text in ('宋禾迟疑。','宋禾迟疑，周行等待。','宋禾迟疑，周行仍在等待回答。','宋禾又追问了收据的来处。'):
            data={'prose':text}
            if t['stage']=='repair':data['repair_note']='继续展开已有动作。'
            if t['stage']=='redesign':data['design_change']='改为先查验凭证来源。'
            engine.submit(self.db,result(t,data))
            if get(self.db,'status')!='paused':t=self.task()
        self.assertEqual(get(self.db,'status'),'paused')
        c=get(self.db,'contract');c['chapter_target_han']=120
        with transaction(self.db):put(self.db,'contract',c)
        engine.resume(self.db)
        new=self.task()
        self.assertEqual(new['stage'],'repair')
        self.assertEqual(self.work(new)['payload']['chapter_length']['target_han'],120)
        self.assertEqual(engine.load_draft(self.db,'main',1)['length_attempts'],1)

    def test_original_semantic_quality_failure_cycle_still_pauses_and_retains_the_candidate(self):
        t=self.planned()
        base='周行逐项说明凭证的来源。宋禾提出先核对记录中的日期，再决定是否作证。二人把各自记得的过程列出，并对其中无法确认的细节保留疑问。周行接受了当面对质的条件，没有替她许诺。宋禾听完双方必须承担的风险后选择继续追问，又将应当询问证人的事项写在纸边。'
        for failure in range(4):
            prose=base+('她把新增的疑点也记在纸上。'*failure)
            data={'prose':prose}
            if t['stage']=='repair':data['repair_note']='改写读者审稿指出的表达问题。'
            if t['stage']=='redesign':data['design_change']='改为围绕宋禾实际核验来组织场景。'
            engine.submit(self.db,result(t,data));respond(self.db,self.path,self.task())
            reader=self.task();work=self.work(reader);review_data=review(work['payload']['checks'],prose)
            review_data['first_read']={'desire':'核验凭证','obstacle':'来源不明','change':'要求对质','read_on_reason':'追问疑点'}
            review_data['verdict']='fix'
            for check in review_data['checks']:
                if check['id']=='language':check['result']='fix';check['reason']='这个工程场景用持续的语义修订失败回执验证原有返工机制。'
            review_data['findings']=[{'severity':'quality','impact':'修订后表达问题仍未解决。',
                'goal':'演出具体的人物反应以替代解释。','evidence':ev(prose)}]
            engine.submit(self.db,result(reader,review_data))
            if failure<3:t=self.task()
        self.assertEqual(get(self.db,'status'),'paused')
        self.assertEqual(get(self.db,'pause_reason')['code'],'quality_exhausted')
        self.assertEqual(engine.load_draft(self.db,'main',1)['prose'],prose)
        self.assertEqual(head(self.db,'main'),0)

    def test_deleting_padding_keeps_the_original_quality_feedback_during_development(self):
        t=self.planned();padding='周行反复解释收据有多重要，宋禾始终没有得到自己的选择。'*8
        engine.submit(self.db,result(t,{'prose':padding}));respond(self.db,self.path,self.task())
        reader=self.task();work=self.work(reader);data=review(work['payload']['checks'],padding)
        data['verdict']='fix';data['first_read']={'desire':'核验收据','obstacle':'来源不明','change':'没有选择','read_on_reason':'解释重复，缺少推进'}
        for check in data['checks']:
            if check['id']=='substance':check['result']='fix';check['reason']='重复解释代替了人物决定。'
        original={'severity':'quality','impact':'重复说明让宋禾的决定失去空间。',
                  'goal':'删掉重复解释，保留宋禾主动核验凭证的选择。','evidence':ev(padding)}
        data['findings']=[original]
        engine.submit(self.db,result(reader,data))
        repair_task=self.task();short='宋禾先问明凭证来源，再由自己决定是否作证。'
        engine.submit(self.db,result(repair_task,{'prose':short,'repair_note':'删除重复解释，改为宋禾主动选择。'}))
        saved=engine.load_draft(self.db,'main',1)
        preserved=next(f for f in saved['findings'] if f.get('kind')!='chapter_development')
        self.assertEqual(preserved['goal'],original['goal'])
        self.assertEqual(preserved['source_prose_hash'],digest(padding))
        self.assertEqual(preserved['evidence_scope'],'previous_candidate_review')
        self.assertEqual(saved['prior_repair_scope'],'chapter')
        self.assertEqual(sum(f.get('kind')=='chapter_development' for f in saved['findings']),1)
        self.assertEqual(self.task()['stage'],'repair')

    def test_declared_rhythm_is_reviewed_in_prose_without_loading_the_plan(self):
        c=get(self.db,'contract');c.pop('chapter_target_han')
        with transaction(self.db):put(self.db,'contract',c)
        p=developed_plan()
        p['rhythm']={'chapter_role':'维持对质前的紧张','carries_in':'收据的来源受到怀疑',
                     'handoff':'宋禾决定继续追问证人',
                     'pressure_curve':[{'scene':1,'movement':'持续升高','cause':'每次核验都暴露新的疑点'}],
                     'breathing_points':[]}
        t=self.planned(p)
        prose='宋禾逐项追问凭证的来处，周行不得不承认还有一名证人未能找到。她没有退却，反而要求立即去查那人的下落。'
        engine.submit(self.db,result(t,{'prose':prose}));respond(self.db,self.path,self.task())
        reader=self.task();work=self.work(reader)
        self.assertIn('pacing',work['payload']['checks'])
        for hidden in ('plan','plans','writer_brief','contract'):self.assertNotIn(hidden,work)
        respond(self.db,self.path,reader)
        self.assertEqual(head(self.db,'main'),1)

    def test_meeting_target_still_requires_semantic_substance_review(self):
        t=self.planned();padding='周行反复说起收据的重要，却没有回应宋禾的顾虑。'*8
        engine.submit(self.db,result(t,{'prose':padding}));respond(self.db,self.path,self.task())
        reader=self.task();work=self.work(reader);checks=work['payload']['checks']
        data=review(checks,padding)
        data['first_read']={'desire':'交付收据','obstacle':'顾虑未处理','change':'没有可感变化','read_on_reason':'缺少继续阅读动力'}
        data['verdict']='fix'
        for check in data['checks']:
            if check['id']=='substance':check['result']='fix';check['reason']='反复解释没有呈现试探、选择或关系后果，篇幅达标不能替代场景展开。'
        data['findings']=[{'severity':'quality','impact':'重复解释淹没人物选择。','goal':'演出双方的顾虑与选择，删除重复解释。','evidence':ev(padding)}]
        engine.submit(self.db,result(reader,data))
        self.assertEqual(head(self.db,'main'),0);self.assertEqual(self.task()['stage'],'repair')

    def test_substance_check_requires_actual_prose_evidence(self):
        prose='周行承认风险，宋禾提出当面对质的条件。'
        data=review(('substance',),prose)
        data['checks'][0].pop('evidence');data['checks'][0]['not_applicable']='已达字数。'
        with self.assertRaises(Error):engine.validate_review(data,('substance',),{1:prose})

    def test_new_author_target_refreshes_pending_review_into_saved_development(self):
        t=self.planned()
        prose='周行逐项说明凭证的来源。宋禾提出先核对记录中的日期，再决定是否作证。二人把各自记得的过程列出，并对其中无法确认的细节保留疑问。周行接受了当面对质的条件，没有替她许诺。宋禾听完双方必须承担的风险后选择继续追问，又将应当询问证人的事项写在纸边。'
        engine.submit(self.db,result(t,{'prose':prose}))
        pending=self.task();self.assertEqual(pending['stage'],'facts')
        c=get(self.db,'contract');c['chapter_target_han']=han_count(prose)+100
        with transaction(self.db):put(self.db,'contract',c)
        new=self.task();self.assertEqual(new['stage'],'repair')
        self.assertNotEqual(new['task_id'],pending['task_id'])
        self.assertEqual(engine.load_draft(self.db,'main',1)['prose'],prose)
        self.assertEqual(self.work(new)['payload']['chapter_length']['shortfall_han'],100)
        old_state=self.db.execute('SELECT state FROM tasks WHERE id=?',(pending['task_id'],)).fetchone()[0]
        self.assertEqual(old_state,'cancelled')

    def test_reuse_original_cannot_bypass_a_new_author_target(self):
        c=get(self.db,'contract');c.pop('chapter_target_han')
        with transaction(self.db):put(self.db,'contract',c)
        drive(self.db,self.path,2)
        original=self.db.execute('SELECT prose FROM chapters WHERE branch=? AND number=1',('main',)).fetchone()[0]
        c=get(self.db,'contract');c['chapter_target_han']=han_count(original)+100
        with transaction(self.db):
            put(self.db,'contract',c)
            engine.begin_revision(self.db,1,'作者明确新的章节展开目标。')
        task=self.task();work=self.work(task)
        receipt=engine.submit(self.db,result(task,{'base_hash':work['payload']['base_hash'],'reuse_original':True}))
        self.assertTrue(receipt['accepted']);self.assertNotIn('fast_committed',receipt)
        self.assertEqual(head(self.db,get(self.db,'work_branch')),0)
        self.assertEqual(self.task()['stage'],'repair')
        self.assertEqual(engine.load_draft(self.db,get(self.db,'work_branch'),1)['prose'],original)


if __name__=='__main__':unittest.main()
