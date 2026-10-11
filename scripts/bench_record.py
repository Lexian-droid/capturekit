"""Measure a real recording: pacing, drops and encoder throughput.

python scripts/bench_record.py --fps 60 --seconds 20 --encoder auto
python scripts/bench_record.py --fps 60 --encoder libx264 --audio system
"""

from __future__ import annotations

import argparse
import os
import platform
import tempfile
import time

import capturekit as ck
from capturekit.encoding import ffmpeg_version, find_ffmpeg


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fps", type=float, default=60)
    ap.add_argument("--seconds", type=float, default=10)
    ap.add_argument("--encoder", default="auto")
    ap.add_argument("--bitrate", type=int)
    ap.add_argument("--audio", choices=("none", "system", "mic"), default="none")
    ap.add_argument("--hwnd", type=int, help="record a window instead of monitor 0")
    ap.add_argument("--output")
    args = ap.parse_args()
    out = args.output or os.path.join(tempfile.gettempdir(), "capturekit-bench.mkv")
    video: ck.MonitorSource | ck.WindowSource = (
        ck.WindowSource(args.hwnd) if args.hwnd else ck.MonitorSource(0)
    )
    audio = {"none": [], "system": [ck.SystemAudioSource()], "mic": [ck.MicrophoneSource()]}[
        args.audio
    ]
    cfg = ck.RecordingConfig(
        fps=args.fps,
        overwrite=True,
        encoder=ck.EncoderConfig(args.encoder, bitrate=args.bitrate),
    )
    rec = ck.Recorder(video, audio, out, cfg)
    rec.start()
    time.sleep(args.seconds)
    res = rec.stop()
    v = res.stats.video
    print(f"machine: {platform.platform()} / {platform.processor()}")
    print(f"ffmpeg: {ffmpeg_version(find_ffmpeg())}")
    print(f"output: {out} ({os.path.getsize(out) / 1e6:.1f} MB)")
    assert v is not None
    print(
        f"{v.output_size[0]}x{v.output_size[1]} @ {v.fps} fps, backend {v.backend}, "
        f"encoder {v.encoder}"
    )
    print(
        f"slots {v.slots}  new {v.frames_new}  dup {v.frames_duplicated}  "
        f"dropped {v.frames_dropped}  late {v.frames_late}"
    )
    print(
        f"capture ms avg {v.capture_ms_avg:.2f} max {v.capture_ms_max:.2f}; "
        f"pacing jitter ms avg {v.pacing_jitter_ms_avg:.2f} max {v.pacing_jitter_ms_max:.2f}"
    )
    print(
        f"encoder write ms avg {v.encoder_write_ms_avg:.2f} max {v.encoder_write_ms_max:.2f}; "
        f"queue max {v.queue_depth_max}"
    )
    for a in res.stats.audio:
        print(
            f"audio {a.name}: {a.duration_s:.3f}s silence {a.silence_inserted_s:.3f}s "
            f"trimmed {a.trimmed_s:.3f}s drift-corr {a.drift_corrected_samples} samples"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
