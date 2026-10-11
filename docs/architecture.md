# Architecture specification

Status: living document. Update it when a design decision changes; record the
reason in `docs/decisions.md`.

## 1. Goals and non-goals

`capturekit` is a Python-first library for monitor, exclusive-window and audio
capture with optional recording to H.264 MP4/MKV through FFmpeg.

* Windows 10/11 is the primary platform. Linux X11 (including explicitly
  selected Xvfb displays) is secondary.
* Wayland sessions are detected and rejected with
  `UnsupportedDisplayServerError`. macOS is out of scope.
* No stealth, persistence, or circumvention of OS capture protections.

## 2. Layering

```
capturekit (public)           types, config, errors, capabilities,
                              MonitorCapture / WindowCapture, AudioCapture,
                              Recorder
      |
capturekit._pipeline          clock, timeline (pause/resume), frame pacer,
                              audio aligner + drift correction, mixer,
                              bounded queues, statistics
      |
capturekit.encoding           FFmpeg discovery, encoder probing, FFmpeg
                              process + pipe management
      |
capturekit._backends.<os>     native code via ctypes only
    windows/  _com (vtable helper), _win32 (windows, monitors, processes),
              _d3d11, dxgi_duplication, wgc, wasapi
    linux/    x11 (Xlib/XShm/XComposite/XRandR), pulse/pipewire audio
```

Rules:

1. The public layer never imports a backend at module import time; backends
   are loaded lazily by `capturekit._platform` so `import capturekit` works on
   every platform (including for type checking and docs).
2. Backends expose internal protocols (`_backends/base.py`): `VideoBackend`
   (pull model: `acquire() -> RawFrame | None`) and `AudioBackend`
   (push model: thread delivering `AudioPacket`s). Public classes adapt them.
3. Native calls are made through ctypes. No compiled extension exists today.
   A compiled component requires a documented, measured justification
   (see `docs/decisions.md`).

## 3. Time base

All timestamps are integer nanoseconds on one monotonic clock
(`capturekit._pipeline.clock.now_ns`):

* Windows: `time.perf_counter_ns()` is QueryPerformanceCounter-based with the
  same origin (verified on build 26200, delta ≈ 1 µs). WGC
  `SystemRelativeTime`, DXGI `LastPresentTime` and WASAPI `u64QPCPosition`
  are all QPC-derived and are converted to this clock without offset.
* Linux: `CLOCK_MONOTONIC` (`time.monotonic_ns`). PulseAudio/PipeWire
  latency-corrected timestamps are converted to it.

## 4. Video pipeline (pull model, CFR output)

```
backend (GPU) --acquire()--> pacer thread --bounded queue--> encoder writer
                                  |                              |
                     absolute deadlines t0 + n/fps       FFmpeg stdin (rawvideo)
```

* The pacer computes absolute deadlines `t0 + n * period` (never
  `sleep(1/fps)` accumulation). Python ≥ 3.11 `time.sleep` on Windows uses a
  high-resolution waitable timer; remaining jitter is measured and reported.
* At each tick the pacer asks the backend for the newest frame. WGC/DXGI
  deliver frames only on change, so an unchanged slot repeats the previous
  buffer (counted as `frames_duplicated`). A late pacer skips the missed slots
  by repeating the last frame (`frames_late`), keeping the slot index tied to
  wall time.
* Output is constant frame rate. FFmpeg rawvideo timestamps are derived from
  slot counts, so A/V sync is fully determined by our slot accounting.
* Queue full (encoder too slow): the new frame is not enqueued; the last
  queued item's repeat count is incremented (`frames_dropped`). Memory stays
  bounded and sync is preserved; content updates are lost and reported.
* GPU→CPU: one `CopySubresourceRegion` of only the crop box into a staging
  texture, `Map`, and one row-pitch-aware copy into a pooled numpy buffer.

## 5. Audio pipeline (push model)

* Each source runs a dedicated thread (WASAPI event-driven capture).
  All sources are requested/converted to float32 interleaved, 48 kHz,
  stereo (WASAPI `AUTOCONVERTPCM`, process loopback uses explicit format).
* `AudioAligner` maps each packet's timestamp through the recording timeline
  (pause-aware) to a sample position and:
  * inserts silence for gaps (endpoint loopback delivers *no packets* while
    nothing plays — a heartbeat fills silence up to `now - margin`),
  * trims overlap,
  * corrects small clock drift with a bounded linear-interpolation resample
    (±0.5 % per packet max), keeping long recordings in sync without clicks.
* Optional `AudioMixer` sums aligned tracks for a mixed track.
* Tracks reach FFmpeg via named pipes (`\\.\pipe\…` on Windows, FIFOs on
  Linux), one writer thread per pipe.

## 6. Recording timeline (pause/resume)

`Timeline` holds segments `(wall_start_ns, media_start_ns)`. Pausing closes
the current segment; resuming opens a new one. Video slot numbering and audio
sample positions are both derived from media time, so pauses produce no gap
in the file and remain in sync.

## 7. Encoding

* FFmpeg located via explicit path, `CAPTUREKIT_FFMPEG`, `PATH`, then the
  optional `imageio-ffmpeg` bundled binary.
* Encoders are *validated by initialisation* (a 0.1 s lavfi encode), not by
  `-encoders` listing (e.g. `h264_qsv` is listed but fails on NVIDIA-only
  machines). Results are cached per FFmpeg binary.
* Auto order: `h264_nvenc` → `h264_qsv` → `h264_amf` → `libx264` → `h264_mf`.
  NVENC/AMF/QSV accept BGRA directly, avoiding swscale CPU conversion.
* `subprocess.Popen` with an argument list (no shell). Output paths are passed
  as single arguments; `-y` only when overwrite is requested.
* FFmpeg stderr is drained continuously (avoids pipe deadlock) and kept in a
  bounded ring buffer for error reports.

## 8. Threading and lifecycle

* Capture, pacing, encoding and audio never run on the caller's thread.
* User callbacks are dispatched on one dedicated callback thread so a slow
  callback cannot stall capture.
* Every worker records its first exception; `Recorder` transitions to
  `FAILED`, invokes `on_error`, stops other workers, finalises the file and
  re-raises from `stop()`/`wait()`.
* COM/WinRT objects are created and used on the thread that owns them (MTA).
  The D3D11 device is multithread-protected because WGC's free-threaded frame
  pool touches it from a worker thread.

## 9. Capability discovery

`capturekit.get_capabilities(display=None)` returns per-feature
`Capability(status, reason)` where status is `SUPPORTED`, `EXPERIMENTAL`,
`UNAVAILABLE` (platform can, this machine/build cannot) or `UNSUPPORTED`
(not implemented / out of scope). See `docs/capabilities.md`.

## 10. Security

* Recording only starts through explicit API calls; nothing persists or
  autostarts. WGC's capture border is left on by default.
* Windows with `WDA_EXCLUDEFROMCAPTURE`/protected content are reported; the
  library never tries to bypass display affinity or DRM.
* All native pointers are validated before use; crop regions are validated
  against the current source size; HWND validity is re-checked.
