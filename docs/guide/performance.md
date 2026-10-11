# Performance

## Measured results

Machine: Windows 11 build 26200, AMD Ryzen (Family 25 Model 68, Zen 3+),
NVIDIA GeForce RTX 4050 Laptop GPU (driver 591.66), single 1920×1080@144 Hz
display, FFmpeg 7.1 (imageio-ffmpeg 0.6.0), Python 3.13. Date: 2026-10-11.
Desktop content was the normal working desktop (≈30 updates/s).

### Raw capture (no encoding)

`python scripts/bench_capture.py --source monitor --seconds 5`

| Backend | Resolution | acquire + GPU→CPU readback p50 / p99 |
| --- | --- | --- |
| DXGI duplication | 1920×1080 | 1.91 ms / 6.41 ms |
| WGC monitor | 1920×1080 | 1.90 ms / 5.25 ms |

### Recording 1080p60 with system audio, 15 s

`python scripts/bench_record.py --fps 60 --seconds 15 --encoder <enc> --audio system`

| Encoder | Slots | Dropped | Late | Capture avg | Jitter avg / max | Encoder write avg |
| --- | --- | --- | --- | --- | --- | --- |
| h264_nvenc | 901 | 0 | 0 | 1.30 ms | 0.30 / 1.08 ms | 1.24 ms |
| libx264 (veryfast, CRF 23) | 901 | 0 | 0 | 2.41 ms | 0.31 / 5.45 ms | 1.23 ms |

From the test suite (`pytest -m slow -s`, 4 s monitor recordings, NVENC):
24, 30 and 60 fps all had 0 dropped / 0 late frames, average pacing jitter
≈ 0.3 ms.

Numbers on other machines will differ; run the scripts above and report
the full output (they print machine and FFmpeg versions).

## Tuning

* Crop (`region`) early: only the region is copied on the GPU and piped.
* Prefer hardware encoders (`encoder="auto"`): NVENC takes BGRA directly,
  avoiding CPU colour conversion.
* `RecordingConfig.queue_frames` (default 8) bounds memory
  (`queue_frames × w × h × 4` bytes) and absorbs encoder hiccups; raise it if
  `frames_dropped` grows while `queue_depth_max` reaches the limit.
* Watch `RecordingStats.video`: `frames_late` = pacer overslept (CPU
  starvation), `frames_dropped` = encoder too slow.

## Known costs / backlog

* Raw BGRA through a pipe: ≈ 500 MB/s at 1080p60. A GPU BGRA→NV12 step would
  cut this to ~190 MB/s (not implemented).
* `Map` stalls until the GPU copy completes; a staging-texture ring would
  overlap readback with the next copy (not implemented; measured cost is
  already ~1–2 ms/frame).
