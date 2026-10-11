# capturekit

Python library for **monitor capture**, **exclusive window capture** (the
window's own pixels, even when covered by other windows) and **audio capture**
(microphone, system audio, *individual applications*), with synchronised
recording to H.264 MKV/MP4 through FFmpeg and hardware encoders.

* Windows 10/11 — DXGI Desktop Duplication, Windows Graphics Capture, WASAPI
  (including per-process loopback). **Implemented and tested.**
* Linux X11 / Xvfb (explicit display strings such as `":99"`) — XShm,
  XComposite, PulseAudio/PipeWire. **In development** (see
  [capabilities](docs/capabilities.md)).
* Wayland-only sessions are detected and rejected; macOS is out of scope.

Pure Python + ctypes (no compiled extension). Runtime dependency: `numpy`.

## Install

```
pip install capturekit            # library
pip install capturekit[ffmpeg]    # + bundled FFmpeg binary (imageio-ffmpeg)
```
Recording needs an FFmpeg executable: `PATH`, `CAPTUREKIT_FFMPEG`,
`EncoderConfig(ffmpeg_path=...)`, or the `[ffmpeg]` extra.

## Quick start

```python
import capturekit as ck

print(ck.get_capabilities().summary())

# One frame of the primary monitor (BGRA numpy array)
img = ck.screenshot()

# Exclusive capture of a window, cropped relative to its client area
win = next(w for w in ck.list_windows() if "Notepad" in w.title)
with ck.ScreenCapture(ck.WindowSource(win, client_area_only=True,
                                      region=ck.Rect(0, 0, 400, 300))) as cap:
    for frame in cap.frames(fps=30, max_frames=60):
        process(frame.data)                       # (300, 400, 4) uint8

# Record the window + only that application's audio + the microphone
rec = ck.Recorder(
    ck.WindowSource(win),
    [ck.ProcessAudioSource.for_window(win), ck.MicrophoneSource()],
    "notepad.mkv",
    ck.RecordingConfig(fps=60, audio_mix=ck.AudioMixMode.BOTH),
    on_error=print,
)
rec.start()
...
rec.pause(); rec.resume()
result = rec.stop()
print(result.stats.video)          # slots, new/duplicated/dropped frames, timings
```

More: [examples/](examples/) · [API guide](docs/guide/api.md).

## Documentation

| Topic | |
| --- | --- |
| What works where | [docs/capabilities.md](docs/capabilities.md) |
| Windows requirements & behaviour | [docs/guide/windows.md](docs/guide/windows.md) |
| Linux X11 / Xvfb | [docs/guide/linux.md](docs/guide/linux.md) |
| Audio sources, process selection | [docs/guide/audio.md](docs/guide/audio.md) |
| Encoders & hardware encoding | [docs/guide/encoding.md](docs/guide/encoding.md) |
| Performance numbers & tuning | [docs/guide/performance.md](docs/guide/performance.md) |
| Troubleshooting | [docs/guide/troubleshooting.md](docs/guide/troubleshooting.md) |
| Architecture / decisions | [docs/architecture.md](docs/architecture.md), [docs/decisions.md](docs/decisions.md) |
| Contributing | [CONTRIBUTING.md](CONTRIBUTING.md) |

## Responsible use

Recording starts only through explicit API calls; capturekit has no
background, hidden or persistent recording features. Windows asking to be
excluded from capture (display affinity) and protected content are respected,
never bypassed. Windows draws its capture border unless you request otherwise
and the OS permits it.

## Status

Alpha. Windows backends are implemented and covered by integration tests that
run on real hardware; Linux backends are under development. See
[docs/PROGRESS.md](docs/PROGRESS.md).
