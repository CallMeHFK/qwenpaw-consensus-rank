# Version-window probe — 2026-10-09

Evidence behind the v1.4.9 fix (`qwenpaw_version.max: 2.1.0 → 2.3.0`). The
claims are about *host* behaviour, so they are probed against the installed
QwenPaw package, not reasoned from this repo.

## Setup

- Host: QwenPaw **2.2.1**, `~/miniforge3/lib/python3.12/site-packages/qwenpaw/`
- Source read: `_version_compat.py` — docstring states the window is
  left-closed / right-open (`>=min, <max`); `check_plugin_version_compat`
  enforces only `current < min_v`, with the full-range check commented out and
  marked "Restore … when re-enabling full range checks".
- Probe: `probe_version_window.py` (this directory) calls the host's real
  `check_plugin_version_compat`, then replays the commented-out full-range
  check verbatim to show what re-enabling would do.

## Output

```
host: 2.2.1
shipped >=1.1.6,<2.3.0: (True, '')
v1.4.8's >=1.1.6,<2.1.0: (True, '')
restored check refuses <2.1.0: True
restored check refuses max=2.2.1 (right-open): True
restored check accepts <2.3.0: True
```

## Read

1. **Right-open is real**: with the full-range check replayed, a ceiling of
   `2.2.1` refuses host 2.2.1 — writing the tested build as `max` excludes the
   very host the range was measured on. `2.3.0` is the honest ceiling for the
   2.2 line.
2. **The v1.4.8 bug is inert today, not harmless**: `<2.1.0` loads fine *now*
   (upper bound disabled), but the restored check refuses it. The shipped
   `2.3.0` survives both regimes.
3. Regression guard: `tests/test_packaging.py::VersionWindowTest` pins
   `min <= 2.2.1 < max` so the window cannot silently shrink below the tested
   host again.
