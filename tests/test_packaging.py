# -*- coding: utf-8 -*-
"""Stdlib-only tests for the release packaging (packaging/*.py).

These are the guards that decide whether an archive QwenPaw can actually
install gets published, so each rejection path is asserted, not just the happy
one. build() and verify() are pointed at throwaway repos; nothing here writes
into the real dist/.
"""

import importlib.util
import io
import json
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parent.parent


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, _ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


packaging = _load("build_plugin_zip", "packaging/build_plugin_zip.py")
release_notes = _load("release_notes", "packaging/release_notes.py")


def _make_repo(root: Path, symlink: bool = False) -> Path:
    """A minimal well-formed plugin tree, laid out like this repo."""
    (root / "backend").mkdir(parents=True)
    (root / "plugin.json").write_text(
        json.dumps({
            "id": "listwise-rank",
            "version": "9.9.9",
            "entry": {"backend": "backend/main.py"},
        }),
        encoding="utf-8",
    )
    (root / "backend" / "main.py").write_text("# entry\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_x.py").write_text("#\n", encoding="utf-8")
    if symlink:
        (root / "backend" / "link.py").symlink_to(root / "backend" / "main.py")
    return root


def _archive(path: Path, members: dict) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, body in members.items():
            archive.writestr(name, body)
    return path


_MANIFEST = json.dumps({"id": "listwise-rank", "version": "9.9.9",
                        "entry": {"backend": "backend/main.py"}})


class CollectTest(unittest.TestCase):
    def test_dev_only_payload_is_left_out_of_the_archive(self):
        names = {p.relative_to(_ROOT).as_posix() for p in packaging.collect(_ROOT)}
        self.assertIn("plugin.json", names)
        self.assertIn("backend/tool_impl.py", names)
        self.assertIn("docs/CHANGELOG.md", names)  # README links into it
        for dev_only in ("tests", ".github", "packaging", "dist"):
            self.assertFalse(
                any(n == dev_only or n.startswith(dev_only + "/") for n in names),
                f"{dev_only}/ must not ship inside the plugin",
            )

    def test_pycache_never_ships(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            cache = repo / "backend" / "__pycache__"
            cache.mkdir()
            (cache / "main.cpython-312.pyc").write_bytes(b"\x00")
            names = {p.relative_to(repo).as_posix() for p in packaging.collect(repo)}
        self.assertNotIn("backend/__pycache__/main.cpython-312.pyc", names)


class BuildTest(unittest.TestCase):
    def test_builds_the_single_top_level_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            with mock.patch.object(packaging, "REPO", repo):
                out = packaging.build("listwise-rank", "9.9.9")
            names = zipfile.ZipFile(out).namelist()
        self.assertEqual(sorted(names), [
            "listwise-rank/backend/main.py",
            "listwise-rank/plugin.json",
        ])

    def test_rebuilding_the_same_tree_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            with mock.patch.object(packaging, "REPO", repo):
                packaging.build("listwise-rank", "9.9.9")
                first = (repo / "dist" / "listwise-rank-qwenpaw-plugin.zip").read_bytes()
                packaging.build("listwise-rank", "9.9.9")
                second = (repo / "dist" / "listwise-rank-qwenpaw-plugin.zip").read_bytes()
        self.assertEqual(first, second)

    def test_symlinked_member_aborts_the_build(self):
        """extractall turns a symlink member into a file holding its target path."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp), symlink=True)
            with mock.patch.object(packaging, "REPO", repo):
                with self.assertRaises(SystemExit) as caught:
                    packaging.build("listwise-rank", "9.9.9")
        self.assertIn("symlink", str(caught.exception))
        self.assertFalse((repo / "dist").exists())

    def test_missing_manifest_aborts_the_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            (repo / "plugin.json").unlink()
            with mock.patch.object(packaging, "REPO", repo):
                with self.assertRaises(SystemExit) as caught:
                    packaging.build("listwise-rank", "9.9.9")
        self.assertIn("plugin.json", str(caught.exception))


class VerifyTest(unittest.TestCase):
    def _verify(self, members: dict, plugin_id="listwise-rank", count=None):
        with tempfile.TemporaryDirectory() as tmp:
            path = _archive(Path(tmp) / "a.zip", members)
            args = (path, plugin_id, "9.9.9", count if count is not None else len(members))
            with self.assertRaises(SystemExit) as caught:
                packaging.verify(*args)
            return str(caught.exception)

    def test_rejects_two_top_level_directories(self):
        reason = self._verify({
            "listwise-rank/plugin.json": _MANIFEST,
            "other/README.md": "x",
        })
        self.assertIn("top level", reason)

    def test_rejects_a_top_level_named_after_something_else(self):
        """GitHub's own source archive installs, but lands under plugins/<repo>-<tag>/."""
        reason = self._verify({"qwenpaw-consensus-rank-9.9.9/plugin.json": _MANIFEST})
        self.assertIn("top level", reason)

    def test_rejects_an_archive_missing_the_declared_entry_target(self):
        reason = self._verify({"listwise-rank/plugin.json": _MANIFEST})
        self.assertIn("backend/main.py", reason)

    def test_rejects_a_truncated_archive(self):
        reason = self._verify(
            {"listwise-rank/plugin.json": _MANIFEST,
             "listwise-rank/backend/main.py": "#"},
            count=99,
        )
        self.assertIn("expected 99", reason)

    def test_accepts_a_wellformed_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _archive(Path(tmp) / "a.zip", {
                "listwise-rank/plugin.json": _MANIFEST,
                "listwise-rank/backend/main.py": "#",
            })
            packaging.verify(path, "listwise-rank", "9.9.9", 2)


class TagGuardTest(unittest.TestCase):
    def test_a_tag_ahead_of_the_manifest_aborts_before_building(self):
        with mock.patch.object(sys, "argv", ["x", "--tag", "v0.0.0"]):
            with self.assertRaises(SystemExit) as caught:
                packaging.main()
        self.assertIn("does not match", str(caught.exception))

    def test_the_shipped_manifest_builds_for_its_own_version(self):
        """End to end on the real tree, in a copy so dist/ stays where it belongs."""
        version = json.loads((_ROOT / "plugin.json").read_text(encoding="utf-8"))["version"]
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            shutil.copytree(
                _ROOT, repo,
                ignore=shutil.ignore_patterns("dist", "__pycache__", ".git", ".pyc"),
            )
            with mock.patch.object(packaging, "REPO", repo), \
                    mock.patch.object(sys, "argv", ["x", "--tag", f"v{version}"]), \
                    mock.patch("sys.stdout", new_callable=io.StringIO):
                packaging.main()
            names = zipfile.ZipFile(
                repo / "dist" / "listwise-rank-qwenpaw-plugin.zip").namelist()
        self.assertIn("listwise-rank/plugin.json", names)
        self.assertIn("listwise-rank/backend/tool_impl.py", names)
        self.assertEqual([n for n in names if n.startswith("listwise-rank/tests/")], [])


class ReleaseNotesTest(unittest.TestCase):
    _CHANGELOG = (
        "# Changelog\n"
        "\n"
        "### v1.5.0 (2026-10-01)\n"
        "\n"
        "- new thing\n"
        "\n"
        "### v1.4.9 (2026-09-30)\n"
        "\n"
        "- old thing\n"
    )

    def _notes(self, tag: str):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "CHANGELOG.md"
            path.write_text(self._CHANGELOG, encoding="utf-8")
            argv = ["release_notes.py", tag, "--changelog", str(path)]
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch("sys.stdout", new_callable=io.StringIO) as out, \
                    mock.patch("sys.stderr", new_callable=io.StringIO) as err:
                release_notes.main()
                return out.getvalue(), err.getvalue()

    def test_date_suffixed_heading_is_matched(self):
        """Headings read "### v1.4.7 (2026-09-22)" — the tag alone is a prefix."""
        notes, _ = self._notes("v1.4.9")
        self.assertEqual(notes.strip(), "- old thing")

    def test_section_stops_at_the_next_version(self):
        notes, _ = self._notes("v1.5.0")
        self.assertEqual(notes.strip(), "- new thing")

    def test_unknown_tag_still_emits_notes_and_warns(self):
        notes, err = self._notes("v9.9.9")
        self.assertIn("docs/CHANGELOG.md", notes)
        self.assertIn("no ### v9.9.9 section", err)


if __name__ == "__main__":
    unittest.main()
