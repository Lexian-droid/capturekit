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
- Docs: README, guides (api, windows, audio, encoding, performance,
  troubleshooting, linux-status), examples, benchmark scripts.

### Latest test results (executed)
`.venv/Scripts/python -m pytest -q -rs` on Windows 11 26200 / RTX 4050 /
FFmpeg 7.1 → **56 passed, 0 skipped, 0 failed** (76 s). Includes:
unit (27), Windows video (12, incl. occlusion pixel tests), audio (6, incl.
process isolation 440 Hz vs 1 kHz), recording (11, incl. 24/30/60 fps, pause,
encoder failure/crash, window closed, A/V sync beacon).
`ruff check`, `ruff format --check`, `mypy --strict` (src): clean.
Linux: **no tests executed** (no backend yet; WSL lacks Xvfb).

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

## Next tasks (priority order)
1. **Linux X11 backend** (monitors/XShm, XComposite windows, explicit
   display, Wayland detection) — needs Xvfb in WSL (`sudo apt install xvfb
   x11-utils x11-apps ffmpeg pulseaudio-utils`, user approval required).
2. Linux audio (parec monitor sources, `--monitor-stream` per-app capture,
   stream add/remove tracking).
3. A/V sync beacon with low-latency WASAPI render (current beacon's
   `PlaySound` latency is unquantified; measured offset +41..+66 ms).
4. Multi-monitor / negative-coordinate validation (needs second display or
   an indirect display driver).
5. Performance: staging texture ring; GPU BGRA→NV12.
6. Audio device disconnect test (manual: unplug headset during recording).
7. CI workflow (GitHub Actions windows-latest: unit tests only — no GPU).

## Blockers
- Xvfb/PulseAudio not installed in WSL Ubuntu; installing needs approval.
- No Intel/AMD GPU: QSV/AMF encoders cannot be tested here.
- Single monitor: multi-monitor paths untested.

## Known limitations
See `docs/capabilities.md` (Untested / Experimental rows).
