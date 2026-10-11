---
name: audio-capture-routing
description: Procedures for capturekit audio sources — WASAPI microphone, endpoint loopback and process loopback on Windows; PulseAudio/PipeWire devices, monitor sources and per-application streams on Linux; window-to-process-to-stream mapping. Use when touching audio backends or audio source selection.
---

# Audio capture and stream routing

## Uniform internal format
Every backend delivers `AudioPacket(data: float32[frames, 2], timestamp_ns,
flags)` at 48 kHz stereo. Conversion happens in the OS where possible
(WASAPI `AUTOCONVERTPCM | SRC_DEFAULT_QUALITY`; Pulse/PipeWire stream format).
Never resample in Python except the tiny drift correction in `av-sync`.

## Windows (WASAPI, `_backends/windows/wasapi.py`)
| Source | Activation | Flags |
| --- | --- | --- |
| Microphone | `IMMDevice::Activate(IAudioClient)` on eCapture endpoint | `EVENTCALLBACK` |
| System audio | eRender endpoint | `LOOPBACK | EVENTCALLBACK` |
| Process audio | `ActivateAudioInterfaceAsync("VAD\\Process_Loopback", IAudioClient, VT_BLOB AUDIOCLIENT_ACTIVATION_PARAMS)` | `LOOPBACK | EVENTCALLBACK` |

Quirks (verified — keep this list current):
- Endpoint loopback delivers **no packets while nothing is playing**. The
  aligner heartbeat must fill silence; never treat the gap as a stall.
- Process loopback `IAudioClient::GetMixFormat` returns `E_NOTIMPL`; pass an
  explicit WAVEFORMATEX (float32, 48 kHz, 2 ch). Requires build ≥ 20348.
- Process loopback completion handler must implement `IAgileObject`
  (it is called on an MTA worker thread).
- `INCLUDE_TARGET_PROCESS_TREE` captures the target and its descendants —
  needed for browsers whose audio comes from a child process. A PID with no
  audio produces silence, not an error; report "no active session" via
  `list_audio_sessions()` so callers can detect it.
- Process loopback must never fall back to endpoint loopback on failure:
  raise `CapabilityUnavailableError`.
- `AUDCLNT_BUFFERFLAGS_SILENT` (0x2): treat buffer as zeros.
  `DATA_DISCONTINUITY` (0x1): count a glitch; aligner realigns by timestamp.
- `u64QPCPosition` is in 100 ns units of QPC → `* 100` to ns.
- Device invalidation: `AUDCLNT_E_DEVICE_INVALIDATED (0x88890004)` → raise
  `AudioDeviceDisconnectedError` via the error channel.

- Sessions report the *rendering* PID. The venv `python.exe` launcher spawns
  the real interpreter as a child, so a fixture's PID differs from its
  session PID; use `_win32.process_tree(pid)` when matching (tested).

Window → audio: `Window.pid` → `ProcessAudioSource(pid, include_tree=True)`.
Ambiguity (launcher processes, shared audio hosts) is resolved by the caller
choosing a PID from `list_audio_sessions()` (IAudioSessionManager2 sessions
with their owning PIDs).

## Linux
See `docs/research/linux-x11-and-audio.md` for verified API details.
- Enumerate via `pactl -f json list sinks|sources|sink-inputs` (works on
  PulseAudio ≥ 16 and pipewire-pulse) — fallback to text parsing.
- System audio: record the default sink's `.monitor` source.
- App audio: per sink-input capture (`parec --monitor-stream=<idx>`); multiple
  sink-inputs from one PID → one capture per stream, mixed by `AudioMixer`.
  New streams appearing mid-recording are polled and attached.
- Never move user streams or change default devices without explicit opt-in.
- Xvfb has no audio; audio availability is independent of the X display.

## Tests
`pytest -m audio`. Process-loopback test plays a generated tone from a child
process (`tests/fixtures/tone_player.py`) and asserts tone energy at the
expected frequency in the captured track and absence of a second tone played
by another process.
