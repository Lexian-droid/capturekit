"""PulseAudio / pipewire-pulse integration tests (play short quiet tones)."""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import time

import numpy as np
import pytest

import capturekit as ck
from capturekit._backends.base import AudioPacket
from tests.conftest import FIXTURES
from tests.helpers import decode_audio, tone_energy

pytestmark = [pytest.mark.audio]


def _server_ok() -> bool:
    if not shutil.which("pactl") or not shutil.which("parec") or not shutil.which("paplay"):
        return False
    return subprocess.run(["pactl", "info"], capture_output=True, check=False).returncode == 0


pytestmark.append(pytest.mark.skipif(not _server_ok(), reason="no PulseAudio/PipeWire server"))


class Player:
    def __init__(self, freq: float, seconds: float) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, str(FIXTURES / "tone_player.py"), "--freq", str(freq),
             "--seconds", str(seconds)],
            stdout=subprocess.PIPE, text=True,
        )  # fmt: skip
        assert self.proc.stdout is not None
        self.proc.stdout.readline()
        self.pid = self.proc.pid

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait(5)


@pytest.fixture
def players():
    created: list[Player] = []

    def make(freq: float, seconds: float = 3.0) -> Player:
        p = Player(freq, seconds)
        created.append(p)
        return p

    yield make
    for p in created:
        p.stop()


def _capture(spec, seconds: float) -> np.ndarray:  # type: ignore[no-untyped-def]
    from capturekit._backends.linux import pulse

    backend = pulse.open_audio(spec, 48000, 2)
    packets: list[AudioPacket] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def on_packet(p: AudioPacket) -> None:
        with lock:
            packets.append(p)

    backend.start(on_packet, errors.append)
    time.sleep(seconds)
    backend.stop()
    assert not errors, errors
    return np.concatenate([p.data for p in packets]).mean(axis=1) if packets else np.zeros(0)


def test_enumeration(players) -> None:
    devs = ck.list_audio_devices()
    assert any(d.kind is ck.AudioDeviceKind.OUTPUT and d.is_default for d in devs)
    p = players(440, 2.0)
    time.sleep(0.5)
    from capturekit._backends.linux.pulse import process_tree

    tree = process_tree(p.pid)
    sessions = ck.list_audio_sessions()
    assert any(s.pid in tree and s.stream_id for s in sessions), sessions


def test_system_audio_captures_tone(players) -> None:
    players(440, 3.0)
    time.sleep(0.3)
    mono = _capture(ck.SystemAudioSource(), 1.5)
    assert len(mono) > 48000
    assert tone_energy(mono, 440) > 1e-3


def test_process_audio_isolated_from_other_streams(players) -> None:
    target = players(440, 4.0)
    players(1000, 4.0)
    time.sleep(0.5)
    mono = _capture(ck.ProcessAudioSource(target.pid), 2.0)
    e440, e1000 = tone_energy(mono, 440), tone_energy(mono, 1000)
    assert e440 > 1e-3, (e440, e1000)
    assert e440 > 100 * max(e1000, 1e-9), (e440, e1000)


def test_process_stream_started_after_capture_is_attached(players) -> None:
    from capturekit._backends.linux import pulse

    # Capture a process that has not started playing yet: the backend must
    # attach the stream when it appears.
    gate = subprocess.Popen(
        [sys.executable, "-c",
         f"import subprocess,sys,time; time.sleep(1.0); "
         f"subprocess.run([sys.executable, r'{FIXTURES / 'tone_player.py'}', '--freq', '660', "
         f"'--seconds', '2'])"],
    )  # fmt: skip
    try:
        backend = pulse.open_audio(ck.ProcessAudioSource(gate.pid), 48000, 2)
        packets: list[AudioPacket] = []
        backend.start(packets.append, lambda e: None)
        time.sleep(3.0)
        backend.stop()
        mono = np.concatenate([p.data for p in packets]).mean(axis=1)
        assert backend.streams_seen, "no sink-input attached"
        assert tone_energy(mono, 660) > 1e-3
    finally:
        gate.kill()


def test_silent_process_gives_silence_not_other_audio(players) -> None:
    players(440, 3.0)  # someone else is playing
    idle = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
    try:
        time.sleep(0.3)
        mono = _capture(ck.ProcessAudioSource(idle.pid), 1.5)
        assert len(mono) > 48000 and float(np.abs(mono).max()) == 0.0
    finally:
        idle.kill()


@pytest.mark.ffmpeg
def test_record_process_audio(players, tmp_path, ffmpeg_exe) -> None:
    p = players(440, 4.0)
    out = tmp_path / "a.mka"
    rec = ck.Recorder(None, [ck.ProcessAudioSource(p.pid)], out, ck.RecordingConfig())
    rec.start()
    time.sleep(2.0)
    res = rec.stop()
    audio = decode_audio(ffmpeg_exe, str(out), 0)
    assert abs(len(audio) / 48000 - res.stats.media_duration_s) < 0.1
    assert tone_energy(audio, 440) > 1e-3


def test_microphone_stream_delivers_audio() -> None:
    if not any(d.kind is ck.AudioDeviceKind.INPUT for d in ck.list_audio_devices()):
        pytest.skip("no input device")
    mono = _capture(ck.MicrophoneSource(), 1.0)
    # Content depends on the environment; ~1 s of samples must arrive.
    assert 0.7 * 48000 < len(mono) < 1.3 * 48000
