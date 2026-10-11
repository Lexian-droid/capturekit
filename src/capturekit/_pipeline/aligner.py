"""Audio alignment to the media timeline: gap filling, overlap trimming and
gentle drift correction. See the av-sync skill for the model.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from .clock import NS_PER_S
from .timeline import Timeline

Samples = npt.NDArray[np.float32]


@dataclass(slots=True)
class AlignerStats:
    packets: int = 0
    packets_dropped_paused: int = 0
    samples_in: int = 0
    samples_out: int = 0
    silence_inserted: int = 0
    samples_trimmed: int = 0
    drift_corrected: int = 0  # net samples added (+) / removed (-) by resampling
    discontinuities: int = 0
    last_error_samples: float = 0.0


class AudioAligner:
    """Converts timestamped packets into a gapless sample stream.

    ``push`` / ``heartbeat`` / ``finish`` return the blocks to emit, in order.
    One aligner per source; not thread-safe (owned by the source's thread).
    """

    def __init__(
        self,
        timeline: Timeline,
        sample_rate: int = 48_000,
        channels: int = 2,
        *,
        gap_ms: float = 20.0,
        overlap_ms: float = 20.0,
        deadband_ms: float = 2.0,
        max_ratio: float = 0.002,
        smoothing: float = 0.05,
    ) -> None:
        self.timeline = timeline
        self.rate = sample_rate
        self.channels = channels
        self.written = 0
        self._gap = int(sample_rate * gap_ms / 1000)
        self._overlap = int(sample_rate * overlap_ms / 1000)
        self._deadband = sample_rate * deadband_ms / 1000
        self._max_ratio = max_ratio
        self._alpha = smoothing
        self._err = 0.0
        self._have_err = False
        self._synced = False
        self._generation = 0
        self.stats = AlignerStats()

    def _pos(self, media_ns: int) -> int:
        return media_ns * self.rate // NS_PER_S

    def _silence(self, n: int) -> Samples:
        self.stats.silence_inserted += n
        return np.zeros((n, self.channels), dtype=np.float32)

    def push(self, data: Samples, timestamp_ns: int, discontinuity: bool = False) -> list[Samples]:
        st = self.stats
        st.packets += 1
        n = int(data.shape[0])
        st.samples_in += n
        if discontinuity:
            st.discontinuities += 1
        media = self.timeline.media_ns(timestamp_ns)
        if media is None or n == 0:
            st.packets_dropped_paused += 1
            return []
        gen = self.timeline.generation
        if gen != self._generation:
            # New segment (start or resume): re-anchor exactly instead of
            # treating the pause boundary as clock drift.
            self._generation = gen
            self._synced = False
            self._have_err = False
        expected = self._pos(media)
        err = expected - self.written
        out: list[Samples] = []
        if not self._synced:
            # First packet: align exactly (any offset is start-up latency, not drift).
            self._synced = True
            if err > 0:
                out.append(self._silence(err))
                self.written += err
                err = 0
            elif err < 0:
                trim = -err
                st.samples_trimmed += min(trim, n)
                if trim >= n:
                    self._synced = False
                    return out
                data = data[trim:]
                n -= trim
                err = 0
        if err > self._gap:
            out.append(self._silence(err))
            self.written += err
            self._have_err = False
        elif -err > self._overlap:
            trim = -err
            st.samples_trimmed += min(trim, n)
            if trim >= n:
                return out
            data = data[trim:]
            n -= trim
            self._have_err = False
        else:
            # Small error: smooth it (timestamps jitter ~1 ms) and nudge the
            # stream length by resampling at most max_ratio per packet.
            if not self._have_err:
                self._err = float(err)
                self._have_err = True
            else:
                self._err += self._alpha * (err - self._err)
            st.last_error_samples = self._err
            if abs(self._err) > self._deadband:
                limit = max(1, int(n * self._max_ratio))
                corr = int(max(-limit, min(limit, round(self._err * 0.1))))
                if corr:
                    data = _resample(data, n + corr)
                    st.drift_corrected += corr
                    self._err -= corr
                    n += corr
        out.append(np.ascontiguousarray(data, dtype=np.float32))
        self.written += n
        st.samples_out = self.written
        return out

    def heartbeat(self, wall_now_ns: int, margin_ns: int = 60_000_000) -> list[Samples]:
        """Fill silence up to ``now - margin`` when no packets arrive
        (endpoint loopback produces no packets while nothing plays)."""
        if not self.timeline.started:
            return []
        target = self._pos(max(0, self.timeline.media_now(wall_now_ns) - margin_ns))
        missing = target - self.written
        if missing <= 0:
            return []
        self.written += missing
        self.stats.samples_out = self.written
        self._have_err = False
        return [self._silence(missing)]

    def finish(self, media_end_ns: int) -> list[Samples]:
        """Pad with silence so the stream ends exactly at ``media_end_ns``."""
        target = self._pos(media_end_ns)
        missing = target - self.written
        if missing <= 0:
            return []
        self.written += missing
        self.stats.samples_out = self.written
        return [self._silence(missing)]


def _resample(data: Samples, m: int) -> Samples:
    n = data.shape[0]
    if n < 2 or m < 1:
        return data
    x_new = np.linspace(0.0, n - 1, m)
    x_old = np.arange(n, dtype=np.float64)
    out = np.empty((m, data.shape[1]), dtype=np.float32)
    for ch in range(data.shape[1]):
        out[:, ch] = np.interp(x_new, x_old, data[:, ch])
    return out
