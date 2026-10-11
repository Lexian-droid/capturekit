# Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `EncoderNotAvailableError: FFmpeg executable not found` | Install FFmpeg, set `CAPTUREKIT_FFMPEG`, or `pip install capturekit[ffmpeg]`. |
| `no usable video encoder` listing reasons | Every candidate failed its 3-frame probe; read the per-encoder reason (driver missing, size too small for NVENC, ...). |
| Window capture is black | The window excludes itself from capture (`Window.capture_excluded`), shows DRM content, or is minimized. Not bypassed by design. |
| Window capture shows a yellow border | Windows' capture indicator. `WindowSource(border=False)` asks the OS to remove it (Windows 11; may be refused). |
| `CapabilityUnavailableError` for process audio | Windows build < 20348. Upgrade to Windows 11 or use `SystemAudioSource`. |
| Process audio is silent | The window's process is not the one playing audio. Find the right PID with `list_audio_sessions()`. |
| Monitor capture stalls during UAC/lock screen | Expected: the secure desktop cannot be duplicated; capture resumes automatically and the last frame is repeated. |
| `CapabilityUnavailableError: ... rotated monitors` | Use `MonitorSource(..., backend=MonitorBackend.WGC)`. |
| `frames_dropped` > 0 | Encoder cannot keep up: use a hardware encoder, lower fps/resolution, or crop. |
| `frames_late` > 0 | The process was starved of CPU; check system load. |
| Coordinates look scaled by 125 %/150 % | Your own app is not DPI aware; capturekit always reports physical pixels. |

Enable logging for details:

```python
import logging; logging.basicConfig(level=logging.DEBUG)
```
Loggers: `capturekit.recorder`, `capturekit.windows.*`.
