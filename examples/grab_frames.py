"""Grab paced frames from a monitor region without recording a file."""

import time

import capturekit as ck

region = ck.Rect(0, 0, 640, 360)
with ck.ScreenCapture(ck.MonitorSource(0, region=region)) as cap:
    t0 = time.perf_counter()
    for frame in cap.frames(fps=30, max_frames=90):
        mean_bgr = frame.data[..., :3].mean(axis=(0, 1))
        print(f"{time.perf_counter() - t0:6.3f}s frame {frame.index} mean BGR {mean_bgr.round(1)}")
