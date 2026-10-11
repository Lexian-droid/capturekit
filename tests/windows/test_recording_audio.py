"""Audio-content recording tests on real WASAPI + FFmpeg (play quiet tones)."""

from __future__ import annotations

import threading
import time

import pytest

import capturekit as ck
from tests.helpers import decode_audio, media_info, tone_energy

pytestmark = [
    pytest.mark.windows,
    pytest.mark.gpu,
    pytest.mark.ffmpeg,
    pytest.mark.audio,
    pytest.mark.interactive,
]


def _threads() -> set[str]:
    return {t.name for t in threading.enumerate() if t.name.startswith("capturekit")}


def _rec(*args, **kw) -> ck.Recorder:  # type: ignore[no-untyped-def]
    return ck.Recorder(*args, **kw)


@pytest.mark.parametrize(
    "mode", [ck.AudioMixMode.SEPARATE, ck.AudioMixMode.MIXED, ck.AudioMixMode.BOTH]
)
def test_two_process_sources_track_layout(tmp_path, play_tone, ffmpeg_exe, mode) -> None:
    a = play_tone(440, 4.0)
    b = play_tone(1000, 4.0)
    out = tmp_path / f"two-{mode.value}.mkv"
    rec = _rec(
        ck.MonitorSource(0, region=ck.Rect(0, 0, 320, 240)),
        [ck.ProcessAudioSource(a.pid, name="a440"), ck.ProcessAudioSource(b.pid, name="b1000")],
        out,
        ck.RecordingConfig(fps=30, audio_mix=mode),
    )
    rec.start()
    time.sleep(2.5)
    res = rec.stop()
    info = media_info(ffmpeg_exe, str(out))
    audio_streams = [s for s in info["streams"] if s[0] == "Audio"]
    expected = {ck.AudioMixMode.SEPARATE: 2, ck.AudioMixMode.MIXED: 1, ck.AudioMixMode.BOTH: 3}
    assert len(audio_streams) == expected[mode], info["raw"]
    video_s = res.stats.video.slots / 30
    tracks = [decode_audio(ffmpeg_exe, str(out), i) for i in range(len(audio_streams))]
    for t in tracks:
        assert abs(len(t) / 48000 - video_s) < 0.06
    e = [(tone_energy(t, 440), tone_energy(t, 1000)) for t in tracks]
    if mode is not ck.AudioMixMode.MIXED:
        assert e[0][0] > 1e-3 and e[0][0] > 100 * e[0][1], e  # track 0: only 440 Hz
        assert e[1][1] > 1e-3 and e[1][1] > 100 * e[1][0], e  # track 1: only 1 kHz
    if mode is not ck.AudioMixMode.SEPARATE:
        assert e[-1][0] > 1e-3 and e[-1][1] > 1e-3, e  # mix: both tones
    if mode is not ck.AudioMixMode.MIXED:
        assert "a440" in info["raw"] and "b1000" in info["raw"]  # track titles


def test_track_titles_and_codecs(tmp_path, ffmpeg_exe) -> None:
    for ext in ("mkv", "mp4"):
        out = tmp_path / f"t.{ext}"
        rec = _rec(
            ck.MonitorSource(0, region=ck.Rect(0, 0, 320, 240)),
            [ck.SystemAudioSource(name="desktop")],
            out,
            ck.RecordingConfig(fps=30),
        )
        rec.start()
        time.sleep(1.0)
        rec.stop()
        info = media_info(ffmpeg_exe, str(out))
        assert ("Video", "h264") in info["streams"], info["raw"]
        assert ("Audio", "aac") in info["streams"], info["raw"]
        if ext == "mkv":
            assert "desktop" in info["raw"]
        else:
            assert b"moof" in out.read_bytes()[:2_000_000]  # fragmented MP4


def test_encoder_auto_selection_prefers_validated_hardware(tmp_path) -> None:
    from capturekit.encoding import find_ffmpeg, probe_encoder

    exe = find_ffmpeg()
    rec = _rec(ck.MonitorSource(0, region=ck.Rect(0, 0, 320, 240)), [], tmp_path / "a.mkv")
    rec.start()
    time.sleep(0.3)
    res = rec.stop()
    chosen = res.stats.video.encoder
    if probe_encoder(exe, "h264_nvenc").usable:
        assert chosen == "h264_nvenc"
    assert chosen is not None and probe_encoder(exe, chosen).usable
    # An unusable explicit encoder with fallback falls back to a usable one.
    if not probe_encoder(exe, "h264_amf").usable:
        rec = _rec(
            ck.MonitorSource(0, region=ck.Rect(0, 0, 320, 240)),
            [],
            tmp_path / "b.mkv",
            ck.RecordingConfig(encoder=ck.EncoderConfig("h264_amf", fallback=True)),
        )
        rec.start()
        time.sleep(0.3)
        res = rec.stop()
        assert res.stats.video.encoder not in (None, "h264_amf")


def test_pause_excludes_audio_played_while_paused(tmp_path, ffmpeg_exe) -> None:
    """One player process plays 440 Hz, silence, 1 kHz, silence, 660 Hz. The
    recorder captures only that process (no background audio) and is paused
    inside the silent gaps, so the 1 kHz segment is played entirely while
    paused (gaps absorb the audio engine's output latency)."""
    import subprocess
    import sys

    from tests.conftest import FIXTURES

    seq = "440:1.0,0:1.0,1000:1.2,0:1.5,660:1.0"
    player = subprocess.Popen(
        [sys.executable, str(FIXTURES / "tone_player.py"), "--sequence", seq],
        stdout=subprocess.PIPE, text=True,
    )  # fmt: skip
    assert player.stdout is not None
    try:
        assert player.stdout.readline().strip() == "playing"
        out = tmp_path / "pause_tones.mkv"
        rec = _rec(None, [ck.ProcessAudioSource(player.pid)], out, ck.RecordingConfig())
        rec.start()
        for line in player.stdout:
            seg = line.strip()
            if seg == "segment 1":
                time.sleep(0.5)
                rec.pause()
            elif seg == "segment 3":
                time.sleep(0.8)
                rec.resume()
        player.wait(10)
        time.sleep(0.3)
        res = rec.stop()
    finally:
        if player.poll() is None:
            player.kill()
    audio = decode_audio(ffmpeg_exe, str(out), 0)
    e440, e1000, e660 = (tone_energy(audio, f) for f in (440, 1000, 660))
    print(
        f"media {res.stats.media_duration_s:.2f}s energies 440={e440:.4g} "
        f"1000={e1000:.4g} 660={e660:.4g}"
    )
    assert e440 > 1e-3 and e660 > 1e-3, (e440, e660)
    assert e1000 < 0.01 * min(e440, e660), (e440, e1000, e660)


def test_process_isolation_with_distractor_positive_control(play_tone) -> None:
    """The distractor must be audible system-wide while absent from the
    process capture of the target."""
    from capturekit._backends.windows import wasapi
    from tests.windows.test_audio import _capture

    target = play_tone(440, 3.0)
    play_tone(1000, 3.0)
    time.sleep(0.2)
    results: dict[str, object] = {}

    def run(kind: str, **kw: object) -> None:
        results[kind] = _capture(wasapi.WasapiCapture(kind, **kw), 1.5)

    threads = [
        threading.Thread(target=run, args=("system",)),
        threading.Thread(target=run, args=("process",), kwargs={"pid": target.pid}),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    system, proc = results["system"], results["process"]
    assert tone_energy(system, 1000) > 1e-3, "distractor was not playing (control failed)"
    assert tone_energy(system, 440) > 1e-3
    assert tone_energy(proc, 440) > 100 * max(tone_energy(proc, 1000), 1e-9)


def test_process_source_without_children_fails_via_public_api(tmp_path) -> None:
    import os

    out = tmp_path / "x.mkv"
    rec = _rec(None, [ck.ProcessAudioSource(os.getpid(), include_children=False)], out)
    with pytest.raises(ck.CapabilityUnavailableError):
        rec.start()
    assert rec.state is ck.RecorderState.FAILED and not out.exists()
    time.sleep(0.2)
    assert not _threads(), _threads()
