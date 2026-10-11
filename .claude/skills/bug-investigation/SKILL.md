---
name: bug-investigation
description: Root-cause analysis procedure for capturekit bugs — crashes in ctypes/COM code, hangs/deadlocks in pipelines, black or stale frames, A/V desync, encoder failures and resource leaks. Use when something fails and the cause is not immediately obvious.
---

# Bug investigation and root-cause analysis

## Procedure
1. **Reproduce** minimally (script in scratchpad or a failing test). Record
   exact command, environment (OS build, GPU, FFmpeg version).
2. **Classify** and follow the matching playbook below.
3. **Hypothesise → test one variable at a time.** Write down what each
   experiment rules out.
4. **Fix the cause, not the symptom** (no retries/sleeps to hide races).
5. **Regression test** that fails before and passes after.
6. **Capture the lesson** in the owning skill's quirks section.

## Playbooks
### Interpreter crash (access violation, no traceback)
- Run `python -X faulthandler`; the native stack points at the call.
- Check: vtable index, `argtypes` (pointer vs value struct, 64-bit handles
  as `c_void_p` not `c_int`), lifetime (released COM object, numpy view of
  unmapped memory, ctypes callback garbage-collected while native code still
  holds it — keep a strong reference on the owning object).

### Hang / deadlock
- `faulthandler.dump_traceback_later(30)` or `py-spy dump --pid`.
- Typical: FFmpeg waiting on an unconnected pipe; writer blocked on full pipe
  while FFmpeg blocks on stderr (must be drained); queue `put()` without
  timeout during shutdown; joining a thread from itself.

### Black / stale frames
- WGC: protected window (display affinity), minimized window, wrong HWND
  (host vs content window), DRM. DXGI: wrong adapter, ACCESS_LOST not handled,
  frame with `LastPresentTime == 0` (pointer-only update — no new image).

### Desync
- Compare video slot count vs audio sample count in `stats`; check
  `audio_silence_inserted_ms` / `trimmed_ms`; see av-sync skill.

### Leaks
- Run the capture open/close loop test (`tests/windows/test_lifecycle.py`)
  watching handle count (`psutil.Process().num_handles()`) and GPU memory.
