# -*- coding: utf-8 -*-
"""Stdlib-only unit tests for backend/tool_impl.py.

tool_impl.py is loaded via importlib (same mechanism as the plugin entry),
so the suite runs with no package install; when agentscope is absent the
module falls back to minimal stand-ins, keeping CI dependency-free.
"""

import asyncio
import io
import json
import os
import re
import unittest
import urllib.error
import urllib.request
import importlib.util
from pathlib import Path
from unittest import mock

_BACKEND = Path(__file__).resolve().parent.parent / "backend"
_spec = importlib.util.spec_from_file_location(
    "tool_impl", _BACKEND / "tool_impl.py")
tool_impl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool_impl)


_CELL_PIPES = re.compile(r"(?<!\\)\|")


def _run(candidates, task="", judges="", seed=42):
    return asyncio.run(tool_impl._run(candidates, task, judges, seed))


def _text(chunk) -> str:
    # agentscope 2.x TextBlock is a pydantic model; the standalone fallback
    # is a plain dict — support both shapes.
    parts = []
    for b in chunk.content:
        parts.append(b.get("text", "") if isinstance(b, dict)
                     else getattr(b, "text", ""))
    return "".join(parts)


class _FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _mock_call_judge(ranking_by_name):
    def fake(judge, prompt, temperature, timeout, max_tokens):
        return ranking_by_name[judge["name"]]
    return mock.patch.object(tool_impl, "_call_judge", side_effect=fake)


def _chain(seed, judge_name, cand_ids, n, p=0):
    """Judge answer text asking for cand_ids (indices into the candidate
    list) best-first, spelled with THAT judge's own anonymous labels for
    pass p (pass 0 keeps the judge's bare name as its mapping key)."""
    key = judge_name if p == 0 else f"{judge_name}#{p}"
    mapping = tool_impl._judge_label_map(n, seed, key)
    inv = {c: lab for lab, c in mapping.items()}
    return ">".join(inv[c] for c in cand_ids)


def _mock_passes(by_name):
    """by_name: judge -> per-pass answer texts; an Exception entry fails
    that pass."""
    counter = {}

    def fake(judge, prompt, temperature, timeout, max_tokens):
        name = judge["name"]
        idx = counter.get(name, 0)
        counter[name] = idx + 1
        seq = by_name[name]
        item = seq[min(idx, len(seq) - 1)]
        if isinstance(item, Exception):
            raise item
        return item
    return mock.patch.object(tool_impl, "_call_judge", side_effect=fake)


class AnonymizationTest(unittest.TestCase):
    """Each judge must get its own candidate->label mapping, otherwise every
    judge shares the same position bias and Borda cannot cancel it."""

    def test_is_a_bijection(self):
        mapping = tool_impl._judge_label_map(4, 42, "j1")
        self.assertEqual(sorted(mapping), ["A", "B", "C", "D"])
        self.assertEqual(sorted(mapping.values()), [0, 1, 2, 3])

    def test_same_judge_is_reproducible(self):
        self.assertEqual(tool_impl._judge_label_map(6, 42, "j1"),
                         tool_impl._judge_label_map(6, 42, "j1"))

    def test_judges_do_not_share_a_mapping(self):
        maps = [tool_impl._judge_label_map(6, 42, f"j{k}") for k in range(3)]
        self.assertNotEqual(maps[0], maps[1])
        self.assertNotEqual(maps[1], maps[2])


class RankVectorTest(unittest.TestCase):
    """Partial ballots are completed once, by imputing the positions the
    judge left free — the same representation feeds Borda and Spearman."""

    def test_full_ballot(self):
        self.assertEqual(tool_impl._rank_vector([1, 0, 2], 3),
                         [1.0, 0.0, 2.0])

    def test_unranked_take_the_free_positions(self):
        # ranked 2 of 4 -> the two unranked share positions 2 and 3
        self.assertEqual(tool_impl._rank_vector([0, 1], 4),
                         [0.0, 1.0, 2.5, 2.5])

    def test_imputation_uses_free_positions_not_free_indices(self):
        # c2 first, c0 second -> the only unused position is 2, so c1 gets 2
        self.assertEqual(tool_impl._rank_vector([2, 0], 3),
                         [1.0, 2.0, 0.0])

    def test_empty_ballot_is_all_one_rank(self):
        self.assertEqual(tool_impl._rank_vector([], 3), [1.0, 1.0, 1.0])


class ConsensusTest(unittest.TestCase):
    def test_mean_rank_averages_ballots(self):
        votes = [tool_impl._rank_vector([0, 1, 2], 3),
                 tool_impl._rank_vector([1, 0, 2], 3)]
        order, mean = tool_impl._consensus(votes, 3)
        self.assertEqual(order, [0, 1, 2])  # #1/#2 tie -> input order wins
        self.assertEqual(mean[2], 2.0)

    def test_ties_break_on_input_order(self):
        votes = [tool_impl._rank_vector([0, 1], 2),
                 tool_impl._rank_vector([1, 0], 2)]
        order, mean = tool_impl._consensus(votes, 2)
        self.assertEqual(order, [0, 1])
        self.assertEqual(mean[0], mean[1])


class SpearmanTest(unittest.TestCase):
    def test_identity(self):
        self.assertAlmostEqual(tool_impl._spearman([0.0, 1.0, 2.0],
                                                   [0.0, 1.0, 2.0]), 1.0)

    def test_reversed(self):
        self.assertAlmostEqual(tool_impl._spearman([2.0, 1.0, 0.0],
                                                   [0.0, 1.0, 2.0]), -1.0)

    def test_handles_ties_within_bounds(self):
        rho = tool_impl._spearman([0.0, 1.5, 1.5], [0.0, 1.0, 2.0])
        self.assertGreaterEqual(rho, -1.0)
        self.assertLessEqual(rho, 1.0)
        self.assertAlmostEqual(rho, 0.866, places=3)

    def test_flat_input_has_no_correlation(self):
        self.assertEqual(tool_impl._spearman([1.0, 1.0], [0.0, 1.0]), 0.0)


class NormalizeBaseUrlTest(unittest.TestCase):
    def test_appends_v1_when_no_path(self):
        self.assertEqual(
            tool_impl._normalize_base_url("https://token.sensenova.cn"),
            "https://token.sensenova.cn/v1")

    def test_keeps_existing_path(self):
        self.assertEqual(
            tool_impl._normalize_base_url("https://api.x.com/v1/"),
            "https://api.x.com/v1")

    def test_empty(self):
        self.assertEqual(tool_impl._normalize_base_url("   "), "")


class ParseRankingTest(unittest.TestCase):
    def test_plain_chain(self):
        self.assertEqual(tool_impl._parse_ranking("A>B>C"), ["A", "B", "C"])

    def test_spaced_lowercase_chain(self):
        self.assertEqual(tool_impl._parse_ranking("a > b > c"),
                         ["A", "B", "C"])

    def test_chain_inside_prose(self):
        self.assertEqual(tool_impl._parse_ranking("最终排序：B>A>C，仅供参考。"),
                         ["B", "A", "C"])

    def test_numbered_list(self):
        self.assertEqual(tool_impl._parse_ranking("1. B\n2. A\n3) C"),
                         ["B", "A", "C"])

    def test_numbered_lowercase(self):
        self.assertEqual(tool_impl._parse_ranking("1) b\n2) a"), ["B", "A"])

    def test_json_list(self):
        self.assertEqual(tool_impl._parse_ranking('["B","A","C"]'),
                         ["B", "A", "C"])

    def test_json_dict_string_ranking(self):
        self.assertEqual(tool_impl._parse_ranking('{"ranking": "A>B>C"}'),
                         ["A", "B", "C"])

    def test_json_dict_list_ranking(self):
        self.assertEqual(tool_impl._parse_ranking('{"ranking": ["C","A"]}'),
                         ["C", "A"])

    def test_dedupe_keeps_first_position(self):
        self.assertEqual(tool_impl._parse_ranking("A>B>A>C"), ["A", "B", "C"])

    def test_plain_single_letters(self):
        self.assertEqual(tool_impl._parse_ranking("最优 A，其次 B"),
                         ["A", "B"])

    def test_unparseable(self):
        self.assertEqual(tool_impl._parse_ranking("无法排序"), [])

    def test_list_input(self):
        self.assertEqual(tool_impl._parse_ranking(["b", "a"]), ["B", "A"])


class ResolveJudgesTest(unittest.TestCase):
    def test_inline_arg(self):
        judges, src = tool_impl._resolve_judges(
            '[{"name":"a","model":"m"}]', {})
        self.assertEqual(src, "inline-arg")
        self.assertEqual(judges[0]["model"], "m")

    def test_inline_bad_json(self):
        with self.assertRaises(ValueError):
            tool_impl._resolve_judges("not json", {})

    def test_missing_model_rejected(self):
        with self.assertRaises(ValueError):
            tool_impl._resolve_judges('[{"name":"a"}]', {})

    def test_config_used_when_no_arg(self):
        judges, src = tool_impl._resolve_judges(
            "", {"judges_json": '[{"name":"c","model":"m"}]'})
        self.assertEqual(src, "plugin-config")
        self.assertEqual(judges[0]["name"], "c")

    def test_judge_models_env(self):
        env = {"JUDGE_MODELS": " m1 , m2 ",
               "OPENAI_BASE_URL": "https://gw/v1"}
        with mock.patch.dict(os.environ, env):
            judges, src = tool_impl._resolve_judges("", {})
        self.assertEqual(src, "env:JUDGE_MODELS")
        self.assertEqual([j["model"] for j in judges], ["m1", "m2"])
        self.assertEqual(judges[0]["base_url"], "https://gw/v1")

    def test_defaults_fallback(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            judges, src = tool_impl._resolve_judges("", {})
        self.assertEqual(src, "builtin-defaults")
        self.assertEqual(len(judges), 3)


class ExplainHttpErrorTest(unittest.TestCase):
    def test_invalid_token(self):
        hint = tool_impl._explain_http_error(
            401, '{"error":{"message":"invalid token"}}')
        self.assertIn("token", hint)

    def test_quota(self):
        self.assertIn("quota", tool_impl._explain_http_error(429, "slow down"))

    def test_no_channel(self):
        hint = tool_impl._explain_http_error(
            503, "no available channel for the model")
        self.assertIn("channel", hint)

    def test_credits(self):
        self.assertIn("credits", tool_impl._explain_http_error(400,
                                                               "CreditsError"))

    def test_gateway_failure(self):
        self.assertIn("gateway-side", tool_impl._explain_http_error(500, "x"))


class CallJudgeTest(unittest.TestCase):
    def _judge(self, **kw):
        judge = {"name": "t", "model": "test-model",
                 "base_url": "https://api.example.com/v1",
                 "api_key_env": "TEST_JUDGE_KEY"}
        judge.update(kw)
        return judge

    def _patch_urlopen(self, fake_urlopen):
        return mock.patch.object(urllib.request, "urlopen",
                                 side_effect=fake_urlopen)

    def test_request_shape_and_per_judge_temperature(self):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["req"] = req
            captured["timeout"] = timeout
            return _FakeResp(
                b'{"choices":[{"message":{"content":"A>B>C"}}]}')

        with mock.patch.dict(os.environ, {"TEST_JUDGE_KEY": "sk-test"}):
            with self._patch_urlopen(fake_urlopen):
                out = tool_impl._call_judge(self._judge(temperature=0.7),
                                            "P", 0.2, 30, 100)
        self.assertEqual(out, "A>B>C")
        req = captured["req"]
        self.assertTrue(req.full_url.endswith("/chat/completions"))
        self.assertEqual(req.get_header("Authorization"), "Bearer sk-test")
        body = json.loads(req.data.decode())
        self.assertEqual(body["temperature"], 0.7)  # judge-level wins
        self.assertEqual(body["max_tokens"], 100)
        self.assertEqual(captured["timeout"], 30)

    def test_extra_body_merged(self):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["req"] = req
            return _FakeResp(b'{"choices":[{"message":{"content":"A>B"}}]}')

        judge = self._judge(extra_body={"chat_template_kwargs":
                                        {"enable_thinking": False}})
        with mock.patch.dict(os.environ, {"TEST_JUDGE_KEY": "k"}):
            with self._patch_urlopen(fake_urlopen):
                tool_impl._call_judge(judge, "P", 0.2, 5, 10)
        body = json.loads(captured["req"].data.decode())
        self.assertEqual(body["chat_template_kwargs"],
                         {"enable_thinking": False})

    def test_http_error_hint(self):
        err = urllib.error.HTTPError(
            "https://x/v1/chat/completions", 401, "Unauthorized", {},
            io.BytesIO(b'{"error":{"message":"invalid token"}}'))
        with mock.patch.dict(os.environ, {"TEST_JUDGE_KEY": "k"}):
            with self._patch_urlopen(lambda req, timeout=None: (_ for _ in ()
                                     ).throw(err)):
                with self.assertRaisesRegex(RuntimeError, "token"):
                    tool_impl._call_judge(self._judge(), "P", 0.2, 5, 10)

    def test_unreachable_endpoint(self):
        err = urllib.error.URLError("connection refused")
        with mock.patch.dict(os.environ, {"TEST_JUDGE_KEY": "k"}):
            with self._patch_urlopen(lambda req, timeout=None: (_ for _ in ()
                                     ).throw(err)):
                with self.assertRaisesRegex(RuntimeError, "unreachable"):
                    tool_impl._call_judge(self._judge(), "P", 0.2, 5, 10)

    def test_timeout(self):
        with mock.patch.dict(os.environ, {"TEST_JUDGE_KEY": "k"}):
            with self._patch_urlopen(lambda req, timeout=None: (_ for _ in ()
                                     ).throw(TimeoutError())):
                with self.assertRaisesRegex(RuntimeError, "timed out"):
                    tool_impl._call_judge(self._judge(), "P", 0.2, 5, 10)

    def test_missing_scheme_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "协议前缀"):
            tool_impl._call_judge(self._judge(base_url="myhost:8000"),
                                  "P", 0.2, 5, 10)

    def test_encrypted_store_value_explains_itself_without_leaking(self):
        # QwenPaw decrypts envs.json into its own process; a raw ENC: value
        # means the tool is running outside it. Say so, and do not echo the
        # ciphertext into an agent-visible report.
        env = {"OPENAI_BASE_URL": "ENC:gAAAAABqqhMLfJXtSiHYAI48vtzZTp8N"}
        judge = self._judge(base_url="", base_url_env="ABSENT_ENV")
        with mock.patch.dict(os.environ, env, clear=False):
            with self.assertRaises(RuntimeError) as ctx:
                tool_impl._call_judge(judge, "P", 0.2, 5, 10)
        self.assertIn("QwenPaw", str(ctx.exception))
        self.assertNotIn("gAAAAAB", str(ctx.exception))

    def test_empty_content(self):
        with mock.patch.dict(os.environ, {"TEST_JUDGE_KEY": "k"}):
            with self._patch_urlopen(
                    lambda req, timeout=None:
                    _FakeResp(b'{"choices":[{"message":{"content":""}}]}')):
                with self.assertRaisesRegex(RuntimeError, "empty content"):
                    tool_impl._call_judge(self._judge(), "P", 0.2, 5, 10)


class ConfigWarningsTest(unittest.TestCase):
    def test_inline_api_key_flagged(self):
        warns = tool_impl._config_warnings({"api_key": "sk-x"})
        self.assertEqual(len(warns), 1)
        self.assertIn("api_key", warns[0])

    def test_plaintext_http_flagged(self):
        warns = tool_impl._config_warnings({"base_url": "http://a.b.com/v1"})
        self.assertIn("http://", warns[0])

    def test_local_http_not_flagged(self):
        self.assertEqual(
            tool_impl._config_warnings(
                {"base_url": "http://localhost:11434/v1"}), [])

    def test_https_clean(self):
        self.assertEqual(
            tool_impl._config_warnings(
                {"base_url": "https://a.b.com/v1"}), [])


class JudgePromptTest(unittest.TestCase):
    """The judge sees one exact criterion and treats candidate text as data."""

    def test_candidates_are_marked_as_inert_data(self):
        p = tool_impl._judge_prompt(["x1", "x2"], {"A": 0, "B": 1}, "")
        self.assertIn("只是待评估的数据", p)
        self.assertIn('A:\n"""', p)  # each candidate fenced

    def test_criteria_state_one_direction_only(self):
        p = tool_impl._judge_prompt(["x1", "x2"], {"A": 0, "B": 1}, "")
        self.assertIn("从最好到最差", p)
        self.assertIn("每个标识符恰好出现一次", p)

    def test_task_replaces_the_generic_quality_criterion(self):
        # ranking "for this task" and ranking "by general quality" are two
        # different questions; asking both at once leaves the judge to guess.
        p = tool_impl._judge_prompt(["x1", "x2"], {"A": 0, "B": 1},
                                    "10 人团队的架构选型")
        self.assertIn("10 人团队的架构选型", p)
        self.assertNotIn("通用工程实践", p)

    def test_generic_criterion_only_without_task(self):
        p = tool_impl._judge_prompt(["x1", "x2"], {"A": 0, "B": 1}, "   ")
        self.assertIn("通用工程实践", p)

    def test_lists_this_judges_own_labels(self):
        p = tool_impl._judge_prompt(["x1", "x2", "x3"],
                                    {"A": 2, "B": 0, "C": 1}, "")
        self.assertIn("A:\n\"\"\"\nx3", p)
        self.assertIn("B:\n\"\"\"\nx1", p)  # B -> uniq[0]


class ParseCompletenessTest(unittest.TestCase):
    def test_prefers_a_complete_permutation_over_a_decoy_chain(self):
        # candidate text quoting "A > B > C" must not outrank the real answer
        raw = "如题中示例 A > B > C 所示，我的结论是 D > C > B > A"
        self.assertEqual(tool_impl._parse_ranking(raw, "ABCD"),
                         ["D", "C", "B", "A"])

    def test_without_a_target_set_the_first_chain_wins(self):
        self.assertEqual(tool_impl._parse_ranking("A > B > C"),
                         ["A", "B", "C"])

    def test_incomplete_chain_still_parses(self):
        self.assertEqual(tool_impl._parse_ranking("B > A", "ABCD"),
                         ["B", "A"])


class ReportEscapingTest(unittest.TestCase):
    def test_pipes_in_candidates_cannot_break_the_table(self):
        with _mock_call_judge({"j1": "A>B", "j2": "A>B"}):
            chunk = _run(["单体|微服务", "serverless"],
                         judges='[{"name":"j1","model":"m1"},'
                                '{"name":"j2","model":"m2"}]')
        text = _text(chunk)
        self.assertIn("单体\\|微服务", text)
        header = next(ln for ln in text.splitlines()
                      if ln.startswith("| 名次 |"))
        cols = len(_CELL_PIPES.findall(header))
        row = next(ln for ln in text.splitlines() if "单体" in ln)
        self.assertEqual(len(_CELL_PIPES.findall(row)), cols, row)


class DuplicateJudgesTest(unittest.TestCase):
    """N clones of one endpoint are one opinion, not N families of it."""

    def test_same_endpoint_and_model_collapses_to_one_vote(self):
        judges = json.dumps([
            {"name": "j1", "model": "m", "base_url": "https://a/v1"},
            {"name": "j2", "model": "m", "base_url": "https://a/v1"},
            {"name": "j3", "model": "m", "base_url": "https://b/v1"},
        ])
        asked = []

        def fake(judge, prompt, temperature, timeout, max_tokens):
            name = judge["name"]
            asked.append(name)
            return _chain(42, name, [1, 0, 2], 3)

        with mock.patch.object(tool_impl, "_call_judge", side_effect=fake):
            chunk = _run(["微服务", "单体", "serverless"], judges=judges)
        text = _text(chunk)
        self.assertEqual(asked, ["j1", "j3"])  # j2 never billed
        self.assertIn("有效 judge：2/2", text)
        self.assertIn("j2", text)
        self.assertIn("合并为一票", text)
        self.assertIn("| 1 | #2 | 1.00 | 单体 |", text)

    def test_different_models_on_one_endpoint_stay_independent(self):
        kept, merged = tool_impl._collapse_duplicate_judges([
            {"name": "j1", "model": "m1", "base_url": "https://a/v1"},
            {"name": "j2", "model": "m2", "base_url": "https://a/v1"},
        ])
        self.assertEqual([j["name"] for j in kept], ["j1", "j2"])
        self.assertEqual(merged, [])

    def test_duplicate_names_are_made_unique(self):
        # maps are keyed by name, so two entries both called "qwen" would
        # share one anonymization and vote in lockstep again
        kept, merged = tool_impl._collapse_duplicate_judges([
            {"name": "qwen", "model": "a", "base_url": "https://a/v1"},
            {"name": "qwen", "model": "b", "base_url": "https://b/v1"},
        ])
        self.assertEqual([j["name"] for j in kept], ["qwen", "qwen#1"])
        self.assertEqual(merged, [])

    def test_endpoint_env_aliases_are_seen_as_the_same_endpoint(self):
        kept, merged = [], []
        env = {"OPENAI_BASE_URL": "https://a/v1/"}
        with mock.patch.dict(os.environ, env):
            kept, merged = tool_impl._collapse_duplicate_judges([
                {"name": "j1", "model": "m", "base_url": "https://a/v1"},
                {"name": "j2", "model": "m"},  # falls back to the same URL
            ])
        self.assertEqual([j["name"] for j in kept], ["j1"])
        self.assertEqual(merged, [("j2", "j1")])


class CandidateBudgetTest(unittest.TestCase):
    """Accuracy falls as the state grows: a wall of prose in one candidate
    dilutes the judge's attention on the comparison it is actually for."""

    def test_long_candidate_is_clipped_in_the_prompt(self):
        p = tool_impl._judge_prompt(["字" * 2000, "短"],
                                    {"A": 0, "B": 1}, "")
        self.assertNotIn("字" * 2000, p)
        self.assertIn("…（截断）", p)
        self.assertLess(len(p), 2000)

    def test_short_candidates_pass_through_untouched(self):
        p = tool_impl._judge_prompt(["完整保留的一句", "短"],
                                    {"A": 0, "B": 1}, "")
        self.assertIn("完整保留的一句\n\"\"\"", p)
        self.assertNotIn("截断", p)

    def test_report_clips_the_same_way(self):
        with _mock_call_judge({"j1": "A>B", "j2": "B>A"}):
            chunk = _run(["字" * 2000, "短"],
                         judges='[{"name":"j1","model":"m1"},'
                                '{"name":"j2","model":"m2"}]')
        text = _text(chunk)
        self.assertIn("…（截断）", text)
        self.assertNotIn("字" * 2000, text)


class StabilityPassesTest(unittest.TestCase):
    """passes>=2: each judge ranks the slate twice under two different
    anonymizations; its two ballots are averaged into ONE vote, and the
    spread between them is the measured position sensitivity."""

    JUDGES = json.dumps([{"name": f"j{i}", "model": f"m{i}"}
                         for i in range(3)])

    def setUp(self):
        patcher = mock.patch.object(tool_impl, "_load_plugin_config",
                                    lambda name: {})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _cfg(self, **kw):
        return mock.patch.object(tool_impl, "_load_plugin_config",
                                 lambda name: kw)

    def test_two_passes_average_inside_a_single_judge_vote(self):
        answers = {
            "j0": [_chain(42, "j0", [0, 1, 2], 3),
                   _chain(42, "j0", [1, 0, 2], 3, p=1)],
            "j1": [_chain(42, "j1", [2, 1, 0], 3),
                   _chain(42, "j1", [2, 1, 0], 3, p=1)],
            "j2": [_chain(42, "j2", [0, 1, 2], 3),
                   _chain(42, "j2", [0, 1, 2], 3, p=1)],
        }
        with self._cfg(passes=2):
            with _mock_passes(answers):
                chunk = _run(["x1", "x2", "x3"], judges=self.JUDGES)
        text = _text(chunk)
        self.assertIn("| 1（并列） | #1 | 1.83 | x1 |", text)
        self.assertIn("| 3 | #3 | 2.33 | x3 |", text)
        self.assertIn("| j0 | m0 | 0.866 |", text)   # one unstable vote
        self.assertIn("位置稳定性", text)
        self.assertIn("**j0**", text)                # named as position-sensitive

    def test_failed_pass_does_not_dilute_the_judges_vote(self):
        answers = {
            "j0": [_chain(42, "j0", [2, 1, 0], 3), RuntimeError("boom")],
            "j1": [_chain(42, "j1", [0, 1, 2], 3),
                   _chain(42, "j1", [0, 1, 2], 3, p=1)],
            "j2": [_chain(42, "j2", [0, 1, 2], 3),
                   _chain(42, "j2", [0, 1, 2], 3, p=1)],
        }
        with self._cfg(passes=2):
            with _mock_passes(answers):
                chunk = _run(["x1", "x2", "x3"], judges=self.JUDGES)
        text = _text(chunk)
        # j0's single usable ballot keeps full weight: mean rank 1.67, not the
        # 1.50 a placeholder-padded (diluted) ballot would give
        self.assertIn("| 1 | #1 | 1.67 | x1 |", text)

    def test_complete_pass_still_counts_when_a_sibling_pass_is_partial(self):
        # live-run finding: one judge returned a full ranking on pass 1 and a
        # truncated one on pass 2. Dropping the whole judge cost the panel a
        # vote it had earned - only the bad pass should go.
        names = ["j1", "j2", "j3"]
        judges = json.dumps([{"name": n, "model": f"m{i}"}
                             for i, n in enumerate(names)])
        answers = {
            "j1": [_chain(42, "j1", [0, 1, 2], 3),
                   _chain(42, "j1", [0, 1], 3, p=1)],
            "j2": [_chain(42, "j2", [0, 1, 2], 3),
                   _chain(42, "j2", [0, 1, 2], 3, p=1)],
            "j3": [_chain(42, "j3", [0, 1, 2], 3),
                   _chain(42, "j3", [0, 1, 2], 3, p=1)],
        }
        with self._cfg(passes=2):
            with _mock_passes(answers):
                chunk = _run(["x1", "x2", "x3"], judges=judges)
        text = _text(chunk)
        self.assertIn("完整票 3/3", text)
        self.assertIn("| 1 | #1 | 1.00 | x1 |", text)
        self.assertIn("复算 3/3 次冠军不变", text)
        self.assertIn("j1", text.split("配置与完整性警告")[1])
        self.assertIn("该遍已丢弃", text)

    def test_passes_are_clamped(self):
        calls = []

        def fake(judge, prompt, temperature, timeout, max_tokens):
            calls.append(judge["name"])
            return _chain(42, judge["name"], [0, 1, 2], 3)

        with self._cfg(passes=99):
            with mock.patch.object(tool_impl, "_call_judge", side_effect=fake):
                _run(["x1", "x2", "x3"], judges=self.JUDGES)
        self.assertEqual(len(calls), 9)  # 3 judges x _MAX_PASSES(3)

    def test_single_pass_says_stability_is_unmeasured(self):
        answers = {f"j{i}": _chain(42, f"j{i}", [0, 1, 2], 3)
                   for i in range(3)}
        with _mock_call_judge(answers):
            chunk = _run(["x1", "x2", "x3"], judges=self.JUDGES)
        text = _text(chunk)
        self.assertIn("位置稳定性未测", text)
        self.assertIn("passes=2", text)
        self.assertIn("| 1 | #1 | 1.00 | x1 |", text)  # listwise path unchanged


class ChampionVerdictTest(unittest.TestCase):
    """Leave-one-out: does the #1 survive dropping any single judge's ballot?

    No threshold is involved - "one ballot can change the champion" is the
    thing a reader actually needs to know, and it is computable from ballots
    the tool already holds.
    """

    def _run_with(self, orders_by_name):
        names = list(orders_by_name)
        judges = json.dumps([{"name": n, "model": f"m{i}"}
                             for i, n in enumerate(names)])
        answers = {n: _chain(42, n, orders_by_name[n], 3) for n in names}
        with _mock_call_judge(answers):
            return _text(_run(["x1", "x2", "x3"], judges=judges))

    def test_unanimous_champion_is_reported_stable(self):
        text = self._run_with({"j1": [0, 1, 2], "j2": [0, 1, 2],
                               "j3": [0, 1, 2]})
        self.assertIn("冠军判定", text)
        self.assertIn("领先第 2 名 1.00", text)
        self.assertIn("冠军稳定", text)

    def test_champion_held_up_by_one_ballot_is_flagged(self):
        # j1 is the load-bearing ballot for #2 (x2): dropping it ties #1/#2,
        # so the winner stops being unique
        text = self._run_with({"j1": [1, 2, 0], "j2": [0, 1, 2],
                               "j3": [1, 0, 2]})
        self.assertIn("冠军判定", text)
        self.assertIn("对单张票敏感", text)
        self.assertIn("j1", text.split("冠军判定")[1])

    def test_tied_champion_is_declared_outright(self):
        text = self._run_with({"j1": [0, 1, 2], "j2": [1, 0, 2],
                               "j3": [0, 1, 2], "j4": [1, 0, 2]})
        self.assertIn("并列，本报告不给唯一冠军", text)


class RunConsensusTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(tool_impl, "_load_plugin_config",
                                    lambda name: {})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_consensus_report(self):
        judges = json.dumps([{"name": "j1", "model": "m1"},
                             {"name": "j2", "model": "m2"}])
        answers = {"j1": _chain(42, "j1", [0, 1, 2], 3),
                   "j2": _chain(42, "j2", [0, 2, 1], 3)}
        with _mock_call_judge(answers):
            chunk = _run(["微服务", "单体", "serverless"], judges=judges)
        self.assertEqual(chunk.state, tool_impl.ToolResultState.SUCCESS)
        text = _text(chunk)
        self.assertIn("共识排序", text)
        self.assertIn("有效 judge：2/2", text)
        # j1 says 0>1>2, j2 says 0>2>1 -> #1 wins, #2/#3 tie at mean 2.50
        self.assertIn("| 1 | #1 | 1.00 | 微服务 |", text)
        self.assertIn("并列", text)
        self.assertIn("不足 3 个", text)       # weak-consensus warning
        self.assertIn("Spearman", text)
        self.assertIn("种子：42", text)

    def test_consensus_is_keyed_by_candidate_not_by_label(self):
        # every judge sees a different A/B/C mapping; agreement must still
        # accumulate on the same underlying candidate.
        names = ["j1", "j2", "j3"]
        judges = json.dumps([{"name": n, "model": f"m{i}"}
                             for i, n in enumerate(names)])
        answers = {n: _chain(42, n, [1, 0, 2], 3) for n in names}
        with _mock_call_judge(answers):
            chunk = _run(["微服务", "单体", "serverless"], judges=judges)
        text = _text(chunk)
        self.assertIn("| 1 | #2 | 1.00 | 单体 |", text)
        self.assertIn("#2 > #1 > #3", text)
        self.assertIn("Judge 间一致度", text)

    def test_incomplete_ballot_does_not_decide_the_consensus(self):
        # j3 only ranked two of three candidates, which implicitly claims
        # its first pick is the best of the set. A ballot that broke the
        # "rank everything" protocol must not swing the winner.
        names = ["j1", "j2", "j3"]
        judges = json.dumps([{"name": n, "model": f"m{i}"}
                             for i, n in enumerate(names)])
        answers = {names[0]: _chain(42, names[0], [0, 1, 2], 3),
                   names[1]: _chain(42, names[1], [0, 1, 2], 3),
                   names[2]: _chain(42, names[2], [1, 2], 3)}
        with _mock_call_judge(answers):
            chunk = _run(["微服务", "单体", "serverless"], judges=judges)
        text = _text(chunk)
        self.assertIn("| 1 | #1 | 1.00 | 微服务 |", text)
        self.assertIn("完整票 2/3", text)
        self.assertIn("未计入共识", text)

    def test_all_ballots_incomplete_is_reported_as_degraded(self):
        judges = json.dumps([{"name": "j1", "model": "m1"},
                             {"name": "j2", "model": "m2"}])
        answers = {"j1": _chain(42, "j1", [0, 1], 3),
                   "j2": _chain(42, "j2", [0, 1], 3)}
        with _mock_call_judge(answers):
            chunk = _run(["微服务", "单体", "serverless"], judges=judges)
        self.assertEqual(chunk.state, tool_impl.ToolResultState.SUCCESS)
        text = _text(chunk)
        self.assertIn("无完整票", text)
        self.assertIn("| 1 | #1 | 1.00 | 微服务 |", text)

    def test_single_valid_vote_is_not_a_consensus(self):
        judges = json.dumps([{"name": "j1", "model": "m1"},
                             {"name": "j2", "model": "m2"}])
        with mock.patch.object(
                tool_impl, "_call_judge",
                side_effect=lambda j, p, t, to, mt: (
                    _chain(42, j["name"], [0, 1], 2) if j["name"] == "j1"
                    else (_ for _ in ()).throw(RuntimeError("down")))):
            chunk = _run(["x1", "x2"], judges=judges)
        self.assertEqual(chunk.state, tool_impl.ToolResultState.SUCCESS)
        self.assertIn("不构成共识", _text(chunk))

    def test_both_rows_of_a_tie_are_marked(self):
        # two judges say 0>1>2, two say 1>0>2 -> #1/#2 tie, #3 stands alone
        names = ["j1", "j2", "j3", "j4"]
        judges = json.dumps([{"name": n, "model": f"m{i}"}
                             for i, n in enumerate(names)])
        picks = {"j1": [0, 1, 2], "j2": [1, 0, 2],
                 "j3": [0, 1, 2], "j4": [1, 0, 2]}
        answers = {n: _chain(42, n, picks[n], 3) for n in names}
        with _mock_call_judge(answers):
            chunk = _run(["x1", "x2", "x3"], judges=judges)
        text = _text(chunk)
        self.assertIn("| 1（并列） | #1 | 1.50 | x1 |", text)
        self.assertIn("| 2（并列） | #2 | 1.50 | x2 |", text)
        self.assertIn("| 3 | #3 | 3.00 | x3 |", text)

    def test_nameless_judges_keep_stable_names_after_merging(self):
        # the merge warning must name the same judges the table shows
        judges = json.dumps([
            {"model": "x", "base_url": "https://a/v1"},
            {"model": "x", "base_url": "https://a/v1"},
            {"model": "y", "base_url": "https://b/v1"},
        ])
        asked = []

        def fake(judge, prompt, temperature, timeout, max_tokens):
            asked.append(judge["name"])
            return _chain(42, judge["name"], [0, 1, 2], 3)

        with mock.patch.object(tool_impl, "_call_judge", side_effect=fake):
            chunk = _run(["x1", "x2", "x3"], judges=judges)
        text = _text(chunk)
        self.assertEqual(asked, ["m0", "m2"])
        self.assertIn("- **m1**：与 m0", text)
        self.assertIn("| m2 | y |", text)

    def test_dedupe_and_bullet_strip(self):
        with _mock_call_judge({"j1": "A>B", "j2": "A>B"}):
            chunk = _run(["单体", " 单体 ", "-微服务"], judges='[{"name":"j1","model":"m1"},{"name":"j2","model":"m2"}]')
        self.assertEqual(chunk.state, tool_impl.ToolResultState.SUCCESS)
        self.assertIn("候选数：2", _text(chunk))

    def test_string_candidates_input(self):
        with _mock_call_judge({"j1": "A>B", "j2": "A>B"}):
            chunk = _run("单体\n微服务",
                         judges='[{"name":"j1","model":"m1"},{"name":"j2","model":"m2"}]')
        self.assertEqual(chunk.state, tool_impl.ToolResultState.SUCCESS)
        self.assertIn("候选数：2", _text(chunk))

    def test_partial_ranking_warning(self):
        judges = json.dumps([{"name": "j1", "model": "m1"},
                             {"name": "j2", "model": "m2"}])
        with _mock_call_judge({"j1": "A>B>C", "j2": "A>B"}):
            chunk = _run(["x1", "x2", "x3"], judges=judges)
        text = _text(chunk)
        self.assertIn("排名不完整", text)
        self.assertIn("j2", text)

    def test_config_warnings_surface_in_report(self):
        judges = json.dumps([
            {"name": "j1", "model": "m",
             "base_url": "http://api.example.com/v1", "api_key": "sk-x"},
            {"name": "j2", "model": "m"},
        ])
        with _mock_call_judge({"j1": "A>B", "j2": "B>A"}):
            chunk = _run(["x1", "x2"], judges=judges)
        text = _text(chunk)
        self.assertIn("明文 http://", text)
        self.assertIn("api_key", text)

    def test_all_judges_fail_with_keys(self):
        judges = json.dumps([{"name": "j1", "model": "m"}])
        with mock.patch.object(tool_impl, "_call_judge",
                               side_effect=RuntimeError("boom")):
            with mock.patch.object(tool_impl, "_detect_config",
                                   return_value=["OPENAI_API_KEY"]):
                chunk = _run(["x1", "x2"], judges=judges)
        self.assertEqual(chunk.state, tool_impl.ToolResultState.ERROR)
        self.assertIn("all judges failed", _text(chunk))
        self.assertIn("boom", _text(chunk))

    def test_setup_wizard_when_unconfigured(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(tool_impl, "_detect_config",
                                   return_value=[]):
                chunk = _run(["x1", "x2"])
        self.assertEqual(chunk.state, tool_impl.ToolResultState.ERROR)
        self.assertIn("初始化向导", _text(chunk))

    def test_too_many_candidates(self):
        chunk = _run([f"c{i}" for i in range(27)])
        self.assertEqual(chunk.state, tool_impl.ToolResultState.ERROR)
        self.assertIn("max 26", _text(chunk))

    def test_need_two_distinct(self):
        chunk = _run(["同一个", "同一个"])
        self.assertEqual(chunk.state, tool_impl.ToolResultState.ERROR)
        self.assertIn(">= 2", _text(chunk))

    def test_bad_judges_config(self):
        chunk = _run(["x1", "x2"], judges='[{"name":"no-model"}]')
        self.assertEqual(chunk.state, tool_impl.ToolResultState.ERROR)
        self.assertIn("bad judges config", _text(chunk))

    def test_wrapper_catches_exceptions(self):
        with mock.patch.object(tool_impl, "_run",
                               side_effect=RuntimeError("kaboom")):
            chunk = asyncio.run(tool_impl.rank_candidates_listwise(["a", "b"]))
        self.assertEqual(chunk.state, tool_impl.ToolResultState.ERROR)
        self.assertIn("kaboom", _text(chunk))


if __name__ == "__main__":
    unittest.main()
