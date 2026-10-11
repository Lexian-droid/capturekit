---
name: performance-profiling
description: How to measure and optimise capturekit capture/encode throughput — benchmark scripts, per-stage timing, py-spy/cProfile usage, known hot spots (GPU readback, memcpy, pipe bandwidth, swscale), and rules for reporting numbers honestly. Use when evaluating FPS targets (24/30/60+) or optimising a pipeline stage.
---

# Performance profiling and optimisation

## Rules
- Numbers in docs must include: command, git commit, machine (CPU/GPU),
  resolution, fps, encoder, date. Never estimate and present as measured.
- Measure first with `scripts/bench_capture.py` and the `Recorder.stats`
  counters before changing code. Optimise the dominant stage only.

## Commands
```
.venv/Scripts/python scripts/bench_capture.py --source monitor --fps 60 --seconds 10
.venv/Scripts/python scripts/bench_capture.py --source window --hwnd <id> --fps 60
.venv/Scripts/python scripts/bench_record.py --fps 60 --encoder auto --seconds 20
python -X importtime -c "import capturekit"
py-spy record -o profile.svg -- python scripts/bench_record.py ...   (if installed)
```
`stats` fields to watch: `capture_ms_p50/p99` (acquire+copy), `pacer_jitter_ms`,
`frames_late`, `frames_dropped`, `queue_depth_max`, `encoder_write_ms`.

## Known cost centres (1080p BGRA = 8.3 MB/frame)
| Stage | Notes |
| --- | --- |
| GPU→staging copy + Map | Map stalls until copy done; staging ring of 2–3 textures hides latency |
| numpy row copy | ~1–2 ms/frame; unavoidable single copy |
| pipe to FFmpeg | 60 fps × 8.3 MB ≈ 500 MB/s; use large writes (whole frame) |
| colour conversion | hw encoders take BGRA (GPU CSC); libx264 needs swscale (CPU) |
| Python per-frame overhead | keep per-frame Python ops O(1), no per-pixel loops |

## Optimisation backlog (evaluate with measurements before doing)
1. Staging texture ring to avoid Map stalls.
2. GPU BGRA→NV12 via D3D11 VideoProcessor (halves pipe bandwidth).
3. Shared-memory transport instead of pipe.
4. Skip readback entirely when the frame is unchanged (duplicate slot).
