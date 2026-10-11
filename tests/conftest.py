"""Child processes must read/write UTF-8 regardless of the host codepage.

PYTHONUTF8 only takes effect at interpreter start, so setting it here cannot
fix this process; test-side IO therefore passes encoding='utf-8' explicitly.
The variable still covers grandchildren (tests that shell out to novel.py).
"""
import os

os.environ.setdefault("PYTHONUTF8", "1")
