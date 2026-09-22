#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Print the changelog section for a release tag, for use as release notes."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", help="release tag, e.g. v1.4.7")
    parser.add_argument(
        "--changelog",
        default=str(REPO / "docs" / "CHANGELOG.md"),
        help="changelog to read (default: docs/CHANGELOG.md)",
    )
    args = parser.parse_args()

    try:
        lines = Path(args.changelog).read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    heading = f"### {args.tag}"
    notes: list[str] = []
    for index, line in enumerate(lines):
        # Headings carry a date suffix: "### v1.4.7 (2026-09-22)".
        if line != heading and not line.startswith(heading + " "):
            continue
        for body in lines[index + 1:]:
            if body.startswith("### ") or body.startswith("## "):
                break
            notes.append(body)
        break
    else:
        sys.stderr.write(f"no {heading} section in {args.changelog}\n")
    text = "\n".join(notes).strip() or f"See `docs/CHANGELOG.md` for {args.tag}."
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
