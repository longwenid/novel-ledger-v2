"""Reviewer-owned literal corrections; never change ledger effects or prose structure."""
import copy
import re
from .store import digest, require
from .patches import apply_patch
from .settlement import state_hash

PROTOCOL='numeric-patch/1'
NUMBER=re.compile(r'[+-]?[0-9]+(?:\.[0-9]+)?|[零〇一二两三四五六七八九十百千万亿]+(?:点[零〇一二两三四五六七八九]+)?半?|半')
TOKEN=re.compile(r'[+-]?[0-9]+(?:[,，][0-9]{3})*(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?|[零〇一二两三四五六七八九十百千万亿]+(?:点[零〇一二两三四五六七八九]+)?半?|半')
TITLE=re.compile(r'^\s*(?:#{1,6}\s*)?第\s*(?P<number>[0-9零〇一二两三四五六七八九十百千万亿]+)\s*[章回节]')


def capability(draft):
    # An unreviewed provisional ledger supplies the reference arithmetic,
    # not a semantic pass. The independent reviewer still performs every check.
    # Standalone reviews (first chapter, single-chapter mode, revision branch)
    # carry no candidate ledger: there this receipt's own events define the
    # settlement, so a literal-only patch cannot alter effects it never had.
    batch=isinstance(draft.get('candidate_events'),list)
    effect=('事件效果与结算必须和本章候选基线一致' if batch
            else '事件按合成稿举证，补丁不得改变本回执任何事件的账本效果')
    return {'protocol':PROTOCOL,'base_hash':digest(draft['prose']),
            'instruction':'仅数字字面笔误可在 data.numeric_correction 交 base_hash/patches/repair_note；每项只替换完整数字（阿拉伯或中文），两类审稿须针对合成稿完整举证并 pass。保持冻结首读，不改账本事件效果、人物、情节、单位或日程；其他问题判 fix。'+effect+'。'}


def compose(work,draft,data,state):
    patch=data.get('numeric_correction')
    require(work['payload'].get('numeric_correction',{}).get('protocol')==PROTOCOL,
            'numeric_correction','此冻结任务未开放数值修正，按正常 fix 返修')
    require(isinstance(patch,dict) and set(patch)=={'base_hash','patches','repair_note'}
            and isinstance(patch['repair_note'],str) and patch['repair_note'].strip(),
            'numeric_correction','数值修正须完整版本、精确补丁及具体依据')
    require(all(data.get(k,{}).get('verdict')=='pass' for k in ('facts','reader')),
            'numeric_correction','数值修正须完整核验合成稿后两类均通过；仍有其他问题时判 fix')
    base=draft['prose'];prose=apply_patch(base,patch)
    literals={(m.start(),m.end()) for m in TOKEN.finditer(base)}
    title=TITLE.match(base.split('\n',1)[0])
    for change in patch['patches']:
        require(set(change)=={'start','old','new'} and NUMBER.fullmatch(change['old'])
                and NUMBER.fullmatch(change['new'])
                and (change['start'],change['start']+len(change['old'])) in literals,
                'numeric_correction','快速修正只替换完整数字字面值；文字、单位、句式和部分数字用普通返修')
        a=change['start'];b=a+len(change['old'])
        require(not (title and a<title.end('number') and b>title.start('number')),
                'numeric_correction','章节编号不能作为数值笔误修改')
        for entity in state['entities'].values():
            for name in [entity['name']]+entity.get('aliases',[]):
                require(not any(m.start()<b and m.end()>a for m in re.finditer(re.escape(name),base)),
                        'numeric_correction','人物与实体名称里的数字不走数值修正')
    return prose,copy.deepcopy(patch)


def effects(events):
    return [{k:v for k,v in event.items() if k not in ('reason','evidence','change_evidence','cause_evidence')} for event in events]


def verify(draft,events,old_state,new_state):
    if old_state is None and not isinstance(draft.get('candidate_events'),list):
        return  # standalone review: this receipt's events are the baseline itself
    require(effects(draft['candidate_events'])==effects(events) and state_hash(old_state)==state_hash(new_state),
            'numeric_correction_state','修正改变了事件效果或结算前态，须正常 fix 返修与传播核验')
