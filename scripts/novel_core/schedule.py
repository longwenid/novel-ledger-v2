"""Explicit chapter time coordinates; legacy schedules retain chapter deltas."""
import re
from .store import require

PROTOCOL='chapter-timeline/1'
INPUT_PROTOCOL='explicit-time-input/1'
BASES=('batch_anchor','chapter_start','absolute_clock')


def contract(explicit=True):
    """Single source for new planning delivery and its compatibility boundary."""
    if not explicit:
        return {'protocol':PROTOCOL,'scene_example':{'time':{'basis':'batch_anchor','offset_days':1}},
                'timeline_example':{'basis':'batch_anchor','end_offset_range':[1,2]},
                'rule':'此冻结任务沿用原协议；旧 time_hint 的 D+N 表示本章推进；可显式选择 time/timeline 的 basis。'}
    return {'protocol':PROTOCOL,'input_protocol':INPUT_PROTOCOL,
            'scene_example':{'time':{'basis':'batch_anchor','offset_days':0}},
            'timeline_example':{'basis':'batch_anchor','end_offset_range':[0,0]},
            'rule':'新任务的硬日程使用 time/timeline，basis 选 batch_anchor / chapter_start / absolute_clock。'
                   '整批倒计时用 batch_anchor，锚点由引擎填写；章内经过天数用 chapter_start。'
                   'time_hint 仅描述叙事时刻，不能单独用 D+N 或 day_span 声明硬日程；旧冻结任务仍按原章首推进协议。'}


def _range(value):
    require(isinstance(value,list) and len(value)==2
            and all(type(n) is int and n>=0 for n in value) and value[0]<=value[1],
            'time_plan','时间范围须为有序非负整数 [最早,最晚]',
            {'recover':'明确 basis，并填写 end_offset_range；absolute_clock 使用 end_clock_range'})
    return list(value)


def normalize(plan, anchor_clock, explicit_required=False):
    """Compile declared coordinates; never infer absolute versus relative days.

    Old time_hint-only inputs keep their documented chapter-start semantics and
    one-day tolerance. Explicit timeline inputs use only their declared range.
    The engine supplies the batch anchor; an author cannot move it in a receipt.
    """
    timeline=plan.get('timeline'); scenes=plan.get('scenes',[])
    scenes=scenes if isinstance(scenes,list) else []
    scene_times=[s['time'] for s in scenes if isinstance(s,dict) and 'time' in s]
    if timeline is None and not scene_times:
        spans=[int(m) for s in scenes if isinstance(s,dict) and isinstance(s.get('time_hint'),str)
               for m in re.findall(r'D\+(\d+)',s['time_hint'])]
        require(not explicit_required or not (spans or 'day_span' in plan or 'day_anchor' in plan),
                'time_basis','新规划的硬日程必须明确坐标，不能猜 D+N 的起点',
                {'recover':'在本次计划填写 scenes[].time 或 timeline；整批第 N 天用 batch_anchor，不把日期当作逐章经过天数。',
                 'time_contract':contract(),'legacy_hints':spans})
        if spans:
            plan['day_anchor']=anchor_clock;plan['day_span']=[min(spans),max(spans)]
        return plan
    points=[]; basis=None
    for time in scene_times:
        require(isinstance(time,dict) and set(time)<=set(('basis','offset_days','clock')),
                'time_plan','场景 time 为明确坐标对象')
        current=time.get('basis');require(current in BASES,'time_plan','场景时间须声明 basis')
        key='clock' if current=='absolute_clock' else 'offset_days'
        require(set(time)=={'basis',key} and type(time[key]) is int and time[key]>=0,
                'time_plan','场景 time 须含 basis 和非负整数 '+key)
        require(basis is None or basis==current,'time_plan','同章场景时间坐标不能混用')
        basis=current;points.append(time[key])
    require(points==sorted(points),'time_plan','场景时间不能倒退；回忆用叙事说明，不修改主线日历')
    if timeline is None:
        key='end_clock_range' if basis=='absolute_clock' else 'end_offset_range'
        timeline={'basis':basis,key:[points[-1],points[-1]]}
    require(isinstance(timeline,dict),'time_plan','timeline 须为对象')
    basis=timeline.get('basis');require(basis in BASES,'time_plan','timeline 须明确时间 basis')
    key='end_clock_range' if basis=='absolute_clock' else 'end_offset_range'
    allowed={'schema','basis',key} | ({'anchor_clock'} if basis=='batch_anchor' else set())
    require(set(timeline)<=allowed and timeline.get('schema',PROTOCOL)==PROTOCOL,
            'time_plan','timeline 字段与坐标类型不一致')
    span=_range(timeline.get(key))
    require(basis!='absolute_clock' or span[1]>=anchor_clock,'time_plan','绝对章末时间早于当前主线时钟')
    require(basis!='absolute_clock' or not points or points[0]>=anchor_clock,'time_plan','场景绝对时间早于当前主线时钟')
    require(not scene_times or (all(t['basis']==basis for t in scene_times) and points[-1]<=span[1]),
            'time_plan','场景时间超过章末时间范围或与 timeline 坐标不同')
    if basis=='batch_anchor':
        require('anchor_clock' not in timeline or (type(timeline['anchor_clock']) is int and timeline['anchor_clock']==anchor_clock),
                'time_plan','批次锚点由引擎按实际前态填写，不能自改锚点')
    normalized={'schema':PROTOCOL,'basis':basis,key:span}
    if basis=='batch_anchor':normalized['anchor_clock']=anchor_clock
    plan['timeline']=normalized
    # New and legacy coordinate predicates must never be ORed together.
    plan.pop('day_span',None);plan.pop('day_anchor',None)
    return plan


def validate_sequence(plans, anchor_clock):
    """Reject provably impossible ranges before any chapter is written."""
    lo=hi=anchor_clock
    for plan in plans:
        if not isinstance(plan,dict):continue
        timeline=plan.get('timeline')
        if not isinstance(timeline,dict):
            expected=bounds(plan,lo)
            upper=bounds(plan,hi)
            if expected is not None:lo=expected[0];hi=upper[1]
            continue
        expected=bounds(plan,lo);upper=bounds(plan,hi)
        next_lo,next_hi=expected[0],upper[1]
        require(next_hi>=lo,'time_plan','后章章末时间早于前章最早可能结束时间',
                {'chapter':plan.get('number'),'previous_earliest':lo,'expected_clock':[next_lo,next_hi]})
        lo=max(lo,next_lo);hi=next_hi


def bounds(plan, before_clock):
    timeline=plan.get('timeline')
    if timeline is None:
        if 'day_span' not in plan:return None
        lo,hi=_range(plan['day_span'])
        return before_clock+lo,before_clock+hi+1,'legacy_chapter_start'
    require(isinstance(timeline,dict) and timeline.get('schema')==PROTOCOL,
            'time_plan','时间协议版本无效')
    basis=timeline.get('basis');require(basis in BASES,'time_plan','时间坐标无效')
    lo,hi=_range(timeline.get('end_clock_range' if basis=='absolute_clock' else 'end_offset_range'))
    anchor=0 if basis=='absolute_clock' else before_clock
    if basis=='batch_anchor':
        anchor=timeline.get('anchor_clock')
        require(type(anchor) is int and anchor>=0,'time_plan','缺少引擎签发的时间锚点')
    return anchor+lo,anchor+hi,basis


def check(plan, before_clock, after_clock, chapter):
    expected=bounds(plan,before_clock)
    if expected is None:return
    lo,hi,basis=expected
    require(lo<=after_clock<=hi,'time_schedule','章末时钟不在计划日程内；核对正文时间与本章计划',
            {'chapter':chapter,'current':after_clock,'before':before_clock,
             'day_advance':after_clock-before_clock,'expected_clock':[lo,hi],
             'expected_advance':[lo-before_clock,hi-before_clock],'basis':basis,
             'day_span':plan.get('day_span'),
             'recovery':{'action':'review_fix','owner':'current_reviewer',
                         'reason':'计划范围与正文推算冲突；数值范围不是修改 clock 的授权。',
                         'finding':{'severity':'blocker','impact':'正文时间与计划坐标冲突，后续倒计时或事件前态不可靠。',
                                    'goal':'明确时间基准后修订日程或正文，复核依赖后章。'}},
             'recover':f'本章章末时钟须为 {lo}~{hi}，实际为 {after_clock}；不能自动调整 clock。'
                       '审稿 pass 被拒绝可在同一任务改判 fix，列出重排日程或修正正文的 finding；'
                       '执笔/返修核对正文与 clock 事件；无草稿可 replan，有草稿走审稿 fix→返修/修订并复核后文，不热修源码或查库猜字段'})
