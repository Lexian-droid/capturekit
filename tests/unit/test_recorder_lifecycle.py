"""Recorder lifecycle tests with fake backends (no hardware, any platform).

These cover behaviour that cannot be forced reliably on real devices:
start/stop races, device loss under each policy, encoder back-pressure and
slot accounting.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import ClassVar

import numpy as np
import pytest

import capturekit as ck
from capturekit._backends.base import AcquiredFrame, AudioPacket, VideoBackendInfo
from capturekit._pipeline.clock import now_ns
from capturekit.recording import Recorder, RecorderState


class FakeVideo:
    def __init__(self, size: tuple[int, int] = (64, 48)) -> None:
        self.info = VideoBackendInfo("fake-video", size)
        self.size = size
        self.n = 0
        self.closed = False
        self.lost_after: int | None = None

    def start(self) -> None:
        pass

    def acquire(self, out, timeout_s: float):  # type: ignore[no-untyped-def]
        self.n += 1
        if self.lost_after is not None and self.n > self.lost_after:
            raise ck.WindowClosedError("fake window closed")
        w, h = self.size
        dest = out if out is not None else np.empty((h, w, 4), np.uint8)
        dest[...] = self.n % 256
        return AcquiredFrame(dest, now_ns(), self.size)

    def close(self) -> None:
        self.closed = True


class FakeAudio:
    name = "fake-audio"
    sample_rate = 48_000
    channels = 2

    def __init__(self) -> None:
        self.stop_evt = threading.Event()
        self.thread: threading.Thread | None = None
        self.fail_with: BaseException | None = None
        self.fail_after_s = 0.3
        self.started = False
        self.stopped = False

    def start(
        self, on_packet: Callable[[AudioPacket], None], on_error: Callable[[BaseException], None]
    ) -> None:
        self.started = True

        def run() -> None:
            t0 = time.monotonic()
            while not self.stop_evt.wait(0.01):
                on_packet(AudioPacket(np.full((480, 2), 0.1, np.float32), now_ns() - 10_000_000))
                if self.fail_with is not None and time.monotonic() - t0 > self.fail_after_s:
                    on_error(self.fail_with)
                    return

        self.thread = threading.Thread(target=run, name="capturekit-fake-audio", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stopped = True
        self.stop_evt.set()
        if self.thread is not None:
            self.thread.join(2)


class FakeFFmpeg:
    write_delay_s = 0.0
    instances: ClassVar[list[FakeFFmpeg]] = []

    def __init__(self, args: list[str]) -> None:
        self.args = args
        self.frames = 0
        self.closed = False
        self.proc = SimpleNamespace(poll=lambda: None, kill=lambda: None)
        self.stderr_tail = ""
        FakeFFmpeg.instances.append(self)

    def write(self, data: memoryview) -> None:
        if self.write_delay_s:
            time.sleep(self.write_delay_s)
        self.frames += 1

    def close_stdin(self) -> None:
        self.closed = True

    def wait(self, timeout: float) -> int:
        return 0

    def kill(self) -> None:
        pass


@pytest.fixture
def fake_platform(monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    state = SimpleNamespace(video=FakeVideo(), audio=[], delay_open_video=0.0)

    def open_video(_src: object) -> FakeVideo:
        time.sleep(state.delay_open_video)
        return state.video

    def open_audio(_spec: object, _rate: int, _ch: int) -> FakeAudio:
        a = FakeAudio()
        state.audio.append(a)
        if getattr(state, "audio_fail", None) is not None:
            a.fail_with = state.audio_fail
        return a

    api = SimpleNamespace(
        thread_context=contextlib.nullcontext, open_video=open_video, open_audio=open_audio
    )
    monkeypatch.setattr("capturekit._platform.backend", lambda: api)
    monkeypatch.setattr("capturekit.recording.ff.find_ffmpeg", lambda _p=None: "ffmpeg")
    monkeypatch.setattr("capturekit.recording.ff.select_encoder", lambda *_a: "fake")
    monkeypatch.setattr("capturekit.recording.ff.FFmpegProcess", FakeFFmpeg)
    FakeFFmpeg.instances = []
    FakeFFmpeg.write_delay_s = 0.0
    yield state
    deadline = time.time() + 5
    while time.time() < deadline and _threads():
        time.sleep(0.05)
    assert not _threads(), f"leaked threads: {_threads()}"


def _threads() -> set[str]:
    return {t.name for t in threading.enumerate() if t.name.startswith("capturekit")}


VIDEO = ck.MonitorSource(0)
MIC = ck.MicrophoneSource()


def test_slot_accounting_invariants(fake_platform, tmp_path) -> None:
    rec = Recorder(VIDEO, [], tmp_path / "a.mkv", ck.RecordingConfig(fps=50))
    rec.start()
    time.sleep(1.0)
    res = rec.stop()
    v = res.stats.video
    assert v is not None
    assert v.slots == pytest.approx(50, abs=3)
    assert v.frames_new + v.frames_duplicated + v.frames_dropped + v.frames_late == v.slots
    assert v.frames_encoded == v.slots == FakeFFmpeg.instances[0].frames
    assert FakeFFmpeg.instances[0].closed and fake_platform.video.closed


def test_slow_encoder_drops_are_accounted_and_bounded(fake_platform, tmp_path) -> None:
    FakeFFmpeg.write_delay_s = 0.05  # 20 writes/s against 60 fps
    rec = Recorder(VIDEO, [], tmp_path / "a.mkv", ck.RecordingConfig(fps=60, queue_frames=3))
    rec.start()
    time.sleep(1.0)
    v = rec.stats.video
    assert v is not None and v.frames_dropped > 0
    assert v.queue_depth_max <= 3  # memory stays bounded
    res = rec.stop()
    v = res.stats.video
    assert v is not None
    assert v.frames_new + v.frames_duplicated + v.frames_dropped + v.frames_late == v.slots
    assert FakeFFmpeg.instances[0].frames == v.slots  # every slot is written (CFR)


def test_stop_during_starting_tears_everything_down(fake_platform) -> None:
    fake_platform.delay_open_video = 0.3
    stopped: list[ck.RecordingResult] = []

    def on_state(state: RecorderState) -> None:
        if state is RecorderState.STARTING:
            stopped.append(rec.stop())  # from the callback thread, while start() runs

    rec = Recorder(VIDEO, [MIC], None, ck.RecordingConfig(fps=30), on_state_change=on_state)
    rec.start()
    assert rec.wait(5)
    assert rec.state is RecorderState.STOPPED
    for a in fake_platform.audio:
        assert not a.started or a.stopped, "audio source left running"
    time.sleep(0.2)
    assert stopped and stopped[0].error is None


def test_audio_device_lost_stop_policy_stops_without_raising(fake_platform) -> None:
    fake_platform.audio_fail = ck.AudioDeviceDisconnectedError("unplugged")
    errors: list[BaseException] = []
    rec = Recorder(None, [MIC], None, ck.RecordingConfig(), on_error=errors.append)
    rec.start()
    assert rec.wait(5), "STOP policy must stop the recorder"
    res = rec.stop()  # target lost is not fatal: no raise
    assert isinstance(res.error, ck.AudioDeviceDisconnectedError)
    time.sleep(0.1)
    assert errors and isinstance(errors[0], ck.AudioDeviceDisconnectedError)


def test_audio_device_lost_continue_policy_keeps_recording(fake_platform) -> None:
    fake_platform.audio_fail = ck.AudioDeviceDisconnectedError("unplugged")
    rec = Recorder(
        VIDEO, [MIC], None, ck.RecordingConfig(fps=30, on_target_lost=ck.TargetLostPolicy.CONTINUE)
    )
    rec.start()
    time.sleep(1.0)
    assert rec.state is RecorderState.RECORDING
    res = rec.stop()
    assert isinstance(res.error, ck.AudioDeviceDisconnectedError)
    # The dead source is padded with silence to the full duration.
    assert res.stats.audio[0].duration_s == pytest.approx(res.stats.media_duration_s, abs=0.05)


def test_fatal_audio_error_fails_recording(fake_platform) -> None:
    fake_platform.audio_fail = ck.AudioDeviceError("driver exploded")
    rec = Recorder(None, [MIC], None, ck.RecordingConfig())
    rec.start()
    assert rec.wait(5)
    assert rec.state is RecorderState.FAILED
    with pytest.raises(ck.AudioDeviceError):
        rec.stop()


def test_video_target_lost_stop_policy(fake_platform, tmp_path) -> None:
    fake_platform.video.lost_after = 20
    rec = Recorder(VIDEO, [], tmp_path / "a.mkv", ck.RecordingConfig(fps=60))
    rec.start()
    assert rec.wait(5)
    res = rec.stop()
    assert isinstance(res.error, ck.WindowClosedError)
    assert res.stats.video is not None and res.stats.video.target_lost


def test_pause_excludes_time_from_both_tracks(fake_platform) -> None:
    chunks: list[ck.AudioChunk] = []
    rec = Recorder(VIDEO, [MIC], None, ck.RecordingConfig(fps=40), on_audio=chunks.append)
    rec.start()
    time.sleep(0.5)
    rec.pause()
    time.sleep(0.6)
    rec.resume()
    time.sleep(0.5)
    res = rec.stop()
    assert res.stats.media_duration_s == pytest.approx(1.0, abs=0.06)
    v = res.stats.video
    assert v is not None and v.slots == pytest.approx(40, abs=3)
    assert res.stats.audio[0].duration_s == pytest.approx(res.stats.media_duration_s, abs=0.02)
    time.sleep(0.2)
    # Audio positions are contiguous across the pause: no gap, no overlap.
    pos = 0
    for c in sorted(chunks, key=lambda c: c.timestamp_ns):
        assert abs(c.timestamp_ns - pos * 1_000_000_000 // 48_000) < 1_000_000
        pos += c.frames
    assert all(not c.data.flags.writeable for c in chunks)


def test_start_failure_cleans_up(fake_platform, monkeypatch) -> None:
    def boom(*_a: object) -> str:
        raise ck.EncoderNotAvailableError("no encoder")

    monkeypatch.setattr("capturekit.recording.ff.select_encoder", boom)
    rec = Recorder(VIDEO, [MIC], "never.mkv", ck.RecordingConfig())
    t = time.monotonic()
    with pytest.raises(ck.EncoderNotAvailableError):
        rec.start()
    assert time.monotonic() - t < 5, "startup failure must not stall on pipe connects"
    assert rec.state is RecorderState.FAILED
