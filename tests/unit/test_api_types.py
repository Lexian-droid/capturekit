from __future__ import annotations

import numpy as np
import pytest

import capturekit as ck
from capturekit._backends.base import blit_clear, crop_box
from capturekit.config import EncoderConfig, RecordingConfig
from capturekit.encoding import ffmpeg as ff
from capturekit.types import validate_region


def test_import_has_no_backend_side_effects() -> None:
    import sys

    assert "capturekit._backends.windows.api" not in sys.modules or sys.platform == "win32"
    assert ck.__version__


def test_rect_helpers() -> None:
    r = ck.Rect(-10, 5, 100, 50)
    assert r.right == 90 and r.bottom == 55
    assert r.intersect(ck.Rect(0, 0, 20, 20)) == ck.Rect(0, 5, 20, 15)
    assert ck.Rect.from_ltrb(-1920, 0, 0, 1080) == ck.Rect(-1920, 0, 1920, 1080)
    assert ck.Rect(0, 0, 0, 5).is_empty()


@pytest.mark.parametrize(
    "region",
    [ck.Rect(0, 0, 0, 10), ck.Rect(-1, 0, 10, 10), ck.Rect(0, 0, 101, 10), ck.Rect(95, 95, 10, 10)],
)
def test_validate_region_rejects(region: ck.Rect) -> None:
    with pytest.raises(ck.InvalidRegionError):
        validate_region(region, (100, 100))


def test_crop_box_and_blit_clear() -> None:
    base = ck.Rect(8, 30, 100, 80)  # e.g. client area inside window
    assert crop_box(base, None) == base
    assert crop_box(base, ck.Rect(10, 10, 20, 20)) == ck.Rect(18, 40, 20, 20)
    assert crop_box(base, ck.Rect(90, 70, 20, 20)) == ck.Rect(98, 100, 10, 10)
    buf = np.full((4, 6, 4), 9, np.uint8)
    blit_clear(buf, 4, 3)
    assert (buf[:3, :4] == 9).all() and (buf[3] == 0).all() and (buf[:, 4:] == 0).all()


def test_recording_config_validation() -> None:
    with pytest.raises(ValueError):
        RecordingConfig(fps=0)
    with pytest.raises(ValueError):
        RecordingConfig(queue_frames=1)
    assert RecordingConfig(fps=59.94).fps == 59.94


def test_process_source_for_window_requires_pid() -> None:
    w = ck.Window(1, "t", None, None, None, ck.Rect(0, 0, 1, 1), True, False)
    with pytest.raises(ck.TargetNotFoundError):
        ck.ProcessAudioSource.for_window(w)
    w2 = ck.Window(1, "t", None, 42, "a.exe", ck.Rect(0, 0, 1, 1), True, False)
    assert ck.ProcessAudioSource.for_window(w2).pid == 42


def test_ffmpeg_command_is_safe_and_complete(tmp_path) -> None:
    out = tmp_path / "-weird name.mkv"
    cmd = ff.build_command(
        "ffmpeg", output=str(out), container=ff.container_for(str(out), None),
        encoder="h264_nvenc", cfg=EncoderConfig(bitrate=8_000_000), video_size=(1280, 720),
        fps=60, audio_inputs=[ff.AudioInput(r"\\.\pipe\x", 48000, 2, "mic")],
    )  # fmt: skip
    assert cmd[-1].startswith("file:") and cmd[-1].endswith("-weird name.mkv")
    assert "-n" in cmd and "-y" not in cmd  # never overwrite implicitly
    assert cmd[cmd.index("-video_size") + 1] == "1280x720"
    assert cmd[cmd.index("-b:v") + 1] == "8000000"
    assert "title=mic" in cmd
    assert cmd[cmd.index("-f", cmd.index("-c:a")) + 1] == "matroska"
    mp4 = ff.build_command(
        "ffmpeg",
        output="a.mp4",
        container=ff.container_for("a.mp4", None),
        encoder="libx264",
        cfg=EncoderConfig(),
        video_size=(64, 64),
        fps=30,
    )
    assert "+frag_keyframe+empty_moov+default_base_moof" in mp4
    assert mp4[mp4.index("-crf") + 1] == "23"


@pytest.mark.parametrize(
    ("fps", "text"), [(30, "30"), (29.97, "30000/1001"), (59.94, "60000/1001"), (12.5, "12.500000")]
)
def test_fps_string(fps: float, text: str) -> None:
    assert ff._fps_str(fps) == text


def test_capability_bool() -> None:
    from capturekit.capabilities import experimental, supported, unavailable

    assert supported() and experimental("x")
    assert not unavailable("x")
