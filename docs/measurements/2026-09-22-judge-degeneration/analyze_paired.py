# -*- coding: utf-8 -*-
"""Analyze the paired A/B degeneration test in this directory (no network)."""
import json
import math
import os
from collections import defaultdict

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results.jsonl")

rows = [json.loads(l) for l in open(PATH, encoding="utf-8")]
by_block = defaultdict(dict)
for r in rows:
    by_block[r["block"]][r["arm"]] = r

ARMS = ("old", "new")
pairs = {k: [] for k in ("usable", "complete")}
infra = {k: 0 for k in ARMS}
for b, arms in sorted(by_block.items()):
    if not all(a in arms for a in ARMS):
        continue
    for arm in ARMS:
        if arms[arm].get("usable") is None:
            infra[arm] += 1
    for metric in pairs:
        o, n = arms["old"], arms["new"]
        if o.get(metric) is None or n.get(metric) is None:
            continue
        pairs[metric].append((o[metric], n[metric]))


def wilson(k, n, z=1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


print(f"完成配对块数: {len(by_block)}  基础设施失败: old={infra['old']} new={infra['new']}")
for metric, label in (("usable", "票可用（解析出 >=2 个标识符）"),
                      ("complete", "票完整（全部 4 个标识符）")):
    d = pairs[metric]
    n = len(d)
    if not n:
        continue
    ko = sum(1 for o, _ in d if o)
    kn = sum(1 for _, x in d if x)
    lo, hi = wilson(kn, n)
    b_ = sum(1 for o, x in d if o and not x)      # old 成 / new 败
    c_ = sum(1 for o, x in d if x and not o)      # new 成 / old 败
    m = b_ + c_
    pval = 1.0 if m == 0 else min(1.0, sum(
        math.comb(m, k) * 0.5 ** m for k in range(min(b_, c_), m + 1))
        * (1 if b_ == c_ else 2))
    print(f"\n[{label}] n={n}")
    print(f"  old 达成 {ko}/{n} = {ko / n:.0%}   new 达成 {kn}/{n} = {kn / n:.0%}"
          f"  (new 95%CI {lo:.0%}–{hi:.0%})")
    print(f"  不一致对: old成/new败={b_}  new成/old败={c_}"
          f"  符号检验精确 p={pval:.3f}")
    print("  判定:", "无法区分（样本太小）" if m == 0 or pval > 0.05
          else "有显著差异")
