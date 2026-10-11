"""End-to-end recording tests: capture + WASAPI + FFmpeg on this machine."""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

import capturekit as ck
from tests.helpers import (
    close_window,
    count_video_frames,
    decode_audio,
    decode_video_gray,
    media_info,
    tone_energy,
)

pytestmark = [pytest.mark.windows, pytest.mark.gpu, pytest.mark.ffmpeg, pytest.mark.interactive]


def _threads() -> set[str]:
    return {t.name for t in threading.enumerate() if t.name.startswith("capturekit")}


@pytest.mark.audio
def test_record_window_with_process_audio(tmp_path, spawn_window, play_tone, ffmpeg_exe) -> None:
    hwnd = spawn_window(color="0,200,0", w=640, h=400)
    tone = play_tone(440, 4.0)
    out = tmp_path / "win.mkv"
    rec = ck.Recorder(
        ck.WindowSource(hwnd, client_area_only=True),
        [ck.ProcessAudioSource(tone.pid, name="tone")],
        out,
        ck.RecordingConfig(fps=30),
    )
    rec.start()
    time.sleep(3.0)
    res = rec.stop()
    assert res.error is None
    v = res.stats.video
    assert v is not None and v.backend == "wgc-window" and v.frames_dropped == 0
    frames = count_video_frames(ffmpeg_exe, str(out))
    assert v.frames_encoded == v.slots, v
    assert frames == v.slots, (frames, v)
    assert abs(frames - 90) <= 3
    audio = decode_audio(ffmpeg_exe, str(out), 0)
    assert abs(len(audio) / 48000 - frames / 30) < 0.05  # tracks end together
    assert tone_energy(audio, 440) > 1e-3
    gray = decode_video_gray(ffmpeg_exe, str(out), 64, 40)
    assert gray.shape[0] == frames
    assert not _threads(), _threads()


@pytest.mark.slow
@pytest.mark.parametrize("fps", [24, 30, 60])
def test_record_monitor_frame_rates(tmp_path, ffmpeg_exe, fps: int) -> None:
    out = tmp_path / f"mon{fps}.mp4"
    rec = ck.Recorder(ck.MonitorSource(0), [], out, ck.RecordingConfig(fps=fps))
    rec.start()
    time.sleep(4.0)
    res = rec.stop()
    v = res.stats.video
    assert v is not None
    print(f"\n{fps} fps: {v}")
    assert v.slots == pytest.approx(4.0 * fps, abs=2 + fps * 0.05)
    assert count_video_frames(ffmpeg_exe, str(out)) == v.slots
    # Real-time capture must keep up on this hardware.
    assert v.frames_dropped + v.frames_late <= v.slots * 0.02, v


def test_pause_resume_excludes_paused_time(tmp_path, spawn_window, ffmpeg_exe) -> None:
    hwnd = spawn_window(color="0,0,200")
    out = tmp_path / "pause.mkv"
    states: list[ck.RecorderState] = []
    rec = ck.Recorder(
        ck.WindowSource(hwnd),
        [ck.SystemAudioSource()],
        out,
        ck.RecordingConfig(fps=30),
        on_state_change=states.append,
    )
    rec.start()
    time.sleep(1.0)
    rec.pause()
    with pytest.raises(ck.RecorderStateError):
        rec.pause()
    time.sleep(1.5)
    rec.resume()
    time.sleep(1.0)
    res = rec.stop()
    assert res.stats.media_duration_s == pytest.approx(2.0, abs=0.15)
    info = media_info(ffmpeg_exe, str(out))
    assert info["duration"] == pytest.approx(2.0, abs=0.2)
    audio = decode_audio(ffmpeg_exe, str(out), 0)
    assert len(audio) / 48000 == pytest.approx(res.stats.media_duration_s, abs=0.06)
    time.sleep(0.1)
    assert states == [
        ck.RecorderState.STARTING,
        ck.RecorderState.RECORDING,
        ck.RecorderState.PAUSED,
        ck.RecorderState.RECORDING,
        ck.RecorderState.STOPPING,
        ck.RecorderState.STOPPED,
    ]


def test_unavailable_encoder_fails_cleanly(tmp_path) -> None:
    from capturekit.encoding import find_ffmpeg, probe_encoder

    if probe_encoder(find_ffmpeg(), "h264_amf").usable:
        pytest.skip("AMF works on this machine")
    out = tmp_path / "x.mkv"
    rec = ck.Recorder(
        ck.MonitorSource(0),
        [],
        out,
        ck.RecordingConfig(encoder=ck.EncoderConfig("h264_amf", fallback=False)),
    )
    with pytest.raises(ck.EncoderNotAvailableError):
        rec.start()
    assert rec.state is ck.RecorderState.FAILED
    assert not out.exists()
    time.sleep(0.2)
    assert not _threads(), _threads()


def test_encoder_crash_is_reported_and_cleaned_up(tmp_path) -> None:
    errors: list[BaseException] = []
    rec = ck.Recorder(
        ck.MonitorSource(0),
        [ck.SystemAudioSource()],
        tmp_path / "crash.mkv",
        ck.RecordingConfig(fps=30),
        on_error=errors.append,
    )
    rec.start()
    time.sleep(0.5)
    assert rec._ffmpeg is not None
    rec._ffmpeg.proc.kill()  # simulate an encoder crash
    assert rec.wait(10), "recorder must stop by itself after an encoder failure"
    assert rec.state is ck.RecorderState.FAILED
    with pytest.raises(ck.EncoderError):
        rec.stop()
    time.sleep(0.2)
    assert errors and isinstance(errors[0], ck.EncoderError)
    assert not _threads(), _threads()


def test_window_closed_during_recording(tmp_path, spawn_window, ffmpeg_exe) -> None:
    hwnd = spawn_window(color="0,200,0")
    errors: list[BaseException] = []
    out = tmp_path / "closed.mkv"
    rec = ck.Recorder(
        ck.WindowSource(hwnd), [], out, ck.RecordingConfig(fps=30), on_error=errors.append
    )
    rec.start()
    time.sleep(1.0)
    close_window(hwnd)
    assert rec.wait(10)
    res = rec.stop()  # not fatal: does not raise
    assert isinstance(res.error, ck.WindowClosedError)
    assert rec.state is ck.RecorderState.STOPPED
    assert count_video_frames(ffmpeg_exe, str(out)) == res.stats.video.slots >= 25
    time.sleep(0.2)
    assert errors and isinstance(errors[0], ck.WindowClosedError)


def test_window_closed_continue_policy(tmp_path, spawn_window) -> None:
    hwnd = spawn_window(color="0,200,0")
    rec = ck.Recorder(
        ck.WindowSource(hwnd),
        [],
        tmp_path / "cont.mkv",
        ck.RecordingConfig(fps=30, on_target_lost=ck.TargetLostPolicy.CONTINUE),
    )
    rec.start()
    time.sleep(0.7)
    close_window(hwnd)
    time.sleep(1.0)
    assert rec.state is ck.RecorderState.RECORDING
    res = rec.stop()
    assert res.stats.video.target_lost and isinstance(res.error, ck.WindowClosedError)
    assert res.stats.video.slots == pytest.approx(51, abs=4)


def test_capture_without_output_delivers_callbacks(spawn_window) -> None:
    hwnd = spawn_window(color="0,200,0", w=320, h=240)
    frames: list[ck.Frame] = []
    chunks: list[ck.AudioChunk] = []
    rec = ck.Recorder(
        ck.WindowSource(hwnd, client_area_only=True),
        [ck.SystemAudioSource()],
        None,
        ck.RecordingConfig(fps=20),
        on_video_frame=frames.append,
        on_audio=chunks.append,
    )
    rec.start()
    time.sleep(1.0)
    rec.stop()
    assert len(frames) >= 1 and frames[0].data.ndim == 3
    assert [f.index for f in frames] == sorted(f.index for f in frames)
    total = sum(c.frames for c in chunks)
    assert total == pytest.approx(rec.stats.media_duration_s * 48000, abs=960)


@pytest.mark.audio
@pytest.mark.slow
def test_av_sync_flash_and_click(tmp_path, spawn_window, ffmpeg_exe) -> None:
    """Fixture window flips black→white and plays a click at the same instant;
    the onsets in the recorded file must line up."""
    hwnd = spawn_window(pattern="flash", flash_ms=700, beep=True, w=320, h=240)
    pid = ck.get_window(hwnd).pid
    out = tmp_path / "sync.mkv"
    fps = 60
    rec = ck.Recorder(
        ck.WindowSource(hwnd, client_area_only=True),
        [ck.ProcessAudioSource(pid)],
        out,
        ck.RecordingConfig(fps=fps),
    )
    rec.start()
    time.sleep(8.0)
    rec.stop()
    gray = decode_video_gray(ffmpeg_exe, str(out), 32, 24).reshape(-1, 32 * 24).mean(axis=1)
    bright = gray > 128
    v_on = [i / fps for i in range(1, len(bright)) if bright[i] and not bright[i - 1]]
    audio = np.abs(decode_audio(ffmpeg_exe, str(out), 0))
    env = np.convolve(audio, np.ones(48) / 48, mode="same")
    loud = env > 0.5 * env.max()
    a_on = [i / 48000 for i in range(1, len(loud)) if loud[i] and not loud[i - 1]]
    a_on = [t for i, t in enumerate(a_on) if i == 0 or t - a_on[i - 1] > 0.3]
    offsets = [min(a_on, key=lambda a: abs(a - v)) - v for v in v_on]
    print(
        f"\nvideo onsets {len(v_on)}, audio onsets {len(a_on)}; "
        f"offsets ms {[round(o * 1000, 1) for o in offsets]}"
    )
    assert len(v_on) >= 4 and len(a_on) >= 4
    med = float(np.median(offsets))
    # Click latency through the audio engine and one-frame video quantisation
    # are inherent; require |offset| within ~1 frame + 45 ms and no trend.
    assert abs(med) < 1 / fps + 0.045, offsets
    drift = offsets[-1] - offsets[0]
    assert abs(drift) < 0.03, offsets
