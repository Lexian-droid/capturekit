"""The single capturekit clock.

Windows: ``time.perf_counter_ns`` is QueryPerformanceCounter scaled to ns with
no offset (verified, see native-api-research skill), so QPC-based timestamps
from WGC, DXGI and WASAPI convert without an offset.
Linux: CLOCK_MONOTONIC, which PulseAudio/PipeWire and X server-side
timestamps can be related to.
"""

from __future__ import annotations

import sys
import threading
import time

if sys.platform == "win32":
    now_ns = time.perf_counter_ns
else:
    now_ns = time.monotonic_ns

NS_PER_S = 1_000_000_000


def hns_to_ns(hundred_ns: int) -> int:
    """Convert 100-ns QPC units (WASAPI u64QPCPosition, WinRT TimeSpan)."""
    return hundred_ns * 100


def qpc_ticks_to_ns(ticks: int, frequency: int) -> int:
    return ticks * NS_PER_S // frequency


def sleep_until(deadline_ns: int, stop: threading.Event | None = None) -> bool:
    """Sleep until the absolute ``deadline_ns``.

    Returns ``False`` if ``stop`` was set while waiting. Wakes at most every
    50 ms to observe ``stop``. On Python >= 3.11 Windows, ``time.sleep`` uses a
    high-resolution waitable timer, so the residual error is typically below
    1 ms; callers measure and report actual jitter instead of assuming it.
    """
    while True:
        if stop is not None and stop.is_set():
            return False
        remaining = deadline_ns - now_ns()
        if remaining <= 0:
            return True
        time.sleep(min(remaining, 50_000_000) / NS_PER_S)
