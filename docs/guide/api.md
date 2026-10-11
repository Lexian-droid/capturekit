# Python API guide

Everything is importable from `capturekit` (`import capturekit as ck`).
All public types are fully typed (`py.typed`).

## Discovery

```python
ck.get_capabilities(display=None)   # Capabilities; .summary() for a text table
ck.list_monitors(display=None)      # [Monitor]  primary first, physical pixels
ck.list_windows(display=None, include_untitled=False)   # [Window]
ck.get_window(handle)               # Window or TargetNotFoundError
ck.list_audio_devices()             # [AudioDevice]  INPUT/OUTPUT, is_default
ck.list_audio_sessions()            # [AudioSession] per-app streams with PIDs
```

`Capability` values are truthy when usable (`SUPPORTED`/`EXPERIMENTAL`) and
carry a `reason`:

```python
caps = ck.get_capabilities()
if not caps.process_audio:
    print("no per-app audio:", caps.process_audio.reason)
```

## Sources

```python
ck.MonitorSource(monitor=None, region=None, include_cursor=True, backend=ck.MonitorBackend.AUTO)
ck.WindowSource(window, region=None, client_area_only=False, include_cursor=False, border=None)
ck.MicrophoneSource(device=None)
ck.SystemAudioSource(device=None)
ck.ProcessAudioSource(pid, include_children=True)   # or .for_window(window)
```

Regions are `ck.Rect(x, y, width, height)` relative to the source.

## Frames without recording

```python
with ck.ScreenCapture(ck.MonitorSource(0)) as cap:
    f = cap.grab()                   # newest frame (copy of last if unchanged)
    f = cap.grab(new_only=True)      # wait for a *new* frame (CaptureTimeoutError)
    for f in cap.frames(fps=30, max_frames=300):   # paced at absolute deadlines
        ...
    for f in cap.frames(fps=None):   # every new frame as it arrives
        ...
```

`Frame.data` is a `(h, w, 4)` `uint8` BGRA array owned by you;
`Frame.timestamp_ns` is on `ck.now_ns()`'s clock; `Frame.content_size` is the
source size (detect window resizes).

## Recording

```python
rec = ck.Recorder(
    video=ck.WindowSource(win),                      # or None for audio-only
    audio=[ck.ProcessAudioSource.for_window(win), ck.MicrophoneSource()],
    output="out.mkv",                                # or None: callbacks only
    config=ck.RecordingConfig(
        fps=60, output_size=None, audio_mix=ck.AudioMixMode.SEPARATE,
        encoder=ck.EncoderConfig("auto", bitrate=12_000_000),
        on_target_lost=ck.TargetLostPolicy.STOP, overwrite=False,
    ),
    on_error=lambda exc: ...,            # dispatcher thread
    on_state_change=lambda state: ...,   # STARTING, RECORDING, PAUSED, STOPPING, STOPPED/FAILED
    on_video_frame=lambda frame: ...,    # media-time timestamps
    on_audio=lambda chunk: ...,
)
rec.start()            # returns once capture is running (raises on failure)
rec.pause(); rec.resume()
rec.stats              # live RecordingStats
rec.wait(timeout)      # True when the recorder stopped by itself (e.g. window closed)
result = rec.stop()    # RecordingResult(output, stats, error); raises fatal errors
```

`Recorder` is also a context manager (`with ck.Recorder(...) as rec:`).

Callbacks never run on capture threads. Frame/audio callbacks are dropped
(and counted in `stats.callbacks_dropped`) if your callback is too slow;
lifecycle and error callbacks are never dropped.

## Errors

All errors derive from `ck.CaptureError`. Most useful:
`TargetNotFoundError`, `WindowClosedError` (a `TargetLostError`),
`InvalidRegionError`, `CapabilityUnavailableError`,
`UnsupportedDisplayServerError`, `EncoderError` / `EncoderNotAvailableError`,
`AudioDeviceDisconnectedError`, `CaptureTimeoutError`, `RecorderStateError`.
