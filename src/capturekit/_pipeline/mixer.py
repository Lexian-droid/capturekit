"""Mixes several aligned sample streams into one."""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable

import numpy as np
import numpy.typing as npt

Samples = npt.NDArray[np.float32]


class SampleFifo:
    def __init__(self, channels: int) -> None:
        self.channels = channels
        self._blocks: deque[Samples] = deque()
        self.size = 0

    def push(self, block: Samples) -> None:
        if block.shape[0]:
            self._blocks.append(block)
            self.size += int(block.shape[0])

    def pop(self, n: int) -> Samples:
        out = np.empty((n, self.channels), dtype=np.float32)
        filled = 0
        while filled < n:
            blk = self._blocks[0]
            take = min(n - filled, blk.shape[0])
            out[filled : filled + take] = blk[:take]
            filled += take
            if take == blk.shape[0]:
                self._blocks.popleft()
            else:
                self._blocks[0] = blk[take:]
        self.size -= n
        return out


class AudioMixer:
    """Sums N aligned inputs; emits only samples available on all live inputs.

    Inputs marked finished count as silence so one ended source cannot stall
    the mix. Output is clipped to [-1, 1].
    """

    def __init__(self, inputs: int, channels: int, sink: Callable[[Samples], None]) -> None:
        self._fifos = [SampleFifo(channels) for _ in range(inputs)]
        self._finished = [False] * inputs
        self._sink = sink
        self._lock = threading.Lock()
        self.channels = channels

    def push(self, index: int, block: Samples) -> None:
        with self._lock:
            self._fifos[index].push(block)
            self._emit_locked(final=False)

    def finish_input(self, index: int) -> None:
        with self._lock:
            self._finished[index] = True
            self._emit_locked(final=all(self._finished))

    def _emit_locked(self, final: bool) -> None:
        live = [f.size for f, done in zip(self._fifos, self._finished, strict=True) if not done]
        if live and not final:
            n = min(live)
        else:
            n = max((f.size for f in self._fifos), default=0)
        if n <= 0:
            return
        mix = np.zeros((n, self.channels), dtype=np.float32)
        for f in self._fifos:
            take = min(n, f.size)
            if take:
                mix[:take] += f.pop(take)
        np.clip(mix, -1.0, 1.0, out=mix)
        self._sink(mix)
