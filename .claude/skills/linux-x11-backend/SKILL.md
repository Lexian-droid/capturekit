---
name: linux-x11-backend
description: How to develop and test capturekit's Linux X11/Xvfb video backend — explicit display selection, XRandR monitor discovery with fallbacks, XShm capture, XComposite exclusive window capture, EWMH enumeration, X error handling, Wayland/XWayland detection, and running tests against private Xvfb servers in WSL. Use when editing src/capturekit/_backends/linux/.
---

# Linux X11 / Xvfb backend

Reference (sources + API sequences): `docs/research/linux-x11-and-audio.md`.

## Display selection rules
- Every public call accepts `display: str | None`. `None` → `$DISPLAY`, but
  only after Wayland checks; an explicit string (`":99"`) bypasses the host
  session check and targets that server directly.
- Wayland-only (`WAYLAND_DISPLAY` set, no usable X server requested) →
  `UnsupportedDisplayServerError`. If the default `$DISPLAY` is XWayland
  (server has the `XWAYLAND` extension) → also unsupported for desktop
  capture; say so in the message. WSLg sets `WAYLAND_DISPLAY=wayland-0` and
  `DISPLAY=:0` (XWayland) — tests must always pass explicit Xvfb displays.

## Monitors
RRGetMonitors (RandR ≥ 1.5) → CRTC enumeration (RandR ≥ 1.2) → Xinerama →
root window geometry. Xvfb exposes one CRTC/output named "screen"; with no
CRTCs GetMonitors returns empty — fall back, never crash.

## Capture
- Monitor/region: `XShmGetImage` on the root window when MIT-SHM is present
  (local connections only), else `XGetImage`. Mark the SHM segment
  `IPC_RMID` immediately after attach; set `image.data = NULL` before
  `XDestroyImage`.
- Window: `XCompositeRedirectWindow(dpy, w, CompositeRedirectAutomatic)`
  (never Manual — a compositor may own it), then `XCompositeNameWindowPixmap`
  → capture from the pixmap. Re-name the pixmap after every ConfigureNotify
  (resize) and MapNotify. Use the top-level *frame* or the client window per
  `client_area_only`.
- Unmapped/iconified windows: pixmap is not available (BadMatch). Report
  `TargetUnavailable`-style state; never substitute desktop pixels.
- FFmpeg x11grab `-window_id` is NOT exclusive capture (plain GetImage on the
  window; obscured areas undefined). Do not use it as a window backend.

## Xlib safety
- Install `XSetErrorHandler` (default handler calls `exit()`); handler is
  process-global — record errors per-display, check after `XSync`.
- One `Display*` per capture thread; libX11 ≥ 1.8 auto-calls `XInitThreads`.
- 32-bit format properties come back as C `long` (8 bytes on x86-64).

## Enumeration
`_NET_CLIENT_LIST` (EWMH) → title `_NET_WM_NAME` (UTF8_STRING) / `WM_NAME`,
pid `_NET_WM_PID` or XRes `XResQueryClientIds`, `WM_CLASS`. No WM (bare
Xvfb): `XQueryTree(root)` children that are mapped InputOutput windows.

## Testing in WSL
Packages needed (ask user before installing):
`xvfb x11-utils x11-apps ffmpeg pulseaudio-utils libpulse0`.
```
wsl -d Ubuntu -- bash -lc "Xvfb :99 -screen 0 1280x720x24 -nolisten tcp & sleep 1; cd '/mnt/d/User/Documents/Projects/Python Projects/Cross-Platform Python Screen and Audio Capture Library' && python3 -m pytest -m xvfb -rs; kill %1"
```
Tests start their own Xvfb via the `xvfb_display` fixture on a free display
number; never reuse :0 in WSL.
