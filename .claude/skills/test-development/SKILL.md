---
name: test-development
description: How to write, organise, run and interpret capturekit tests — unit vs platform integration vs performance tests, pytest markers and skip rules, deterministic fixture windows and tone players, pixel verification for exclusive window capture, and regression triage. Use when adding tests or analysing test failures.
---

# Test development and regression analysis

## Layout
```
tests/unit/            pure Python, run everywhere (timeline, aligner, config,
                       ffmpeg command builder, region validation)
tests/windows/         marker windows (+gpu/audio/ffmpeg as needed)
tests/linux/           markers x11 / xvfb
tests/perf/            marker slow; prints measured numbers, asserts loose floors
tests/fixtures/        helper programs run as subprocesses
```

## Markers (pyproject `[tool.pytest.ini_options]`)
`windows, x11, xvfb, gpu, audio, ffmpeg, slow, interactive`.
`conftest.py` auto-skips when requirements are missing **with a reason**.
Report skipped counts explicitly: a skip is not a pass.

## Commands
```
.venv/Scripts/python -m pytest                       # all available
.venv/Scripts/python -m pytest tests/unit -q         # fast
.venv/Scripts/python -m pytest -m "windows and not slow" -rs
.venv/Scripts/python -m pytest -m slow -s            # perf, shows numbers
wsl -d Ubuntu -- bash -lc 'cd /mnt/d/... && python3 -m pytest -m xvfb -rs'
```
Always use `-rs` when reporting so skip reasons are visible.

## Exclusive-window-capture verification pattern
1. Spawn `tests/fixtures/color_window.py --color 0,200,0 --title <uuid>` (target).
2. Spawn an occluder window (`--color 200,0,0 --topmost`) overlapping it.
3. Find target by title, capture via `WindowCapture`.
4. Assert ≥ 99% of client-area pixels equal the target colour (±2 per channel)
   and 0% match the occluder colour.
5. Also verify a *desktop* capture of the same area does show the occluder —
   proves the test is meaningful (the occluder really covers the target).

## Regression analysis procedure
1. Re-run the failing test alone with `-x -vv -s`.
2. Determine flakiness: `pytest --count`-style loop (`for i in 1..10`).
3. Bisect with `git bisect run python -m pytest <test>` when history exists.
4. Write the regression test first, then fix (bug-investigation skill).
5. Record root cause in `docs/PROGRESS.md` test log if non-obvious.
