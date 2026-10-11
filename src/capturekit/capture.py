"""Frame capture without recording."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from types import TracebackType

import numpy as np

from ._backends.base import AcquiredFrame, VideoBackend
from ._pipeline.clock import NS_PER_S, now_ns, sleep_until
from ._worker import BackendThread
from .errors import CaptureTimeoutError
from .sources import MonitorSource, VideoSource, WindowSource
from .types import Frame, Monitor, Rect, Window


class ScreenCapture:
    """Capture frames from a monitor or a window.

    Example::

        with ScreenCapture(MonitorSource(0)) as cap:
            frame = cap.grab()          # newest frame (BGRA numpy array)
            for frame in cap.frames(fps=30, max_frames=90):
                ...

    Thread-safe; all native work happens on an internal backend thread.
    """

    def __init__(self, source: VideoSource) -> None:
        self.source = source
        self._worker: BackendThread | None = None
        self._backend_name = ""
        self._last: Frame | None = None
        self._index = 0
        self._lock = threading.Lock()

    # -- lifecycle -----------------------------------------------------------
    def open(self) -> ScreenCapture:
        if self._worker is None:
            worker = BackendThread(self.source, name="capturekit-capture")
            backend = worker.start()
            self._backend_name = backend.info.name
            self._worker = worker
        return self

    def close(self) -> None:
        if self._worker is not None:
            self._worker.stop()
            self._worker = None

    def __enter__(self) -> ScreenCapture:
        return self.open()

    def __exit__(
        self, et: type[BaseException] | None, ev: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.close()

    @property
    def backend_name(self) -> str:
        return self._backend_name

    # -- capture ---------------------------------------------------------------
    def _acquire(self, timeout_s: float) -> AcquiredFrame | None:
        if self._worker is None:
            self.open()
        assert self._worker is not None

        def fn(b: VideoBackend) -> AcquiredFrame | None:
            return b.acquire(None, timeout_s)

        return self._worker.call(fn, timeout_s + 10)

    def grab(self, timeout: float = 2.0, *, new_only: bool = False) -> Frame:
        """Return the newest frame.

        If nothing changed since the previous call, a copy of the previous
        frame is returned (unless ``new_only``). Raises
        :class:`CaptureTimeoutError` if no frame is available in ``timeout``.
        """
        with self._lock:
            first_wait = timeout if (self._last is None or new_only) else 0.0
            acq = self._acquire(first_wait)
            if acq is None:
                if self._last is None or new_only:
                    raise CaptureTimeoutError(f"no frame within {timeout:.2f}s")
                last = self._last
                return Frame(last.data.copy(), last.timestamp_ns, last.index, last.content_size)
            frame = Frame(acq.data, acq.timestamp_ns, self._index, acq.content_size)
            self._index += 1
            self._last = frame
            return Frame(frame.data.copy(), frame.timestamp_ns, frame.index, frame.content_size)

    def frames(
        self, fps: float | None = 30.0, *, max_frames: int | None = None, timeout: float = 2.0
    ) -> Iterator[Frame]:
        """Iterate frames.

        With ``fps`` frames are produced at absolute deadlines (repeating the
        previous image when nothing changed). With ``fps=None`` only new
        frames are yielded, as fast as the source produces them.
        """
        count = 0
        if fps is None:
            while max_frames is None or count < max_frames:
                yield self.grab(timeout, new_only=True)
                count += 1
            return
        period = NS_PER_S / fps
        t0 = now_ns()
        n = 0
        while max_frames is None or count < max_frames:
            sleep_until(t0 + int(n * period))
            yield self.grab(timeout)
            count += 1
            # Skip deadlines already missed (slow consumer) instead of bursting.
            n = max(n + 1, int((now_ns() - t0) / period))


def screenshot(
    target: Monitor | Window | int | None = None,
    region: Rect | None = None,
    *,
    timeout: float = 2.0,
) -> np.ndarray:
    """One-shot BGRA screenshot of a monitor (default primary) or window."""
    src: VideoSource
    if isinstance(target, Window):
        src = WindowSource(target, region=region)
    else:
        src = MonitorSource(target, region=region)
    with ScreenCapture(src) as cap:
        return cap.grab(timeout).data
