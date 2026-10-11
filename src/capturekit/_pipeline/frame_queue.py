"""Bounded frame queue with duplicate-slot merging, plus a buffer pool.

Only *new* frames hold buffers and count against the capacity. A duplicate
slot ("repeat the previous frame") is a counter merged into the queue tail,
so the pacer never blocks and memory stays bounded even when the encoder
falls behind.
"""

from __future__ import annotations

import collections
import threading
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

Buffer = npt.NDArray[np.uint8]


@dataclass(slots=True)
class FrameItem:
    buffer: Buffer
    timestamp_ns: int


@dataclass(slots=True)
class DupItem:
    count: int


QueueItem = FrameItem | DupItem


class FrameQueue:
    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._items: collections.deque[QueueItem] = collections.deque()
        self._frames = 0
        self._closed = False
        self._cond = threading.Condition()
        self.max_depth = 0

    def put_frame(self, item: FrameItem) -> bool:
        """Enqueue a new frame. Returns False (not enqueued) if full."""
        with self._cond:
            if self._closed:
                return False
            if self._frames >= self._capacity:
                return False
            self._items.append(item)
            self._frames += 1
            self.max_depth = max(self.max_depth, self._frames)
            self._cond.notify()
            return True

    def put_dup(self, count: int = 1) -> None:
        with self._cond:
            if self._closed or count <= 0:
                return
            if self._items and isinstance(self._items[-1], DupItem):
                self._items[-1].count += count
            else:
                self._items.append(DupItem(count))
            self._cond.notify()

    def get(self, timeout_s: float | None = None) -> QueueItem | None:
        """Next item, or ``None`` when closed and drained (or timed out)."""
        with self._cond:
            if not self._cond.wait_for(lambda: self._items or self._closed, timeout=timeout_s):
                return None
            if not self._items:
                return None
            item = self._items.popleft()
            if isinstance(item, FrameItem):
                self._frames -= 1
            return item

    def close(self) -> None:
        """No more puts; consumers drain remaining items then get ``None``."""
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    def drain(self) -> list[QueueItem]:
        with self._cond:
            items = list(self._items)
            self._items.clear()
            self._frames = 0
            return items

    @property
    def depth(self) -> int:
        with self._cond:
            return self._frames


class BufferPool:
    """Recycles fixed-shape frame buffers to avoid per-frame large allocations."""

    def __init__(self, shape: tuple[int, int, int], max_buffers: int) -> None:
        self.shape = shape
        self._max = max_buffers
        self._free: list[Buffer] = []
        self._allocated = 0
        self._lock = threading.Lock()

    def acquire(self) -> Buffer | None:
        """A free buffer, or ``None`` if ``max_buffers`` are all in use."""
        with self._lock:
            if self._free:
                return self._free.pop()
            if self._allocated >= self._max:
                return None
            self._allocated += 1
        return np.zeros(self.shape, dtype=np.uint8)

    def release(self, buf: Buffer) -> None:
        with self._lock:
            self._free.append(buf)
