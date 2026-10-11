#!/usr/bin/env python3
"""带进度条的 pytest 入口：python validation/run_tests.py [任意 pytest 参数]。

零第三方依赖：先收集用例数，再把 pytest 的点号流实时折算成单行进度条。
点号行（含 [NN%] 标记）被进度条取代；摘要、失败详情等真实输出原样透传。
"""
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOT = frozenset(b'.FEsxX')          # pytest 每个用例恰好一个状态字符
PERCENT_LINE = re.compile(r'^\s*\[\s*\d+%\]\s*$')
WIDTH = 26


def collect_total(extra):
    out = subprocess.run([sys.executable, '-m', 'pytest', '--collect-only', '-q', *extra],
                         cwd=ROOT, capture_output=True)
    text = out.stdout.decode('utf-8', 'replace')
    m = re.search(r'(\d+) tests? collected', text)
    if m:
        return int(m.group(1))
    return sum(1 for ln in text.splitlines() if ln.replace('\\', '/').startswith('tests/'))


def fmt(seconds):
    seconds = max(0, int(seconds))
    return f'{seconds // 60:02d}:{seconds % 60:02d}'


def main(argv):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    extra = [a for a in (argv or ['tests/']) if a != '-q']  # 进度条取代点号行
    total = collect_total(extra)
    print(f'已收集 {total} 个用例，开始执行…', flush=True)
    env = {**os.environ, 'PYTHONUNBUFFERED': '1'}   # 管道下也要逐用例刷出
    proc = subprocess.Popen([sys.executable, '-m', 'pytest', '-q', '--tb=short', *extra],
                            cwd=ROOT, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, env=env)
    done = failed = 0
    start = time.time()
    last_len = 0
    line = bytearray()
    plain = False
    in_dots = True   # 行首为点号区；进入空白/百分标记后本行不再计点
    assert proc.stdout is not None
    try:
        while True:
            ch = proc.stdout.read(1)
            if not ch:
                break
            if plain:
                sys.stdout.buffer.write(ch)
                sys.stdout.buffer.flush()
                continue
            if in_dots and ch[0] in DOT:
                done += 1
                if ch[0] in b'FE':
                    failed += 1
                elapsed = time.time() - start
                frac = done / total if total else 0.0
                filled = int(WIDTH * frac)
                note = f' | 失败 {failed}' if failed else ''
                left = elapsed / frac - elapsed if frac else 0.0
                bar = '#' * filled + '-' * (WIDTH - filled)
                text = (f'\r[{bar}] {done}/{total} {frac:5.0%}'
                        f' | 用时 {fmt(elapsed)} | 预计剩余 {fmt(left)}{note}')
                sys.stdout.write(text + ' ' * max(0, last_len - len(text)))
                sys.stdout.flush()
                last_len = len(text)
            elif ch == b'\n':
                decoded = line.decode('utf-8', 'replace')
                if PERCENT_LINE.match(decoded):
                    in_dots = True        # 完整的一行 [NN%] 标记，继续计点
                else:
                    plain = True          # 摘要/失败详情从这里开始，原样透传
                    sys.stdout.write('\r' + ' ' * last_len + '\r')
                    sys.stdout.write(decoded)
                    sys.stdout.flush()
                line.clear()
            else:
                if ch != b' ':
                    in_dots = False       # 正文区：本行剩余字符不再当点号
                line += ch
    except KeyboardInterrupt:
        proc.terminate()
        return proc.wait()
    if last_len:
        sys.stdout.write('\n')
        sys.stdout.flush()
    return proc.wait()


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
