# Audio sources

| Source | Captures | Windows | Linux |
| --- | --- | --- | --- |
| `MicrophoneSource(device=None)` | an input device | WASAPI capture endpoint | Planned |
| `SystemAudioSource(device=None)` | everything played on an output device | WASAPI endpoint loopback | Planned (monitor source) |
| `ProcessAudioSource(pid)` | audio rendered by one process **and its child processes** | WASAPI process loopback (build ≥ 20348) | Planned |

`device` accepts an `AudioDevice` from `list_audio_devices()` or its `id`.

All tracks are float32 / 48 kHz / stereo; the OS audio engine converts
formats and sample rates.

## Choosing the process for a window

`ProcessAudioSource.for_window(window)` uses the window's owner PID with
process-tree inclusion. This covers applications that play audio from a child
process (browsers, Electron apps, and — observed in our own tests — Python
virtual-environment launchers, whose `python.exe` starts the real interpreter
as a child).

It does **not** cover audio produced by an unrelated process (e.g. a separate
audio service). Inspect `list_audio_sessions()`, which lists every audio
session with its PID, process name and activity, and pass the right PID
explicitly:

```python
for s in ck.list_audio_sessions():
    print(s.pid, s.process_name, s.display_name, "active" if s.is_active else "")
```

A process that is silent produces silence — not an error, and never the
audio of other applications. Per-process capture **never falls back** to
system audio: if it is unavailable, opening the source raises
`CapabilityUnavailableError`.

`include_children=False` is not available on Windows (WASAPI's only other mode
means "everything *except* this process tree") and raises
`CapabilityUnavailableError`.

## Tracks and mixing

`RecordingConfig(audio_mix=...)`:

* `SEPARATE` (default): one track per source, titled with the source name.
* `MIXED`: one track with the sum of all sources (clipped to ±1.0).
* `BOTH`: separate tracks followed by a `mix` track.

## Synchronisation

Every packet carries a QPC timestamp from WASAPI. capturekit aligns audio to
the recording timeline: silence is inserted when a source delivers nothing
(endpoint loopback sends no packets while nothing plays), overlaps are
trimmed, the stream is re-anchored after start/resume, and slow clock drift is
corrected by resampling ≤ 0.2 %. Per-track statistics
(`RecordingStats.audio`) report inserted silence, trimmed audio and drift
correction.
