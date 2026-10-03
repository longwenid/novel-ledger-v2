"""Single-file HTML book view over settings and prose."""
import json
import tempfile
import unittest
from pathlib import Path
from support import *
from novel_core.store import put, transaction
from novel_core import engine, bookpage
import novel


class BookPageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        initialize(self.path,contract());self.db=connect(self.path)
    def tearDown(self): self.db.close();self.temp.cleanup()

    def test_html_contains_all_setting_surfaces(self):
        drive(self.db,self.path,count=1)
        outcome=novel.dispatch(novel.parser().parse_args(['html','--project',str(self.path)]))
        self.assertEqual(outcome['file'],'book.html')
        page=(self.path/'book.html').read_text(encoding='utf-8')
        self.assertEqual(outcome['chars'],len(page))
        self.assertIn('<!DOCTYPE html>',page)
        self.assertIn('创作约定',page)
        self.assertIn('账本测试',page)                                    # contract title
        self.assertIn('正文',page)
        self.assertIn('周行把收据交给宋禾',page)                          # committed prose
        self.assertIn('章计划',page)
        self.assertIn('事件流',page)
        self.assertIn('提炼记忆',page)
        self.assertIn('事实账本',page)
        self.assertIn('novel.sqlite3',page)                               # derived-view note

    def test_custom_out_resolved_relative_to_project(self):
        t=engine.next_task(self.db,self.path);respond(self.db,self.path,t)
        outcome=novel.dispatch(novel.parser().parse_args(
            ['html','--project',str(self.path),'--out','views/book.html']))
        self.assertEqual(outcome['file'],'views/book.html')
        self.assertTrue((self.path/'views'/'book.html').exists())
        self.assertNotIn('C:',outcome['file'])

    def test_style_anchor_surface(self):
        with transaction(self.db):
            put(self.db,'style_anchor',{'voice':'平实白描','traits':['克制叙述,动作可追'],'sample':'刀出鞘时,雨还没停。','source':'author'})
        drive(self.db,self.path,count=1)
        page=bookpage.render(self.db,'main')
        self.assertIn('平实白描',page)
        self.assertIn('克制叙述,动作可追',page)

    def test_render_on_empty_book_does_not_crash(self):
        page=bookpage.render(self.db,'main')
        self.assertIn('创作约定',page)
        self.assertTrue('（无）' in page or '尚无' in page)


if __name__=='__main__':unittest.main()
