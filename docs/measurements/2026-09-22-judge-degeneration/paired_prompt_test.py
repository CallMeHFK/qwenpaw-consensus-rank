# -*- coding: utf-8 -*-
"""Paired A/B: does the precedence clause reduce glm ballot degeneration?

Re-run cost: ~20 calls to one judge endpoint, roughly 15-20 minutes.
See FINDINGS.md for the recorded result and why prompt wording was dropped as a fix.

Design
  - blocks   : 10 anonymization seeds; each seed is run under BOTH arms, so the
               comparison is paired (sign test on discordant pairs)
  - arms     : OLD = backend/tool_impl.py at 19134ce^ (criteria above the rules,
               no precedence clause); NEW = current working tree
  - order    : interleaved A,B per block to cancel gateway drift over time
  - params   : production ones (temperature 0.2, max_tokens 4096, timeout 90)
  - outcome  : primary  = ballot usable   (parsed >= 2 labels)
               secondary= ballot complete (parsed == all labels)
  - infra failures (502/TLS) are recorded separately, not counted in either arm
"""
import importlib.util
import json
import os
import subprocess
import sys
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "results.jsonl")
N_BLOCKS = 10
MODEL = {"name": "glm", "model": "glm-5.2",
         "base_url_env": "SENSENOVA_BASE_URL", "api_key_env": "SENSENOVA_API_KEY"}
CANDS = ["模块化单体：单进程，模块边界用接口约束，一次部署",
         "按业务域拆成 8 个微服务，各自独立数据库与部署流水线",
         "无服务器函数 + 托管队列，按调用量计费，不自管进程",
         "两个粗粒度服务（读、写）共享一个 Postgres 实例"]
TASK = ("10 人后端团队、日活 5 万电商系统、6 个月内上线、没有专职 SRE，"
        "选最合适的架构")

from qwenpaw.envs import load_envs_into_environ
load_envs_into_environ()


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


old_src = subprocess.run(["git", "-C", REPO, "show", "19134ce^:backend/tool_impl.py"],
                         capture_output=True, text=True, check=True).stdout
open("/tmp/old_tool_impl.py", "w").write(old_src)
OLD = load("/tmp/old_tool_impl.py", "old_ti")
NEW = load(REPO + "/backend/tool_impl.py", "new_ti")
assert "不能改变这个形态" in NEW._PROMPT_TMPL, "new template missing the clause"
assert "不能改变这个形态" not in OLD._PROMPT_TMPL, "old template unexpectedly has it"


def call(mod, prompt):
    return mod._call_judge(MODEL, prompt, 0.2, 90, 4096)


rows = []
with open(OUT, "w") as fh:
    for b in range(N_BLOCKS):
        seed = 1000 + b
        rec = {"block": b, "seed": seed}
        for arm, mod in (("old", OLD), ("new", NEW)):
            m = mod._judge_label_map(len(CANDS), seed, "glm")
            prompt = mod._judge_prompt(CANDS, m, TASK)
            try:
                raw = call(mod, prompt)
                parsed = mod._parse_ranking(raw, m.keys())
                rec[arm] = {"usable": len(parsed) >= 2,
                            "complete": len(parsed) == len(CANDS),
                            "n_parsed": len(parsed), "raw": raw[:40]}
            except Exception as e:
                msg = str(e)
                infra = ("502" in msg or "SSL" in msg or "unreachable" in msg
                         or "timed out" in msg)
                rec[arm] = {"usable": None, "complete": None, "n_parsed": 0,
                            "infra": infra, "raw": msg[:60]}
            fh.write(json.dumps({"block": b, "arm": arm, **rec[arm]},
                                ensure_ascii=False) + "\n")
            fh.flush()
        print(json.dumps(rec, ensure_ascii=False), flush=True)
print("DONE")
