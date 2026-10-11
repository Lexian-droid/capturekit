"""Fixtures for X11 tests: private Xvfb servers and test windows."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Iterator

import pytest

from tests.conftest import FIXTURES


def _free_display() -> str:
    for n in range(90, 140):
        if not os.path.exists(f"/tmp/.X11-unix/X{n}") and not os.path.exists(f"/tmp/.X{n}-lock"):
            return f":{n}"
    raise RuntimeError("no free X display number")


def start_xvfb(size: str = "1280x720x24") -> tuple[str, subprocess.Popen[bytes]]:
    disp = _free_display()
    proc = subprocess.Popen(
        ["Xvfb", disp, "-screen", "0", size, "-nolisten", "tcp"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(100):
        if os.path.exists(f"/tmp/.X11-unix/X{disp[1:]}"):
            break
        time.sleep(0.05)
    time.sleep(0.2)
    return disp, proc


@pytest.fixture
def xvfb() -> Iterator[str]:
    """A private Xvfb display for one test (never the user's $DISPLAY)."""
    if shutil.which("Xvfb") is None:
        pytest.skip("Xvfb not installed")
    disp, proc = start_xvfb()
    yield disp
    proc.terminate()
    proc.wait(5)


@pytest.fixture
def x11_window(xvfb: str) -> Iterator[Callable[..., int]]:
    procs: list[subprocess.Popen[str]] = []

    def spawn(**kw: object) -> int:
        args = [sys.executable, str(FIXTURES / "x11_color_window.py"), "--display", xvfb]
        for k, v in kw.items():
            args += [f"--{k}", str(v)]
        p = subprocess.Popen(args, stdout=subprocess.PIPE, text=True)
        procs.append(p)
        assert p.stdout is not None
        line = p.stdout.readline()
        if not line.startswith("WINDOW "):
            p.kill()
            raise RuntimeError(f"x11 fixture failed: {line!r}")
        time.sleep(0.2)
        return int(line.split()[1])

    spawn.procs = procs  # type: ignore[attr-defined]
    yield spawn
    for p in procs:
        if p.poll() is None:
            p.kill()
        p.wait(5)
