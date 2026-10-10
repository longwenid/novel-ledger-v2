"""Replay frozen preparation inputs on a disposable clone; never edit the book.

Input sizes cover complete delivery JSON and distinct referenced prose files per
task. The optional o200k tokenizer is a common comparison ruler, not Gemini or
host billing. System prompts, cache billing and tool wrappers are excluded.
"""
import argparse
import copy
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from novel_core import contextflow
from novel_core.store import connect, get, put, digest


def delivery_texts(db,work):
    hashes=set()
    def visit(value):
        if isinstance(value,dict):
            for key,item in value.items():
                if key.endswith('prose_hash') and isinstance(item,str) and key[:-4]+'path' in value:hashes.add(item)
                else:visit(item)
        elif isinstance(value,list):
            for item in value:visit(item)
    visit(work)
    texts=[json.dumps(work,ensure_ascii=False,indent=2)+'\n']
    for hashed in sorted(hashes):
        text=get(db,'candidate_text:'+hashed)
        if not isinstance(text,str) or digest(text)!=hashed:raise ValueError('Referenced frozen prose is missing or corrupt')
        texts.append(text)
    return texts


def replay(project,branch,chapter):
    project=Path(project).resolve()
    live=sqlite3.connect((project/'novel.sqlite3').as_uri()+'?mode=ro',uri=True)
    with tempfile.TemporaryDirectory(prefix='novel-material-benchmark-') as tmp:
        clone=sqlite3.connect(Path(tmp)/'novel.sqlite3');live.backup(clone);clone.close();live.close()
        db=connect(tmp)
        try:
            rows=db.execute('SELECT stage,input FROM tasks WHERE branch=? AND chapter=? ORDER BY rowid',(branch,chapter)).fetchall()
            works=[json.loads(row['input']) for row in rows]
            bundles=[]
            for work in works:
                ident=work.get('payload',{}).get('bundle')
                if work['stage'] in ('context_scan','context_reduce') and ident not in bundles:bundles.append(ident)
            if not bundles:raise ValueError('No frozen preparation bundles for this chapter and branch')
            bases=[];summaries=[]
            for ident in bundles:
                bundle=get(db,contextflow.view_key(ident));base=copy.deepcopy(bundle['core'])
                base.pop('context_materials',None);base.pop('context_delivery',None)
                for doc in bundle['documents']:
                    container=base;path=doc['field'].split('.')
                    for key in path[:-1]:container=container.setdefault(key,{})
                    container[path[-1]]=json.loads(doc['text'])
                bases.append(base)
                summaries.append({'stage':base['stage'],'source_units':sum(len(d['units']) for d in bundle['documents'])})
            # Remove only derived caches from the disposable clone.
            db.execute("DELETE FROM meta WHERE key LIKE 'context_bundle:%'")
            db.execute('DELETE FROM material_preparation_cache')
            put(db,'preparation_policy','adaptive')
            optimized=[]
            for base in bases:
                work=contextflow.fit(db,base)
                if work['stage'] in ('context_scan','context_reduce'):
                    raise ValueError('This source requires streaming; compare a moderate exact-record case')
                for old in works:
                    if old['stage']==base['stage'] and 'review_context_hash' in old:work['review_context_hash']=old['review_context_hash']
                for field in contextflow.MATERIAL_FIELDS:
                    if field in base and work.get(field)!=base[field]:raise AssertionError('Source content changed: '+field)
                optimized.append(work)
            baseline_texts=[text for work in works for text in delivery_texts(db,work)]
            optimized_texts=[text for work in optimized for text in delivery_texts(db,work)]
            before=sum(map(len,baseline_texts));after=sum(map(len,optimized_texts))
            report={'scope':'frozen delivery JSON and unique referenced prose files per task; excludes system prompts, receipts, chat history and tool wrappers',
                'project':str(project),'branch':branch,'chapter':chapter,'frozen_sources':summaries,
                'baseline':{'tasks':len(works),'preparation_tasks':sum(w['stage'] in ('context_scan','context_reduce') for w in works),'input_chars':before},
                'optimized':{'tasks':len(optimized),'preparation_tasks':0,'input_chars':after,'source_content_preserved':True},
                'input_char_reduction_percent':round(100*(1-after/before),2),
                'host_time_and_billing':'not measured; dispatch reduction is structural, not a wall-clock forecast'}
            try:
                import tiktoken
                encoding=tiktoken.get_encoding('o200k_base')
                old_tokens=sum(len(encoding.encode(t,disallowed_special=())) for t in baseline_texts)
                new_tokens=sum(len(encoding.encode(t,disallowed_special=())) for t in optimized_texts)
                report['token_comparison']={'tokenizer':'o200k_base; comparison only, not Gemini billing',
                    'baseline':old_tokens,'optimized':new_tokens,'reduction_percent':round(100*(1-new_tokens/old_tokens),2)}
            except (ImportError,OSError):report['token_comparison']=None
            return report
        finally:db.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',required=True);parser.add_argument('--branch',required=True)
    parser.add_argument('--chapter',type=int,required=True);parser.add_argument('--out',required=True)
    args=parser.parse_args();report=replay(args.project,args.branch,args.chapter)
    out=Path(args.out).resolve();out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
