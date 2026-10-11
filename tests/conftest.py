"""Shared pytest configuration.

Hardware/platform-dependent tests are marked and skipped *with a reason* when
the requirement is missing. A skipped test is not a passing test.
"""

from __future__ import annotations

import functools
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@functools.cache
def _ffmpeg() -> str | None:
    try:
        from capturekit.encoding import find_ffmpeg

        return find_ffmpeg()
    except Exception:
        return None


@functools.cache
def _gpu_ok() -> str | None:
    if sys.platform != "win32":
        return "not Windows"
    try:
        from capturekit._backends.windows import _d3d11

        dev, ctx = _d3d11.create_device()
        dev.release()
        ctx.release()
        return None
    except Exception as exc:
        return f"no D3D11 device: {exc}"


@functools.cache
def _audio_ok() -> str | None:
    try:
        import capturekit as ck

        devs = ck.list_audio_devices()
    except Exception as exc:
        return f"audio enumeration failed: {exc}"
    if not any(d.is_default and d.kind is ck.AudioDeviceKind.OUTPUT for d in devs):
        return "no default audio output device"
    return None


def _x11_display() -> str | None:
    return os.environ.get("CAPTUREKIT_TEST_DISPLAY")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        reasons = []
        if "windows" in item.keywords and sys.platform != "win32":
            reasons.append("requires Windows")
        if "gpu" in item.keywords and sys.platform == "win32" and (r := _gpu_ok()):
            reasons.append(r)
        if "ffmpeg" in item.keywords and _ffmpeg() is None:
            reasons.append("FFmpeg executable not found")
        if "audio" in item.keywords and sys.platform == "win32" and (r := _audio_ok()):
            reasons.append(r)
        if "x11" in item.keywords and not sys.platform.startswith("linux"):
            reasons.append("requires Linux/X11")
        if "interactive" in item.keywords and os.environ.get("CAPTUREKIT_NO_WINDOWS") == "1":
            reasons.append("CAPTUREKIT_NO_WINDOWS=1 (visible test windows disabled)")
        if reasons:
            item.add_marker(pytest.mark.skip(reason="; ".join(reasons)))


@pytest.fixture
def ffmpeg_exe() -> str:
    exe = _ffmpeg()
    assert exe is not None
    return exe


@pytest.fixture
def spawn_window() -> Iterator[Callable[..., int]]:
    """Start ``tests/fixtures/color_window.py``; returns the HWND."""
    procs: list[subprocess.Popen[str]] = []

    def spawn(**kw: object) -> int:
        args = [sys.executable, str(FIXTURES / "color_window.py")]
        for k, v in kw.items():
            if v is True:
                args.append(f"--{k}")
            elif v not in (None, False):
                args += [f"--{k.replace('_', '-')}", str(v)]
        p = subprocess.Popen(args, stdout=subprocess.PIPE, text=True)
        procs.append(p)
        assert p.stdout is not None
        line = p.stdout.readline()
        if not line.startswith("HWND "):
            p.kill()
            raise RuntimeError(f"fixture window failed to start: {line!r}")
        time.sleep(0.4)  # first paint + DWM composition
        return int(line.split()[1])

    yield spawn
    for p in procs:
        if p.poll() is None:
            p.kill()
        p.wait(5)


@pytest.fixture
def play_tone() -> Iterator[Callable[..., subprocess.Popen[str]]]:
    procs: list[subprocess.Popen[str]] = []

    def play(freq: float, seconds: float = 3.0, amplitude: float = 0.08) -> subprocess.Popen[str]:
        p = subprocess.Popen(
            [sys.executable, str(FIXTURES / "tone_player.py"), "--freq", str(freq),
             "--seconds", str(seconds), "--amplitude", str(amplitude)],
            stdout=subprocess.PIPE, text=True,
        )  # fmt: skip
        procs.append(p)
        assert p.stdout is not None
        p.stdout.readline()  # "playing"
        return p

    yield play
    for p in procs:
        if p.poll() is None:
            p.kill()
        p.wait(5)
