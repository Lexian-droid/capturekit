---
name: av-sync
description: Audio/video synchronization design and verification for capturekit — shared clock, pause-aware timeline, CFR frame pacing, audio gap filling, overlap trimming, drift correction and how to measure A/V offset in recorded files. Use when changing _pipeline/ timing code or investigating sync drift.
---

# Audio/video synchronisation

Code: `src/capturekit/_pipeline/` — `clock.py`, `timeline.py`, `pacer.py`,
`aligner.py`, `mixer.py`.

## Model
- One clock (`now_ns`). Every frame/packet timestamp is converted to it at the
  backend boundary.
- `Timeline.media_ns(wall_ns)` maps wall time to media time, excluding paused
  intervals; returns `None` while paused.
- Video: slot `n` covers media time `[n*P, (n+1)*P)`; pacer fires at
  `wall(t0) + n*P` (absolute). FFmpeg rawvideo timestamps = slot index.
- Audio: sample `k` ↔ media time `k / 48000`. Aligner computes expected
  position `p = media_ns(packet.ts) * 48000 / 1e9`, compares with samples
  written `w`:
  - `p - w > gap_threshold (20 ms)` → insert silence `p - w`.
  - `w - p > overlap_threshold (20 ms)` → trim overlap from packet head.
  - otherwise → drift correction: resample packet to `n + c` samples where
    `c = clamp(k_p * (p - w), ±0.5% n)` (proportional controller).
- Heartbeat: if no packet for > 100 ms, fill silence up to
  `media_now - 60 ms` (loopback silence, device stall).

## Invariants to test
1. Total audio samples ≈ total video slots × 48000 / fps (± one packet).
2. Pause of D seconds adds 0 to both durations.
3. A clock running 100 ppm fast for 1 h stays within ±20 ms
   (`tests/unit/test_aligner.py` simulates).

## Measuring sync in a real file
1. Record a flash+beep fixture (`tests/fixtures/av_sync_beacon.py`: window
   flips black→white and plays a 1 kHz beep at the same QPC instant).
2. Extract: `ffprobe -show_frames` for video luminance (or decode with FFmpeg to
   rawvideo gray and find the step), audio onset by envelope threshold.
3. Offset = audio_onset − video_onset; report mean and drift over time.
Acceptable: |offset| ≤ 1 frame period + 10 ms, drift ≤ 5 ms/min.
