"""Automated quality guard: syntax compatibility, assets validity and link integrity."""
from __future__ import annotations
import ast
import glob
import json
import os
from pathlib import Path
import py_compile
import re
import tempfile
import unittest

import sys
ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / 'scripts'
sys.path.insert(0, str(SCRIPTS_DIR))
REFERENCES_DIR = ROOT / 'references'
ASSETS_DIR = ROOT / 'assets'


class QualityGuardTests(unittest.TestCase):
    def test_all_python_files_compile(self):
        """Ensure all Python source files compile cleanly under current Python."""
        errors = []
        for py_path in ROOT.rglob('*.py'):
            if '/.' in str(py_path):
                continue
            try:
                py_compile.compile(str(py_path), doraise=True)
            except Exception as exc:
                errors.append(f"{py_path}: {exc}")
        self.assertEqual(errors, [], "Python compilation failed for files:\n" + "\n".join(errors))

    def test_no_fstring_backslash_expressions(self):
        """Python 3.9-3.11 forbid backslashes inside f-string expressions.
        Ensure no code uses Python 3.12+ backslash-in-f-string syntax.
        """
        violations = []
        for py_path in ROOT.rglob('*.py'):
            if '/.' in str(py_path):
                continue
            text = py_path.read_text(encoding='utf-8')
            try:
                tree = ast.parse(text, filename=str(py_path))
            except SyntaxError as e:
                violations.append(f"{py_path}: SyntaxError {e}")
                continue

            class FStringVisitor(ast.NodeVisitor):
                def visit_JoinedStr(self, node):
                    for val in node.values:
                        if isinstance(val, ast.FormattedValue):
                            expr_segment = ast.get_source_segment(text, val.value)
                            if expr_segment and '\\' in expr_segment:
                                violations.append(f"{py_path}:{val.lineno}: Backslash in f-string expression '{expr_segment}'")
                    self.generic_visit(node)

            FStringVisitor().visit(tree)
        self.assertEqual(violations, [], "Found f-string expressions containing backslashes:\n" + "\n".join(violations))

    def test_no_deprecated_runner_imports(self):
        """Ensure no active code imports deleted legacy runner/driver modules."""
        violations = []
        forbidden = ('novel_core.runner', 'novel_core.driver_bootstrap')
        for py_path in ROOT.rglob('*.py'):
            if '/.' in str(py_path) or py_path.name == 'test_quality_guard.py':
                continue
            text = py_path.read_text(encoding='utf-8')
            for mod in forbidden:
                if mod in text:
                    violations.append(f"{py_path}: contains reference to deprecated '{mod}'")
        self.assertEqual(violations, [], "Found deprecated module references:\n" + "\n".join(violations))

    def test_markdown_relative_links(self):
        """Ensure all markdown documentation links resolve to existing files."""
        md_files = list(ROOT.glob('*.md')) + list(REFERENCES_DIR.glob('*.md'))
        link_pattern = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')
        broken = []
        for md_file in md_files:
            content = md_file.read_text(encoding='utf-8')
            for match in link_pattern.finditer(content):
                text, target = match.groups()
                if any(target.startswith(prefix) for prefix in ('http://', 'https://', 'file://', '#', 'mailto:')):
                    continue
                clean_target = target.split('#')[0]
                if not clean_target:
                    continue
                resolved = (md_file.parent / clean_target).resolve()
                if not resolved.exists():
                    broken.append(f"{md_file.name}: [{text}]({target}) -> missing {resolved}")
        self.assertEqual(broken, [], "Broken markdown relative links found:\n" + "\n".join(broken))

    def test_assets_examples_validity(self):
        """Validate all json assets against their respective core schema validators."""
        from novel_core.store import initialize
        from novel_core.design import validate_spine

        # 1. contract.example.json
        contract_path = ASSETS_DIR / 'contract.example.json'
        self.assertTrue(contract_path.exists())
        contract = json.loads(contract_path.read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as temp:
            init_res = initialize(temp, contract)
            self.assertEqual(init_res.get('status'), 'ready')

        # 2. spatial.example.json
        spatial_path = ASSETS_DIR / 'spatial.example.json'
        self.assertTrue(spatial_path.exists())
        spatial_contract = json.loads(spatial_path.read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as temp:
            init_res = initialize(temp, spatial_contract)
            self.assertEqual(init_res.get('status'), 'ready')

        # 3. characters.example.json
        chars_path = ASSETS_DIR / 'characters.example.json'
        self.assertTrue(chars_path.exists())
        chars = json.loads(chars_path.read_text(encoding='utf-8'))
        profiles = chars if isinstance(chars, list) else chars.get('characters', chars.get('profiles', [chars]))
        self.assertTrue(bool(profiles))
        for p in profiles:
            self.assertIn('entity', p)
            self.assertIn('biography', p)
            self.assertIn('voice', p)

        # 4. design.example.json
        design_path = ASSETS_DIR / 'design.example.json'
        self.assertTrue(design_path.exists())
        design_data = json.loads(design_path.read_text(encoding='utf-8'))
        self.assertIn('world_plan', design_data)
        spine_ids = validate_spine(design_data['world_plan']['event_spine'])
        self.assertTrue(len(spine_ids) > 0)


if __name__ == '__main__':
    unittest.main()
