"""Measure what a finished unattended run actually cost, per agent.

Usage: python validation/session_cost.py <root-of-extracted-session>

Takes a DSH session export (`session.v4.jsonl` plus `subagents/<id>/`), which is
what a real book run leaves behind, and reports token totals and tool-payload
sizes per agent. It is the ruler for the delivery-cost contract in
references/architecture.md: the measurements behind per-type envelope slicing
came from running it on a 16-worker export (11.07M tokens total, of which
9.996M was cache-read of re-sent context).
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict

TMP = os.environ.get("TMP", r"C:\Users\longwen\AppData\Local\Temp")


def load(path):
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def text_len(x):
    if x is None:
        return 0
    if isinstance(x, str):
        return len(x)
    if isinstance(x, list):
        return sum(text_len(i) for i in x)
    if isinstance(x, dict):
        return sum(text_len(v) for v in x.values())
    return 0


def usage_of(rec):
    u = rec.get("usage") or (rec.get("data") or {}).get("usage") or {}
    return u if isinstance(u, dict) else {}


def report(path, label):
    recs = load(path)
    tot = defaultdict(int)
    n_msg = 0
    for r in recs:
        u = usage_of(r)
        if u:
            n_msg += 1
            for k, v in u.items():
                if isinstance(v, (int, float)):
                    tot[k] += v
    print(f"\n=== {label} ===")
    print(f"  records={len(recs)} usage-bearing={n_msg} file={os.path.getsize(path)/1024:.0f}KB")
    print("  usage:", {k: int(v) for k, v in tot.items()})
    calls = Counter()
    arg_sizes = defaultdict(int)
    for r in recs:
        if r.get("type") == "tool/call":
            d = r.get("data") or {}
            name = d.get("name") or "?"
            calls[name] += 1
            arg_sizes[name] += text_len(d.get("arguments"))
    for name, n in calls.most_common():
        print(f"    tool {name}: n={n} argsBytes={arg_sizes[name]}")
    return tot


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else os.path.join(TMP, "nlv2-ref-session")
    grand = defaultdict(int)
    main_usage = report(os.path.join(root, "session.v4.jsonl"), "MAIN (controller)")
    for k, v in main_usage.items():
        grand[k] += v
    sub_root = os.path.join(root, "subagents")
    if os.path.isdir(sub_root):
        for cid in sorted(os.listdir(sub_root)):
            p = os.path.join(sub_root, cid, "session.v4.jsonl")
            if os.path.exists(p):
                u = report(p, f"sub {cid[:8]}")
                for k, v in u.items():
                    grand[k] += v
    print("\n=== GRAND TOTAL ===")
    print({k: int(v) for k, v in grand.items()})


if __name__ == "__main__":
    main()
