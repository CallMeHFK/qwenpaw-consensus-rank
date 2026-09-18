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


class SpearmanTest(unittest.TestCase):
    def test_perfect(self):
        self.assertAlmostEqual(
            tool_impl._spearman(["A", "B", "C"], ["A", "B", "C"],
                                ["A", "B", "C"]), 1.0)

    def test_reversed(self):
        self.assertAlmostEqual(
            tool_impl._spearman(["C", "B", "A"], ["A", "B", "C"],
                                ["A", "B", "C"]), -1.0)

    def test_partial_ranking_uses_median_rank(self):
        # judge ranked only A, B; missing C gets the median rank (1.0)
        self.assertAlmostEqual(
            tool_impl._spearman(["A", "B"], ["A", "B", "C"],
                                ["A", "B", "C"]), 0.75)

    def test_too_few(self):
        self.assertEqual(tool_impl._spearman(["A"], ["A"], ["A"]), 0.0)


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


class RunConsensusTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(tool_impl, "_load_plugin_config",
                                    lambda name: {})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_consensus_report(self):
        judges = json.dumps([{"name": "j1", "model": "m1"},
                             {"name": "j2", "model": "m2"}])
        with _mock_call_judge({"j1": "A>B>C", "j2": "A>C>B"}):
            chunk = _run(["微服务", "单体", "serverless"], judges=judges)
        self.assertEqual(chunk.state, tool_impl.ToolResultState.SUCCESS)
        text = _text(chunk)
        self.assertIn("共识排序", text)
        self.assertIn("有效 judge：2/2", text)
        self.assertIn("| 1 | A | 6 |", text)  # Borda: A=6, B=3, C=3
        self.assertIn("并列", text)            # B/C tie is marked
        self.assertIn("不足 3 个", text)       # weak-consensus warning
        self.assertIn("Spearman", text)

    def test_dedupe_and_bullet_strip(self):
        with _mock_call_judge({"j1": "A>B", "j2": "A>B"}):
            chunk = _run(["单体", " 单体 ", "-微服务"], judges='[{"name":"j1","model":"m"},{"name":"j2","model":"m"}]')
        self.assertEqual(chunk.state, tool_impl.ToolResultState.SUCCESS)
        self.assertIn("候选数：2", _text(chunk))

    def test_string_candidates_input(self):
        with _mock_call_judge({"j1": "A>B", "j2": "A>B"}):
            chunk = _run("单体\n微服务",
                         judges='[{"name":"j1","model":"m"},{"name":"j2","model":"m"}]')
        self.assertEqual(chunk.state, tool_impl.ToolResultState.SUCCESS)
        self.assertIn("候选数：2", _text(chunk))

    def test_partial_ranking_warning(self):
        judges = json.dumps([{"name": "j1", "model": "m"},
                             {"name": "j2", "model": "m"}])
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
