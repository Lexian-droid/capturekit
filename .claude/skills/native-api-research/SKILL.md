---
name: native-api-research
description: Procedure for researching and verifying Windows/X11/PipeWire native API behaviour (signatures, vtable order, GUIDs, minimum OS builds, undocumented behaviour) before implementing it in capturekit. Use before writing any new ctypes binding or relying on a platform behaviour.
---

# Native API research and verification

Goal: every native fact the code relies on is either (a) cited from an
authoritative source or (b) verified by an experiment on this machine — and
recorded so nobody re-derives it.

## Source priority
1. Microsoft Learn API reference (`learn.microsoft.com/windows/win32/api/...`,
   `learn.microsoft.com/uwp/api/...`) — signatures, minimum builds, contracts.
2. Windows SDK headers (vtable order is the declaration order in the header,
   including inherited interfaces). Mirror: `github.com/tpn/winsdk-10`, or
   the `windows-rs`/`win32metadata` repos.
3. Official samples: `microsoft/Windows-classic-samples`
   (ApplicationLoopback), `robmikh/Win32CaptureSample` (WGC, by the WGC
   owner), `microsoft/Windows-universal-samples` (ScreenCaptureforHWND).
4. X.org protocol specs (`gitlab.freedesktop.org/xorg/proto/xorgproto`),
   libX11/libXcomposite man pages, PipeWire/PulseAudio docs.
5. OBS Studio sources (libobs-winrt, win-capture, linux-capture) — reference
   for real-world quirks, not authority.

Blog posts / StackOverflow are leads only. Mark unverified claims
`UNVERIFIED` in docs until tested.

## Procedure for a new COM / WinRT binding
1. Find the interface IID and full inheritance chain in the header.
2. Count vtable slots: IUnknown = 0..2; IInspectable adds 3..5; then the
   interface's own methods in declaration order (WinRT property getters
   `get_X`, setters `put_X`, events `add_X`/`remove_X` each take a slot).
3. Write the binding in `_backends/windows/` using `ComPtr.call(index,
   restype, argtypes, *args)` and add the index to
   `windows-capture-backend/reference/vtables.md`.
4. Experiment: minimal script in the scratchpad that calls the method and
   checks HRESULT == 0 and plausible output. Never trust an index untested —
   a wrong index usually returns garbage or crashes the interpreter.
5. Parameterised WinRT interfaces (e.g. `TypedEventHandler<A,B>`): compute
   the IID with `capturekit._backends.windows._winrt.pinterface_iid`
   (UUIDv5 of the signature string, namespace
   `11f47ad5-7b73-42c0-abae-878b1e16adee`) and cross-check with a known value.

## Procedure for an OS-version-dependent feature
1. Record the minimum build from Learn's Requirements table.
2. Implement runtime detection by *trying* the API (QI for the interface,
   `ApiInformation`-style checks) — build numbers are a hint only.
3. Expose the result in `get_capabilities()` with a human-readable reason.

## Recording findings
- Verified facts and quirks → the relevant backend skill's reference file.
- Design-affecting facts → `docs/decisions.md`.
- Unknown/unverifiable behaviour → `docs/capabilities.md` "untested" column.

## Verified facts log (append; include date + build)
- 2026-10-11, Win11 26200: `time.perf_counter_ns()` == QPC ns (delta ~1 µs);
  QPC frequency 10 MHz.
- `AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS` min client: Windows 10 build 20348
  (Learn). Mode 0 = include target process tree, 1 = exclude.
- `GraphicsCaptureSession` 1803; `IsCursorCaptureEnabled` 2004 (19041);
  `CreateForWindow` interop 1903. `IsBorderRequired`: 20348/Win11.
- Microsoft does not document WGC behaviour for minimized windows → measured
  2026-10-11: no frames while minimized, resume on restore
  (`tests/windows/test_video.py::test_window_minimized_behaviour`).
- Guessing IIDs from memory failed once (IDXGIOutput1). Always copy IIDs from
  headers (MinGW-w64 `mingw-w64-headers/include/*.idl` is fetchable as raw text).
