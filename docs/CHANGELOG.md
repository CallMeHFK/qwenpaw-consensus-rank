# Changelog

All notable changes to the `listwise-rank` QwenPaw plugin. The README keeps only the recent entries; this file has the full history, newest first.

### v1.4.9 (2026-09-22)

- **The declared QwenPaw ceiling excluded every host this plugin actually
  runs on.** `plugin.json` said `qwenpaw_version.max: 2.1.0` while v1.4.2 onward
  has been developed, installed and measured on 2.2.1. The field is
  left-closed / right-open (`_version_compat.py`: `>=min, <max`), so it claimed
  incompatibility with 2.1.0 *and everything newer*. Raised to `2.3.0`, which is
  the honest ceiling: it covers the tested 2.2 line and stops short of claiming
  anything about 3.x. Writing `2.2.1` would have been worse than the bug — it
  refuses the build the range was measured on.
- **Why it matters even though nothing breaks today**: the host currently
  enforces only `>= min`, with the full-range check left in the source as
  "restore when re-enabling". A stale `max` is inert until that line returns,
  and then it is a load-time refusal with a message pointing at the manifest.
- No change to the tool's behaviour, prompt or report.

### v1.4.8 (2026-09-22)

No behaviour change in the tool. This release is about shipping it.

- **First GitHub Release, with the packaging to keep it honest.**
  `packaging/build_plugin_zip.py` builds the installable archive and
  `.github/workflows/release.yml` publishes it on a tag push: run the suite,
  build, create/refresh the release, attach the asset, take the notes from this
  file. The archive layout is the one both QwenPaw install paths accept — a
  single `listwise-rank/` top directory holding `plugin.json`. GitHub's own
  `Source code` archive *also* installs (the repo root has a `plugin.json`), it
  just unpacks `tests/` and `.github/` into `~/.qwenpaw/plugins/` as well, which
  is why the README now names the asset instead of saying "download the zip".
- **Build guards, each with a test that feeds it the bad input**: a tag that does
  not match `plugin.json`, an archive whose top level is not exactly
  `[listwise-rank]`, a missing manifest entry target, a truncated member list, and
  a symlinked source file — the last one aborts rather than ships, because
  `zipfile.extractall` lands a symlink member as a text file containing the
  target path, which reads like a plugin bug on the user's machine. Output is
  deterministic (sorted members, fixed timestamp), so a re-run after a failed job
  byte-compares. `tests/test_packaging.py`, suite 103 → 119, and CI now builds the
  archive on every push so these paths cannot rot silently.
- **Verified against the host it ships to**: the built archive went through
  QwenPaw 2.2.1's own `_safe_extract_zip` → `_find_plugin_dir` →
  `PluginLoader.load_plugin_from_path` into a temp plugins dir and loaded with no
  diagnostics, tool registration hook included.
- **README restructured**: a Quick start (install → keys → enable/verify) above
  the fold, the full changelog moved to this file, a Troubleshooting table, and
  the restart rule split by install path (release/CLI install hot-loads, `git
  clone` into `plugins/` needs a restart). Stale content fixed: the version probe
  asserted `expect: 1.4.3 loaded` while 1.4.7 was current.
- **`plugin.json` text**: the `passes` help repeated its retry sentence verbatim
  and ended with "Default 1" twice; `api_key_hint` pointed at the README section
  this release renamed.

### v1.4.7 (2026-09-22)

- **A transient-endpoint retry was also being reported as a format retry.** Found
  with a local HTTP stub that 502s once and then answers normally (3 real
  requests, no model spend): the counter for "reply was degenerate" was
  incremented on *any* second attempt, so a recovered 502 printed both
  `端点瞬时故障重试 1 次后恢复` and a false `1 遍回复不合格`. The two reasons are now
  counted separately and each direction has a regression test.
- Method note worth keeping: the stub test exists because the mocked-`_call_judge`
  suite could not catch this — the bug lived in how the two retry reasons shared a
  counter, which only the reporting layer exposed.

### v1.4.6 (2026-09-22)

- **Transient endpoint failures get one retry too.** Measured on this machine:
  the internal vLLM gateway answered 502 on two runs and served fine afterwards,
  and another judge threw a one-off SSL EOF — yet each such blip cost a whole
  vote and dropped a 3-judge panel to "共识质量有限". Now 502/5xx, connection
  and TLS failures and read timeouts are re-asked once after 1s and the report
  says `端点瞬时故障重试 N 次后恢复`.
- **Auth and capacity errors are still never retried** — bad token, quota / rate
  limit, `no available channel`, unset base_url: they do not self-heal, and a
  retry would only bury the actionable hint.
- Tests 100 -> 101 (policy covered in both directions). Not yet observed firing
  live: reproducing a real 502 on demand is not something the test controls.

### v1.4.5 (2026-09-22)

- **A degenerate reply is retried once.** When a judge's answer parses to fewer
  than 2 identifiers, the same pass is asked again with a positively restated
  format line (`重发（格式硬要求）：只输出一行，把全部 N 个标识符用 > 连接，形如
  B>C>D>A。`) — no quoting of the wrong behaviour back at the model, because the
  v1.4.4 measurement showed prohibition phrasing is at best neutral. Cost is one
  extra call and only on the ~15% of calls that actually degenerate, so the
  expected loss of a ballot drops from ~15% to ~2% (0.15^2, independence
  assumed). Endpoint errors (502 / TLS / 401 / quota) are deliberately not
  retried — those are reported with their existing actionable hints.
- Measured honestly: the retry path has unit coverage (100 tests) but the live
  confirmation run hit zero degenerate replies in 4 calls, so it has not yet
  been observed firing against a real model.

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
