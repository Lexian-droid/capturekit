"""Measure raw capture throughput (no encoding).

    python scripts/bench_capture.py --source monitor --seconds 5
    python scripts/bench_capture.py --source window --hwnd 123456 --seconds 5
    python scripts/bench_capture.py --source monitor --region 0,0,1280,720

Reports acquire+readback latency for new frames and new-frame rate. The
backend is polled as fast as possible (timeout 0) to find the ceiling.
"""

from __future__ import annotations

import argparse
import platform
import statistics
import time

import numpy as np

import capturekit as ck
from capturekit._backends.base import VideoBackend
from capturekit._worker import BackendThread


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=("monitor", "monitor-wgc", "window"), default="monitor")
    ap.add_argument("--hwnd", type=int)
    ap.add_argument("--region", help="x,y,w,h")
    ap.add_argument("--seconds", type=float, default=5.0)
    args = ap.parse_args()
    region = ck.Rect(*map(int, args.region.split(","))) if args.region else None
    src: ck.MonitorSource | ck.WindowSource
    if args.source == "window":
        if args.hwnd is None:
            ap.error("--hwnd required for window source")
        src = ck.WindowSource(args.hwnd, region=region)
    else:
        backend = ck.MonitorBackend.WGC if args.source == "monitor-wgc" else ck.MonitorBackend.AUTO
        src = ck.MonitorSource(0, region=region, backend=backend)

    worker = BackendThread(src)
    worker.start()

    def run(b: VideoBackend) -> tuple[list[float], int, tuple[int, int]]:
        first = b.acquire(None, 2.0)
        assert first is not None
        buf = np.empty_like(first.data)
        lat: list[float] = []
        polls = 0
        end = time.perf_counter() + args.seconds
        while time.perf_counter() < end:
            t = time.perf_counter()
            f = b.acquire(buf, 0.0)
            polls += 1
            if f is not None:
                lat.append((time.perf_counter() - t) * 1000)
            else:
                time.sleep(0.0005)
        return lat, polls, (first.data.shape[1], first.data.shape[0])

    lat, polls, size = worker.call(run, args.seconds + 30)
    worker.stop()
    print(f"machine: {platform.platform()} / {platform.processor()}")
    print(f"source: {args.source} {size[0]}x{size[1]}  duration {args.seconds:.1f}s")
    if lat:
        q = statistics.quantiles(lat, n=100)
        print(f"new frames: {len(lat)} ({len(lat) / args.seconds:.1f}/s), polls {polls}")
        print(f"acquire+readback ms: p50 {q[49]:.2f}  p99 {q[98]:.2f}  max {max(lat):.2f}")
    else:
        print("no new frames (static content?)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
