# qwenpaw-consensus-rank

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/CallMeHFK/qwenpaw-consensus-rank/actions/workflows/ci.yml/badge.svg)](https://github.com/CallMeHFK/qwenpaw-consensus-rank/actions/workflows/ci.yml)
[![QwenPaw](https://img.shields.io/badge/QwenPaw-%3E%3D1.1.6-green)](https://github.com/agentscope-ai/QwenPaw)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)

QwenPaw tool plugin: **multi-judge consensus ranking** — cross-family LLM judges
independently rank anonymized candidates; the complete ballots are averaged by
rank into a consensus with a Spearman consistency report, avoiding single-model
bias.

Methodology from the Co-ReAct paper (arXiv:2605.23590) "listwise rank" block:
a blind user-head proposes ideas, then three judges on separate architectures
each return **a full ranking of the slate rather than a scalar score**; the
rankings are vote-aggregated (Borda) into an expert consensus that the agent's
reward then tracks by Spearman rank correlation.

Two deliberate departures from the paper, both because its own procedure has
the gaps below:

| 论文做法 | 问题 | 本插件 |
|---|---|---|
| 一次共享置换 + 中性标识符"to remove positional bias" | 只消除输入顺序锚定；标签位置偏好仍在 N 张票之间完全共模 | 每个 judge 一套独立映射（+ `passes` 让同一 judge 多套映射自平均） |
| Borda 直接求和 | 论文未定义残缺票/并列怎么处理；实测一张 2/10 的残缺票能独断冠军 | 只统计完整票，残缺票单列 |
| 共识即 ground truth | 未度量单个 judge 有多"位置驱动" | `位置稳定性`：judge 内两遍映射的 ρ |

## How it works

```
candidates ──► normalize/dedupe ──► anonymize (its OWN seeded A/B/C map per judge)
                                        │
              ┌─────────────────────────┘
              ▼
   N cross-family judges ──► each returns a full ranking (A>B>C...)
              │                 (concurrent, failures skipped w/ warnings)
              ▼
      rank averaging ──► consensus + per-judge Spearman ρ + judge↔judge ρ
     (complete ballots only)
```

Why multiple judges? A single model has systematic tastes (prefers longer
answers, certain phrasings). Cross-family voting + anonymization cancels that
bias — but only the *independent* part of it, so this plugin is strict about
what counts as independent:

- **Each judge gets its own candidate→label mapping.** With one shared map, the
  taste for early/late letters is common-mode and survives aggregation: N
  judges would vote as one. The report prints the seed; `seed=0` draws a fresh
  one per run.
- **Same endpoint + same model = one vote.** Clones of a gateway are N copies
  of one opinion; extras are merged and named in the warnings.
- **Only complete ballots are averaged.** A judge that ranked 3 of 10
  candidates implicitly claimed "best of ten" over candidates it never faced;
  its ballot keeps its own ρ row but does not decide the consensus.
- **Candidate text is inert data.** Instructions inside a candidate are not
  followed, and the parser prefers the chain covering every label, so a
  candidate quoting `A > B > C` cannot outrank the judge's real answer.

## Tool signature

`rank_candidates_listwise(candidates, task="", judges="", seed=42)`

| Arg | Notes |
|---|---|
| `candidates` | 2–26 plain strings; clipped to 600 chars per candidate for the judges |
| `task` | the ranking criterion — with it judges rank by task fit **only**, without them by generic engineering quality. Pass it whenever a task exists |
| `judges` | optional JSON array override of judge configs (see below) |
| `seed` | seed for the per-judge anonymization maps (default 42, 0 = fresh random map per run; the value used is printed in the report) |

Returns a markdown report: mean-rank consensus table, per-judge Spearman ρ vs
the consensus, judge↔judge ρ (agreement that does not depend on the consensus
the judges themselves created), integrity warnings (merged duplicate judges,
incomplete ballots), skipped-judge warnings, and a banner when fewer than 3
independent complete ballots were averaged (with 1 ballot it says plainly that
the result is one model's opinion, not a consensus).

## Initialization (first run)

After install + restart, the plugin probes provider keys **at registration
time** (env var names only — values are never logged):

- **Keys found** → log: `Listwise Rank: N provider key(s) detected (...)`,
  built-in default judges are ready.
- **No keys** → log warns, and the **first tool call returns a setup wizard**
  instead of a bare error: quick-start paths, a provider cheat-sheet
  (OpenAI / DeepSeek / Kimi / 智谱 GLM / 阿里百炼 Qwen / OpenRouter /
  SiliconFlow / 本地 vLLM·Ollama), and a `judges_json` template for
  multi-vendor mixing.

**Any OpenAI-compatible provider works** — official APIs or self-hosted
gateways (vLLM / Ollama / one-api / new-api). Keys live in
`~/.qwenpaw.secret/envs.json` or process env; restart QwenPaw after editing
envs.json.

## Configuration

### Judges resolution order

1. Tool argument `judges` (JSON array string) — per-call lineups
2. Plugin config field `judges_json` (Settings UI) — fixed lineup
3. `JUDGE_MODELS` env (comma-separated model names on the global gateway)
4. Built-in defaults — a **cross-family, multi-endpoint lineup** (see below);
   judges whose endpoint is unset or unreachable are **skipped with a
   warning** instead of failing the whole run

### Built-in default lineup (v1.1+)

| Judge | Model | Endpoint env | Family |
|---|---|---|---|
| `agnes` | `agnes-2.5-flash` | `OPENAI_BASE_URL` + `OPENAI_API_KEY` | Agnes |
| `qwen35` | `qwen3.5-122b-a10b-fp8` (thinking off) | `NEW_API_URL` + `NEW_API_KEY` | Qwen (vLLM) |
| `glm` | `glm-5.2` | `SENSENOVA_BASE_URL` + `SENSENOVA_API_KEY` | GLM (Zhipu) |

Zero-config when these env vars exist; any judge with a missing/dead endpoint
is skipped and reported, so a single expired token no longer breaks consensus.

### Judge entry fields

```json
{
  "name": "agnes-flash",
  "model": "agnes-2.5-flash",
  "base_url": "https://your-gateway/v1",     // optional; or base_url_env (env var name)
  "base_url_env": "OPENAI_BASE_URL",          // optional; falls back to $OPENAI_BASE_URL
  "api_key_env": "OPENAI_API_KEY",            // optional, default OPENAI_API_KEY
  "temperature": 0.2,                         // optional
  "extra_body": {                              // optional, merged into request body
    "chat_template_kwargs": {"enable_thinking": false}
  }
}
```

Endpoint resolution: `base_url` > `base_url_env` > `$OPENAI_BASE_URL`.
A URL without any path gets `/v1` appended automatically
(`https://token.sensenova.cn` → `https://token.sensenova.cn/v1`).

`extra_body` adapts gateway-specific params — e.g. vLLM serving heavy-thinking
models (qwen3.5-122b etc.) burns the whole `max_tokens` budget on reasoning
unless you disable thinking as shown above.

It is also how you constrain the output shape on gateways that support it,
which is the most reliable way to kill ranking-parse errors:

```json
"extra_body": {"response_format": {"type": "json_object"}}      // OpenAI-style
"extra_body": {"guided_json": {...}}                             // vLLM-style
```

The parser already accepts `{"ranking": "A>B>C"}`, a JSON array, a plain
`A>B>C` chain and numbered lists, and it prefers whichever chain covers every
label — but a schema-constrained judge cannot drift at all.

**Duplicate judges are merged.** Two entries resolving to the same
`base_url` + `model` are one opinion, so they get one vote; the dropped name is
named in the report warnings. Configure genuinely different families.

Judge failures are reported with actionable hints: expired token (401),
no payment method / out of credits, quota exceeded (429), or model without
an active channel under the token group (503).

### Environment variables

| Variable | Required | Notes |
|---|---|---|
| `<JUDGE>_API_KEY` | per judge | the variable named by each judge's `api_key_env` |
| `<JUDGE>_BASE_URL` / `_URL` | optional | per-judge gateway, referenced by `base_url_env` (built-in lineup uses `NEW_API_URL` / `SENSENOVA_BASE_URL` / `OPENAI_BASE_URL`) |
| `OPENAI_BASE_URL` | optional | fallback gateway when a judge has no `base_url`/`base_url_env` |
| `JUDGE_MODELS` | optional | quick lineup without JSON: `m1,m2,m3` |

Keys can live in QwenPaw's secret store (`~/.qwenpaw.secret/envs.json`) or in
regular environment variables.

### Settings UI fields (per-tool config)

| Field | Default | Notes |
|---|---|---|
| `judges_json` | empty | judge array JSON; leave empty for the fallback layers |
| `temperature` | 0.2 | judge sampling temperature |
| `timeout` | 180 | per-judge HTTP timeout (seconds) |
| `max_tokens` | 4096 | per-judge completion budget |
| `passes` | 1 | 1–3; with ≥2 each judge ranks the same slate under that many independent anonymizations |

### 位置稳定性（`passes` >= 2）

One ballot per judge tells you *what* it ranked first, not how much that order
depended on which candidate happened to sit under label `A`. With `passes=2`
every judge ranks the slate twice under two different mappings:

- the judge's **ballot is the average of its own passes**, so its label-position
  taste cancels inside its own vote instead of relying on other judges to
  average it out — and the judge still casts exactly one vote (a failed pass
  does not halve its weight);
- the report gains a **位置稳定性** column = Spearman ρ between that judge's own
  passes; below `0.9` the judge is named as possibly position-driven;
- cost is `judges × passes` calls, and a judge's passes run in series (one
  gateway never self-rate-limits). Outputs are ~30 tokens each, so the extra
  spend is essentially the prompt repeated once.

Real tail of a `passes=2` run where the third judge flipped completely:

```markdown
| Judge | 模型 | ρ | 位置稳定性 | 原始排序 |
|---|---|---|---|---|
| agnes | a | 1.000 | 1.000 | #3 > #2 > #1 |
| qwen35 | b | 1.000 | 1.000 | #3 > #2 > #1 |
| glm | c | 0.000 | -1.000 | #3 > #2 > #1 |

Judge 间一致度（两两 ρ 均值，不受共识循环影响）：0.333

### 配置与完整性警告
- **glm**：2/2 遍映射给出的排序不一致（位置稳定性 ρ=-1.000 < 0.9），它的名次可能由标签位置驱动，建议换 judge 或加 passes
```

Reference point for reading that column: an architectural fix for listwise
position sensitivity reports τ=0.9883 / ρ=0.9984 across permutations on a
fine-tuned reranker ([arXiv:2604.27599](https://arxiv.org/abs/2604.27599)).
A general chat model used as a judge will be worse than that — the value here
is finding out *which* of your judges is order-driven, which a single-pass
report cannot show at all.

## Install

1. Copy this repo into `~/.qwenpaw/plugins/listwise-rank/`:
   ```bash
   git clone https://github.com/CallMeHFK/qwenpaw-consensus-rank.git \
       ~/.qwenpaw/plugins/listwise-rank
   ```
2. Configure the judge API keys (env vars / secret store, see above).
3. Restart QwenPaw; look for `✓ Loaded plugin 'listwise-rank' successfully`
   in the log.
4. Enable the **rank_candidates_listwise** tool in your agent settings
   (plugin tools are disabled by default).

No extra pip dependencies — stdlib only (`urllib`).

## Development

Stdlib-only test suite — no `agentscope` / `qwenpaw` install required:

```bash
python -m unittest discover -s tests -v
```

CI runs the suite on Python 3.10–3.12 via GitHub Actions (`.github/workflows/ci.yml`).

## Privacy ⚠️

Candidate texts and the `task` background are sent to **every configured
judge endpoint**. For sensitive data: configure only intranet/local judges,
and always reference keys via `api_key_env` — never inline them in
`judges_json` or call arguments.

Additional hardening tips:

- **Always use `https://` for `base_url`.** The tool does not enforce TLS;
  an `http://` gateway would transmit candidate texts in plaintext.
- API keys are read from environment variables at call time and are only
  placed in the `Authorization` header — they never appear in URLs, logs,
  or error messages.
- The tool performs no telemetry and writes no files; the only network
  traffic is the judge calls themselves.
- Candidates are fenced and treated as inert data (instructions inside them
  are not followed), but that is a *correctness* guard, not a confidentiality
  one: text from an untrusted source still leaves the machine and lands in a
  third-party model's context.

## Example output

Real report produced by the current code (one judge returned a partial ballot):

```markdown
# 多 Judge 共识排序报告

候选数：3 ｜ 有效 judge：3/3（完整票 2/3，配置来源：inline-arg）
匿名映射种子：42 ｜ 每个 judge 一份独立映射
任务背景：10 人团队、日活 5 万的电商系统架构选型

## 共识排序（平均名次）

| 名次 | 候选 | 平均名次 | 候选内容 |
|---|---|---|---|
| 1 | #3 | 1.00 | 模块化单体：单进程 + 清晰模块边界 |
| 2（并列） | #1 | 2.50 | 微服务拆分：按业务域拆成 8 个服务 |
| 3（并列） | #2 | 2.50 | 服务化中间态：3 个粗粒度服务 |

## Judge 一致性（Spearman ρ vs 共识）

| Judge | 模型 | ρ | 原始排序 |
|---|---|---|---|
| qwen35 | qwen3.5-122b | 1.000 | #3 > #1 > #2 |
| agnes | agnes-2.5-flash | 0.500 | #3 > #2 > #1 |
| glm | glm-5.2 | 0.500 | #3 > #2 |

Judge 间一致度（两两 ρ 均值，不受共识循环影响）：0.500

### 配置与完整性警告
- **glm**：排名不完整，缺少 #1 的位次（未计入共识，仅单列其 ρ 供参考）

> ⚠️ 有效 judge 不足 3 个，共识质量有限，建议修复失败端点后重跑。
```

How to read it:

- `#N` refers to the N-th item of your input `candidates` list — each judge saw
  its own letters, so `#N` is the only stable identity across ballots.
- **平均名次** is the mean rank over complete ballots only (1.00 = everyone
  agreed it is first). Equal values are marked 并列 on every tied row.
- Per-judge **ρ vs 共识** is circular by construction (the judge helped create
  the consensus). Use **Judge 间一致度** to ask "did the models agree at all";
  low mean-rank spread with high inter-judge ρ is the signal you want. High
  repeatability is not correctness — it says the judges agree, not that the
  winner is right.
- **位置稳定性** (needs `passes` >= 2) is per-judge and consensus-independent:
  it asks whether that judge's order survives a reshuffle of the anonymization.
  A judge with ρ=1.000 against the consensus but 0.500 against itself is
  telling you its ballot is partly label position.

## Changelog

### v1.4.0 (2026-09-20)

- **`passes` (1–3, default 1): per-judge permutation ensembling.** With
  `passes>=2` each judge ranks the same slate under several independent
  anonymizations; its ballots are averaged into that judge's single vote, so
  its own label-position taste cancels inside its ballot instead of depending
  on the other judges to average it out. A failed pass does not dilute the
  judge's weight.
- **`位置稳定性` column** — Spearman ρ between a judge's own passes, with the
  judge named in the warnings below 0.9. This is the measurement the source
  paper does not make: it asserts that one shared permutation "removes
  positional bias" and that Borda is "robust to a single judge being an
  outlier", neither of which is checkable from a single-pass report.
- **Methodology section corrected**: the paper's listwise-rank block has judges
  return a full ranking and *explicitly not* a scalar score — the README's old
  "1–100 score + verdict" description was wrong (that belongs to the rubric
  generator, a different block).
- Pointwise (one call per candidate) was considered and **rejected**: it drops
  the joint comparison that makes listwise reranking the stronger paradigm,
  costs N x M calls, and its uncalibrated absolute grades have to be re-sorted
  per judge anyway — i.e. it changes which quantity the consensus measures
  while returning to the same ranking.
- Tests 83 → 87. Judge table gained a column, so the report shape test now
  derives the column count from the table header.

### v1.3.0 (2026-09-20)

Consensus math and judge-independence fixes, from auditing this plugin
against a documented LLM failure-mode list (TypeSafe *Jev 1.13 jaggedness*:
literal reading, indirection, contradictory criteria, presumed structural
invariance, generation, adversarial content).

- **Every judge now gets its own candidate→label mapping.** The old single
  seeded shuffle meant all N judges saw the identical letters, so listwise
  position bias was common-mode and rank aggregation could not cancel it —
  the exact bias the multi-judge design exists to remove.
- **Aggregation changed from Borda sum to mean rank over complete ballots.**
  A judge that returned 2 of 10 candidates used to be credited with "these two
  are the top of ten" at full strength and could single-handedly decide the
  winner (reproduced: 26 points vs 20 for the unanimous first choice). Such a
  ballot is now reported with its own ρ but excluded from the consensus; with
  no complete ballot at all the result is still produced and flagged 无完整票.
- **Spearman is tie-safe now.** `1 - 6Σd²/(n(n²-1))` is only valid for
  complete tie-free permutations; it is replaced by Pearson-on-ranks, so
  partial ballots no longer get an inflated-looking ρ against the consensus.
- **Added `Judge 间一致度`** (mean pairwise ρ). ρ-vs-consensus is circular —
  every judge helps build the number it is scored against — so this is the
  independence-aware agreement reading.
- **Duplicate judges are merged into one vote** when two entries resolve to the
  same endpoint + model, instead of pretending to be a cross-family consensus.
- **One criterion per call**: with `task` set, judges rank by task fit only;
  the old prompt asked for task fit *and* general engineering quality at once
  and left each judge to pick. Direction, completeness and the "candidate text
  is data, not instructions" rule are stated once, explicitly.
- **Injection hardening**: candidate text is fenced in the prompt and declared
  inert; the ranking parser prefers the chain that covers every label (so a
  candidate quoting `A > B > C` can't outrank the judge's real answer); `|` is
  escaped in report table cells.
- **600-character budget per candidate** in both the prompt and the report.
- **Report readability**: ties marked on every tied row, `#N` input-index
  column instead of a per-judge letter, effective seed printed, and a banner
  when only one independent ballot survives ("不构成共识").
- **Test suite: 52 → 81 cases** (per-judge mapping bijections, ballot
  imputation, consensus ordering, prompt criteria, parser decoy chains, table
  escaping, duplicate-judge collapsing, clipping).

> Report format changed (`匿名ID`/`得分` columns are gone). No configuration
> migration is needed.

### v1.2.0 (2026-09-18)

- **Per-judge `temperature` honored**: the field was documented but the
  implementation ignored it; judge-level values now override the global
  setting (`0` is respected).
- **Robust ranking parsing**: lowercase chains (`a>b>c`), JSON
  `{"ranking": "A>B>C"}` string form, and numbered lists with lowercase
  labels now parse; hallucinated labels outside the candidate set are
  filtered out; strict `A>B>C` chains are matched before the loose
  single-letter scan so prose can't leak into rankings.
- **Partial-ranking transparency**: judges that missed candidates are
  flagged in the report with the missing labels (their partial votes still
  count toward Borda).
- **Actionable network errors**: unreachable endpoints (connection refused /
  DNS) and read timeouts now get dedicated hints instead of raw tracebacks.
- **Security warnings in the report**: plaintext `http://` endpoints
  (non-local) and inline `api_key` fields (ignored by design) are flagged;
  `base_url` without a scheme fails fast with a clear message.
- **Tie markers** in the consensus table when adjacent Borda scores are
  equal; judge consistency table sorted correctly for ρ = 0.0 (previously
  conflated with failed judges).
- **Test suite** (`tests/`, 50+ cases, stdlib only) plus GitHub Actions CI
  on Python 3.10–3.12; plugin config source is now labeled correctly
  (`plugin-config` vs `inline-arg`) in the report.

### v1.1.0 (2026-09-01)

- **New built-in default lineup**: cross-family judges across three endpoints
  (`OPENAI_BASE_URL` / `NEW_API_URL` / `SENSENOVA_BASE_URL`), all referenced
  by env-var names — no hardcoded URLs. A dead gateway is now skipped with a
  warning instead of failing the whole consensus run.
- **`base_url_env` judge field**: pin each judge's gateway via an env var.
- **Auto `/v1`**: base URLs without a path get `/v1` appended
  (fixes the SenseNova `404` gotcha).
- **Actionable judge error hints**: expired token (401), missing payment /
  credits, quota exceeded (429), model without an active channel (503) —
  each reports what to do instead of a bare `HTTP Error`.
- **First-run initialization**: registration-time provider probe (log hint)
  plus a setup wizard returned on first use when no provider is configured;
  cheat-sheet covers any OpenAI-compatible provider (official APIs or local
  vLLM/Ollama gateways).
- README: default-lineup table, troubleshooting hints, env-var docs.

### v1.0.0

- Initial release: anonymized listwise ranking, Borda aggregation, Spearman
  consistency report, seeded shuffle, per-judge skip warnings.

## License

MIT
