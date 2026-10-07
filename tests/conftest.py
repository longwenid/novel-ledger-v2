"""Child processes must read/write UTF-8 regardless of the host codepage."""
import os

os.environ.setdefault("PYTHONUTF8", "1")
