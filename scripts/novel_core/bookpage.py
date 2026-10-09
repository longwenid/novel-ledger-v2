"""Self-contained single-file HTML view over the whole book.

Renders every canon-level surface — contract, style anchor, layered design,
character profiles, spatial records, ledger state, chapter plans, full prose,
event chain, distilled memories — as one static page. Structured cards first;
raw JSON only as collapsed fallbacks. No server, no external assets. The file
is a derived read-only view; the database stays the single source of truth."""
import html
import json
import os
import time
from pathlib import Path

OP_LABELS = {'entity': '实体', 'alias': '别名', 'state': '状态', 'knowledge': '认知', 'resource': '资源',
             'hook': '承诺', 'clock': '时钟', 'character_development': '成长', 'anchor': '锚定',
             'anchor_assert': '锚定核', 'anchor_change': '锚定变', 'place': '场所', 'place_change': '场所变',
             'link': '联系', 'link_change': '联系变', 'placement': '站位', 'move': '移动', 'perceive': '感知'}
TIER_LABELS = {'major': '主要人物', 'supporting': '配角', 'walk_on': '路人'}

CSS = ''':root{--bg:#f7f4ee;--card:#fffdf8;--ink:#2b261e;--muted:#8a8070;--line:#e6dfd0;--accent:#8a6a35;
--accent-soft:#f2ead9;--ok:#3c6e3a;--ok-soft:#e4efdf;--warn:#8a5a12;--warn-soft:#f6e8c8;--bad:#8a3226;--bad-soft:#f3ded8;
--serif:Georgia,"Times New Roman","Noto Serif SC","Source Han Serif SC",SimSun,serif;
--sans:"Microsoft YaHei","PingFang SC","Hiragino Sans GB",system-ui,sans-serif;
--paper:#fbf8f1}
@media (prefers-color-scheme:dark){:root{--bg:#191713;--card:#211e18;--ink:#e9e2d2;--muted:#9c9280;--line:#3b352a;
--accent:#d6b478;--accent-soft:#332c1e;--ok:#a5c99a;--ok-soft:#26331f;--warn:#e0bd7c;--warn-soft:#3a2f17;--bad:#dfa294;--bad-soft:#3c241d;--paper:#1d1a15}}
*{box-sizing:border-box}body{margin:0;font-family:var(--sans);background:var(--bg);color:var(--ink);line-height:1.7;font-size:15px}
.bar{position:sticky;top:0;z-index:40;display:flex;align-items:center;gap:14px;padding:9px 20px;
 background:var(--bg);border-bottom:1px solid var(--line)}
.bar .menu{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:8px;
 padding:4px 11px;font-size:15px;cursor:pointer}
.bar .brand{display:flex;flex-direction:column;line-height:1.25;min-width:0}
.bar .brand b{font-family:var(--serif);font-size:16.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bar .brand span{color:var(--muted);font-size:11.5px;white-space:nowrap}
.bar .grow{flex:1}
.mode-tabs{display:flex;background:var(--card);border:1px solid var(--line);border-radius:11px;overflow:hidden;flex:0 0 auto}
.mode-tabs button{border:none;background:transparent;color:var(--ink);cursor:pointer;font-size:13.5px;padding:7px 16px}
.mode-tabs button.active{background:var(--accent);color:var(--bg);font-weight:700}
.layout{display:grid;grid-template-columns:212px minmax(0,1fr);gap:24px;padding:20px 24px 0}
body.side-off .layout{grid-template-columns:minmax(0,1fr)}
body.side-off aside{display:none}
aside{position:sticky;top:54px;align-self:start;max-height:calc(100vh - 62px);overflow-y:auto;padding:2px 4px 34px 0}
aside nav a{display:block;padding:5px 12px;margin:1px 0;border-radius:8px;color:var(--ink);text-decoration:none;font-size:13.5px}
aside nav a:hover{background:var(--accent-soft)}
.toc-select{width:100%;padding:6px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink);margin-top:10px}
main{min-width:0;padding:6px 0 70px}
h2{font-size:20px;margin:34px 0 4px;font-family:var(--serif);scroll-margin-top:64px}
h2:first-child{margin-top:10px}.sub{color:var(--muted);font-size:12.5px;margin:0 0 12px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(176px,1fr));gap:10px;margin:12px 0}
.stat{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 14px}
.stat b{display:block;font-size:21px;font-family:var(--serif)}.stat span{color:var(--muted);font-size:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 18px;margin:10px 0}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:10px}
@media(max-width:860px){.layout{display:block;padding:14px 14px 0}
 aside{position:fixed;left:0;top:0;bottom:0;width:240px;max-height:none;background:var(--card);z-index:60;
  padding:16px 12px;transform:translateX(-105%);transition:transform .2s;border-right:1px solid var(--line)}
 body.side-open aside{transform:none}
 body.side-open .scrim{display:block}.grid2{grid-template-columns:1fr}
 .bar .brand span{display:none}.bar{gap:10px;padding:8px 12px}.mode-tabs button{padding:7px 12px}}
.scrim{display:none;position:fixed;inset:0;background:rgba(0,0,0,.35);z-index:50}
.pill{display:inline-block;border-radius:20px;padding:1px 11px;font-size:12px;background:var(--accent-soft);color:var(--accent);margin:0 5px 5px 0}
.pill.ok{background:var(--ok-soft);color:var(--ok)}.pill.warn{background:var(--warn-soft);color:var(--warn)}
.pill.bad{background:var(--bad-soft);color:var(--bad)}.pill.dim{background:var(--bg);color:var(--muted)}
table.t{width:100%;border-collapse:collapse;font-size:13.5px;margin:8px 0}
table.t th{color:var(--muted);font-weight:600;text-align:left;padding:5px 10px 5px 0;border-bottom:1px solid var(--line);white-space:nowrap}
table.t td{padding:6px 10px 6px 0;border-bottom:1px dashed var(--line);vertical-align:top}
details{background:var(--card);border:1px solid var(--line);border-radius:12px;margin:8px 0;padding:4px 16px}
details>summary{cursor:pointer;font-weight:600;padding:9px 0;list-style:none;display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
details>summary::before{content:"▸";color:var(--muted);transition:transform .15s}
details[open]>summary::before{transform:rotate(90deg)}
details[open]>summary{border-bottom:1px dashed var(--line);margin-bottom:10px}
.note{color:var(--muted);font-size:12.5px}
.quote{font-family:var(--serif);background:var(--accent-soft);border-left:3px solid var(--accent);border-radius:0 8px 8px 0;padding:10px 16px;margin:8px 0;white-space:pre-wrap}
.raw{margin-top:8px}
.raw summary{font-weight:400;color:var(--muted);font-size:12px}
pre{background:var(--bg);border-radius:8px;padding:10px 12px;overflow-x:auto;font-size:12px;white-space:pre-wrap;word-break:break-word}
.bar2{height:7px;background:var(--line);border-radius:5px;overflow:hidden;margin:4px 0 2px}
.bar2 i{display:block;height:100%;background:var(--accent)}
.tools{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}
.tools input{flex:1;min-width:170px;padding:6px 12px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink);font-size:13px}
.tools button{padding:6px 14px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink);cursor:pointer;font-size:13px}
.tools button:hover{background:var(--accent-soft)}
.evt{padding:7px 0;border-bottom:1px dashed var(--line)}.evt:last-child{border-bottom:none}
.evt .head{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap}
.evt .ev{font-size:13.5px}
footer{color:var(--muted);font-size:12px;padding:18px 24px 44px}
.plans .vol{font-family:var(--serif);font-size:16px;margin:20px 0 4px;color:var(--accent)}
.plan-rows .row{display:grid;grid-template-columns:5em 1fr;gap:12px;margin:7px 0}
.plan-rows .lbl{color:var(--muted);font-weight:600;font-size:13px;padding-top:1px}
.scene{background:var(--bg);border-radius:8px;padding:6px 12px;margin:4px 0;font-size:13px}

/* ===== 阅读器视图 ===== */
#view-read{min-height:100vh}
body.reading{background:var(--paper)}
body.reading .bar{border-bottom-color:transparent;background:var(--paper)}
body.reading .mode-tabs button.active{background:var(--accent-soft);color:var(--accent)}
body.reading footer{background:var(--paper);border-top:1px solid var(--line);margin-top:40px}
.rd-tools{position:sticky;top:52px;z-index:30;display:flex;gap:8px;align-items:center;flex-wrap:wrap;
 padding:8px 14px;background:var(--paper);border-bottom:1px solid var(--line)}
.rd-tools .grow{flex:1}
.rd-tools .lab{color:var(--muted);font-size:12px}
.rd-tools button{padding:5px 13px;border:1px solid var(--line);border-radius:8px;background:transparent;color:var(--ink);cursor:pointer;font-size:13px}
.rd-tools button:hover{background:var(--accent-soft)}
.rd-tools button.on{background:var(--accent);border-color:var(--accent);color:var(--paper);font-weight:700}
.rd-tools .themes{display:flex;gap:6px;margin:0 4px}
.rd-tools .themes i{width:20px;height:20px;border-radius:50%;border:1px solid var(--line);cursor:pointer;display:inline-block}
.rd-tools .themes i.on{outline:2px solid var(--accent);outline-offset:2px}
.rd-wrap{width:min(94%,46em);margin:0 auto;padding:0 8px 40px}
.rd-wrap.wide{width:min(97%,62em)}
.ch-index.rd{margin-top:14px}
.ch-index.rd .item{padding:13px 10px;border-bottom:none;border-radius:12px}
.ch-index.rd .item:hover{background:var(--accent-soft)}
.ch-index.rd .book{font-family:var(--serif);text-align:center;margin:34px 0 8px;font-size:22px;letter-spacing:.12em}
.ch-index.rd .book-sub{text-align:center;color:var(--muted);font-size:12.5px;margin-bottom:26px}
.ch-index.rd .vol{font-family:var(--serif);color:var(--accent);font-size:14px;margin:22px 4px 4px;letter-spacing:.1em}
.chapter{max-width:100%;margin:0 auto;width:100%}
.ch-head{text-align:center;margin:44px 0 10px;font-family:var(--serif)}
.ch-head .orn{color:var(--accent);letter-spacing:.6em;font-size:11px}
.ch-head h3{font-size:1.6em;margin:12px 0 0;font-weight:600;letter-spacing:.1em}
.ch-head .meta{color:var(--muted);font-size:12.5px;margin-top:8px}
.chapter .prose{font-size:var(--reader-size,20px);line-height:2.1;text-align:justify;margin-top:30px;
 font-family:var(--reader-family,var(--serif));letter-spacing:.02em;overflow-wrap:break-word}
.chapter .prose p{margin:0 0 .9em;text-indent:2em}
.chapter .prose p:first-of-type{text-indent:0}
.ch-nav{display:flex;justify-content:space-between;align-items:center;gap:10px;margin:48px 0 8px;
 border-top:1px solid var(--line);padding-top:22px}
.ch-nav button{padding:10px 20px;border:1px solid var(--line);border-radius:22px;background:var(--card);color:var(--ink);cursor:pointer;font-size:14px}
.ch-nav button:hover:not(:disabled){background:var(--accent-soft)}
.ch-nav button:disabled{opacity:.3;cursor:default}
.ch-nav .back{flex:0 0 auto;border-radius:8px;border:none;background:transparent;color:var(--muted)}
.ch-nav .pos{font-family:var(--serif);color:var(--muted);font-size:13px}
::selection{background:var(--accent-soft)}
html.sans-reading .chapter .prose{font-family:var(--sans);letter-spacing:0}
html.sepia .chapter .prose{color:#4a3b26}
html.sepia .ch-head .orn{color:#a8843f}
html.sepia .ch-head h3,html.sepia .ch-index.rd .ttl,html.sepia .ch-index.rd .book{color:#3d3222}
html.sepia .rd-tools,html.sepia .rd-wrap,html.sepia footer,html.sepia .ch-nav{background:transparent}
html.sepia body,html.sepia{background:#f4ead6}
html.sepia .rd-tools,html.sepia .bar{background:#f4ead6;border-color:#e0d2b4}
html.sepia .ch-nav button{background:#fbf4e4;border-color:#e0d2b4}
html.night{--bg:#141210;--card:#1d1a15;--ink:#cfc6b4;--muted:#7e7666;--line:#2e2a22;
 --accent:#b99b62;--accent-soft:#2b251a;--paper:#141210}
html.night .chapter .prose{color:#b9b09d}
@media print{.bar,.tools,.ch-nav,.rd-tools,.scrim,footer .re{display:none}
 body,.layout,body.reading,html.sepia body{background:#fff}
 .layout{display:block;padding:0}.chapter{display:block!important}}'''


def _esc(value):
    return html.escape(str(value), quote=False)


def _pretty(value):
    return html.escape(json.dumps(value, ensure_ascii=False, indent=1), quote=False)


def _pills(items, cls=''):
    return ''.join(f'<span class="pill {cls}">{_esc(i)}</span>' for i in items)


def _raw(value, label='原始数据'):
    return f'<details class="raw"><summary>{_esc(label)}</summary><pre>{_pretty(value)}</pre></details>'


def _event_html(e, names):
    op = e.get('op', '?')
    label = OP_LABELS.get(op, op)
    def who(eid):
        return names.get(eid, eid)
    if op == 'resource':
        src = who(e.get('from')) if e.get('from') else '外部'
        dst = who(e.get('to')) if e.get('to') else '消耗'
        body = f'<b>{_esc(e.get("quantity",""))}</b> {_esc(e.get("asset",""))}（{_esc(e.get("unit",""))}）：{_esc(src)} → {_esc(dst)}　<span class="note">{_esc(e.get("reason",""))}</span>'
    elif op == 'hook':
        status = e.get('status', '')
        cls = {'open': 'warn', 'paid': 'ok', 'closed': 'dim'}.get(status, '')
        body = f'<span class="pill {cls}">{_esc(status)}</span>{_esc(e.get("text",""))}　<span class="note">最迟第{_esc(e.get("due","?"))}章{" · " + _esc(e.get("reason","")) if e.get("reason") else ""}</span>'
    elif op == 'knowledge':
        body = f'<b>{_esc(who(e.get("entity","")))}</b>·{_esc(e.get("topic",""))}：<i>{_esc(e.get("claim",""))}</i>　<span class="note">{_esc(e.get("stance",""))} · 源 {_esc(e.get("source",""))}</span>'
    elif op == 'state':
        body = f'<b>{_esc(who(e.get("entity","")))}</b>.{_esc(e.get("key",""))}：{_esc(e.get("before"))} → <b>{_esc(e.get("after"))}</b>'
    elif op == 'entity':
        ent = e.get('entity') or {}
        body = f'登记 <b>{_esc(ent.get("name",""))}</b>（{_esc(ent.get("kind",""))}，ID {_esc(ent.get("id",""))}）'
    elif op == 'alias':
        body = f'<b>{_esc(who(e.get("entity","")))}</b> 增加称呼「{_esc(e.get("alias",""))}」'
    elif op == 'clock':
        body = f'世界时钟：<b>{_esc(e.get("before"))}</b> → <b>{_esc(e.get("after"))}</b>'
    else:
        rest = {k: v for k, v in e.items() if k not in ('op', 'evidence', 'reason')}
        body = _esc(json.dumps(rest, ensure_ascii=False))
        if e.get('reason'):
            body += f'　<span class="note">{_esc(e["reason"])}</span>'
    quote = (e.get('evidence') or {}).get('quote', '')
    evidence = f'<br><span class="note">证据：「{_esc(quote)}」</span>' if quote else ''
    return f'<div class="evt"><div class="head"><span class="pill">{_esc(label)}</span><span class="ev">{body}</span></div>{evidence}</div>'


def _profile_html(name, record):
    p = record.get('data', {})
    bio, voice, arc = p.get('biography', {}), p.get('voice', {}), p.get('arc', {})
    tier = TIER_LABELS.get(p.get('tier', ''), p.get('tier', ''))
    out = [f'<details><summary>👤 {_esc(name)}<span class="pill">{_esc(tier)}</span>'
           f'<span class="note">第 {record.get("revision","?")} 版 · 基于 {_esc(record.get("based_on_chapter","?"))} 章 · 成长 {record.get("growth_count",0)} 次</span></summary>']
    pairs = [('社会身份', bio.get('public_role')), ('诉求', bio.get('desire')), ('恐惧', bio.get('fear')),
             ('底线', bio.get('boundary')), ('内在矛盾', bio.get('contradiction'))]
    rows = ''.join(f'<tr><th>{_esc(k)}</th><td>{_esc(v)}</td></tr>' for k, v in pairs if v)
    if bio.get('values'):
        rows += f'<tr><th>价值排序</th><td>{_pills(bio["values"])}</td></tr>'
    out.append(f'<table class="t">{rows}</table>')
    out.append('<p class="note">表达习惯</p>')
    vpairs = [('语域', voice.get('register')), ('节奏', voice.get('rhythm')), ('压力下', voice.get('stress_shift'))]
    vrows = ''.join(f'<tr><th>{_esc(k)}</th><td>{_esc(v)}</td></tr>' for k, v in vpairs if v)
    if voice.get('tactics'):
        vrows += f'<tr><th>策略</th><td>{_pills(voice["tactics"])}</td></tr>'
    if voice.get('relationship_modes'):
        vrows += '<tr><th>关系语气</th><td>' + '<br>'.join(f'{_esc(m.get("audience",""))}：{_esc(m.get("approach",""))}' for m in voice['relationship_modes']) + '</td></tr>'
    if voice.get('avoid'):
        vrows += f'<tr><th>避免</th><td class="note">{_esc("；".join(voice["avoid"]))}</td></tr>'
    out.append(f'<table class="t">{vrows}</table>')
    if arc:
        out.append(f'<p><b>成长方向</b> {_esc(arc.get("direction",""))}</p>')
        if arc.get('not_yet_earned'):
            out.append(f'<p class="note">尚未获得依据：{_esc("；".join(arc["not_yet_earned"]))}</p>')
    for item in bio.get('experiences', []):
        cls = 'ok' if item.get('status') == 'established' else 'dim'
        out.append(f'<p><span class="pill {cls}">{_esc("已确立" if item.get("status")=="established" else "设计")}</span>{_esc(item.get("text",""))}</p>')
    for ex in p.get('examples', []):
        out.append(f'<div class="quote">{_esc(ex.get("note",""))}</div>')
    if p.get('change_reason'):
        out.append(f'<p class="note">本次更新原因：{_esc(p["change_reason"])}</p>')
    out.append(_raw(record))
    out.append('</details>')
    return ''.join(out)


def render(db, branch):
    from .store import get, chapters as chapter_rows
    from .ledger import replay
    from .engine import status, audit
    from . import design, characters

    contract = get(db, 'contract')
    state = replay(db, branch)
    rows = chapter_rows(db, branch)
    report = audit(db, branch)
    stat = status(db)
    budget = stat.get('volume_budget', {})
    open_hooks = {k: v for k, v in state['hooks'].items() if v.get('status') == 'open'}
    head_n = len(rows)
    names = {eid: meta['name'] for eid, meta in state['entities'].items()}
    anchor = get(db, 'style_anchor')
    usage = stat.get('context_usage', {})
    generated = time.strftime('%Y-%m-%d %H:%M')

    nav = ''.join(f'<a href="#{key}">{label}</a>' for key, label in
                  [('overview', '概览'), ('contract', '创作约定'), ('anchor', '文风锚点'), ('design', '分层设计'),
                   ('characters', '人物档案'), ('spatial', '空间档案'), ('ledger', '事实账本'),
                   ('plans', '章计划'), ('events', '事件流'), ('memories', '提炼记忆')])
    page = ['<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{_esc(contract.get("title","书稿"))} · 全书视图</title><style>{CSS}</style></head><body>'
            '<header class="bar"><button class="menu" onclick="toggleSide()">☰</button>'
            '<div class="brand"><b>' + _esc(contract.get('title', '书稿')) + '</b>'
            f'<span>分支 {_esc(branch)} · {head_n} 章 · {report["han_characters"]:,} 汉字 · 生成于 {generated}</span></div>'
            '<div class="grow"></div>'
            '<div class="mode-tabs">'
            '<button type="button" data-m="settings" onclick="setMode(\'settings\')">📚 看设定</button>'
            '<button type="button" data-m="read" onclick="setMode(\'read\')">📖 读正文</button></div></header>'
            '<div class="scrim" onclick="toggleSide()"></div>'
            '<div class="layout"><aside>'
            f'<nav id="nav-settings">{nav}</nav>'
            '<select id="jump-read" class="toc-select" style="display:none" onchange="var o=this.selectedOptions[0];if(o.dataset.n)showChapter(+o.dataset.n)">'
            f'<option>跳转章节…</option>' + ''.join(f'<option data-n="{r["number"]}">第{r["number"]}章</option>' for r in rows) +
            '</select></aside><main><div id="view-settings">']

    # 概览
    page.append('<h2 id="overview">概览</h2>'
                f'<div class="sub">数据库派生视图 · 真源 novel.sqlite3 · 审计{"通过" if report["ok"] else "发现问题"}</div>'
                '<div class="cards">'
                f'<div class="stat"><b>{head_n}</b><span>已提交章节</span></div>'
                f'<div class="stat"><b>{report["han_characters"]:,}</b><span>累计汉字</span></div>'
                f'<div class="stat"><b>{len(open_hooks)}</b><span>待兑承诺</span></div>'
                f'<div class="stat"><b>{_esc(stat["status"])}</b><span>运行状态</span></div>'
                f'<div class="stat"><b>{usage.get("tasks",0)}</b><span>任务 · {usage.get("session_handoffs",0)} 次交接</span></div></div>')
    if report['problems']:
        page.append(f'<div class="card" style="border-color:var(--bad)"><b>审计问题</b><pre>{_pretty(report["problems"])}</pre></div>')

    # 创作约定
    page.append('<h2 id="contract">创作约定</h2><div class="sub">作者认可的全书边界,不为过关改约</div>')
    page.append('<div class="card"><table class="t">'
                f'<tr><th>读者承诺</th><td>{_esc(contract.get("reader_promise",""))}</td></tr>'
                f'<tr><th>主角驱动</th><td>{_esc(contract.get("protagonist_drive",""))}</td></tr>'
                f'<tr><th>结局</th><td>{_esc(contract.get("ending",""))}</td></tr>'
                f'<tr><th>字数区间</th><td>{contract.get("word_range",[0,0])[0]:,} ~ {contract.get("word_range",[0,0])[-1]:,} 汉字</td></tr></table></div>')
    if contract.get('world_rules'):
        page.append('<p class="note">世界规则</p><div class="card"><ul>' +
                    ''.join(f'<li>{_esc(r)}</li>' for r in contract['world_rules']) + '</ul></div>')
    if contract.get('arcs'):
        page.append('<p class="note">人物弧</p><div class="card">' + _pills(contract['arcs']) + '</div>')
    volumes = budget.get('volumes') or []
    if volumes:
        page.append('<p class="note">卷册进度(参考基线,非交稿门槛)</p><div class="cards">')
        for v in volumes:
            base = v.get('baseline_han') or 1
            pct = min(100, round(100 * v.get('written_han', 0) / base))
            page.append(f'<div class="stat"><b>{_esc(v["volume"])}</b><span>{v.get("written_han",0):,} / {base:,} 汉字'
                        f'{" · 已收官" if v.get("closed") else ""}</span><div class="bar2"><i style="width:{pct}%"></i></div></div>')
        page.append('</div>')
    page.append(_raw(contract, '完整约定 JSON'))

    # 文风锚点
    page.append('<h2 id="anchor">文风锚点</h2><div class="sub">执笔与 voice_match 审稿的唯一文风真源</div>')
    if anchor:
        if anchor.get('voice') or anchor.get('traits'):
            page.append('<div class="card">' + (f'<p><span class="pill">voice · {_esc(anchor.get("voice"))}</span></p>' if anchor.get('voice') else ''))
            for trait in anchor.get('traits', []):
                page.append(f'<p style="margin:4px 0">◦ {_esc(trait)}</p>')
            page.append('</div>')
        if anchor.get('sample'):
            page.append(f'<div class="quote">{_esc(anchor["sample"])}</div>')
        if anchor.get('sample_close'):
            page.append(f'<div class="quote">{_esc(anchor["sample_close"])}</div>')
    else:
        page.append('<p class="note">未设定 style_anchor;approve 定调会自动快照试章,或用 config 补设。</p>')

    # 分层设计
    page.append('<h2 id="design">分层设计</h2><div class="sub">未来提案,不覆盖当前事实</div>')
    if design.enabled(db):
        for scope in ['world'] + [v['id'] for v in contract.get('volumes', [])]:
            record = design.current(db, branch, scope)
            if not record:
                continue
            usable = design.usable(db, branch, scope)
            data = record.get('data', {})
            page.append(f'<details><summary>设计 · {_esc(scope)}<span class="pill {"ok" if usable else "warn"}">'
                        f'{"有效" if usable else "已失效"} · 基于 {_esc(record.get("sources") and len(record["sources"]) or 0)} 章</span></summary>')
            spine = data.get('event_spine')
            if spine:
                labels = {'main': '主线', 'subplot': '支线', 'timeline': '时间线', 'tension': '张力曲线'}
                page.append('<p><b>四层事件脊柱</b></p><table class="t"><tr><th>层</th><th>触点</th><th>跨度</th><th>事件</th></tr>')
                for layer in ('main', 'subplot', 'timeline', 'tension'):
                    for item in spine.get(layer, []):
                        page.append(f'<tr><td><span class="pill">{_esc(labels[layer])}</span></td>'
                                    f'<td class="note">{_esc(item["id"])}</td><td>{_esc(item["span"])}</td>'
                                    f'<td>{_esc(item["event"])}</td></tr>')
                page.append('</table>')
            if scope != 'world' and (data.get('carries_in') or data.get('climax') or data.get('handoff')):
                page.append('<p><b>卷纲</b></p><table class="t">'
                            + (f'<tr><th>承接上卷</th><td>{_esc(data["carries_in"])}</td></tr>' if data.get('carries_in') else '')
                            + (f'<tr><th>卷高潮</th><td>{_esc(data["climax"])}</td></tr>' if data.get('climax') else '')
                            + (f'<tr><th>下卷交接</th><td>{_esc(data["handoff"])}</td></tr>' if data.get('handoff') else '')
                            + '</table>')
            for key, value in data.items():
                if key in ('event_spine', 'carries_in', 'climax', 'handoff'):
                    continue
                if isinstance(value, str) and value:
                    page.append(f'<p><b>{_esc(key)}</b></p><p>{_esc(value)}</p>')
                elif isinstance(value, list) and value:
                    page.append(f'<p><b>{_esc(key)}</b></p><p>{_pills([str(i)[:40] for i in value])}</p>')
                elif isinstance(value, dict) and value:
                    page.append(f'<p><b>{_esc(key)}</b></p><table class="t">' +
                                ''.join(f'<tr><th>{_esc(k)}</th><td>{_esc(str(v)[:80])}</td></tr>' for k, v in value.items()) + '</table>')
            page.append(_raw(record))
            page.append('</details>')
    else:
        page.append('<p class="note">未启用分层设计(legacy 模式)。</p>')

    # 人物档案
    page.append('<h2 id="characters">人物档案</h2><div class="sub">创作小传与声线解读,非客观事实</div>')
    profiles = [(e['name'], characters.current(db, branch, eid)) for eid, e in state['entities'].items()
                if characters.person(state, eid) and characters.current(db, branch, eid)]
    page.append(''.join(_profile_html(n, r) for n, r in sorted(profiles)) or '<p class="note">尚无人物档案。</p>')

    # 空间档案
    page.append('<h2 id="spatial">空间档案</h2><div class="sub">证据锚定的场所与站位;地图是局部图,不证明未登记空间不存在</div>')
    places = state.get('places', {})
    if places:
        for eid, meta in sorted(places.items()):
            name = state['entities'].get(eid, {}).get('name', eid)
            fields = {k: v for k, v in meta.items() if k not in ('source',)}
            page.append(f'<details><summary>📍 {_esc(name)}<span class="note">{_esc(eid)}</span></summary>'
                        f'<table class="t">' + ''.join(f'<tr><th>{_esc(k)}</th><td>{_esc(str(v)[:100])}</td></tr>' for k, v in fields.items()) +
                        '</table>' + _raw(meta) + '</details>')
    else:
        page.append('<p class="note">尚无场所记录。</p>')
    anchors = state.get('anchors', {})
    if anchors:
        page.append(f'<details><summary>锚定事实({len(anchors)})</summary><table class="t"><tr><th>事实</th><th>值</th><th>策略</th></tr>' +
                    ''.join(f'<tr><td>{_esc(k)}</td><td>{_esc(str(state["facts"].get(k,"?")))}</td><td>{_esc(v.get("policy",""))}</td></tr>'
                            for k, v in sorted(anchors.items())) + '</table></details>')
    placements = state.get('placements', {})
    if placements:
        page.append(f'<details><summary>当前站位({len(placements)})</summary><table class="t"><tr><th>实体</th><th>位置</th><th>持有者</th></tr>' +
                    ''.join(f'<tr><td>{_esc(state["entities"].get(e,{}).get("name",e))}</td>'
                            f'<td>{_esc(p.get("position",{}).get("place","—"))}</td>'
                            f'<td>{_esc(p.get("position",{}).get("holder","—"))}</td></tr>' for e, p in sorted(placements.items())) +
                    '</table></details>')
    developments = state.get('character_developments', {})
    if developments:
        page.append(f'<details><summary>成长记录({sum(len(v) for v in developments.values())})</summary>' +
                    _raw(developments) + '</details>')

    # 事实账本
    page.append('<h2 id="ledger">事实账本</h2>'
                f'<div class="sub">时钟 {state["clock"]} · 实体 {len(state["entities"])} · 状态 {len(state["facts"])} · '
                f'认知 {len(state["knowledge"])} · 资源 {len(state["resources"])} · 承诺 {len(state["hooks"])}</div>')
    if open_hooks:
        page.append(f'<details open><summary>待兑承诺({len(open_hooks)})</summary>')
        for k, v in sorted(open_hooks.items(), key=lambda kv: kv[1].get('due', 0)):
            due = v.get('due', 0)
            cls = 'bad' if due <= head_n else 'warn'
            imp_tag = '<span class="pill">重要</span>' if v.get('important', True) else ''
            seed_ch = _esc((v.get('seed') or {}).get('chapter', '?'))
            page.append(f'<div class="evt"><div class="head"><b>{_esc(k)}</b><span class="pill {cls}">最迟第{due}章 · {left}</span>'
                        f'{imp_tag}</div>{_esc(v.get("text", ""))}'
                        f'<br><span class="note">埋设于第{seed_ch}章 · 延期 {v.get("extensions",0)}/2</span></div>')
        page.append('</details>')
    if state['resources']:
        page.append('<details open><summary>资源账</summary><table class="t"><tr><th>持有者</th><th>资产</th><th>数量</th></tr>' +
                    ''.join(f'<tr><td>{_esc(state["entities"].get(json.loads(k)[0],{}).get("name",json.loads(k)[0]))}</td>'
                            f'<td>{_esc(json.loads(k)[1])}</td><td><b>{_esc(v)}</b> {_esc(json.loads(k)[2])}</td></tr>'
                            for k, v in sorted(state['resources'].items())) + '</table></details>')
    if state['knowledge']:
        page.append('<details><summary>认知</summary><table class="t"><tr><th>谁</th><th>话题</th><th>命题</th><th>立场</th></tr>' +
                    ''.join(f'<tr><td>{_esc(state["entities"].get(k.split(":")[0],{}).get("name",k.split(":")[0]))}</td>'
                            f'<td>{_esc(k.split(":",1)[1])}</td><td><i>{_esc(v.get("claim",""))}</i></td>'
                            f'<td><span class="pill dim">{_esc(v.get("stance",""))}</span></td></tr>'
                            for k, v in sorted(state['knowledge'].items())) + '</table></details>')
    if state['facts']:
        page.append('<details><summary>动态状态</summary><table class="t"><tr><th>实体</th><th>属性</th><th>当前值</th></tr>' +
                    ''.join(f'<tr><td>{_esc(names.get(k.split(":")[0], k.split(":")[0]))}</td><td>{_esc(k.split(":",1)[1])}</td><td>{_esc(str(v)[:120])}</td></tr>'
                            for k, v in sorted(state['facts'].items())) + '</table></details>')
    page.append('<details><summary>实体名录</summary><table class="t"><tr><th>名称</th><th>类型</th><th>ID</th><th>称呼</th></tr>' +
                ''.join(f'<tr><td>{_esc(v["name"])}</td><td>{_esc(v.get("kind",""))}</td><td class="note">{_esc(k)}</td>'
                        f'<td>{_esc("、".join(v.get("aliases",[])) or "—")}</td></tr>' for k, v in sorted(state['entities'].items())) +
                '</table>' + _raw({'facts': state['facts']}) + '</details>')

    # 章计划
    plans = db.execute('SELECT number,data FROM plans WHERE branch=? ORDER BY number', (branch,)).fetchall()
    page.append(f'<h2 id="plans">章计划({len(plans)})</h2><div class="sub">滚动细化的近期路线;重大转折须两案择优</div><div class="plans">')
    purposes = {v['id']: v.get('purpose', '') for v in contract.get('volumes', [])}
    current_volume = None
    for row in plans:
        p = json.loads(row['data'])
        if p.get('volume') != current_volume:
            current_volume = p.get('volume')
            page.append(f'<div class="vol">卷 { _esc(current_volume or "") }{_esc(" · " + purposes[current_volume]) if current_volume in purposes else ""}</div>')
        flags = ('<span class="pill warn">卷末复核</span>' if p.get('volume_end') else '') + \
                ('<span class="pill bad">终章</span>' if p.get('book_end') else '') + \
                ('<span class="pill dim">重大转折</span>' if p.get('major_turn') else '')
        scene_bits = ''.join(f'<div class="scene">📍 {_esc(names.get(s.get("place"), s.get("place") or s.get("label","?")))}'
                             f'　👥 {_esc("、".join(names.get(x, x) for x in s.get("present",[])) or "无人")}</div>'
                             for s in p.get('scenes', []))
        turn = ''
        if p.get('major_turn') and p.get('alternatives'):
            turn = ('<div class="row"><span class="lbl">两案</span><span>' +
                    '<br>'.join(f'{i+1}. {_esc(a)}' for i, a in enumerate(p['alternatives'])) +
                    f'<br><span class="note">择优：{_esc(p.get("selection_reason",""))}</span></span></div>')
        page.append(f'<details id="plan{row["number"]}"><summary><span class="pill">第{row["number"]}章</span>'
                    f'{_esc(p.get("goal",""))}{flags}</summary><div class="plan-rows">'
                    f'<div class="row"><span class="lbl">目标</span><span>{_esc(p.get("goal",""))}</span></div>'
                    f'<div class="row"><span class="lbl">阻力</span><span>{_esc(p.get("conflict",""))}</span></div>'
                    f'<div class="row"><span class="lbl">变化</span><span>{_esc(p.get("change",""))}</span></div>'
                    + (f'<div class="row"><span class="lbl">人物</span><span>{_pills(names.get(e, e) for e in p.get("entities",[]))}</span></div>' if p.get('entities') else '')
                    + (f'<div class="row"><span class="lbl">钩子</span><span>{_pills(p.get("hooks",[]))}</span></div>' if p.get('hooks') else '')
                    + (f'<div class="row"><span class="lbl">脊柱</span><span>{_pills(p.get("spine_refs",[]))}</span></div>' if p.get('spine_refs') else '')
                    + (f'<div class="row"><span class="lbl">场景</span><span>{scene_bits}</span></div>' if scene_bits else '')
                    + (f'<div class="row"><span class="lbl">依赖</span><span>{_pills([f"第{n}章" for n in p.get("dependencies",[])])}</span></div>' if p.get('dependencies') else '')
                    + turn
                    + '</div>' + _raw(p) + '</details>')
    page.append('</div>')

    # 事件流
    page.append('<h2 id="events">事件流</h2><div class="sub">哈希链顺序回放;每项事件以正文证据为据</div>')
    chapters_with_events = db.execute('SELECT chapter,COUNT(*) c FROM events WHERE branch=? GROUP BY chapter ORDER BY chapter', (branch,)).fetchall()
    for row in chapters_with_events:
        events = [json.loads(r[0]) for r in db.execute('SELECT data FROM events WHERE branch=? AND chapter=? ORDER BY idx', (branch, row['chapter']))]
        page.append(f'<details><summary>第{row["chapter"]}章<span class="pill dim">{row["c"]} 事件</span></summary>'
                    + ''.join(_event_html(e, names) for e in events) + '</details>')
    if not chapters_with_events:
        page.append('<p class="note">尚无登记事件。</p>')

    # 提炼记忆
    memories = db.execute('SELECT scope,through,data FROM memories WHERE branch=? ORDER BY through', (branch,)).fetchall()
    page.append(f'<h2 id="memories">提炼记忆({len(memories)})</h2>')
    for row in memories:
        m = json.loads(row['data'])
        body = m.get('text') if isinstance(m, dict) else str(m)
        page.append(f'<details><summary>{_esc(row["scope"])} · 至第{row["through"]}章</summary>'
                    f'<div class="quote">{_esc(body)}</div>' + (_raw(m) if isinstance(m, dict) else '') + '</details>')
    if not memories:
        page.append('<p class="note">尚无提炼记忆。</p>')

    # ===== 读正文视图(专用阅读器界面)=====
    page.append('</div><div id="view-read" hidden>')
    page.append('<div class="rd-tools">'
                '<span class="lab">字号</span>'
                '<button onclick="readerSize(-1)">A-</button><button onclick="readerSize(1)">A+</button>'
                '<span class="grow"></span>'
                '<span class="lab">版心</span>'
                '<button id="rw-std" onclick="readerWide(false)">适中</button>'
                '<button id="rw-wide" onclick="readerWide(true)">加宽</button>'
                '<span class="grow"></span>'
                '<span class="lab">纸色</span><span class="themes">'
                '<i class="th-paper on" onclick="readerTheme(\'paper\')" style="background:#fbf8f1" title="纸白"></i>'
                '<i class="th-sepia" onclick="readerTheme(\'sepia\')" style="background:#f4ead6" title="羊皮"></i>'
                '<i class="th-dark" onclick="readerTheme(\'dark\')" style="background:#211e18" title="夜间"></i></span>'
                '<span class="grow"></span>'
                '<button id="rb-serif" onclick="readerFamily(true)">衬线</button>'
                '<button id="rb-sans" onclick="readerFamily(false)">黑体</button>'
                '<span class="grow"></span>'
                + (f'<button onclick=\'showChapter({rows[0]["number"]})\'>从第一章读起</button>' if rows else '<span class="lab">暂无章节</span>')
                + '</div>')
    page.append('<div class="rd-wrap" id="rd-wrap"><div class="ch-index rd" id="ch-index">')
    if rows:
        page.append(f'<div class="book">❦ {_esc(contract.get("title","书稿"))} ❦</div>'
                    f'<div class="book-sub">{len(rows)} 章 · {report["han_characters"]:,} 字 · 目录</div>')
        volume_titles = {v['id']: v.get('purpose', '') for v in contract.get('volumes', [])}
        seen_volume = None
        for row in rows:
            data = json.loads(row['data'])
            plan = db.execute('SELECT data FROM plans WHERE branch=? AND number=?', (branch, row['number'])).fetchone()
            vol = json.loads(plan[0]).get('volume', '') if plan else ''
            if vol and vol != seen_volume:
                seen_volume = vol
                purpose = volume_titles.get(vol, '')
                page.append(f'<div class="vol">{_esc("卷 " + vol + (" · " + purpose if purpose else ""))}</div>')
            lines = [l.strip() for l in row['prose'].split('\n') if l.strip()]
            first = lines[0] if lines else ''
            title_like = len(first) <= 25 and first[:1] in '第卷'
            ttl = first if title_like else f'第{row["number"]}章'
            page.append(f'<div class="item" onclick="showChapter({row["number"]})"><span class="no">第{row["number"]}章</span>'
                        f'<span class="ttl">{_esc(ttl)}</span><span class="sum">{_esc(data.get("summary",""))}</span></div>')
    else:
        page.append('<p class="note">尚无已提交章节。</p>')
    page.append('</div>')
    for i, row in enumerate(rows):
        data = json.loads(row['data'])
        lines = [l.strip() for l in row['prose'].split('\n') if l.strip()]
        first = lines[0] if lines else ''
        title_like = len(first) <= 25 and first[:1] in '第卷'
        ttl = first if title_like else f'第{row["number"]}章'
        prose = ''.join(f'<p>{_esc(par)}</p>' for par in (lines[1:] if title_like else lines))
        prev_n = rows[i - 1]['number'] if i > 0 else 0
        next_n = rows[i + 1]['number'] if i + 1 < len(rows) else 0
        prev_btn = ('disabled' if not prev_n else f"onclick='showChapter({prev_n})'")
        next_btn = ('disabled' if not next_n else f"onclick='showChapter({next_n})'")
        page.append(f'<section class="chapter" id="read-{row["number"]}" hidden>'
                    f'<div class="ch-head"><div class="orn">❖ ❖ ❖</div><h3>{_esc(ttl)}</h3>'
                    f'<div class="meta">{len(row["prose"]):,} 字 · {_esc(data.get("summary",""))}</div></div>'
                    f'<div class="prose">{prose}</div>'
                    f'<div class="ch-nav"><button {prev_btn}>← 上一章</button>'
                    f"<button class='back' onclick='showIndex()'>目录</button>"
                    f'<span class="pos">{i + 1} / {len(rows)}</span>'
                    f'<button {next_btn}>下一章 →</button></div></section>')
    page.append('</div></main></div><footer>本页为派生只读视图,真源是 novel.sqlite3;重新生成:novel.py html --project &lt;PROJECT&gt;'
                f' · 任务 {usage.get("tasks",0)} · 会话交接 {usage.get("session_handoffs",0)} · 输入 {usage.get("input_chars",0):,} 字符'
                ' · <span class="re">Ctrl+P 打印可导出当前视图 PDF</span></footer>'
                '<script>function setMode(m,keep){var r=document.getElementById(\'view-read\'),s=document.getElementById(\'view-settings\');'
                'r.hidden=m!==\'read\';s.hidden=m!==\'settings\';'
                'document.body.classList.toggle(\'reading\',m===\'read\');'
                'document.getElementById(\'nav-settings\').style.display=m===\'settings\'?\'\':\'none\';'
                'document.getElementById(\'jump-read\').style.display=m===\'read\'?\'\':\'none\';'
                'document.querySelectorAll(\'.mode-tabs button\').forEach(function(b){b.classList.toggle(\'active\',b.dataset.m===m)});'
                'if(!keep)history.replaceState(null,\'\',m===\'read\'?\'#read\':\'#settings\')}'
                'function showChapter(n,keep){setMode(\'read\',true);'
                'var ix=document.getElementById(\'ch-index\');'
                'document.querySelectorAll(\'.chapter\').forEach(function(s){s.hidden=true});'
                'if(ix)ix.hidden=true;var el=document.getElementById(\'read-\'+n);if(el)el.hidden=false;'
                'if(!keep)history.replaceState(null,\'\',\'#read-\'+n);window.scrollTo(0,0)}'
                'function showIndex(){setMode(\'read\',true);'
                'document.querySelectorAll(\'.chapter\').forEach(function(s){s.hidden=true});'
                'var ix=document.getElementById(\'ch-index\');if(ix)ix.hidden=false;'
                'location.hash=\'#read\'}'
                'function filterBook(q){const t=q.trim().toLowerCase();'
                "document.querySelectorAll('.ch-index .item').forEach(function(d){d.style.display=!t||d.textContent.toLowerCase().includes(t)?'':'none'})}"
                'var RS={size:parseInt(localStorage.getItem(\'bs_rsize\')||\'20\',10),serif:localStorage.getItem(\'bs_rserif\')!==\'0\','
                'wide:localStorage.getItem(\'bs_rwide\')===\'1\',theme:localStorage.getItem(\'bs_rtheme\')||\'paper\'};'
                'function applyReader(){document.documentElement.style.setProperty(\'--reader-size\',RS.size+\'px\');'
                'document.documentElement.classList.toggle(\'sans-reading\',!RS.serif);'
                'document.documentElement.classList.toggle(\'sepia\',RS.theme===\'sepia\');'
                'document.documentElement.classList.toggle(\'night\',RS.theme===\'dark\');'
                'var w=document.getElementById(\'rd-wrap\');if(w)w.classList.toggle(\'wide\',RS.wide);'
                'var a=document.getElementById(\'rb-serif\'),b=document.getElementById(\'rb-sans\'),'
                'c=document.getElementById(\'rw-std\'),d=document.getElementById(\'rw-wide\');'
                'if(a&&b){a.classList.toggle(\'on\',RS.serif);b.classList.toggle(\'on\',!RS.serif)}'
                'if(c&&d){c.classList.toggle(\'on\',!RS.wide);d.classList.toggle(\'on\',RS.wide)}'
                'document.querySelectorAll(\'.rd-tools .themes i\').forEach(function(t){'
                't.classList.toggle(\'on\',t.classList.contains(\'th-\'+RS.theme))})}'
                'function readerSize(v){RS.size=Math.min(28,Math.max(15,RS.size+v));'
                'localStorage.setItem(\'bs_rsize\',RS.size);applyReader()}'
                'function readerFamily(v){RS.serif=v;localStorage.setItem(\'bs_rserif\',v?\'1\':\'0\');applyReader()}'
                'function readerWide(v){RS.wide=v;localStorage.setItem(\'bs_rwide\',v?\'1\':\'0\');applyReader()}'
                'function readerTheme(t){RS.theme=t;localStorage.setItem(\'bs_rtheme\',t);applyReader()}'
                'applyReader();'
                'function toggleSide(){if(matchMedia("(max-width:860px)").matches){'
                'document.body.classList.toggle("side-open")}else{document.body.classList.toggle("side-off");'
                'localStorage.setItem("bs_side",document.body.classList.contains("side-off")?"0":"1")}}'
                'if(localStorage.getItem("bs_side")==="0"&&!matchMedia("(max-width:860px)").matches){document.body.classList.add("side-off")}'
                'var h=location.hash||\'\';if(h.indexOf(\'#read\')===0){setMode(\'read\',true);'
                'var m=h.match(/\\d+/);if(m)showChapter(+m[0],true)}else{setMode(\'settings\',true)}</script>'
                '</body></html>')
    return ''.join(page)


def save(path, page):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f'.{os.getpid()}.tmp')
    temp.write_text(page, encoding='utf-8')
    os.replace(temp, path)
