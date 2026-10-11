# Capability matrix

Legend: **Implemented** = real backend + tests executed on that platform ·
**Experimental** = works, limited validation / known gaps · **Untested** = code
exists, never executed on that platform · **Unsupported** = not implemented or
out of scope (raises a typed error) · **Planned** = not implemented yet.

Query at runtime with `capturekit.get_capabilities().summary()`.

Last verified: 2026-10-11, Windows 11 build 26200, RTX 4050 Laptop GPU,
FFmpeg 7.1 (imageio-ffmpeg), `pytest -rs` → 75 passed / 0 skipped.

## Video

| Capability | Windows 10/11 | Linux X11 | Xvfb | Wayland |
| --- | --- | --- | --- | --- |
| Monitor enumeration (physical px, negative coords) | Implemented | Planned | Planned | Unsupported |
| Monitor capture (full) | Implemented (DXGI) | Planned | Planned | Unsupported |
| Monitor capture (region) | Implemented | Planned | Planned | Unsupported |
| Monitor capture via WGC | Implemented (`MonitorBackend.WGC`) | – | – | – |
| Non-primary / negative-coordinate monitors | Untested (single-monitor test machine) | Planned | – | Unsupported |
| Rotated monitors | DXGI: Unsupported (typed error) · WGC: Untested | Planned | – | Unsupported |
| Cursor in monitor capture | Experimental (DXGI software compositing, not pixel-verified) | Planned | Planned | – |
| Window enumeration (title, class, PID, process) | Implemented | Planned | Planned | Unsupported |
| Exclusive window capture (occlusion-free) | Implemented (WGC, pixel-verified with partial + full occlusion) | Planned (XComposite) | Planned | Unsupported |
| Window region crop (window/client relative) | Implemented | Planned | Planned | – |
| Client-area-only capture | Implemented | Planned | Planned | – |
| Window resize handling | Implemented (pool recreate + session refresh) | Planned | Planned | – |
| Window closure detection | Implemented (`WindowClosedError`) | Planned | Planned | – |
| Minimized windows | No new frames (measured); last frame held, see Known limitations | Planned | – | – |
| Capture-excluded windows (`WDA_EXCLUDEFROMCAPTURE`) | Reported (`Window.capture_excluded`), never bypassed (tested) | – | – | – |
| WGC capture border removal | Experimental (`border=False` accepted on build 26200) | – | – | – |

## Audio

| Capability | Windows | Linux (PulseAudio/PipeWire) |
| --- | --- | --- |
| Device enumeration | Implemented | Planned |
| Microphone | Implemented | Planned |
| System audio (loopback / monitor source) | Implemented | Planned |
| Per-process audio | Implemented (WASAPI process loopback, build ≥ 20348; isolation verified 440 Hz vs 1 kHz distractor) | Planned (`parec --monitor-stream`) |
| Process-tree inclusion | Implemented (always on; `include_children=False` → `CapabilityUnavailableError`) | Planned |
| Session/stream enumeration with PIDs | Implemented (`IAudioSessionManager2`) | Planned |
| Multiple simultaneous sources | Implemented | Planned |
| Separate tracks / mixed track | Implemented (`AudioMixMode`; SEPARATE/MIXED/BOTH verified by per-track tone content) | Planned |
| Device disconnection | Experimental (mapped to `AudioDeviceDisconnectedError`; not tested by unplugging) | Planned |

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
| Wayland detection (`UnsupportedDisplayServerError`) | Planned (with Linux backend) |
| macOS | Out of scope |
