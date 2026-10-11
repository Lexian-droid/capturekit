# capturekit — project rules for Claude Code

Python-first library for monitor, **exclusive window**, and audio capture with
optional FFmpeg recording. Read `docs/PROGRESS.md` first in every session —
it holds current state, next tasks, blockers and the latest real test results.

## Platforms
- Windows 10/11: primary. Video: DXGI Desktop Duplication (monitors), Windows
  Graphics Capture (windows). Audio: WASAPI (mic, endpoint loopback, process
  loopback ≥ build 20348).
- Linux X11 + explicit Xvfb displays (`":99"`): secondary. XShm/XRandR,
  XComposite for windows, PulseAudio/PipeWire audio.
- Wayland-only session → `UnsupportedDisplayServerError`. Never capture the
  XWayland root and call it desktop support.
- macOS: out of scope. Do not write macOS code.

## Architecture rules (details: `docs/architecture.md`)
- Layout: `src/capturekit/` public API; `_pipeline/` timing/sync/queues;
  `encoding/` FFmpeg; `_backends/windows|linux/` native code via **ctypes**.
- `import capturekit` must work on every platform; backends load lazily.
- Window capture must target the window resource (WGC `CreateForWindow`,
  XComposite pixmap). **Never** crop a desktop capture and call it window
  capture. No BitBlt/PrintWindow as primary path.
- Process audio must never silently fall back to system audio.
- One clock: `capturekit._pipeline.clock.now_ns()` (QPC on Windows,
  CLOCK_MONOTONIC on Linux). Schedule with absolute deadlines.
- Bounded queues everywhere; count and report every dropped/duplicated frame
  and every inserted/trimmed audio sample.
- No blocking work on the caller's thread; callbacks run on a dispatcher thread.
- Errors are never swallowed: record, surface via `on_error`, re-raise on `stop()`.
- No Rust/C++ unless measured need; then isolate + document in `docs/decisions.md`.

## Coding standards
- Python ≥ 3.10, full type hints, `mypy --strict` clean for `src/`.
- `ruff check` + `ruff format`. Line length 100.
- ctypes: always set `argtypes`/`restype`; check every HRESULT via
  `_com.check_hr`; release COM pointers deterministically (`ComPtr.release`).
- Public dataclasses are `frozen=True, slots=True`.
- Comments explain *why* (platform quirks, API contracts), not what.

## Dependencies
- Runtime: `numpy` only. Optional: `imageio-ffmpeg` (bundled FFmpeg binary).
- Dev: pytest, mypy, ruff. Add a dependency only with a stated purpose in
  `docs/decisions.md`.

## Testing (details: `.claude/skills/test-development`)
- `pytest` from the venv: `.venv/Scripts/python -m pytest` (Windows).
- Markers: `windows`, `x11`, `xvfb`, `gpu`, `audio`, `ffmpeg`, `slow`,
  `interactive`. Hardware tests skip when unavailable — **a skip is not a pass**.
- Window-capture tests must use the deterministic fixture windows in
  `tests/fixtures/` and verify pixels under occlusion.
- Never report a test as passing unless it ran. Never fabricate benchmarks;
  benchmark numbers in docs must cite the command, machine and date.

## Making changes
1. Read `docs/PROGRESS.md` and the relevant skill before touching a subsystem.
2. Verify native API facts against Microsoft Learn / X.org / PipeWire docs
   (skill `native-api-research`); record non-obvious findings in the skill.
3. Small, independently testable increments; run the affected tests.
4. Update docs (`docs/capabilities.md`, guides) in the same change.
5. Update `docs/PROGRESS.md` (done / next / blockers / test results).

## Definition of done
Feature implemented on a real backend, tests written and *executed* on the
target platform (or explicitly listed as untested in `docs/capabilities.md`),
mypy + ruff clean, docs + capability matrix updated, no unbounded buffers,
resources released on error paths.

## Preserving context across sessions
- `docs/PROGRESS.md`: authoritative status log — update before ending work.
- `docs/decisions.md`: ADR-style decisions with reasons.
- Skills in `.claude/skills/*/SKILL.md`: reusable procedures and platform
  quirks. When you learn a reusable lesson, add it to the matching skill.
- Specialist reviewers live in `.claude/agents/`. Agents never delegate to
  other agents.

## Safety
Ask before: destructive git/file operations, system-wide audio or display
configuration changes, installing system packages, publishing. No stealth
recording, no bypassing capture exclusion/DRM, no shell-interpolated commands.
