"""WASAPI audio integration tests (real endpoints on this machine).

These play short, quiet tones through the default output device.
"""

from __future__ import annotations

import itertools
import threading
import time

import numpy as np
import pytest

import capturekit as ck
from capturekit._backends.base import AudioPacket
from capturekit._backends.windows import wasapi
from tests.helpers import tone_energy

pytestmark = [pytest.mark.windows, pytest.mark.audio]


def _capture(backend, seconds: float) -> np.ndarray:
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
    if not packets:
        return np.zeros(0, np.float32)
    ts = [p.timestamp_ns for p in packets]
    assert all(b >= a for a, b in itertools.pairwise(ts)), "timestamps must be monotonic"
    return np.concatenate([p.data for p in packets]).mean(axis=1)


def test_device_and_session_enumeration() -> None:
    devs = ck.list_audio_devices()
    assert any(d.kind is ck.AudioDeviceKind.OUTPUT and d.is_default for d in devs)
    assert all(d.id and d.backend == "wasapi" for d in devs)
    sessions = ck.list_audio_sessions()
    assert all(isinstance(s, ck.AudioSession) for s in sessions)


def test_system_loopback_captures_tone(play_tone) -> None:
    play_tone(440, 2.5)
    mono = _capture(wasapi.WasapiCapture("system"), 1.5)
    assert len(mono) > 48000
    assert tone_energy(mono, 440) > 10 * tone_energy(mono, 1000)


def test_process_loopback_isolates_target(play_tone) -> None:
    if not wasapi.process_loopback_supported():
        pytest.skip("process loopback needs build 20348+")
    target = play_tone(440, 3.0)
    play_tone(1000, 3.0)  # distractor in another process
    time.sleep(0.2)
    # The venv python.exe is a launcher: audio is rendered by its child process.
    # Sessions report the rendering PID, which must be inside the target's tree.
    from capturekit._backends.windows._win32 import process_tree

    tree = process_tree(target.pid)
    deadline = time.time() + 2
    while time.time() < deadline:
        if any(s.pid in tree and s.is_active for s in ck.list_audio_sessions()):
            break
        time.sleep(0.1)
    else:
        pytest.fail(f"no active audio session in process tree {tree}")
    mono = _capture(wasapi.WasapiCapture("process", pid=target.pid), 1.5)
    e440, e1000 = tone_energy(mono, 440), tone_energy(mono, 1000)
    assert e440 > 1e-3, "target tone missing"
    assert e440 > 100 * max(e1000, 1e-9), (e440, e1000)


def test_process_loopback_unknown_pid_fails_without_fallback() -> None:
    with pytest.raises(ck.TargetNotFoundError):
        wasapi.WasapiCapture("process", pid=0x7FFFFFF0).start(lambda p: None, lambda e: None)


def test_process_source_rejects_children_exclusion() -> None:
    import os

    cap = wasapi.WasapiCapture("process", pid=os.getpid(), include_tree=False)
    with pytest.raises(ck.CapabilityUnavailableError):
        cap.start(lambda p: None, lambda e: None)


def test_microphone_capture_runs() -> None:
    if not any(d.kind is ck.AudioDeviceKind.INPUT for d in ck.list_audio_devices()):
        pytest.skip("no microphone")
    mono = _capture(wasapi.WasapiCapture("microphone"), 1.0)
    # Content is environment dependent; the stream must deliver ~1 s of audio.
    assert 0.7 * 48000 < len(mono) < 1.3 * 48000
