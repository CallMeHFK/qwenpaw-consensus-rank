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

After install + restart, confirm the loaded version through the API (the
plugin's own registration log lines do not reliably reach `qwenpaw.log`, so
don't look for them):

```bash
curl -s http://127.0.0.1:19999/api/plugins | python3 -c "
import json,sys; d=json.load(sys.stdin)
p=[x for x in (d if isinstance(d,list) else d['plugins'])
   if x.get('id')=='listwise-rank'][0]
print(p['version'], 'loaded' if p['loaded'] else 'NOT LOADED')"
# expect: 1.4.3 loaded
```

- **Keys found** → judges run against their endpoints.
- **No keys anywhere** → the first tool call returns a setup wizard instead of
  a bare error: quick-start paths, a provider cheat-sheet (OpenAI / DeepSeek /
  Kimi / 智谱 GLM / 阿里百炼 Qwen / OpenRouter / SiliconFlow / 本地 vLLM·Ollama),
  and a `judges_json` template for multi-vendor mixing.

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

### Per-tool config fields

| Field | Default | Notes |
|---|---|---|
| `judges_json` | empty | judge array JSON; leave empty for the fallback layers |
| `temperature` | 0.2 | judge sampling temperature |
| `timeout` | 180 | per-judge HTTP timeout (seconds) |
| `max_tokens` | 4096 | per-judge completion budget |
| `passes` | 1 | 1–3; with ≥2 each judge ranks the same slate under that many independent anonymizations |

**How a field actually reaches the tool** (QwenPaw 2.2.1 — every route below was
run against a live install; `plugins/registry.py:1061`, `app/routers/tools.py:346,445`):

1. **Env var — no UI required, survives everything.** Add to
   `~/.qwenpaw.secret/envs.json` and restart:
   ```
   "LISTWISE_RANK_PASSES": "2"
   ```
   Supported: `LISTWISE_RANK_PASSES` (1–3). Judge lineup can also come from `JUDGE_MODELS`.
2. **Per-agent tool config.** Written by the web UI's tool-config form, or:
   ```bash
   curl -s -X POST http://127.0.0.1:19999/api/tools/rank_candidates_listwise/config \
     -H 'Content-Type: application/json' \
     -d '{"config": {"passes": 2}}'
   # read it back:
   curl -s http://127.0.0.1:19999/api/tools/rank_candidates_listwise/config
   ```
   The body **must** wrap the keys in `"config"` — a flat `{"passes": 2}` validates
   against `ToolConfigUpdate` as an empty config and silently clears the stored one.
   Values land in `~/.qwenpaw/workspaces/<agent>/agent.json` under
   `tools.builtin_tools.rank_candidates_listwise.config`, and this store is
   **per agent** — set it once per agent you use.
3. Precedence: tool config > env var > default.

If the settings form shows no `passes` field, check what the running app can see
— the form is generated from the manifest on disk:
```bash
curl -s http://127.0.0.1:19999/api/tools | python3 -c "
import json,sys; t=[x for x in json.load(sys.stdin)
                    if x['name']=='rank_candidates_listwise'][0]
print([f['name'] for f in (t.get('config_fields') or [])])"
# expect: ['judges_json', 'temperature', 'timeout', 'passes', 'max_tokens']
```
An old list means the install directory still holds an old `plugin.json`. Whether
that build renders plugin tool config in the UI at all varies; route 1 works either
way. To confirm the running **code** version, look at the report header a call
returns: v1.3+ prints `候选数：N ｜ 有效 judge：x/y（完整票 …）` and
`匿名映射种子：…`; older builds print a plain `共识排序（Borda 聚合）` table.

Note on `plugin.json`: `meta.tools[].config_fields` declares the form; a build that
renders it will show these fields after a restart (the manifest is read at registration).

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

### Verify the install

Ask the agent to rank three obviously different options with a task, e.g.
"用 rank_candidates_listwise 排序：候选[单体/三服务/八微服务]，任务=10 人团队 6 个月上线".
A working install returns `# 多 Judge 共识排序报告` with 候选数：3 and at least one
`有效 judge`. No provider key anywhere → it returns the setup wizard instead of an
error, which also means the plugin loaded.

Checks worth doing once:

```bash
# config route (per agent):
curl -s http://127.0.0.1:19999/api/tools/rank_candidates_listwise/config
```

| Symptom | Meaning |
|---|---|
| report header shows the old wording / `位置稳定性` column missing | the process still runs a previous version — restart |
| `all judges failed; check base_url / api_key_env` | keys not visible to the process (envs.json edited but not restarted) |
| `base_url 取到的是 QwenPaw 密文（ENC:...）` | the tool was invoked outside QwenPaw; run it inside the app |

### Upgrading from an earlier version

Restart is required (the plugin code and its manifest are read at registration).
One caveat found on real installs: entries already written into
`workspaces/<agent>/agent.json` are **never refreshed** — both write sites are
guarded by `if tool_name not in builtin_tools` (`plugins/api.py:287`,
`app/routers/plugins.py:293`) — so the stored `description` keeps the text from
whenever the tool first appeared (e.g. "Borda-aggregated" from v1.1.x). The tool
function itself comes from the plugin, so behaviour is current; only the
description shown in the tools list is stale. Delete that entry and restart to
have it re-created from the current manifest.

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

冠军判定：#3 领先第 2 名 1.50 个平均名次；去掉任一 judge 复算 2/2 次冠军不变 → 冠军稳定

## Judge 一致性（Spearman ρ vs 共识）

| Judge | 模型 | ρ | 位置稳定性（未测） | 原始排序 |
|---|---|---|---|---|
| qwen35 | qwen3.5-122b | 1.000 | - | #3 > #1 > #2 |
| agnes | agnes-2.5-flash | 0.500 | - | #3 > #2 > #1 |
| glm | glm-5.2 | 0.500 | - | #3 > #2 |

Judge 间一致度（两两 ρ 均值，不受共识循环影响）：0.500

> 位置稳定性未测：设 `LISTWISE_RANK_PASSES=2`（写进 ~/.qwenpaw.secret/envs.json 后重启，或本工具的 passes 配置项），让每个 judge 在两套匿名映射下各排一次，可得到 judge 内的位置稳定性 ρ，并把该 judge 的位置偏好从它的票里平均掉。

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
- **冠军判定** is a leave-one-out check on the ballots already in hand (no extra
  calls, no tuned threshold): it recomputes the consensus without each judge in
  turn. `冠军稳定` means no single ballot can change who is first; `对单张票敏感`
  names the load-bearing judge, and the honest reading is "these two are
  effectively tied". An exact tie for first is declared as no unique champion.
  This is why pairwise top-2 rematches were not added: 3 majority votes flip a
  truly-better option ~35% of the time at 60% pairwise accuracy, and the
  sensitivity they detect is already measured upstream by `位置稳定性` and here
  by the leave-one-out check.
- **位置稳定性** (needs `passes` >= 2) is per-judge and consensus-independent:
  it asks whether that judge's order survives a reshuffle of the anonymization.
  A judge with ρ=1.000 against the consensus but 0.500 against itself is
  telling you its ballot is partly label position.

## Changelog

### v1.4.4 (2026-09-22)

- **Task wording can no longer override the ranking shape** — added as a
  precedence clause naming the identifier count, placed above the criterion it
  could contradict, after `glm-5.2` was seen answering a bare `D` once the
  `task` text read as an imperative (`…选最合适的架构`).
- **Measured, and it did not prove out.** Paired A/B on that model, 10 blocks,
  each anonymization seed run under both templates, interleaved to cancel
  gateway drift, production params (temp 0.2, max_tokens 4096): usable ballots
  9/10 before vs 8/10 after, complete ballots 9/10 vs 7/10, sign-test exact
  p=1.0 on both. The degeneration is a low-rate stochastic behaviour of the
  model, not a consequence of that one phrasing, and the clause is at best
  neutral — an earlier "4/4 fixed" claim from a 4-call probe was over-extrapolation
  and is retracted here. Detecting a 20-point effect at this endpoint would need
  ~90 paired blocks (~180 calls, hours), so prompt wording is not where this gets
  fixed; a per-pass retry on a degenerate ballot is.
- The clause stays because it removes a genuine ambiguity (a task may be legally
  phrased as "pick the best one") at the cost of one sentence — not because the
  measurement supports it as a fix.
- Reading `位置稳定性` with any of this in mind: a judge that recites `A>B>C>D`
  under every anonymization passes a single-pass check but scores low intra-judge
  rho. That template answer is what the column exists to surface.

### v1.4.3 (2026-09-21)

A fresh install had to be usable straight from the README; it wasn't.

- **`LISTWISE_RANK_PASSES` env var.** QwenPaw 2.2.1 exposes per-tool config over
  HTTP only (`app/routers/tools.py`), and reading it needs an agent context, so
  the `passes` field added in v1.4.0 had no reachable setting on a clean install.
  Env var → `~/.qwenpaw.secret/envs.json`, restart, done. Precedence stays
  tool config > env > default, and the default remains 1 (silently doubling
  everyone's judge calls is not a default).
- **README config/Install sections rewritten around measured behaviour**: the
  `POST /api/tools/<tool>/config` body must wrap keys in `"config"` (a flat
  `{"passes": 2}` validates as an empty config and wipes the stored one),
  per-tool config is per agent, and a verification + troubleshooting table was
  added. The upgrade caveat is documented: entries already present in
  `workspaces/<agent>/agent.json` are never refreshed by registration, so an
  upgraded install keeps showing the old tool description.
- **Tests are now hermetic.** They were reading this machine's live agent
  config — setting `passes` locally changed what the suite asserted. The suite
  now pins `_load_plugin_config` and clears the env var, and is verified green
  both clean and with `LISTWISE_RANK_PASSES=2` in the environment.

### v1.4.2 (2026-09-21)

- **A truncated pass no longer disqualifies the judge's complete passes.**
  Found by the first live run against real endpoints: `agnes` returned one full
  ranking and one that dropped a candidate, so the whole judge was excluded;
  with a second gateway down on 502 the panel was left with a single vote and
  the consensus (and the leave-one-out check) died with it. Completeness is now
  judged per pass; the dropped pass is named in the warnings.
- First real-endpoint validation recorded (3 configured judge endpoints,
  `passes=2`): the 502 hint, the plaintext-`http://` warning, the actionable
  skip path, and the new columns all behaved as designed, and both public
  judges were named as position-driven (intra-judge ρ 0.40 / -0.40) — which is
  the case `passes` exists to catch.
- A `base_url` that resolves to a QwenPaw `ENC:` ciphertext now says so (that
  store is decrypted inside the QwenPaw process only) instead of failing as a
  malformed URL, and the value is truncated so no secret material lands in the
  agent-visible report.

### v1.4.1 (2026-09-21)

- **`冠军判定` line** under the consensus table: leave-one-out over the ballots
  already collected — the consensus is recomputed with each judge removed, and
  if any removal changes who is first (or makes first place a tie) the line
  names that load-bearing judge and says 对单张票敏感. Zero extra calls, no
  tuned margin threshold, and an exact tie for first is now stated as "no
  unique champion" instead of silently picking one.
- Pairwise top-2 rematches deliberately **not** implemented, with the reasoning
  recorded in the README: at 60% pairwise accuracy a 3-vote majority flips the
  truly-better candidate ~35% of the time, and neutralizing the duel's own
  two-position bias needs both orders plus repeats — while the sensitivity it
  would detect is already covered by `位置稳定性` (upstream) and this
  leave-one-out check (downstream).

### v1.4.0 (2026-09-21)

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
