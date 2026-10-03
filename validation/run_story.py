"""Replay the actual authored sample and recorded editorial decisions through the CLI core.

Not a model simulator: prose and independent reader judgments are checked-in artifacts.
The pilot approval is a test commissioning decision, not approval for a user's real book.
"""
import json
import re
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from novel_core import engine
from novel_core.store import initialize,connect,get,dump,digest,write_json

ROOT=Path(__file__).resolve().parent
STORY=ROOT/'story-v2'
PROSE={n:(STORY/'chapters'/f'{n:02}.md').read_text().rstrip() for n in range(1,13)}


def evidence(n,quote=None,text=None):
    text=text or PROSE[n]
    quote=quote or next(p for p in text.split('\n\n') if not p.startswith('#'))
    return {'chapter':n,'start':text.index(quote),'quote':quote}


PLANS=[
 ('追问八缸旧账','师父签印与两缸记忆相冲突','不再关门回避街坊'),
 ('向船户核实运输','帮忙会危及船户生计','花钱取得有限但可靠的证言'),
 ('核对官仓存根','查册权限与存根缺失','选择先核对水尺'),
 ('寻找票背刻痕依据','涨水及证据不足','从刀痕转向运输条件'),
 ('争取查账协助','仓吏要求自己的主动选择被如实记录','承诺公开经手过程，取得税页索引'),
 ('查两条运油路径','记录只证明运送不证明现有库存','确认重复票号和染坊方向'),
 ('面对撤账交易','个人解困与公共真相冲突','拒收油罐，明确求助边界'),
 ('找到押货工','船户要求公开自己的参与','花完余钱共同接触证人'),
 ('让库存可核验','证人不肯在证词上按印','接受他开门的边界并着手分配'),
 ('公开旧账去向','管仓试图以救急推迟核查','不替师父抹错，将账与物并列公示'),
 ('把油送到需要的人处','救灾与揭账各需承担后果','履约具名，陆青失去差事'),
 ('结清旧债并建立新约','澄清不等于恢复生活','修复关系与生活，接受责任可追索的新交换')]


def planned(n):
    goal,conflict,change=PLANS[n-1]
    row={'number':n,'volume':'v1' if n<=6 else 'v2','goal':goal,'conflict':conflict,'change':change,
         'entities':['xu','adi','lu','master'],'hooks':['oil-debt','watermark','lu-promise'],
         'topics':['oil-route'],'volume_end':n in (6,12),'book_end':n==12}
    if n in (6,10):
        row.update(major_turn=True,alternatives=['凭个人记忆当面指控管仓','让文书、见证与现有物资相互校验'],selection_reason='让证据改变公共处境，同时让人物承担可见责任。')
    if n>1: row['dependencies']=[1] if n>=7 else []
    return row


def event(n,op,quote,**fields):
    return {'op':op,**fields,'evidence':evidence(n,quote)}


def events(n,text=None):
    data=[]
    if n==1:
        data=[event(n,'entity','卖豆腐的顾婆婆朝灯铺看了一眼，没有开口。',entity={'id':'gu','name':'顾婆婆','kind':'person'}),
              event(n,'hook','票上写着八缸。',id='oil-debt',before=None,text='弄清领油八缸与实到两缸的矛盾',status='open',due=10),
              event(n,'hook','背面有一道短刻痕，刀口压破了“领讫”的末笔，不像随手划的。',id='knife-mark',before=None,text='核实显眼票背刻痕的含义',status='open',due=6)]
    if n==2:
        data=[event(n,'resource','阿荻收下三枚，许砚还剩九枚。',asset='cash',unit='枚',quantity='3',reason='购买船户负责的消息',**{'from':'xu','to':'adi','from_before':'12','to_before':'0'}),
              event(n,'knowledge','你师傅那日是从下仓回来的，坐我的船。两只缸，我记得，因为其中一只漏，我拿自己的旧衣裳塞了半路。',entity='xu',topic='oil-route',before=None,claim='师父白日经下仓收到两缸',stance='believes',source='阿荻亲历叙述')]
    if n==3:
        data=[event(n,'entity','她说旧票要管仓纪德签字才准查。',entity={'id':'ji','name':'纪德','kind':'person'}),
              event(n,'hook','他决定去核对下仓的水尺。',id='watermark',before=None,text='核对刻痕与运输的关系',status='open',due=6)]
    if n==4:
        data=[event(n,'resource','许砚付了两枚铜钱，身上剩七枚。',asset='cash',unit='枚',quantity='2',reason='借绳探查水尺',**{'from':'xu','to':None,'from_before':'9'})]
    if n==5:
        data=[event(n,'hook','许砚答应陆青，公开呈证时写明她主动核查的经过。',id='lu-promise',before=None,text='如实记载陆青主动核查',status='open',due=11)]
    if n==6:
        data=[event(n,'hook','刀痕没替他指出水位，阿荻说过的下仓路线却引着他找到了两条不同的路。',id='watermark',before='open',status='paid'),
              event(n,'hook','票背那道短痕，只说明这张票验过，不记水位，更不能证明是陈师傅留的暗号。',id='knife-mark',before='open',status='paid'),
              event(n,'knowledge','白日一船两缸，走下仓小桥，领油人姓陈。夜里一船六缸，走宽水门，入东街染坊。两笔都用了四百十七号。',entity='xu',topic='oil-route',before='师父白日经下仓收到两缸',claim='税房记录显示同号两条运输路线，六缸入染坊',stance='knows',source='税房抄件并加日戳')]
    if n==7:
        data=[event(n,'state','许砚把油罐推回原处，没有收下。',entity='xu',key='offer_from_ji',before=None,after='refused')]
    if n==8:
        data=[event(n,'resource','他把剩下七枚铜钱交给阿荻付这趟船钱，自己一枚也没有了。',asset='cash',unit='枚',quantity='7',reason='载往染坊接触证人',**{'from':'xu','to':'adi','from_before':'7','to_before':'3'}),
              event(n,'entity','阿荻认识其中一个，姓孟，夜间押六缸油的正是他。',entity={'id':'meng','name':'孟师傅','kind':'person'})]
    if n==9:
        data=[event(n,'knowledge','用去一缸。另五缸，纪管仓说等城外来接。',entity='xu',topic='oil-stock',before=None,claim='染坊仍有五缸待运',stance='believes',source='孟师傅告知'),
              event(n,'state','我开门，让大家看。门上锁坏了，本来也是我管。',entity='meng',key='help_boundary',before=None,after='开门见证，不签证词')]
    if n==10:
        data=[event(n,'hook','八缸油的去向终于公开，许砚没有替师傅抹掉签错的那一笔。',id='oil-debt',before='open',status='paid'),
              event(n,'knowledge','孟师傅把五只封口缸一一推到院中，第六只空缸也推出来。',entity='xu',topic='oil-stock',before='染坊仍有五缸待运',claim='院内见五只封口缸与一只空缸',stance='knows',source='在场亲见')]
    if n==11:
        data=[event(n,'hook','他按约写明，是陆青主动核对了仓簿，并提供税页索引。',id='lu-promise',before='open',status='paid'),
              event(n,'state','师爷随即收走了她的仓匙，说越例翻查，先停差，等县里核办。',entity='lu',key='employment',before=None,after='停差待核办')]
    if n==12:
        data=[event(n,'state','灯铺实领两缸，原并记六缸另案追核。',entity='xu',key='oil_debt',before=None,after='灯铺六缸并记债务分离'),
              event(n,'state','纪德被收了管仓印',entity='ji',key='authority',before=None,after='管仓印被收'),
              event(n,'hook','她说不是白要，用以后两趟短渡换，只在风平时算。',id='new-lamp-exchange',before=None,text='船灯换风平时两趟短渡，生活继续的非核心约定',status='open',due=14,important=False)]
    if text:
        for row in data:
            row['evidence']=evidence(n,row['evidence']['quote'],text)
    return data


FACT_NOTES={
1:'票记八缸与主角两缸记忆的矛盾尚未被当作结案事实，建立追索承诺。',
2:'三枚消息钱使余额从十二变九；阿荻证言记为主角相信的有来源信息。',
3:'九枚钱未被接收，不能再扣一次；管仓身份和水尺查证承诺新增。',
4:'两枚借绳费使九变七，水尺只是线索，正文明确不能据此定罪。',
5:'陆青提供信息的条件是如实记载主动核查，不能把主动选择转成失言。',
6:'两条税账与前章一船八缸记录形成可查矛盾；未据文书断言库存。',
7:'油罐被拒绝，不能记为获得资源；交易诱因与人物贫困承接。',
8:'七枚船费实际全付，余额为零；阿荻末枚先退再收不计两次。',
9:'五缸库存仍是证人所述；开门代替签证词并非无条件支持。',
10:'亲见缸数与抄件共同公开去向，核心承诺兑现；不把师父签印伪造。',
11:'两加两加一共五缸，分配是计划与当场装运，未把所有运输提前记为完成；陆青付出停差代价。',
12:'六缸并记债务被分离，不称纪德案件已判完；许砚仍无钱，新增交换非核心收尾。'}


def fact_review(n,text):
    refs=[evidence(n,None,text)]
    es=events(n,text)
    if es: refs=[es[0]['evidence']]
    checks=[]
    for key in engine.FACT_CHECKS:
        row={'id':key,'result':'pass','reason':FACT_NOTES[n], 'evidence':refs}
        if key=='time':row['reason']='按当前时序继续；没有无解释的日期倒退。'+('第三日水退为正文明确时间跳跃。' if n==12 else '')
        if key=='world_rules':row['reason']='解决问题使用运输、文书与人物选择，没有新设无代价能力；旧防潮阵只消耗灯油。'
        if key=='resources' and n not in (2,3,4,7,8,10,11,12):
            row.update(evidence=[],not_applicable='本章没有明确数量收支；已有余额未改变。')
        checks.append(row)
    return {'verdict':'pass','checks':checks,'findings':[],'events':es,'summary':PLANS[n-1][2]+'；'+FACT_NOTES[n]}


def reader_result(n,review,text=None):
    r=next(row for row in review['chapters'] if row['number']==n)
    data={'verdict':r['verdict'],'first_read':r['first_read'],'checks':[],'findings':[]}
    for row in r['checks']:
        out={k:row[k] for k in ('id','result','reason')}
        if out['result']=='na':
            out.update(result='pass',not_applicable=out['reason'])
        if row.get('quote'):out['evidence']=[evidence(n,row['quote'],text)]
        else:out.update(evidence=[],not_applicable=row.get('not_applicable',row['reason']))
        data['checks'].append(out)
    for row in r['findings']:
        data['findings'].append({k:row[k] for k in ('severity','impact','goal')}|{'evidence':evidence(n,row['quote'],text)})
    return data


def long_review(info,reader):
    first,last=info['first'],info['through']
    mapping={'causality':'change','character_arcs':'character','payoffs':'read_on','pacing':'repetition'}
    checks=[]
    if info['scope'].endswith('_chunk'):
        source=reader_result(last,reader)
        for key in info['checks']:
            if key in mapping:
                check=next(r for r in source['checks'] if r['id']==mapping[key])
                checks.append({'id':key,'result':'pass','reason':check['reason'], 'evidence':check['evidence']})
            else:
                es=events(last)
                ref=es[0]['evidence'] if es else evidence(last)
                checks.append({'id':key,'result':'pass','reason':FACT_NOTES[last], 'evidence':[ref]})
    else:
        by_ch={n:events(n) for n in range(first,last+1)}
        available=[e['evidence'] for n,es in by_ch.items() for e in es]
        # Whole-range analysis only asserts milestones already inside this reviewed range.
        progress='；'.join(f'第{n}章：{PLANS[n-1][2]}' for n in range(first,last+1))
        for key in info['checks']:
            refs=[]
            if key=='causality':
                reason='前一步选择形成下一步条件：'+progress
                refs=available[:1]+available[-1:]
            elif key=='character_arcs':
                reason='范围内人物从自身处境作出有代价的选择；不把尚未发生的终局反填到前段。'+progress
                refs=available[:1]+available[-1:]
            elif key=='payoffs' or key=='core_promises':
                paid=[e for es in by_ch.values() for e in es if e['op']=='hook' and e['status']=='paid']
                reason='本范围明确结算：'+('、'.join(e['id'] for e in paid) or '尚无到期核心承诺，先建立与推进追索')+'。未完事项保留，不以提及代替兑现。'
                refs=[e['evidence'] for e in paid] or available[:1]
            elif key=='pacing':
                reason='从'+PLANS[first-1][0]+'推进到'+PLANS[last-1][2]+'，期间调查、议价、公开选择承担不同作用。各章重复判断见逐章全文审稿。'
                refs=[evidence(first),evidence(last)]
            elif key=='world_rules':
                resource=[e for es in by_ch.values() for e in es if e['op']=='resource']
                reason='数量变化按显式支付入账；证言先作认知记录，亲见后才修订。没有凭空资源或新增超能力解决冲突。'
                refs=[e['evidence'] for e in resource] or available[-1:]
            elif key=='ending':
                reason=reader['book']['reason']
                refs=[e['evidence'] for e in by_ch[last]]
            elif key=='length':
                reason='实际篇幅按工具计数符合压缩测试合同；不代表百万字长跑。'
                refs=[evidence(last)]
            checks.append({'id':key,'result':'pass','reason':reason,'evidence':refs})
    return {'verdict':'pass','checks':checks,'findings':[],
            'memory':{'text':'本段从'+PLANS[first-1][0]+'推进到'+PLANS[last-1][2]+'。保留人物合作边界与实际资源代价，旧事实以来源章为准。','sources':[evidence(first),evidence(last)]}}


def main():
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--project',required=True);args=p.parse_args()
    review=json.loads((STORY/'reader-review.json').read_text())
    initial_review=json.loads((STORY/'initial/reader-review.json').read_text())
    assert review.get('source_hashes')=={f'{n:02}.md':digest(s) for n,s in PROSE.items()}, 'reader receipts are stale'
    if review['book']['verdict']!='pass' or any(r['verdict']!='pass' for r in review['chapters']):
        raise SystemExit('Independent reader requests revision; resolve and re-review before replay.')
    contract=json.loads((ROOT.parent/'assets/contract.example.json').read_text())
    contract['context_mode']='legacy' # Replay the recorded pre-context-task experiment.
    contract['character_mode']='legacy'
    contract['planning_mode']='legacy' # Reproduce the recorded v1.0 experiment, without inventing design receipts.
    contract['word_range']=[4000,12000]
    for volume in contract['volumes']:volume.pop('word_budget',None) # Preserve the recorded pre-budget experiment.
    contract['validation_only']='压缩测试书；非实际百万字小说。试章批准由测试委托预授权。'
    if not (Path(args.project)/'novel.sqlite3').exists():initialize(args.project,contract)
    db=connect(args.project)
    assert get(db,'contract')==contract, 'refuse to overwrite another project'
    trace=[{'task':r['id'],'chapter':r['chapter'],'stage':r['stage'],'accepted':True} for r in db.execute("SELECT * FROM tasks WHERE state='done' ORDER BY rowid")] 
    try:
        for _ in range(250):
            task=engine.next_task(db,args.project)
            if task.get('stage')=='await_author':engine.approve(db);continue
            if not task.get('task_id'):
                if task.get('status')!='completed':raise RuntimeError(task)
                report={'kind':'real-authored-prose-with-recorded-model-reviews','review_mode':'serial pipeline; separate fresh-context reader reviewed all twelve chapters once','pilot_approval':'test commissioning, not real author approval','source_hashes':{str(n):digest(s) for n,s in PROSE.items()},'result':task,'tasks':trace,'limitations':['compressed sample, not million-character validation','not cross-provider verification','chapter 4 original draft and actual independent finding exercise repair; other editorial changes preceded replay','long-range review supplied by implementing editor, not a second blind reader']}
                write_json(STORY/'execution-report.json',report)
                return
            stage,n=task['stage'],task['chapter'];info=json.loads(Path(task['input_path']).read_text())['payload']
            if stage=='plan':data={'chapters':[planned(i) for i in range(n,min(n+5,12)+1)]}
            elif stage=='draft':
                text=PROSE[n]
                if n==4:
                    text=(STORY/'initial/04.md').read_text().rstrip()
                data={'prose':text}
            elif stage in ('repair','redesign'):data={'prose':PROSE[n],'repair_note':'按独立读者发现改正水位与桥洞净空关系，保持水尺只是线索。','design_change':'调查基于运输路径，刀痕不再被当作未经核实的暗号。'}
            elif stage=='facts':data=fact_review(n,info['prose'])
            elif stage=='reader':
                if n==4 and info['prose']!=PROSE[4]:data=reader_result(n,initial_review,info['prose'])
                else:data=reader_result(n,review)
            else:data=long_review(info,review)
            result={'schema':'novel-ledger-v2.result/1','task_id':task['task_id'],'input_hash':task['input_hash'],'data':data}
            write_json(task['output_path'],result)
            response=engine.submit(db,result)
            trace.append({'task':task['task_id'],'chapter':n,'stage':stage,'accepted':response.get('accepted')})
        raise RuntimeError('too many tasks')
    finally:db.close()

if __name__=='__main__':main()
