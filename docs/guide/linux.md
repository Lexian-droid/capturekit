# Linux X11 and Xvfb

Tested on Ubuntu 26.04 (WSL2) with Xvfb, libX11 1.8.13, Python 3.14 and the
WSLg PulseAudio 17 server. Native X11 desktops with a window manager or
compositor and PipeWire systems are **untested** (see
[capabilities](../capabilities.md)).

## Requirements

* X client libraries: `libx11-6 libxext6 libxcomposite1 libxdamage1 libxrandr2 libxres1 libxinerama1`
  (present on virtually every desktop install).
* Audio: `pulseaudio-utils` (`pactl`, `parec`) talking to PulseAudio or
  pipewire-pulse.
* Recording: FFmpeg.
* Tests: `xvfb x11-utils`.

## Choosing the X server

Every function accepts `display`:

```python
ck.list_monitors(display=":99")
ck.list_windows(display=":99")
ck.ScreenCapture(ck.MonitorSource(0, display=":99"))
ck.get_capabilities(":99")
```

`Monitor` and `Window` objects remember their display, so sources built from
them target the same server automatically.

* `display=None` uses `$DISPLAY` — **unless** the session is Wayland
  (`WAYLAND_DISPLAY` set or `XDG_SESSION_TYPE=wayland`), which raises
  `UnsupportedDisplayServerError`: `$DISPLAY` would be XWayland, which only
  contains X11 clients, not your desktop.
* An explicit display is always honoured, even from a Wayland host (e.g. an
  Xvfb server, or WSLg).
* If the target server is XWayland, monitor capture is refused; window
  capture of X11 clients is experimental.

## Monitor capture

Monitors come from RandR 1.5 `GetMonitors`, falling back to CRTCs, Xinerama
and finally the root window size. Xvfb reports one monitor named `screen`.
Frames are read from the root window with MIT-SHM (`XShmGetImage`) when the
server is local, otherwise `XGetImage`. Only 24/32-bit TrueColor visuals
(BGRX) are supported; others raise `CapabilityUnavailableError`.
The X padding byte is normalised to alpha 255.

## Window capture (XComposite)

The window is redirected off-screen with XComposite **Automatic**
redirection (shareable with any compositing manager) and read from its own
pixmap, so **overlapping windows never appear** (verified pixel-exactly with
partially and fully covering windows on Xvfb). Damage events skip unchanged
frames. The server keeps painting the window on screen normally; redirection
is removed when capture stops.

* `client_area_only=True` captures the client window (no WM decorations);
  `False` (default) captures the window-manager frame when one exists.
* Resizes are followed (the pixmap is re-named on `ConfigureNotify`).
* Unmapped/iconified windows have no pixmap: no new frames are produced
  (`grab()` returns the last frame, recordings repeat it) until the window
  is mapped again. Desktop pixels are never substituted.
* A destroyed window raises `WindowClosedError`.
* Without the Composite extension, window capture is unavailable (no
  fallback to screen cropping).
* FFmpeg's `x11grab -window_id` is not used: it reads the window area
  without redirection and is not occlusion-free.

## X server failures

Xlib normally calls `exit()` on protocol and connection errors. capturekit
installs handlers for its own connections only (other X toolkits in the
process keep their handlers): errors become exceptions, and if the X server
dies capture raises a `CaptureError` instead of terminating Python
(tested by killing Xvfb mid-capture). This needs libX11 ≥ 1.7
(`XSetIOErrorExitHandler`); with older libX11 a dying server still ends the
process.

## Audio

| Source | Implementation |
| --- | --- |
| `MicrophoneSource` | `parec` on the source (default: server default source) |
| `SystemAudioSource` | `parec` on `<sink>.monitor` (default sink) |
| `ProcessAudioSource(pid)` | one `parec --monitor-stream=<sink-input>` per stream of the process (and children), mixed |

Per-application capture records each matching sink-input directly — it
never moves streams, creates sinks or changes your routing, and never falls
back to the sink monitor. Streams that start after capture began are
attached within ~0.5 s; ended streams are dropped. A silent or audio-less
process yields silence. `ProcessAudioSource(..., stream_ids=("42",))` pins
specific sink-inputs from `list_audio_sessions()`.

Limitations: pipe-based audio has no hardware timestamps (estimated from read
time; the aligner absorbs the jitter); PipeWire-native
(`pw-record`) is not used; `pactl -f json` requires PulseAudio ≥ 16 or
pipewire-pulse. Xvfb has no audio — audio and display are independent.

## Xvfb quick start

```
Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp &
python -c "import capturekit as ck; print(ck.get_capabilities(':99').summary())"
```
