from __future__ import annotations

import numpy as np
import pytest

from capturekit._pipeline.aligner import AudioAligner
from capturekit._pipeline.frame_queue import BufferPool, DupItem, FrameItem, FrameQueue
from capturekit._pipeline.mixer import AudioMixer
from capturekit._pipeline.timeline import Timeline

S = 1_000_000_000
RATE = 48_000


# --- timeline -------------------------------------------------------------------


def test_timeline_maps_and_excludes_pauses() -> None:
    tl = Timeline()
    tl.start(10 * S)
    assert tl.media_ns(10 * S + 5) == 5
    assert tl.media_ns(9 * S) is None
    assert tl.pause(12 * S) == 2 * S
    assert tl.paused
    assert tl.media_ns(13 * S) is None
    assert tl.media_now(13 * S) == 2 * S
    tl.resume(15 * S)
    assert tl.media_ns(16 * S) == 3 * S
    assert tl.media_ns(11 * S) == 1 * S  # history is preserved
    assert tl.wall_for_media(4 * S) == 17 * S
    assert tl.generation == 2
    assert tl.stop(20 * S) == 7 * S


def test_timeline_state_errors() -> None:
    tl = Timeline()
    with pytest.raises(RuntimeError):
        tl.pause(0)
    tl.start(0)
    with pytest.raises(RuntimeError):
        tl.resume(1)
    with pytest.raises(RuntimeError):
        tl.start(1)


# --- frame queue ------------------------------------------------------------------


def test_frame_queue_capacity_and_dup_merging() -> None:
    q = FrameQueue(2)
    a = np.zeros((2, 2, 4), np.uint8)
    assert q.put_frame(FrameItem(a, 1))
    q.put_dup()
    q.put_dup(2)
    assert q.put_frame(FrameItem(a, 2))
    assert not q.put_frame(FrameItem(a, 3))  # full: only frames count
    q.put_dup()
    items = [q.get(0), q.get(0), q.get(0), q.get(0)]
    assert isinstance(items[0], FrameItem)
    assert isinstance(items[1], DupItem) and items[1].count == 3
    assert isinstance(items[2], FrameItem)
    assert isinstance(items[3], DupItem) and items[3].count == 1
    q.close()
    assert q.get() is None


def test_buffer_pool_is_bounded() -> None:
    pool = BufferPool((4, 4, 4), 2)
    a, b = pool.acquire(), pool.acquire()
    assert a is not None and b is not None
    assert pool.acquire() is None
    pool.release(a)
    assert pool.acquire() is a


# --- aligner ----------------------------------------------------------------------


def _packets(
    start_ns: int,
    seconds: float,
    ppm: float = 0.0,
    jitter_ns: int = 0,
    size: int = 480,
    seed: int = 1,
):
    """Packets from a device clock running ``ppm`` fast relative to QPC."""
    rng = np.random.default_rng(seed)
    total = int(RATE * seconds)
    produced = 0
    while produced < total:
        # device has produced `produced` samples; in QPC time that took less/more
        t = start_ns + int(produced / RATE / (1 + ppm * 1e-6) * S)
        t += int(rng.integers(-jitter_ns, jitter_ns + 1)) if jitter_ns else 0
        yield np.full((size, 2), 0.1, np.float32), t
        produced += size


def _run(aligner: AudioAligner, packets) -> int:
    total = 0
    for data, ts in packets:
        for blk in aligner.push(data, ts):
            total += len(blk)
    return total


def test_aligner_exact_when_clocks_match() -> None:
    tl = Timeline()
    tl.start(0)
    al = AudioAligner(tl)
    total = _run(al, _packets(0, 10.0))
    assert total == RATE * 10
    assert al.stats.silence_inserted == 0 and al.stats.drift_corrected == 0


@pytest.mark.parametrize("ppm", [-500.0, -300.0, 100.0, 500.0])
def test_aligner_corrects_drift_over_one_hour(ppm: float) -> None:
    tl = Timeline()
    tl.start(0)
    al = AudioAligner(tl)
    seconds = 3600.0
    start = 5_000_000  # first packet 5 ms after start (> jitter)
    _run(al, _packets(start, seconds, ppm=ppm, jitter_ns=1_000_000))
    # Stream position must track wall time within 20 ms after an hour.
    last_wall = start / S + seconds / (1 + ppm * 1e-6)
    err_ms = abs(al.written / RATE - last_wall) * 1000
    assert err_ms < 20, err_ms
    # Only the initial 5 ms offset is filled with silence; drift is handled by
    # resampling, never by gaps.
    assert al.stats.silence_inserted <= RATE * 0.007
    assert al.stats.samples_trimmed == 0


def test_aligner_fills_gaps_and_trims_overlap() -> None:
    tl = Timeline()
    tl.start(0)
    al = AudioAligner(tl)
    blk = np.ones((480, 2), np.float32)
    al.push(blk, 0)
    out = al.push(blk, S // 2)  # 490 ms gap
    assert len(out) == 2 and np.all(out[0] == 0)
    assert al.written == RATE // 2 + 480
    out = al.push(blk, S // 2 - S // 10)  # 100 ms in the past → trimmed entirely
    assert out == [] and al.stats.samples_trimmed == 480


def test_aligner_heartbeat_and_finish() -> None:
    tl = Timeline()
    tl.start(0)
    al = AudioAligner(tl)
    out = al.heartbeat(S, margin_ns=0)
    assert sum(len(b) for b in out) == RATE
    out = al.finish(2 * S)
    assert sum(len(b) for b in out) == RATE
    assert al.written == 2 * RATE


def test_aligner_pause_resume_has_no_gap() -> None:
    tl = Timeline()
    tl.start(0)
    al = AudioAligner(tl)
    _run(al, _packets(0, 2.0))
    tl.pause(2 * S)
    dropped = _run(al, _packets(2 * S, 1.0))  # during pause → dropped
    assert dropped == 0
    tl.resume(5 * S)
    _run(al, _packets(5 * S + 3_000_000, 2.0))  # 3 ms start latency after resume
    assert abs(al.written - 4 * RATE) <= 480
    assert al.stats.drift_corrected == 0


# --- mixer --------------------------------------------------------------------------


def test_mixer_sums_aligned_inputs_and_clips() -> None:
    out: list[np.ndarray] = []
    mx = AudioMixer(2, 2, out.append)
    mx.push(0, np.full((100, 2), 0.7, np.float32))
    assert out == []  # waiting for input 1
    mx.push(1, np.full((60, 2), 0.6, np.float32))
    assert len(out) == 1 and len(out[0]) == 60 and np.allclose(out[0], 1.0)
    mx.finish_input(1)
    assert sum(len(b) for b in out) == 100
    assert np.allclose(out[-1], 0.7)
