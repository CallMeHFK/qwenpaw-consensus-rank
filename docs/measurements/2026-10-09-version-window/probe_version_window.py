# -*- coding: utf-8 -*-
"""Probe the installed QwenPaw's plugin version-window check.

Covers the three claims v1.4.9's fix rests on, against the host that is
actually installed — not a vendored copy:

1. the window is left-closed / right-open (`>=min, <max`);
2. today the host enforces only `>= min` (upper bound commented out);
3. therefore the shipped `>=1.1.6, <2.3.0` loads on 2.2.1, the old
   `<2.1.0` also loads *today*, but both `<2.1.0` and a `2.2.1` ceiling
   become load-time refusals the day the full-range check is restored.

Run:  ~/miniforge3/bin/python probe_version_window.py
"""

from types import SimpleNamespace

from packaging.version import Version
from qwenpaw.__version__ import __version__
from qwenpaw._version_compat import check_plugin_version_compat


def probe(min_s, max_s):
    manifest = SimpleNamespace(
        qwenpaw_version=SimpleNamespace(min=min_s, max=max_s),
        min_version=min_s,
        max_version=max_s,
    )
    return check_plugin_version_compat(manifest)


def restored_full_range_refuses(min_s, max_s):
    """The commented-out check in check_plugin_version_compat, verbatim."""
    current = Version(__version__)
    return current < Version(min_s) or current >= Version(max_s)


if __name__ == "__main__":
    print("host:", __version__)
    print("shipped >=1.1.6,<2.3.0:", probe("1.1.6", "2.3.0"))
    print("v1.4.8's >=1.1.6,<2.1.0:", probe("1.1.6", "2.1.0"))
    print("restored check refuses <2.1.0:",
          restored_full_range_refuses("1.1.6", "2.1.0"))
    print("restored check refuses max=2.2.1 (right-open):",
          restored_full_range_refuses("1.1.6", "2.2.1"))
    print("restored check accepts <2.3.0:",
          not restored_full_range_refuses("1.1.6", "2.3.0"))
