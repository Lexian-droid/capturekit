# Linux X11 and Xvfb

**Status: in development.** The Windows backends are complete; the Linux
backend design is researched (see `docs/research/linux-x11-and-audio.md`)
and implementation is the next milestone. Until it lands, capturekit raises
`CapabilityUnavailableError` on Linux. Nothing below is claimed as working
yet.

## Planned design

* Every API takes `display: str | None`. An explicit display (`":99"`) is
  always honoured, independent of the desktop session.
* Monitors: RandR 1.5 `GetMonitors` → CRTC enumeration → Xinerama → root
  geometry (Xvfb exposes one output named `screen`).
* Monitor/region capture: MIT-SHM `XShmGetImage` (local servers), falling
  back to `XGetImage`.
* Window capture: XComposite *automatic* redirection + `NameWindowPixmap`,
  re-named after every resize; unmapped/iconified windows have no pixmap and
  are reported, never replaced with desktop pixels. FFmpeg `x11grab
  -window_id` is **not** used: it reads the window area without composite
  redirection and is not occlusion-free.
* Wayland-only sessions → `UnsupportedDisplayServerError`; an XWayland
  default display is not treated as desktop capture.
* Audio: PulseAudio / pipewire-pulse via `parec` (monitor sources, and
  `--monitor-stream` for a single application stream).

## Xvfb quick reference

```
Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp &
python -c "import capturekit as ck; print(ck.list_monitors(display=':99'))"
```
Xvfb provides no audio devices.
