# -*- coding: utf-8 -*-
"""Listwise multi-judge consensus ranking tool.

Pipeline (from the listwise-rank-eval skill, Co-ReAct methodology):
1. Normalize + dedupe candidates, anonymize to A/B/C... (seeded shuffle).
2. Multiple cross-family LLM judges independently produce full rankings.
3. Borda count aggregation -> consensus ranking.
4. Spearman rho per judge vs consensus -> outlier warnings.

Judges resolution order:
  1. inline ``judges`` tool argument (JSON array string)
  2. plugin config field ``judges_json``
  3. ``JUDGE_MODELS`` env (comma separated, global OPENAI_BASE_URL/OPENAI_API_KEY)
  4. built-in defaults (qwen3-14b/deepseek-v3/gpt-4o on global gateway)

Each judge entry: name, model, base_url?, api_key_env?, temperature?,
extra_body?  (extra_body merges arbitrary fields into the request body,
e.g. vLLM's chat_template_kwargs.enable_thinking=false).
"""

import asyncio
import json
import logging
import os
import random
import re
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from agentscope.message import TextBlock
from agentscope.message import ToolResultState
from agentscope.tool import ToolChunk

logger = logging.getLogger(__name__)

_DEFAULT_JUDGES = [
    {"name": "qwen", "model": "qwen3-14b"},
    {"name": "deepseek", "model": "deepseek-v3"},
    {"name": "gpt", "model": "gpt-4o"},
]

_PROMPT = (
    "你是评估专家。下面给出一组匿名候选（标识符 A、B、C……）。"
    "请对它们按'质量'从高到低做完整排序，"
    "只输出排序，格式：A>B>C>D（不要解释）。\n"
    "若未提供具体任务背景，请按候选在通用工程实践中的质量与有效性排序，"
    "必须给出完整排序。\n\n候选：\n{cands}"
)

_MAX_CANDIDATES = 26  # A..Z labels


def _load_plugin_config(tool_name: str) -> Dict[str, Any]:
    """Read per-tool config from the QwenPaw runtime (gracefully degrade)."""
    try:
        from qwenpaw.plugins import get_tool_config

        cfg = get_tool_config(tool_name)
        return cfg if isinstance(cfg, dict) else {}
    except Exception:  # standalone / tests / runtime not ready
        return {}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lstrip("-*·").strip())


def _parse_judges(raw: str) -> Tuple[List[Dict[str, Any]], str]:
    """Parse a judges JSON string -> (judges, source_label)."""
    data = json.loads(raw)
    judges = data.get("judges", data) if isinstance(data, dict) else data
    if not isinstance(judges, list) or not judges:
        raise ValueError("judges must be a non-empty JSON array")
    return judges, "inline-arg"


def _resolve_judges(
    judges_param: str,
    tool_cfg: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], str]:
    """Resolve judge list: inline arg > config > JUDGE_MODELS > defaults."""
    if judges_param and judges_param.strip():
        return _parse_judges(judges_param)

    cfg_json = str(tool_cfg.get("judges_json", "") or "").strip()
    if cfg_json:
        return _parse_judges(cfg_json)

    env_models = os.environ.get("JUDGE_MODELS", "").strip()
    if env_models:
        base = os.environ.get("OPENAI_BASE_URL", "")
        key_env = "OPENAI_API_KEY"
        judges = [
            {"name": f"m{i}", "model": m.strip(), "base_url": base,
             "api_key_env": key_env}
            for i, m in enumerate(env_models.split(",")) if m.strip()
        ]
        return judges, "env:JUDGE_MODELS"

    return _DEFAULT_JUDGES, "builtin-defaults"


def _resolve_endpoint(judge: Dict[str, Any]) -> Tuple[str, str]:
    base = str(judge.get("base_url") or os.environ.get(
        "OPENAI_BASE_URL", "")).strip().rstrip("/")
    key = os.environ.get(
        str(judge.get("api_key_env") or "OPENAI_API_KEY"), "").strip()
    return base, key


def _call_judge(
    judge: Dict[str, Any],
    prompt: str,
    temperature: float,
    timeout: float,
    max_tokens: int,
) -> str:
    """One judge call -> raw ranking text. Raises on failure/empty."""
    base, key = _resolve_endpoint(judge)
    if not base or not key:
        raise RuntimeError(
            f"missing endpoint/key (base_url={'set' if base else 'unset'}, "
            f"key_env={judge.get('api_key_env', 'OPENAI_API_KEY')})")
    body: Dict[str, Any] = {
        "model": judge["model"],
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    extra = judge.get("extra_body")
    if isinstance(extra, dict):
        body.update(extra)
    req = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + key},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    ch = (data.get("choices") or [{}])[0]
    msg = ch.get("message") or {}
    content = (msg.get("content") or "").strip()
    if not content:  # reasoning models may park the answer elsewhere
        content = (msg.get("reasoning_content") or "").strip()
    if not content:
        raise RuntimeError(f"empty content (finish={ch.get('finish_reason')})")
    return content


def _parse_ranking(raw: str) -> List[str]:
    """Extract A>B>C style / '1. A 2. B' / JSON list from judge output."""
    if isinstance(raw, list):
        return [str(x).strip().upper() for x in raw if str(x).strip()]
    raw = raw.strip()
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(x).strip().upper() for x in data]
        if isinstance(data, dict) and "ranking" in data:
            return [str(x).strip().upper() for x in data["ranking"]]
    except Exception:
        pass
    items = re.findall(r"\b([A-Z])\b", raw)
    if not items:
        numbered = re.findall(r"^\s*(\d+)[.):]\s*([A-Z])", raw, re.M)
        items = [x[1] for x in sorted(numbered, key=lambda t: int(t[0]))]
    seen: List[str] = []
    for it in items:
        if it not in seen:
            seen.append(it)
    return seen


def _spearman(ranking: List[str], consensus: List[str],
              all_ids: List[str]) -> float:
    r1 = {x: i for i, x in enumerate(ranking)}
    r2 = {x: i for i, x in enumerate(consensus)}
    n = len(all_ids)
    if n < 2:
        return 0.0
    avg = (n - 1) / 2.0
    d2 = sum((r1.get(x, avg) - r2.get(x, avg)) ** 2 for x in all_ids)
    return 1 - 6 * d2 / (n * (n * n - 1))


async def rank_candidates_listwise(
    candidates: List[str],
    task: str = "",
    judges: str = "",
    seed: int = 42,
) -> ToolChunk:
    """Rank candidates via multi-judge consensus and return a report.

    Cross-family LLM judges each independently rank the anonymized
    candidates; results are Borda-aggregated into a consensus with a
    Spearman consistency report, avoiding single-model bias.

    Args:
        candidates: Candidate list, each a plain string (2-26 items).
        task: Optional task background so judges rank "for this task"
            (reduces refusals and improves relevance).
        judges: Optional judges override, JSON array string. Each entry:
            name, model, base_url?, api_key_env?, temperature?, extra_body?.
            Leave empty to use plugin config / JUDGE_MODELS env / defaults.
        seed: Anonymization shuffle seed for reproducibility (default 42;
            0 = random order each run).

    Returns:
        ToolChunk: markdown consensus report (Borda table + judge
        consistency table + skipped-judge warnings).
    """
    try:
        return await _run(candidates, task, judges, seed)
    except Exception as e:
        logger.error("listwise rank failed: %s", e, exc_info=True)
        return ToolChunk(
            state=ToolResultState.ERROR,
            content=[TextBlock(type="text",
                               text=f"Error: listwise rank failed - {e}")],
        )


async def _run(
    candidates: List[str],
    task: str,
    judges_param: str,
    seed: int,
) -> ToolChunk:
    tool_cfg = _load_plugin_config("rank_candidates_listwise")

    # --- normalize + dedupe ---
    seen_set = set()
    uniq: List[str] = []
    for c in candidates or []:
        n = _norm(c)
        k = n.lower()
        if n and k not in seen_set:
            seen_set.add(k)
            uniq.append(n)
    if len(uniq) < 2:
        return ToolChunk(
            state=ToolResultState.ERROR,
            content=[TextBlock(type="text",
                               text="Error: need >= 2 distinct candidates")],
        )
    if len(uniq) > _MAX_CANDIDATES:
        return ToolChunk(
            state=ToolResultState.ERROR,
            content=[TextBlock(type="text", text=(
                f"Error: too many candidates ({len(uniq)}); "
                f"max {_MAX_CANDIDATES} (labels A..Z)"))],
        )

    # --- anonymize ---
    idx = list(range(len(uniq)))
    if seed:
        random.Random(seed).shuffle(idx)
    else:
        random.shuffle(idx)
    labels = [chr(ord("A") + i) for i in range(len(uniq))]
    label_of = {labels[i]: uniq[pos] for i, pos in enumerate(idx)}
    cand_text = "\n".join(f"{lab}: {label_of[lab]}" for lab in labels)

    prompt = _PROMPT.format(cands=cand_text)
    if task and task.strip():
        prompt = f"任务背景：{task.strip()}\n\n" + prompt

    # --- resolve judges ---
    try:
        judges, src = _resolve_judges(judges_param, tool_cfg)
    except Exception as e:
        return ToolChunk(
            state=ToolResultState.ERROR,
            content=[TextBlock(type="text",
                               text=f"Error: bad judges config - {e}")],
        )

    temperature = float(tool_cfg.get("temperature", 0.2) or 0.2)
    timeout = float(tool_cfg.get("timeout", 180) or 180)
    max_tokens = int(tool_cfg.get("max_tokens", 4096) or 4096)

    # --- call judges concurrently ---
    async def one(judge: Dict[str, Any], k: int) -> Dict[str, Any]:
        name = str(judge.get("name") or f"m{k}")
        try:
            raw = await asyncio.to_thread(
                _call_judge, judge, prompt, temperature, timeout, max_tokens)
            ranking = _parse_ranking(raw)
            missing = [x for x in labels if x not in ranking]
            if len(ranking) < 2:
                raise RuntimeError(f"unparseable ranking: {raw[:80]!r}")
            return {"name": name, "model": judge.get("model", ""),
                    "ranking": ranking, "missing": missing, "error": ""}
        except Exception as e:
            return {"name": name, "model": judge.get("model", ""),
                    "ranking": [], "missing": labels, "error": str(e)}

    results = list(await asyncio.gather(
        *[one(j, k) for k, j in enumerate(judges)]))

    valid = [r for r in results if r["ranking"]]
    if not valid:
        detail = "\n".join(
            f"- {r['name']}: {r['error'] or 'unknown'}" for r in results)
        return ToolChunk(
            state=ToolResultState.ERROR,
            content=[TextBlock(type="text", text=(
                "Error: all judges failed; check base_url / api_key_env.\n"
                + detail))],
        )

    # --- Borda aggregate ---
    n = len(labels)
    scores = {lab: 0 for lab in labels}
    for r in valid:
        for pos, lab in enumerate(r["ranking"]):
            if lab in scores:
                scores[lab] += n - pos
    consensus = sorted(labels, key=lambda x: -scores[x])

    # --- report ---
    lines = ["# 多 Judge 共识排序报告", ""]
    lines.append(f"候选数：{n} ｜ 有效 judge：{len(valid)}/{len(results)}"
                 f"（配置来源：{src}）")
    if task and task.strip():
        lines.append(f"任务背景：{task.strip()}")
    lines += ["", "## 共识排序（Borda 聚合）", "",
              "| 名次 | 匿名ID | 得分 | 候选内容 |", "|---|---|---|---|"]
    for pos, lab in enumerate(consensus):
        lines.append(f"| {pos + 1} | {lab} | {scores[lab]} | "
                     f"{label_of[lab]} |")

    lines += ["", "## Judge 一致性（Spearman ρ vs 共识）", "",
              "| Judge | 模型 | ρ | 原始排序 |", "|---|---|---|---|"]
    for r in sorted(results, key=lambda x: -(len(x["ranking"]) > 0 and
                     _spearman(x["ranking"], consensus, labels) or -1)):
        if r["ranking"]:
            rho = _spearman(r["ranking"], consensus, labels)
            order = " > ".join(r["ranking"])
            lines.append(f"| {r['name']} | {r['model']} | {rho:.3f} | "
                         f"{order} |")
        else:
            lines.append(f"| {r['name']} | {r['model']} | - | "
                         f"失败：{r['error'][:60]} |")

    skipped = [r for r in results if not r["ranking"]]
    if skipped:
        lines += ["", "### 被跳过的 judge（警告）"]
        for r in skipped:
            lines.append(f"- {r['name']}：{r['error']}")
    if len(valid) < 3:
        lines += ["", "> ⚠️ 有效 judge 不足 3 个，共识质量有限，"
                     "建议修复失败端点后重跑。"]

    return ToolChunk(
        state=ToolResultState.SUCCESS,
        content=[TextBlock(type="text", text="\n".join(lines))],
    )
