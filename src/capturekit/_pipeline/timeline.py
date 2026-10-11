"""Pause-aware mapping between wall-clock time and media time."""

from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass(slots=True)
class _Segment:
    wall_start: int
    media_start: int
    wall_end: int | None = None


class Timeline:
    """Maps capturekit-clock nanoseconds to media nanoseconds.

    Media time starts at 0 when :meth:`start` is called and does not advance
    while paused. All methods are thread-safe.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._segments: list[_Segment] = []
        self._stopped = False

    # -- state changes -------------------------------------------------
    def start(self, wall_ns: int) -> None:
        with self._cond:
            if self._segments:
                raise RuntimeError("timeline already started")
            self._segments.append(_Segment(wall_ns, 0))
            self._cond.notify_all()

    def pause(self, wall_ns: int) -> int:
        """Pause at ``wall_ns``; returns the media time at the pause."""
        with self._cond:
            seg = self._active()
            if seg is None:
                raise RuntimeError("timeline not running")
            wall_ns = max(wall_ns, seg.wall_start)
            seg.wall_end = wall_ns
            return seg.media_start + (wall_ns - seg.wall_start)

    def resume(self, wall_ns: int) -> None:
        with self._cond:
            if not self._segments or self._segments[-1].wall_end is None:
                raise RuntimeError("timeline not paused")
            last = self._segments[-1]
            assert last.wall_end is not None
            media = last.media_start + (last.wall_end - last.wall_start)
            self._segments.append(_Segment(max(wall_ns, last.wall_end), media))
            self._cond.notify_all()

    def stop(self, wall_ns: int) -> int:
        """Stop at ``wall_ns`` (pausing if running); returns final media time."""
        with self._cond:
            self._stopped = True
            self._cond.notify_all()
            if not self._segments:
                return 0
            seg = self._segments[-1]
            if seg.wall_end is None:
                seg.wall_end = max(wall_ns, seg.wall_start)
            return seg.media_start + (seg.wall_end - seg.wall_start)

    # -- queries -------------------------------------------------------
    def _active(self) -> _Segment | None:
        if self._segments and self._segments[-1].wall_end is None:
            return self._segments[-1]
        return None

    @property
    def generation(self) -> int:
        """Number of recording segments so far (increments on every resume)."""
        with self._lock:
            return len(self._segments)

    @property
    def started(self) -> bool:
        with self._lock:
            return bool(self._segments)

    @property
    def paused(self) -> bool:
        return self.is_paused()

    def is_paused(self) -> bool:
        """Method form of :attr:`paused` (re-evaluated on each call; another
        thread may pause or resume at any time)."""
        with self._lock:
            return bool(self._segments) and self._segments[-1].wall_end is not None

    @property
    def stopped(self) -> bool:
        with self._lock:
            return self._stopped

    def media_ns(self, wall_ns: int) -> int | None:
        """Media time of ``wall_ns`` or ``None`` if it falls outside recording."""
        with self._lock:
            for seg in reversed(self._segments):
                if wall_ns >= seg.wall_start:
                    if seg.wall_end is not None and wall_ns >= seg.wall_end:
                        return None
                    return seg.media_start + (wall_ns - seg.wall_start)
            return None

    def media_now(self, wall_now: int) -> int:
        """Media time reached at ``wall_now`` (clamped while paused)."""
        with self._lock:
            if not self._segments:
                return 0
            seg = self._segments[-1]
            end = wall_now if seg.wall_end is None else min(wall_now, seg.wall_end)
            return seg.media_start + max(0, end - seg.wall_start)

    def wall_for_media(self, media_ns: int) -> int | None:
        """Wall time at which the *active* segment reaches ``media_ns``."""
        with self._lock:
            seg = self._active()
            if seg is None:
                return None
            return seg.wall_start + (media_ns - seg.media_start)

    def wait_running(self, timeout_s: float) -> bool:
        """Block until the timeline is running (not paused) or stopped."""
        with self._cond:
            return self._cond.wait_for(
                lambda: self._stopped or self._active() is not None, timeout=timeout_s
            )
