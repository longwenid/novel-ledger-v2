"""Child processes must read/write UTF-8 regardless of the host codepage.

PYTHONUTF8 only takes effect at interpreter start, so setting it here cannot
fix this process; test-side IO therefore passes encoding='utf-8' explicitly.
The variable still covers grandchildren (tests that shell out to novel.py).

`tempfile` can fall back to an in-repo scratch directory when the default temp
location is not writable for this process (a confined Windows sandbox grants
only the repository tree). The fallback is transparent: tests keep calling
`tempfile.TemporaryDirectory()`.
"""
import os
import tempfile
from pathlib import Path

os.environ.setdefault("PYTHONUTF8", "1")


def _usable(directory):
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / '.write-probe'
        probe.write_text('ok', encoding='utf-8')
        probe.unlink()
        return True
    except OSError:
        return False


if not _usable(Path(tempfile.gettempdir())):
    _SCRATCH = Path(__file__).resolve().parents[1] / '.pytest-tmp'
    if _usable(_SCRATCH):
        tempfile.tempdir = str(_SCRATCH)
        os.environ['TMPDIR'] = os.environ['TEMP'] = os.environ['TMP'] = str(_SCRATCH)
