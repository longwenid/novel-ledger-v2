"""Result files that are not UTF-8 (GBK hosts) fail with recovery guidance.

A leading UTF-8 BOM (PowerShell '>' redirection) is unambiguous, so it is
stripped instead of bouncing a full worker retry; genuinely broken encodings
still fail with the recovery path.
"""
import tempfile
import unittest
from pathlib import Path

import support  # registers scripts/ on sys.path for `import novel`
import novel
from novel_core.store import Error


class ResultEncodingTests(unittest.TestCase):
    def test_gbk_result_reports_recovery_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'result.json'
            path.write_text('{"summary":"他把柴刀放回了渡口"}', encoding='gbk')
            with self.assertRaises(Error) as ctx:
                novel.read(path)
            self.assertEqual(ctx.exception.code, 'encoding')
            self.assertIn('宿主文件写入工具', str(ctx.exception))
            self.assertEqual(ctx.exception.details, {'path': str(path)})

    def test_bom_prefix_is_stripped_not_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'result.json'
            path.write_bytes((chr(0xfeff)+'{"ok":1}').encode('utf-8'))
            self.assertEqual(novel.read(path), {'ok': 1})

    def test_plain_utf8_still_parses(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'result.json'
            path.write_text('{"summary":"灯又亮了"}', encoding='utf-8')
            self.assertEqual(novel.read(path), {'summary': '灯又亮了'})


if __name__ == '__main__':
    unittest.main()
