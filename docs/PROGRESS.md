# Development progress

Authoritative status log. Update at the end of every working session.

## Current state (2026-10-11)

### Done
- Agentic infrastructure: `CLAUDE.md`, 10 skills (`.claude/skills/`), 6
  specialist agents (`.claude/agents/`), architecture spec, decision log,
  capability matrix, Linux research reference.
- Package skeleton (`src/capturekit`, hatchling, typed, numpy-only runtime).
- Core pipeline: clock, pause-aware timeline, CFR pacer, bounded frame queue
  with duplicate merging + buffer pool, audio aligner (gap/overlap/drift),
  mixer, callback dispatcher.
- Windows video: DXGI Desktop Duplication (region, software cursor, access-lost
  recovery), Windows Graphics Capture for windows (client area, crop, resize,
  closure, border/cursor options) and monitors.
- Windows audio: WASAPI microphone, endpoint loopback, process loopback
  (process tree), device + session enumeration.
- FFmpeg encoding: discovery, probe-validated encoder selection, MKV +
  fragmented MP4, named-pipe audio tracks, crash handling.
- Recorder: start/pause/resume/stop, separate/mixed tracks, stats,
  target-lost policies, callbacks-only mode.
- Docs: README, guides (api, windows, linux, audio, encoding, performance,
  troubleshooting), examples, benchmark scripts.
- Linux X11/Xvfb: display resolution + Wayland rejection, RandR monitors,
  MIT-SHM capture, XComposite exclusive window capture (Damage, resize,
  unmap, destroy), EWMH/XQueryTree enumeration, X-Resource PIDs, error
  handlers that never exit() for capturekit's own connections.
- Linux audio (PulseAudio protocol): devices, sessions, microphone, system
  monitor, per-application `parec --monitor-stream` with live stream tracking.

### Latest test results (executed)
`.venv/Scripts/python -m pytest -q -rs` on Windows 11 26200 / RTX 4050 /
FFmpeg 7.1 → **75 passed, 0 skipped, 0 failed** (≈2 min; after review fixes). Initial run: 56. Includes:
unit (27), Windows video (12, incl. occlusion pixel tests), audio (6, incl.
process isolation 440 Hz vs 1 kHz), recording (11, incl. 24/30/60 fps, pause,
encoder failure/crash, window closed, A/V sync beacon).
`ruff check`, `ruff format --check`, `mypy --strict` (src): clean.
Linux (WSL2 Ubuntu 26.04, Python 3.14, private Xvfb servers, WSLg
PulseAudio 17): `PYTHONPATH=src python3 -m pytest -rs` → **58 passed, 0
skipped** (unit 37, X11 14, Pulse 7). Windows suite after Linux work: 75
passed, 20 skipped (Linux-only tests, with reasons).

### Independent review (2026-10-11, architecture/security + test reviewers)
Fixed: start/stop race leaving audio captured after a "stopped" recorder;
backend-thread start timeout leaking a live capture session; CONTINUE policy
ignored for audio device loss; shutdown stalls (~30 s per audio track when
FFmpeg never started) and unbounded `stop(timeout)`; shutdown stages not
isolated (FFmpeg could be orphaned); `shutil.which` executing an
`ffmpeg.exe` from the current directory; pipe close blocking on
FlushFileBuffers; misleading error after pipe abort; DXGI keeping an
invalid duplication; unresolved futures after capture close; malformed
cursor shapes fatal; WGC frame leak on error; audio chunks mutable while
shared; stray output file after a failed start; wrong `on_audio`
timestamps when silence and data were emitted together (found by a new test).
New tests: fake-backend recorder lifecycle suite (platform independent),
multi-source/mix-mode track content, codec/container/fMP4, encoder
auto-selection + fallback, isolated pause test, distractor positive control,
WGC monitor pixels, full-window frame, −500 ppm drift.

### Bugs found and fixed this session (root causes)
1. DXGI frames with `LastPresentTime == 0` have an all-zero texture → only
   treat present-time frames as images; loop until timeout.
2. FFmpeg probing raw pipes for 5 s → 229 dropped frames at start →
   `-probesize 32 -analyzeduration 0`.
3. Aligner treated start/resume offsets as drift → exact re-anchor per
   timeline segment.
4. Interpreter crash after capturing a destroyed window → MTA torn down by
   last `RoUninitialize` → process-wide `CoIncrementMTAUsage` (D8).
5. ComObject freed inside its own Release thunk → bounded graveyard.
6. Static window after resize never re-presents → session refresh (D9).

### Linux findings (root causes)
1. `XShmGetImage` on a pixmap clears the XImage colour masks → validate the
   pixel format from the Visual.
2. Xlib's default IO error handler calls `exit(1)` before the per-display
   exit handler → install a returning IO handler (chained for foreign
   displays) + `XSetIOErrorExitHandler`.
3. Test race: window destruction is noticed asynchronously.

## Next tasks (priority order)
1. Validate Linux on a native X11 desktop with a WM + compositor (frame
   capture, `_NET_WM_STATE_HIDDEN`, Automatic redirection coexistence) and
   on a PipeWire system (`--monitor-stream` via pipewire-pulse).
2. Linux cursor capture (XFixes) and a multi-monitor Xvfb (+xinerama) test.
3. A/V sync beacon with low-latency WASAPI render (current beacon's
   `PlaySound` latency is unquantified; measured offset +41..+66 ms).
4. Multi-monitor / negative-coordinate validation (needs second display or
   an indirect display driver).
5. Performance: staging texture ring; GPU BGRA→NV12.
6. Audio device disconnect test (manual: unplug headset during recording).
7. CI workflow (GitHub Actions windows-latest: unit tests only — no GPU).

## Blockers
- No Intel/AMD GPU: QSV/AMF encoders cannot be tested here.
- Single monitor: multi-monitor paths untested.

## Known limitations
See `docs/capabilities.md` (Untested / Experimental rows).
