# Capability matrix

Legend: **Implemented** = real backend + tests executed on that platform ·
**Experimental** = works, limited validation / known gaps · **Untested** = code
exists, never executed on that platform · **Unsupported** = not implemented or
out of scope (raises a typed error) · **Planned** = not implemented yet.

Query at runtime with `capturekit.get_capabilities().summary()`.

Last verified: 2026-10-11.
* Windows 11 build 26200, RTX 4050 Laptop GPU, FFmpeg 7.1 (imageio-ffmpeg):
  `pytest -rs` → 75 passed, 20 skipped (Linux-only tests).
* Ubuntu 26.04 (WSL2), Python 3.14, Xvfb, libX11 1.8.13, WSLg PulseAudio 17,
  FFmpeg (distro): `pytest -rs` → 58 passed, 0 skipped (Windows tests not collected).

## Video

| Capability | Windows 10/11 | Linux X11 (Xvfb tested) | Wayland |
| --- | --- | --- | --- |
| Monitor enumeration (physical px, negative coords) | Implemented | Implemented (RandR → CRTC → Xinerama → root; Xvfb tested, multi-monitor untested) | Unsupported |
| Monitor capture (full / region) | Implemented (DXGI) | Implemented (MIT-SHM, XGetImage fallback) | Unsupported |
| Monitor capture via WGC | Implemented (`MonitorBackend.WGC`) | – | – |
| Non-primary / negative-coordinate monitors | Untested (single-monitor test machine) | Untested | Unsupported |
| Rotated monitors | DXGI: Unsupported (typed error) · WGC: Untested | Untested | Unsupported |
| Cursor in monitor capture | Experimental (DXGI software compositing, not pixel-verified) | Unsupported (not implemented) | – |
| Window enumeration (title, class, PID, process) | Implemented | Implemented (EWMH or XQueryTree; PID via X-Resource) | Unsupported |
| Exclusive window capture (occlusion-free) | Implemented (WGC, pixel-verified, partial + full occlusion) | Implemented (XComposite, pixel-verified on Xvfb); with a WM/compositor: Untested | Unsupported |
| Window region crop / client area | Implemented | Implemented | – |
| Window resize handling | Implemented | Implemented | – |
| Window closure detection | Implemented (`WindowClosedError`) | Implemented | – |
| Minimized/unmapped windows | No new frames (measured); last frame held | No new frames (tested with unmap/map) | – |
| Capture-excluded windows (`WDA_EXCLUDEFROMCAPTURE`) | Reported, never bypassed (tested) | n/a | – |
| WGC capture border removal | Experimental | n/a (no border) | – |
| Wayland session detection | n/a | Implemented (`UnsupportedDisplayServerError` without explicit display; tested) | – |
| X server death | n/a | Implemented (exception, interpreter survives; libX11 ≥ 1.7; tested) | – |

## Audio

| Capability | Windows | Linux (PulseAudio protocol) |
| --- | --- | --- |
| Device enumeration | Implemented | Implemented (`pactl -f json`) |
| Microphone | Implemented | Implemented (stream delivery tested on WSLg `RDPSource`; content not verified) |
| System audio (loopback / monitor source) | Implemented | Implemented (tested on WSLg PulseAudio 17) |
| Per-process audio | Implemented (WASAPI process loopback, build ≥ 20348; isolation verified) | Implemented (`parec --monitor-stream`; isolation verified on PulseAudio 17; PipeWire: Untested) |
| Process-tree inclusion | Implemented (always on) | Implemented (`include_children`) |
| Streams appearing during capture | n/a (WASAPI handles) | Implemented (tested) |
| Session/stream enumeration with PIDs | Implemented | Implemented |
| Multiple sources / separate & mixed tracks | Implemented | Implemented (shared pipeline; Linux multi-source recording untested) |
| Device disconnection | Experimental (not tested by unplugging) | Experimental (parec exit → `AudioDeviceDisconnectedError`; untested) |

## Recording & pipeline

| Capability | Status |
| --- | --- |
| H.264 in MKV / fragmented MP4 | Implemented |
| Encoder auto-selection with validation (NVENC → QSV → AMF → x264 → MF) | Implemented; NVENC + libx264 verified, QSV/AMF probe correctly rejected on NVIDIA-only machine |
| QSV / AMF encoding | Untested (no Intel/AMD GPU available) |
| 24 / 30 / 60 fps 1080p | Implemented, measured (see `docs/guide/performance.md`) |
| Pause / resume | Implemented (tone played only while paused is ≥ 600× attenuated in the output; video duration excludes pause) |
| Bounded queues, drop/duplicate accounting | Implemented |
| Clock drift correction | Implemented (unit-tested: −500…+500 ppm over 1 h → < 20 ms) |
| A/V sync measurement | Experimental: median audio-after-video offset +23…+49 ms across runs, measured with a `PlaySound` beacon whose own latency (±10 ms jitter) is not quantified; no trend detectable within beacon jitter over 8 s. Long-term drift evidence = simulated 1 h unit tests |
| Encoder crash handling | Implemented (tested: FFmpeg killed mid-recording → `EncoderError`, threads cleaned up) |
| Capture without output file (callbacks / `ScreenCapture`) | Implemented |
| macOS | Out of scope |
