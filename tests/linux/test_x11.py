"""X11 integration tests against private Xvfb servers (no window manager)."""

from __future__ import annotations

import contextlib
import ctypes
import os
import time

import numpy as np
import pytest

import capturekit as ck
from tests.helpers import color_fraction

pytestmark = [pytest.mark.x11, pytest.mark.xvfb]

GREEN = (0, 200, 0)
RED = (200, 0, 0)
QUAD = ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0))


def _xlib():  # type: ignore[no-untyped-def]
    from capturekit._backends.linux import _xlib

    return _xlib


def _do(display: str, fn_name: str, *args: int) -> None:
    """Run one Xlib request against ``display`` from the test process."""
    X = _xlib()
    fn = getattr(X.x11, fn_name)
    fn.argtypes = (ctypes.c_void_p,) + (ctypes.c_ulong,) * len(args)
    with X.XDisplay(display) as d:
        fn(d.ptr, *args)
        d.sync()


def test_monitors_on_explicit_display(xvfb: str) -> None:
    mons = ck.list_monitors(xvfb)
    assert len(mons) == 1 and mons[0].is_primary
    assert mons[0].rect == ck.Rect(0, 0, 1280, 720)
    assert mons[0].display == xvfb and mons[0].name == "screen"  # Xvfb RandR output


def test_capabilities_report(xvfb: str) -> None:
    caps = ck.get_capabilities(xvfb)
    assert caps.display_server == "xvfb"
    assert caps.monitor_capture and caps.window_capture


def test_window_enumeration(xvfb: str, x11_window) -> None:
    wid = x11_window(title="enum-target")
    wins = {w.handle: w for w in ck.list_windows(xvfb)}
    w = wins[wid]
    assert w.title == "enum-target" and w.class_name == "CapturekitTestWindow"
    assert w.pid == x11_window.procs[-1].pid  # via X-Resource
    assert w.rect == ck.Rect(100, 100, 640, 360) and w.display == xvfb
    with pytest.raises(ck.TargetNotFoundError):
        ck.get_window(0x7FFFFF, xvfb)


def test_monitor_region_capture(xvfb: str, x11_window) -> None:
    x11_window(pattern="quad", x=200, y=100, w=400, h=300)
    img = ck.screenshot(ck.list_monitors(xvfb)[0], ck.Rect(200, 100, 400, 300))
    assert img.shape == (300, 400, 4)
    for (y0, x0), c in zip(((0, 0), (0, 200), (150, 0), (150, 200)), QUAD, strict=True):
        assert color_fraction(img[y0 + 5 : y0 + 145, x0 + 5 : x0 + 195], c) > 0.99
    assert (img[..., 3] == 255).all()  # undefined X byte normalised


@pytest.mark.parametrize("cover", ["partial", "full"])
def test_window_capture_is_exclusive_under_occlusion(xvfb: str, x11_window, cover: str) -> None:
    target = x11_window(color="0,200,0", x=100, y=100, w=400, h=300)
    if cover == "partial":
        x11_window(color="200,0,0", x=250, y=200, w=400, h=300)
    else:
        x11_window(color="200,0,0", x=50, y=50, w=600, h=500)
    desktop = ck.screenshot(ck.list_monitors(xvfb)[0], ck.Rect(100, 100, 400, 300))
    assert color_fraction(desktop, RED) > (0.2 if cover == "partial" else 0.99)
    with ck.ScreenCapture(ck.WindowSource(ck.get_window(target, xvfb))) as cap:
        assert cap.backend_name == "x11-composite"
        img = cap.grab().data
    assert img.shape == (300, 400, 4)
    assert color_fraction(img, GREEN) > 0.99
    assert color_fraction(img, RED) == 0.0


def test_window_region_crop(xvfb: str, x11_window) -> None:
    wid = x11_window(pattern="quad", x=100, y=100, w=400, h=300)
    x11_window(color="200,0,0", x=0, y=0, w=800, h=600)  # cover completely
    win = ck.get_window(wid, xvfb)
    for region, colour in (
        (ck.Rect(0, 0, 200, 150), QUAD[0]),
        (ck.Rect(200, 150, 200, 150), QUAD[3]),
        (ck.Rect(10, 160, 50, 40), QUAD[2]),
    ):
        with ck.ScreenCapture(ck.WindowSource(win, region=region)) as cap:
            img = cap.grab().data
        assert img.shape[:2] == (region.height, region.width)
        assert color_fraction(img, colour) > 0.99, (region, colour)
    with pytest.raises(ck.InvalidRegionError):
        ck.ScreenCapture(ck.WindowSource(win, region=ck.Rect(0, 0, 401, 10))).open()


def test_window_resize_followed(xvfb: str, x11_window) -> None:
    wid = x11_window(color="0,200,0", w=300, h=200)
    with ck.ScreenCapture(ck.WindowSource(ck.get_window(wid, xvfb))) as cap:
        assert cap.grab().data.shape == (200, 300, 4)
        _do(xvfb, "XResizeWindow", wid, 500, 350)
        deadline = time.time() + 3
        shape: tuple[int, ...] = ()
        while time.time() < deadline and shape != (350, 500, 4):
            try:
                f = cap.grab(0.3, new_only=True)
            except ck.CaptureTimeoutError:
                continue
            shape = f.data.shape
        assert shape == (350, 500, 4)
        assert color_fraction(f.data, GREEN) > 0.99


def test_unmapped_window_yields_no_frames_then_resumes(xvfb: str, x11_window) -> None:
    wid = x11_window(color="0,200,0")
    with ck.ScreenCapture(ck.WindowSource(ck.get_window(wid, xvfb))) as cap:
        cap.grab()
        _do(xvfb, "XUnmapWindow", wid)
        time.sleep(0.2)
        with pytest.raises(ck.CaptureTimeoutError):
            cap.grab(0.5, new_only=True)
        assert cap.grab().data.size  # last frame still available
        _do(xvfb, "XMapWindow", wid)
        f = cap.grab(2.0, new_only=True)
        assert color_fraction(f.data, GREEN) > 0.99


def test_destroyed_window_raises(xvfb: str, x11_window) -> None:
    wid = x11_window(color="0,200,0")
    with ck.ScreenCapture(ck.WindowSource(ck.get_window(wid, xvfb))) as cap:
        cap.grab()
        x11_window.procs[-1].kill()
        x11_window.procs[-1].wait(5)
        with pytest.raises(ck.WindowClosedError):
            for _ in range(30):  # closure is noticed asynchronously
                with contextlib.suppress(ck.CaptureTimeoutError):
                    cap.grab(0.1, new_only=True)


def test_wayland_session_without_display_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    with pytest.raises(ck.UnsupportedDisplayServerError):
        ck.list_monitors()
    monkeypatch.delenv("WAYLAND_DISPLAY")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    with pytest.raises(ck.UnsupportedDisplayServerError):
        ck.list_monitors()


def test_explicit_display_works_from_wayland_host(xvfb: str, monkeypatch) -> None:
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    assert ck.list_monitors(xvfb)[0].display == xvfb


def test_server_death_is_reported_not_fatal() -> None:
    from tests.linux.conftest import start_xvfb

    disp, proc = start_xvfb("640x480x24")
    try:
        cap = ck.ScreenCapture(ck.MonitorSource(ck.list_monitors(disp)[0])).open()
        cap.grab()
        proc.kill()
        proc.wait(5)
        with pytest.raises(ck.CaptureError):
            for _ in range(10):
                cap.grab(0.1)
                time.sleep(0.05)
        cap.close()
    finally:
        if proc.poll() is None:
            proc.kill()
    assert os.getpid()  # the interpreter survived (Xlib did not call exit())


@pytest.mark.ffmpeg
def test_record_window_on_xvfb(xvfb: str, x11_window, tmp_path, ffmpeg_exe) -> None:
    from tests.helpers import count_video_frames, decode_video_gray

    wid = x11_window(color="0,200,0", w=320, h=240)
    out = tmp_path / "x11.mkv"
    rec = ck.Recorder(
        ck.WindowSource(ck.get_window(wid, xvfb)),
        [],
        out,
        ck.RecordingConfig(fps=30, encoder=ck.EncoderConfig("libx264")),
    )
    rec.start()
    time.sleep(2.0)
    res = rec.stop()
    v = res.stats.video
    assert v is not None and v.backend == "x11-composite"
    assert count_video_frames(ffmpeg_exe, str(out)) == v.slots
    gray = decode_video_gray(ffmpeg_exe, str(out), 32, 24)
    assert gray.shape[0] == v.slots and np.abs(gray.astype(int).mean() - 117) < 15  # green luma
