"""Recording: synchronised video + audio capture, optionally encoded by FFmpeg.

Thread layout (see docs/architecture.md):

* video thread   – owns the video backend, paces CFR slots, fills FrameQueue
* consumer       – writes frames to FFmpeg stdin and/or dispatches callbacks
* audio threads  – one per source (backend-owned); align + fan out samples
* audio clock    – heartbeat that fills silence when sources deliver nothing
* track writers  – one per FFmpeg audio input pipe
* dispatcher     – runs user callbacks (never on capture threads)
"""

from __future__ import annotations

import enum
import functools
import logging
import os
import queue
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

import numpy as np

from . import _platform
from ._backends.base import AudioBackend, AudioPacket, VideoBackend, blit_clear
from ._pipeline.aligner import AudioAligner, Samples
from ._pipeline.clock import NS_PER_S, now_ns, sleep_until
from ._pipeline.frame_queue import BufferPool, DupItem, FrameItem, FrameQueue
from ._pipeline.mixer import AudioMixer
from ._pipeline.timeline import Timeline
from .config import AudioMixMode, RecordingConfig, TargetLostPolicy
from .encoding import ffmpeg as ff
from .encoding.pipes import OutputPipe, create_pipe
from .errors import (
    CaptureTimeoutError,
    EncoderError,
    RecorderStateError,
    TargetLostError,
)
from .sources import AudioSourceSpec, VideoSource, audio_source_label
from .types import AudioChunk, Frame

log = logging.getLogger("capturekit.recorder")

AUDIO_CHANNELS = 2


class RecorderState(enum.Enum):
    IDLE = "idle"
    STARTING = "starting"
    RECORDING = "recording"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class _Accum:
    __slots__ = ("count", "max", "total")

    def __init__(self) -> None:
        self.count = 0
        self.total = 0.0
        self.max = 0.0

    def add(self, v: float) -> None:
        self.count += 1
        self.total += v
        if v > self.max:
            self.max = v

    @property
    def avg(self) -> float:
        return self.total / self.count if self.count else 0.0


@dataclass(frozen=True, slots=True)
class VideoStats:
    backend: str
    encoder: str | None
    fps: float
    output_size: tuple[int, int]
    content_size: tuple[int, int]
    slots: int  #: frames in the output (= duration * fps)
    frames_new: int  #: slots filled with newly captured content
    frames_duplicated: int  #: slots repeating the previous image (no change)
    frames_dropped: int  #: captured frames discarded because the encoder lagged
    frames_late: int  #: slots skipped because the pacer woke up late
    frames_encoded: int  #: frames written to the encoder (incl. duplicates)
    queue_depth: int
    queue_depth_max: int
    capture_ms_avg: float
    capture_ms_max: float
    pacing_jitter_ms_avg: float
    pacing_jitter_ms_max: float
    encoder_write_ms_avg: float
    encoder_write_ms_max: float
    target_lost: bool


@dataclass(frozen=True, slots=True)
class AudioTrackStats:
    name: str
    device: str
    packets: int
    duration_s: float
    silence_inserted_s: float
    trimmed_s: float
    drift_corrected_samples: int
    discontinuities: int


@dataclass(frozen=True, slots=True)
class RecordingStats:
    state: RecorderState
    output: str | None
    media_duration_s: float
    wall_elapsed_s: float
    video: VideoStats | None
    audio: tuple[AudioTrackStats, ...]
    callbacks_dropped: int


@dataclass(frozen=True, slots=True)
class RecordingResult:
    output: str | None
    stats: RecordingStats
    error: BaseException | None


# --- helpers -------------------------------------------------------------------


class _Dispatcher:
    """Runs user callbacks on one thread; frame callbacks are dropped (and
    counted) when the consumer is slow, lifecycle callbacks never are."""

    def __init__(self) -> None:
        self._q: queue.Queue[tuple[Callable[..., None], tuple[object, ...]] | None] = queue.Queue()
        self.dropped = 0
        self._thread = threading.Thread(target=self._run, name="capturekit-callbacks", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            fn, args = item
            try:
                fn(*args)
            except Exception:
                log.exception("exception in user callback %r", fn)

    def post(self, fn: Callable[..., None], *args: object, droppable: bool = False) -> None:
        if droppable and self._q.qsize() > 64:
            self.dropped += 1
            return
        self._q.put((fn, args))

    def close(self, timeout_s: float = 5.0) -> None:
        self._q.put(None)
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout_s)


class _TrackWriter:
    """Streams one audio track into an FFmpeg input pipe."""

    MAX_QUEUED_S = 30.0

    def __init__(self, label: str, sample_rate: int, on_error: Callable[[BaseException], None]):
        self.pipe: OutputPipe = create_pipe(label)
        self._q: queue.Queue[bytes | None] = queue.Queue()
        self._queued = 0
        self._limit = int(self.MAX_QUEUED_S * sample_rate * AUDIO_CHANNELS * 4)
        self._lock = threading.Lock()
        self._on_error = on_error
        self._failed = False
        self._aborted = False
        self._thread = threading.Thread(
            target=self._run, name=f"capturekit-track-{label}", daemon=True
        )
        self._thread.start()

    def put(self, block: Samples) -> None:
        if self._failed:
            return
        data = np.ascontiguousarray(block, dtype=np.float32).tobytes()
        with self._lock:
            self._queued += len(data)
            over = self._queued > self._limit
        if over:
            self._failed = True
            self._on_error(
                EncoderError(
                    f"FFmpeg is not consuming audio (track backlog > {self.MAX_QUEUED_S:.0f}s)"
                )
            )
            return
        self._q.put(data)

    def _run(self) -> None:
        try:
            self.pipe.connect()
            while True:
                data = self._q.get()
                if data is None:
                    break
                with self._lock:
                    self._queued -= len(data)
                if not self._failed:
                    self.pipe.write(data)
        except BaseException as exc:
            self._failed = True
            if not self._aborted:  # after abort() the cause is reported by FFmpeg's exit
                self._on_error(exc)
        finally:
            self.pipe.close()

    def finish(self) -> None:
        self._q.put(None)

    def abort(self) -> None:
        """Stop writing; unblocks connect() if FFmpeg never opened the pipe."""
        self._aborted = True
        self._failed = True
        self.pipe.abort()

    def join(self, timeout_s: float) -> None:
        self._thread.join(timeout_s)
        if self._thread.is_alive():
            self.abort()
            self._thread.join(2)


class _AudioInput:
    def __init__(
        self, index: int, spec: AudioSourceSpec, backend: AudioBackend, aligner: AudioAligner
    ) -> None:
        self.index = index
        self.spec = spec
        self.name = audio_source_label(spec)
        self.backend = backend
        self.aligner = aligner
        self.lock = threading.Lock()
        self.writer: _TrackWriter | None = None


# --- recorder ------------------------------------------------------------------


class Recorder:
    """Records a video source and/or audio sources.

    ``output=None`` captures without writing a file; use ``on_video_frame`` /
    ``on_audio`` to receive data. Callbacks run on a dedicated dispatcher
    thread; in recorder callbacks ``timestamp_ns`` is *media time* (ns since
    the start of the recording, pauses excluded) so video and audio callbacks
    share one timeline.

    Example::

        rec = Recorder(WindowSource(win), [ProcessAudioSource.for_window(win)],
                       "out.mkv", RecordingConfig(fps=60))
        rec.start(); ...; result = rec.stop()
    """

    def __init__(
        self,
        video: VideoSource | None = None,
        audio: Sequence[AudioSourceSpec] = (),
        output: str | os.PathLike[str] | None = None,
        config: RecordingConfig | None = None,
        *,
        on_error: Callable[[BaseException], None] | None = None,
        on_state_change: Callable[[RecorderState], None] | None = None,
        on_video_frame: Callable[[Frame], None] | None = None,
        on_audio: Callable[[AudioChunk], None] | None = None,
    ) -> None:
        if video is None and not audio:
            raise ValueError("a recording needs a video source and/or audio sources")
        self.video_source = video
        self.audio_sources = tuple(audio)
        self.output = str(Path(output).resolve()) if output is not None else None
        self.config = config or RecordingConfig()
        self._on_error = on_error
        self._on_state = on_state_change
        self._on_frame = on_video_frame
        self._on_audio = on_audio

        self._state = RecorderState.IDLE
        self._state_lock = threading.RLock()
        self._timeline = Timeline()
        self._stop_evt = threading.Event()
        self._stop_requested = threading.Event()
        self._audio_closed = False
        self._done = threading.Event()
        self._error: BaseException | None = None
        self._fatal = False
        self._dispatcher: _Dispatcher | None = None
        self._ffmpeg: ff.FFmpegProcess | None = None
        self._encoder: str | None = None
        self._start_wall = 0
        self._end_media_ns: int | None = None
        self._shutdown_started = False
        self._output_preexisted = True

        # video
        self._video_thread: threading.Thread | None = None
        self._consumer_thread: threading.Thread | None = None
        self._video_ready = threading.Event()
        self._video_error: BaseException | None = None
        self._video_backend_name = ""
        self._out_size = (0, 0)
        self._content_size = (0, 0)
        self._queue = FrameQueue(self.config.queue_frames)
        self._pool: BufferPool | None = None
        self._slots = 0
        self._new = self._dup = self._dropped = self._late = self._encoded = 0
        self._capture_ms = _Accum()
        self._jitter_ms = _Accum()
        self._write_ms = _Accum()
        self._target_lost = False

        # audio
        self._inputs: list[_AudioInput] = []
        self._mix_writer: _TrackWriter | None = None
        self._mixer: AudioMixer | None = None
        self._clock_thread: threading.Thread | None = None

    # -- public API ------------------------------------------------------------
    @property
    def state(self) -> RecorderState:
        return self._state

    @property
    def error(self) -> BaseException | None:
        return self._error

    def start(self) -> Recorder:
        with self._state_lock:
            if self._state is not RecorderState.IDLE:
                raise RecorderStateError(f"cannot start from state {self._state.value}")
            self._set_state(RecorderState.STARTING)
        try:
            self._start()
        except BaseException as exc:
            with self._state_lock:
                self._error, self._fatal = exc, True
            self._shutdown(time.monotonic() + 60)
            self._remove_partial_output()
            raise
        # stop() or a worker failure during STARTING only *requests* a stop;
        # the starting thread performs it once initialisation has finished.
        with self._state_lock:
            aborted = self._stop_requested.is_set()
            if aborted:
                self._set_state(RecorderState.STOPPING)
        if aborted:
            self._shutdown(time.monotonic() + 60)
            if self._fatal and self._error is not None:
                self._remove_partial_output()
                raise self._error
        return self

    def _remove_partial_output(self) -> None:
        """Delete an output file created by a start that failed (never a file
        that existed before start; overwrite=False guarantees it was new)."""
        if self.output is None or self._ffmpeg is None or self._output_preexisted:
            return
        try:
            Path(self.output).unlink(missing_ok=True)
        except OSError as exc:
            log.warning("could not remove partial output %s: %s", self.output, exc)

    def pause(self) -> None:
        with self._state_lock:
            if self._state is not RecorderState.RECORDING:
                raise RecorderStateError(f"cannot pause from state {self._state.value}")
            self._timeline.pause(now_ns())
            self._set_state(RecorderState.PAUSED)

    def resume(self) -> None:
        with self._state_lock:
            if self._state is not RecorderState.PAUSED:
                raise RecorderStateError(f"cannot resume from state {self._state.value}")
            self._timeline.resume(now_ns())
            self._set_state(RecorderState.RECORDING)

    def stop(self, timeout: float = 60.0) -> RecordingResult:
        """Stop, finalise the output and return the result.

        Raises the first fatal error (encoder/backend failure). A lost target
        under ``TargetLostPolicy.STOP`` is reported in ``result.error`` and via
        ``on_error`` but does not raise.
        """
        deadline = time.monotonic() + timeout
        with self._state_lock:
            if self._state is RecorderState.IDLE:
                raise RecorderStateError("recorder was never started")
            self._stop_requested.set()
            run_here = self._state in (RecorderState.RECORDING, RecorderState.PAUSED)
            if run_here:
                self._set_state(RecorderState.STOPPING)
        if run_here:
            self._shutdown(deadline)
        if not self._done.wait(max(0.0, deadline - time.monotonic())):
            raise CaptureTimeoutError("recorder did not stop in time")
        if self._fatal and self._error is not None:
            raise self._error
        return RecordingResult(self.output, self.stats, self._error)

    def wait(self, timeout: float | None = None) -> bool:
        """Block until the recorder has stopped (e.g. after the target was lost)."""
        return self._done.wait(timeout)

    def __enter__(self) -> Recorder:
        return self.start()

    def __exit__(
        self, et: type[BaseException] | None, ev: BaseException | None, tb: TracebackType | None
    ) -> None:
        if self._state not in (RecorderState.IDLE,):
            try:
                self.stop()
            except BaseException:
                if et is None:
                    raise
                # The body's exception propagates; never drop ours silently.
                log.exception("Recorder.stop() failed while handling another exception")

    @property
    def stats(self) -> RecordingStats:
        now = now_ns()
        fps = self.config.fps
        media = (
            self._end_media_ns
            if self._end_media_ns is not None
            else self._timeline.media_now(now)
            if self._timeline.started
            else 0
        )
        video = None
        if self.video_source is not None:
            video = VideoStats(
                backend=self._video_backend_name,
                encoder=self._encoder,
                fps=fps,
                output_size=self._out_size,
                content_size=self._content_size,
                slots=self._slots,
                frames_new=self._new,
                frames_duplicated=self._dup,
                frames_dropped=self._dropped,
                frames_late=self._late,
                frames_encoded=self._encoded,
                queue_depth=self._queue.depth,
                queue_depth_max=self._queue.max_depth,
                capture_ms_avg=self._capture_ms.avg,
                capture_ms_max=self._capture_ms.max,
                pacing_jitter_ms_avg=self._jitter_ms.avg,
                pacing_jitter_ms_max=self._jitter_ms.max,
                encoder_write_ms_avg=self._write_ms.avg,
                encoder_write_ms_max=self._write_ms.max,
                target_lost=self._target_lost,
            )
        rate = self.config.audio_sample_rate
        tracks = tuple(
            AudioTrackStats(
                name=i.name,
                device=getattr(i.backend, "device_name", ""),
                packets=i.aligner.stats.packets,
                duration_s=i.aligner.written / rate,
                silence_inserted_s=i.aligner.stats.silence_inserted / rate,
                trimmed_s=i.aligner.stats.samples_trimmed / rate,
                drift_corrected_samples=i.aligner.stats.drift_corrected,
                discontinuities=i.aligner.stats.discontinuities,
            )
            for i in self._inputs
        )
        return RecordingStats(
            state=self._state,
            output=self.output,
            media_duration_s=media / NS_PER_S,
            wall_elapsed_s=(now - self._start_wall) / NS_PER_S if self._start_wall else 0.0,
            video=video,
            audio=tracks,
            callbacks_dropped=self._dispatcher.dropped if self._dispatcher else 0,
        )

    # -- internals ---------------------------------------------------------------
    def _set_state(self, state: RecorderState) -> None:
        self._state = state
        if self._on_state is not None and self._dispatcher is not None:
            self._dispatcher.post(self._on_state, state)

    def _report_error(self, exc: BaseException, fatal: bool) -> None:
        with self._state_lock:
            first = self._error is None
            if first:
                self._error = exc
                self._fatal = fatal
        if first and self._on_error is not None and self._dispatcher is not None:
            self._dispatcher.post(self._on_error, exc)
        if not first:
            log.debug("subsequent error ignored: %r", exc)

    def _fail(self, exc: BaseException, fatal: bool = True) -> None:
        """Called from worker threads: report and stop asynchronously."""
        self._report_error(exc, fatal)
        with self._state_lock:
            self._stop_requested.set()
            if self._state not in (RecorderState.RECORDING, RecorderState.PAUSED):
                return  # STARTING: start() performs the shutdown; else already stopping
            self._set_state(RecorderState.STOPPING)
        threading.Thread(
            target=self._shutdown,
            args=(time.monotonic() + 120,),
            name="capturekit-shutdown",
            daemon=True,
        ).start()

    def _on_audio_error(self, exc: BaseException) -> None:
        if isinstance(exc, TargetLostError) and (
            self.config.on_target_lost is TargetLostPolicy.CONTINUE
        ):
            # e.g. headset unplugged: keep recording; the clock heartbeat fills
            # this track with silence.
            self._report_error(exc, fatal=False)
            return
        self._fail(exc, fatal=not isinstance(exc, TargetLostError))

    def _start(self) -> None:
        cfg = self.config
        self._dispatcher = _Dispatcher()
        if self._on_state is not None:
            self._dispatcher.post(self._on_state, RecorderState.STARTING)
        if self.output is not None:
            out = Path(self.output)
            if not out.parent.is_dir():
                raise FileNotFoundError(f"output directory {out.parent} does not exist")
            self._output_preexisted = out.exists()
            if self._output_preexisted and not cfg.overwrite:
                raise FileExistsError(f"{out} exists (set RecordingConfig.overwrite=True)")

        if self.video_source is not None:
            self._video_thread = threading.Thread(
                target=self._video_main, name="capturekit-video", daemon=True
            )
            self._video_thread.start()
            if not self._video_ready.wait(cfg.start_timeout_s + 5):
                raise CaptureTimeoutError("video source did not start in time")
            if self._video_error is not None:
                raise self._video_error
        if self._stop_requested.is_set():
            return

        api = _platform.backend()
        rate = cfg.audio_sample_rate
        for i, spec in enumerate(self.audio_sources):
            backend = api.open_audio(spec, rate, AUDIO_CHANNELS)
            self._inputs.append(
                _AudioInput(i, spec, backend, AudioAligner(self._timeline, rate, AUDIO_CHANNELS))
            )

        audio_inputs: list[ff.AudioInput] = []
        if self.output is not None:
            mode = cfg.audio_mix
            if mode in (AudioMixMode.SEPARATE, AudioMixMode.BOTH) or len(self._inputs) == 1:
                for inp in self._inputs:
                    inp.writer = _TrackWriter(f"a{inp.index}", rate, self._fail)
                    audio_inputs.append(
                        ff.AudioInput(inp.writer.pipe.path, rate, AUDIO_CHANNELS, inp.name)
                    )
            if mode in (AudioMixMode.MIXED, AudioMixMode.BOTH) and len(self._inputs) > 1:
                self._mix_writer = _TrackWriter("mix", rate, self._fail)
                audio_inputs.append(
                    ff.AudioInput(self._mix_writer.pipe.path, rate, AUDIO_CHANNELS, "mix")
                )
        if (
            len(self._inputs) > 1
            and (cfg.audio_mix is not AudioMixMode.SEPARATE or self.output is None)
            and (self._mix_writer is not None or self._on_audio is not None)
        ):
            sink = self._mix_writer.put if self._mix_writer is not None else (lambda _b: None)
            self._mixer = AudioMixer(len(self._inputs), AUDIO_CHANNELS, sink)

        if self._stop_requested.is_set():
            return
        # Audio sources start before FFmpeg so a source that cannot open fails
        # before any output file exists; packets that arrive before the
        # timeline starts are discarded by the aligners.
        for inp in self._inputs:
            inp.backend.start(functools.partial(self._on_packet, inp), self._on_audio_error)
        if self._inputs:
            self._clock_thread = threading.Thread(
                target=self._audio_clock_main, name="capturekit-audio-clock", daemon=True
            )
            self._clock_thread.start()
        if self._stop_requested.is_set():
            return
        if self.output is not None:
            exe = ff.find_ffmpeg(cfg.encoder.ffmpeg_path)
            if self.video_source is not None:
                self._encoder = ff.select_encoder(exe, cfg.encoder, self._out_size)
            cmd = ff.build_command(
                exe,
                output=self.output,
                container=ff.container_for(self.output, cfg.container),
                encoder=self._encoder,
                cfg=cfg.encoder,
                video_size=self._out_size if self.video_source is not None else None,
                fps=cfg.fps,
                audio_inputs=audio_inputs,
                overwrite=cfg.overwrite,
            )
            log.debug("ffmpeg command: %s", cmd)
            self._ffmpeg = ff.FFmpegProcess(cmd)

        if self.video_source is not None:
            self._consumer_thread = threading.Thread(
                target=self._consumer_main, name="capturekit-encoder", daemon=True
            )
            self._consumer_thread.start()

        with self._state_lock:
            self._start_wall = now_ns()
            self._timeline.start(self._start_wall)
            if self._state is RecorderState.STARTING:
                self._set_state(RecorderState.RECORDING)

    # -- video -----------------------------------------------------------------
    def _video_main(self) -> None:
        cfg = self.config
        api = _platform.backend()
        backend: VideoBackend | None = None
        try:
            with api.thread_context():
                backend = api.open_video(self.video_source)
                try:
                    backend.start()
                    self._video_backend_name = backend.info.name
                    first = backend.acquire(None, cfg.start_timeout_s)
                    if first is None:
                        raise CaptureTimeoutError(
                            f"no frame from the video source within {cfg.start_timeout_s:.1f}s"
                        )
                    fh, fw = first.data.shape[:2]
                    ow, oh = cfg.output_size or (fw, fh)
                    ow, oh = max(2, ow - ow % 2), max(2, oh - oh % 2)
                    self._out_size = (ow, oh)
                    self._content_size = first.content_size
                    self._pool = BufferPool((oh, ow, 4), cfg.queue_frames + 3)
                    first_buf = self._pool.acquire()
                    assert first_buf is not None
                    ch, cw = min(fh, oh), min(fw, ow)
                    first_buf[:ch, :cw] = first.data[:ch, :cw]
                    blit_clear(first_buf, cw, ch)
                except BaseException as exc:
                    self._video_error = exc
                    return
                finally:
                    self._video_ready.set()
                self._pace(backend, first_buf)
        except BaseException as exc:
            self._fail(exc)
        finally:
            if backend is not None:
                try:
                    backend.close()
                except Exception:
                    log.exception("closing video backend failed")
            self._queue.close()

    def _pace(self, backend: VideoBackend, first_buf: np.ndarray) -> None:
        assert self._pool is not None
        tl = self._timeline
        period = NS_PER_S / self.config.fps
        pool, q = self._pool, self._queue
        pending_first: np.ndarray | None = first_buf
        n = 0
        while not self._stop_evt.is_set():
            if not tl.started or tl.paused:
                tl.wait_running(0.05)
                continue
            deadline = tl.wall_for_media(int(n * period))
            if deadline is None:
                continue
            if not sleep_until(deadline, self._stop_evt):
                break
            now = now_ns()
            if tl.is_paused():  # paused while we slept
                continue
            self._jitter_ms.add(max(0, now - deadline) / 1e6)
            current = int(tl.media_now(now) // period)
            if current > n and pending_first is None:
                q.put_dup(current - n)
                self._late += current - n
                n = current
            if self._target_lost:
                if pending_first is not None:
                    q.put_frame(FrameItem(pending_first, now))
                    pending_first = None
                else:
                    q.put_dup(1)
                    self._dup += 1
                n += 1
                self._slots = n
                continue
            buf = pool.acquire()
            if buf is None:
                q.put_dup(1)
                self._dropped += 1
            else:
                t0 = now_ns()
                try:
                    res = backend.acquire(buf, 0)
                except TargetLostError as exc:
                    pool.release(buf)
                    lost_policy = self.config.on_target_lost
                    self._target_lost = True
                    if lost_policy is TargetLostPolicy.STOP:
                        self._fail(exc, fatal=False)
                        if pending_first is not None:
                            q.put_frame(FrameItem(pending_first, now))
                            n += 1
                            self._slots = n
                        break
                    self._report_error(exc, fatal=False)
                    res = None
                    buf = None
                self._capture_ms.add((now_ns() - t0) / 1e6)
                if res is not None:
                    assert buf is not None
                    self._content_size = res.content_size
                    if pending_first is not None:
                        pool.release(pending_first)
                        pending_first = None
                    if q.put_frame(FrameItem(buf, res.timestamp_ns)):
                        self._new += 1
                    else:
                        pool.release(buf)
                        q.put_dup(1)
                        self._dropped += 1
                else:
                    if buf is not None:
                        pool.release(buf)
                    if pending_first is not None:
                        if q.put_frame(FrameItem(pending_first, now)):
                            self._new += 1
                        pending_first = None
                    else:
                        q.put_dup(1)
                        self._dup += 1
            n += 1
            self._slots = n

    def _consumer_main(self) -> None:
        q, pool = self._queue, self._pool
        assert pool is not None
        last: np.ndarray | None = None
        slot = 0
        period = NS_PER_S / self.config.fps
        ffm = self._ffmpeg
        try:
            while True:
                item = q.get()
                if item is None:
                    break
                if isinstance(item, FrameItem):
                    if ffm is not None:
                        t0 = now_ns()
                        ffm.write(memoryview(item.buffer).cast("B"))
                        self._write_ms.add((now_ns() - t0) / 1e6)
                    self._encoded += 1
                    if self._on_frame is not None and self._dispatcher is not None:
                        f = Frame(item.buffer.copy(), int(slot * period), slot, self._content_size)
                        self._dispatcher.post(self._on_frame, f, droppable=True)
                    if last is not None:
                        pool.release(last)
                    last = item.buffer
                    slot += 1
                elif isinstance(item, DupItem):
                    if last is None:
                        continue
                    for _ in range(item.count):
                        if ffm is not None:
                            t0 = now_ns()
                            ffm.write(memoryview(last).cast("B"))
                            self._write_ms.add((now_ns() - t0) / 1e6)
                        self._encoded += 1
                    slot += item.count
        except BaseException as exc:
            self._fail(exc)
            # Keep draining so the video thread never blocks on a full pool.
            while q.get() is not None:
                pass
        finally:
            if ffm is not None:
                ffm.close_stdin()

    # -- audio -------------------------------------------------------------------
    def _on_packet(self, inp: _AudioInput, pkt: AudioPacket) -> None:
        if self._audio_closed:
            return  # a source thread outlived stop(); its data is past the end
        with inp.lock:
            blocks = inp.aligner.push(pkt.data, pkt.timestamp_ns, pkt.discontinuity)
            self._emit_audio(inp, blocks)

    def _emit_audio(self, inp: _AudioInput, blocks: list[Samples]) -> None:
        # The aligner has already advanced `written` past all of `blocks`.
        pos = inp.aligner.written - sum(len(b) for b in blocks)
        for b in blocks:
            if not len(b):
                continue
            start, pos = pos, pos + len(b)
            b.flags.writeable = False  # shared by writer, mixer and callbacks
            if inp.writer is not None:
                inp.writer.put(b)
            if self._mixer is not None:
                self._mixer.push(inp.index, b)
            if self._on_audio is not None and self._dispatcher is not None:
                chunk = AudioChunk(
                    b,
                    start * NS_PER_S // self.config.audio_sample_rate,
                    self.config.audio_sample_rate,
                    inp.name,
                )
                self._dispatcher.post(self._on_audio, chunk, droppable=True)

    def _audio_clock_main(self) -> None:
        while not self._stop_evt.wait(0.02):
            now = now_ns()
            for inp in self._inputs:
                with inp.lock:
                    self._emit_audio(inp, inp.aligner.heartbeat(now))

    # -- shutdown -------------------------------------------------------------------
    def _shutdown(self, deadline: float) -> None:
        with self._state_lock:
            already = self._shutdown_started
            self._shutdown_started = True
        if already:
            self._done.wait(max(0.0, deadline - time.monotonic()))
            return
        try:
            self._shutdown_inner(deadline)
        finally:
            with self._state_lock:
                self._set_state(
                    RecorderState.FAILED if self._fatal and self._error else RecorderState.STOPPED
                )
            # Set before closing the dispatcher: a user callback blocked in
            # stop() must be released before we join the dispatcher thread.
            self._done.set()
            if self._dispatcher is not None:
                self._dispatcher.close()

    def _step(self, what: str, fn: Callable[[], object]) -> None:
        """Run one shutdown stage; record failures but always continue."""
        try:
            fn()
        except BaseException as exc:
            log.exception("shutdown stage %r failed", what)
            self._report_error(exc, fatal=True)

    @staticmethod
    def _left(deadline: float, cap: float) -> float:
        return max(0.1, min(cap, deadline - time.monotonic()))

    def _shutdown_inner(self, deadline: float) -> None:
        end_wall = now_ns()
        final_media = self._timeline.stop(end_wall) if self._timeline.started else 0
        self._stop_evt.set()
        self._stop_requested.set()
        vt = self._video_thread
        if vt is not None and vt is not threading.current_thread():
            vt.join(self._left(deadline, 15))
        self._queue.close()
        if self.video_source is not None and self._timeline.started:
            end_media = int(self._slots * NS_PER_S / self.config.fps)
        else:
            end_media = final_media
        self._end_media_ns = end_media

        for inp in self._inputs:
            self._step(f"stop audio {inp.name}", inp.backend.stop)
        self._audio_closed = True
        if self._clock_thread is not None:
            self._clock_thread.join(self._left(deadline, 5))

        def finish_audio() -> None:
            if self._timeline.started:
                for inp in self._inputs:
                    with inp.lock:
                        self._emit_audio(inp, inp.aligner.finish(end_media))
            if self._mixer is not None:
                for inp in self._inputs:
                    self._mixer.finish_input(inp.index)

        self._step("finish audio", finish_audio)
        writers = [i.writer for i in self._inputs if i.writer is not None]
        if self._mix_writer is not None:
            writers.append(self._mix_writer)
        ffm = self._ffmpeg
        try:
            ffmpeg_gone = ffm is None or ffm.proc.poll() is not None
            for w in writers:
                if ffmpeg_gone:
                    w.abort()  # FFmpeg never started or already exited
                w.finish()
            ct = self._consumer_thread
            if ct is not None:
                ct.join(self._left(deadline, 30))
                if ct.is_alive() and ffm is not None:
                    ffm.kill()
                    ct.join(5)
            for w in writers:
                w.join(self._left(deadline, 30))
        finally:
            if ffm is not None:
                self._step("wait for FFmpeg", lambda: self._finish_ffmpeg(ffm, deadline))

    def _finish_ffmpeg(self, ffm: ff.FFmpegProcess, deadline: float) -> None:
        rc = ffm.wait(self._left(deadline, 60))
        if rc != 0:
            self._report_error(
                EncoderError(
                    "FFmpeg exited with an error", stderr_tail=ffm.stderr_tail, returncode=rc
                ),
                fatal=True,
            )
        elif ffm.stderr_tail:
            log.debug("ffmpeg output:\n%s", ffm.stderr_tail)
