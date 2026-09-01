# qwenpaw-consensus-rank

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![QwenPaw](https://img.shields.io/badge/QwenPaw-%3E%3D1.1.6-green)](https://github.com/agentscope-ai/QwenPaw)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)

QwenPaw tool plugin: **multi-judge consensus ranking** — cross-family LLM judges
independently rank anonymized candidates; results are Borda-aggregated into a
consensus with a Spearman consistency report, avoiding single-model bias.

Methodology from the Co-ReAct paper (arXiv:2605.23590) "listwise rank" block:
a blind user-head proposes ideas, then N judges rank all of them in one pass
(1–100 score + verdict) — single-head head-to-head comparison or quality
judging alone has no yardstick when user claims are unverifiable.

## How it works

```
candidates ──► normalize/dedupe ──► anonymize (seeded shuffle → A/B/C...)
                                        │
              ┌─────────────────────────┘
              ▼
   N cross-family judges ──► each returns a full ranking (A>B>C...)
              │                 (concurrent, failures skipped w/ warnings)
              ▼
        Borda aggregation ──► consensus ranking + per-judge Spearman ρ
```

Why multiple judges? A single model has systematic tastes (prefers longer
answers, certain phrasings). Cross-family voting + anonymization cancels that
bias — that's the point of configuring judges from different model families.

## Tool signature

`rank_candidates_listwise(candidates, task="", judges="", seed=42)`

| Arg | Notes |
|---|---|
| `candidates` | 2–26 plain strings |
| `task` | optional task background so judges rank "for this task" (reduces refusals) |
| `judges` | optional JSON array override of judge configs (see below) |
| `seed` | anonymization shuffle seed for reproducibility (default 42, 0 = random) |

Returns a markdown report: Borda consensus table, per-judge Spearman ρ vs
consensus, skipped-judge warnings, and a weak-consensus warning when fewer
than 3 judges are effective.

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

## Example output

```markdown
# 多 Judge 共识排序报告
候选数：3 ｜ 有效 judge：3/3
任务背景：10 人团队、日活 5 万的电商系统架构选型

## 共识排序（Borda 聚合）
| 名次 | 匿名ID | 得分 | 候选内容 |
| 1 | A | 9 | 模块化单体：单进程 + 清晰模块边界 |
| 2 | C | 6 | 服务化中间态：3 个粗粒度服务 |
| 3 | B | 3 | 微服务拆分：按业务域拆成 8 个服务 |

## Judge 一致性（Spearman ρ vs 共识）
| agnes-flash | 1.000 | A > C > B |
| qwen-122b   | 1.000 | A > C > B |
| sensenova-ds| 1.000 | A > C > B |
```

## Changelog

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
