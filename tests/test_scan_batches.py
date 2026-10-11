"""Cross-document packing reduces dispatches while retaining exact source coverage."""
import copy
import tempfile
import unittest
from pathlib import Path

from support import contract, initialize, connect, get, put, dump
from novel_core import contextflow, engine, taskio
from novel_core.store import Error, digest


class ScanBatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        c=contract();c['context_mode']='bounded';c['preparation_policy']='semantic'
        initialize(self.path,c);self.db=connect(self.path)
        self.base={'schema':'novel-ledger-v2.pack/1','branch':'main','chapter':2,'stage':'draft',
                   'contract':get(self.db,'contract'),'plan':{'goal':'核对每个来源'},'payload':{},
                   'state':{'knowledge':{'hero:k'+str(i):{'claim':'必要条件'+str(i)} for i in range(49)}},
                   'selected':[{'kind':'source','text':'另文档来源'+str(i)} for i in range(6)]}
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def advance_to_tail(self):
        for index in range(3):
            work=contextflow.fit(self.db,self.base)
            self.assertEqual(work['stage'],'context_scan')
            self.assertNotIn('pages',work['payload']);self.assertEqual(work['payload']['page_unit_count'],16)
            contextflow.accept(self.db,work,{'cannot_compact':True,'reason':'完整保留不同认知条件'},'scan'+str(index))
        return contextflow.fit(self.db,self.base)
    def register(self,work):
        self.db.execute('INSERT INTO tasks(id,branch,stage,chapter,input_hash,input,state) VALUES (?,?,?,?,?,?,?)',
                        ('batch','main',work['stage'],2,digest(work),dump(work),'pending'))
        return engine.task_files(self.db,self.path,dict(self.db.execute("SELECT * FROM tasks WHERE id='batch'").fetchone()))

    def test_55_units_use_four_scans_with_complete_exact_source_chains(self):
        work=self.advance_to_tail();pages=work['payload']['pages']
        self.assertEqual([p['page_unit_count'] for p in pages],[1,6])
        self.assertEqual(work['payload']['page_unit_count'],7)
        self.assertIn('[提交契约]',work['guide']);self.assertIn('local_semantic_start',work['guide'])
        envelope=self.register(work)
        for page in pages:
            text=(self.path/page['prose_path']).read_text(encoding='utf-8');unit=page['units'][0]
            quote=text[unit['local_semantic_start']:unit['local_semantic_end']]
            self.assertTrue(quote)
            self.assertEqual(unit['semantic_start'],page['start']+unit['local_semantic_start'])
            located=taskio.locate(self.db,'batch',quote,document=page['document'])
            self.assertEqual(located,{'document':page['document'],'start':unit['semantic_start'],'quote':quote})
        self.assertEqual(len(envelope['prose_files']),2)
        contextflow.accept(self.db,work,{'cannot_compact':True,'reason':'保留所有原页，不能以压缩丢失约束'},'batch')
        final=contextflow.fit(self.db,self.base)
        self.assertEqual(final['stage'],'draft')
        bundle=get(self.db,contextflow.view_key(work['payload']['bundle']))
        self.assertEqual(bundle['scans'],4);self.assertEqual(bundle['reductions'],0)
        coverage=[]
        for node in bundle['nodes']:
            coverage.extend(node['coverage'] if isinstance(node['coverage'],list) else [node['coverage']])
        for doc in bundle['documents']:
            spans=[r for r in coverage if r['document']==doc['id']];cursor=0
            for span in spans:self.assertEqual(span['start'],cursor);cursor=span['end']
            self.assertEqual(cursor,len(doc['text']))
        self.assertEqual(len(bundle['nodes'][-1]['notes']),2)

    def test_partial_batch_coverage_or_out_of_page_evidence_never_advances(self):
        work=self.advance_to_tail();self.register(work);pages=work['payload']['pages']
        coverage=[{k:p[k] for k in ('document','document_hash','start','end')} for p in pages]
        original=get(self.db,contextflow.view_key(work['payload']['bundle']))
        data={'coverage':coverage,'notes':[],'omission_reason':'合成工程夹具'}
        for corrupt in (lambda r:r['coverage'].pop(),lambda r:r['coverage'][1].update(end=0)):
            r=copy.deepcopy(data);corrupt(r)
            receipt={'schema':'novel-ledger-v2.result/1','task_id':'batch','input_hash':digest(work),'data':r}
            with self.assertRaises(Error) as caught:engine.submit(self.db,receipt)
            self.assertEqual(caught.exception.code,'context_coverage')
            self.assertEqual(get(self.db,contextflow.view_key(work['payload']['bundle'])),original)
        r=copy.deepcopy(data);doc=original['documents'][0]
        r['notes']=[{'text':'伪造已读位置','evidence':[{'document':doc['id'],'start':0,'quote':doc['text'][:10]}]}]
        with self.assertRaises(Error) as caught:contextflow.accept(self.db,work,r,'bad')
        self.assertEqual(caught.exception.code,'context_evidence')
        self.assertEqual(get(self.db,contextflow.view_key(work['payload']['bundle'])),original)

    def test_old_bundle_keeps_single_document_layout(self):
        work=self.advance_to_tail();ident=work['payload']['bundle']
        bundle=get(self.db,contextflow.view_key(ident));bundle.pop('scan_layout');put(self.db,contextflow.view_key(ident),bundle)
        old=contextflow.fit(self.db,self.base)
        self.assertNotIn('pages',old['payload']);self.assertEqual(old['payload']['page_unit_count'],1)
        contextflow.accept(self.db,old,{'cannot_compact':True,'reason':'旧冻结协议'},'old')
        rest=contextflow.fit(self.db,self.base);self.assertEqual(rest['payload']['page_unit_count'],6)

    def test_session_chapter_six_source_distributions_keep_coverage_with_eight_scans(self):
        # Unit counts from the frozen session sources; no real book content is copied.
        for counts in ([19,11,3,22],[21,15,19]):
            with self.subTest(source_units=counts):
                base={k:copy.deepcopy(v) for k,v in self.base.items() if k not in ('state','selected')}
                for field,size in zip(('state','continuity','character_context','selected'),counts):
                    if field=='selected':base[field]=[{'text':'合成来源'+str(i)} for i in range(size)]
                    else:
                        container={'state':'knowledge','continuity':'links','character_context':'profiles'}[field]
                        base[field]={container:{str(i):{'claim':'合成完整单元'+str(i)} for i in range(size)}}
                scans=0;covered=0
                while True:
                    work=contextflow.fit(self.db,base)
                    if work['stage']!='context_scan':break
                    scans+=1;covered+=work['payload']['page_unit_count']
                    contextflow.accept(self.db,work,{'cannot_compact':True,'reason':'完整保留每个原单元'},'distribution'+str(counts)+str(scans))
                self.assertEqual(covered,55);self.assertEqual(scans,4)


if __name__=='__main__':unittest.main()
