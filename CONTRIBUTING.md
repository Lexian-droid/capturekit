# Contributing

## Setup

```
python -m venv .venv
.venv/Scripts/python -m pip install -e .[dev]      # Windows
.venv/bin/python -m pip install -e .[dev]          # Linux
```

## Checks (all must pass before a change is merged)

```
ruff check src tests && ruff format --check src tests
mypy                        # strict, src/
pytest -rs                  # skips print their reason
```

Hardware tests are marked (`windows`, `gpu`, `audio`, `ffmpeg`, `x11`,
`xvfb`, `slow`, `interactive`) and skip when the requirement is missing. A
skipped test is **not** a passing test: when you report results, include the
pass/skip/fail counts and skip reasons. Integration tests open visible test
windows and play short quiet tones; set `CAPTUREKIT_NO_WINDOWS=1` to skip the
window tests.

## Rules

* Read `CLAUDE.md` (applies to humans too) and `docs/architecture.md`.
* Native bindings: verify IIDs/vtable order against the SDK headers and add
  them to `.claude/skills/windows-capture-backend/reference/vtables.md`.
* Never present window capture that crops the desktop as window capture, and
  never fall back from per-process audio to system audio.
* Update `docs/capabilities.md` and the guides with any behaviour change;
  claims must be backed by executed tests or marked Untested/Experimental.
* Benchmarks: include command, machine, versions and date.
