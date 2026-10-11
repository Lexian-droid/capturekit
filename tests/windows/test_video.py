"""Windows video capture integration tests (real DXGI / WGC on this machine)."""

from __future__ import annotations

import time

import numpy as np
import pytest

import capturekit as ck
from tests.helpers import (
    close_window,
    color_fraction,
    minimize_window,
    resize_window,
    restore_window,
)

pytestmark = [pytest.mark.windows, pytest.mark.gpu, pytest.mark.interactive]

GREEN = (0, 200, 0)
RED = (200, 0, 0)
QUAD = ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0))


def _client_screen_rect(hwnd: int) -> ck.Rect:
    from capturekit._backends.windows import _win32

    bounds = _win32.window_bounds(hwnd)
    client = _win32.client_rect_in_window(hwnd)
    return client.translate(bounds.x, bounds.y)


def test_enumeration(spawn_window) -> None:
    mons = ck.list_monitors()
    assert mons and sum(m.is_primary for m in mons) == 1
    assert all(m.width > 0 and m.height > 0 for m in mons)
    assert [m.index for m in mons] == list(range(len(mons)))
    hwnd = spawn_window(title="ck-enum-test")
    wins = {w.handle: w for w in ck.list_windows()}
    assert hwnd in wins
    w = wins[hwnd]
    assert w.title == "ck-enum-test" and w.pid and w.process_name.lower().startswith("python")
    assert w.class_name == "CapturekitTestWindow"
    assert not w.capture_excluded and not w.is_minimized
    assert ck.get_window(hwnd) == w
    with pytest.raises(ck.TargetNotFoundError):
        ck.get_window(0x7FFFFFF0)


def test_monitor_capture_full_and_region() -> None:
    mon = ck.list_monitors()[0]
    with ck.ScreenCapture(ck.MonitorSource(0)) as cap:
        f = cap.grab()
        assert f.data.shape == (mon.height, mon.width, 4) and f.data.dtype == np.uint8
        assert cap.backend_name == "dxgi-duplication"
    with ck.ScreenCapture(ck.MonitorSource(0, region=ck.Rect(10, 20, 300, 200))) as cap:
        assert cap.grab().data.shape == (200, 300, 4)
    with pytest.raises(ck.InvalidRegionError):
        ck.ScreenCapture(ck.MonitorSource(0, region=ck.Rect(0, 0, mon.width + 1, 10))).open()


def test_monitor_region_matches_screen_content(spawn_window) -> None:
    hwnd = spawn_window(pattern="quad", x=200, y=200, w=640, h=400, topmost=True)
    cr = _client_screen_rect(hwnd)
    mon = ck.list_monitors()[0]
    region = cr.translate(-mon.rect.x, -mon.rect.y)
    img = ck.screenshot(0, region)
    h, w = img.shape[:2]
    # Each quadrant (inset to avoid edges) must have the expected colour.
    for (y0, x0), c in zip(((0, 0), (0, w // 2), (h // 2, 0), (h // 2, w // 2)), QUAD, strict=True):
        q = img[y0 + 10 : y0 + h // 2 - 10, x0 + 10 : x0 + w // 2 - 10]
        assert color_fraction(q, c) > 0.99, c


@pytest.mark.parametrize("cover", ["partial", "full"])
def test_window_capture_is_exclusive_under_occlusion(spawn_window, cover: str) -> None:
    target = spawn_window(color="0,200,0", x=200, y=200, w=640, h=400)
    if cover == "partial":
        spawn_window(color="200,0,0", x=400, y=300, w=500, h=400, topmost=True)
    else:
        spawn_window(color="200,0,0", x=100, y=100, w=900, h=700, topmost=True)
    time.sleep(0.3)
    # Prove the occluder really covers the target on screen.
    cr = _client_screen_rect(target)
    desktop = ck.screenshot(0, cr)
    assert color_fraction(desktop, RED) > (0.15 if cover == "partial" else 0.99)

    with ck.ScreenCapture(ck.WindowSource(target, client_area_only=True)) as cap:
        img = cap.grab().data
    assert img.shape[:2] == (cr.height, cr.width)
    assert color_fraction(img, GREEN) > 0.99
    assert color_fraction(img, RED) == 0.0


def test_window_region_crop_is_window_relative(spawn_window) -> None:
    hwnd = spawn_window(pattern="quad", x=300, y=250, w=640, h=400)
    spawn_window(color="200,0,0", x=250, y=200, w=800, h=600, topmost=True)  # cover it
    cr = _client_screen_rect(hwnd)
    hw, hh = cr.width // 2, cr.height // 2
    for region, colour in (
        (ck.Rect(0, 0, hw, hh), QUAD[0]),
        (ck.Rect(hw, hh, cr.width - hw, cr.height - hh), QUAD[3]),
        (ck.Rect(0, hh + 5, 40, 30), QUAD[2]),
    ):
        src = ck.WindowSource(hwnd, region=region, client_area_only=True)
        with ck.ScreenCapture(src) as cap:
            img = cap.grab().data
        assert img.shape[:2] == (region.height, region.width)
        assert color_fraction(img[2:-2, 2:-2], colour) > 0.99, (region, colour)
    with pytest.raises(ck.InvalidRegionError):
        ck.ScreenCapture(
            ck.WindowSource(hwnd, region=ck.Rect(0, 0, 5000, 10), client_area_only=True)
        ).open()


def test_window_resize_is_followed(spawn_window) -> None:
    hwnd = spawn_window(color="0,200,0", x=200, y=200, w=500, h=300)
    with ck.ScreenCapture(ck.WindowSource(hwnd, client_area_only=True)) as cap:
        first = cap.grab()
        resize_window(hwnd, 900, 600)
        # The first frame after a resize still uses the old frame-pool size;
        # the backend recreates the pool and later frames have the new size.
        deadline = time.time() + 3
        frame = first
        while time.time() < deadline and frame.data.shape[1] <= first.data.shape[1] + 300:
            try:
                frame = cap.grab(0.5, new_only=True)
            except ck.CaptureTimeoutError:
                continue
        assert frame.data.shape[1] > first.data.shape[1] + 300
        assert color_fraction(frame.data, GREEN) > 0.99


def test_window_close_raises_window_closed(spawn_window) -> None:
    hwnd = spawn_window()
    with ck.ScreenCapture(ck.WindowSource(hwnd)) as cap:
        cap.grab()
        close_window(hwnd)
        with pytest.raises(ck.WindowClosedError):
            for _ in range(20):
                cap.grab(0.2, new_only=True)


def test_window_minimized_behaviour(spawn_window) -> None:
    """Documents measured WGC behaviour for minimized windows (see capabilities)."""
    hwnd = spawn_window(color="0,200,0")
    with ck.ScreenCapture(ck.WindowSource(hwnd, client_area_only=True)) as cap:
        cap.grab()
        minimize_window(hwnd)
        time.sleep(0.5)
        try:
            cap.grab(0.3, new_only=True)  # drain frames produced while minimizing
        except ck.CaptureTimeoutError:
            pass
        with pytest.raises(ck.CaptureTimeoutError):
            cap.grab(1.0, new_only=True)  # no new frames while minimized
        held = cap.grab()  # the last frame remains available
        assert held.data.size > 0
        restore_window(hwnd)
        time.sleep(0.5)
        f = cap.grab(2.0)
        assert color_fraction(f.data, GREEN) > 0.99


def test_capture_excluded_window_is_not_bypassed(spawn_window) -> None:
    hwnd = spawn_window(color="0,200,0", exclude=True)
    w = ck.get_window(hwnd)
    assert w.capture_excluded
    with ck.ScreenCapture(ck.WindowSource(hwnd, client_area_only=True)) as cap:
        try:
            img = cap.grab(2.0).data
        except ck.CaptureTimeoutError:
            return  # no content delivered at all: acceptable
    assert color_fraction(img, GREEN) < 0.01


def test_wgc_monitor_backend(spawn_window) -> None:
    mon = ck.list_monitors()[0]
    with ck.ScreenCapture(ck.MonitorSource(0, backend=ck.MonitorBackend.WGC)) as cap:
        f = cap.grab()
        assert cap.backend_name == "wgc-monitor"
        assert f.data.shape == (mon.height, mon.width, 4)
    hwnd = spawn_window(pattern="quad", x=200, y=200, w=640, h=400, topmost=True)
    cr = _client_screen_rect(hwnd).translate(-mon.rect.x, -mon.rect.y)
    src = ck.MonitorSource(0, region=cr, backend=ck.MonitorBackend.WGC)
    with ck.ScreenCapture(src) as cap:
        img = cap.grab().data
    h, w = img.shape[:2]
    for (y0, x0), c in zip(((0, 0), (0, w // 2), (h // 2, 0), (h // 2, w // 2)), QUAD, strict=True):
        q = img[y0 + 10 : y0 + h // 2 - 10, x0 + 10 : x0 + w // 2 - 10]
        assert color_fraction(q, c) > 0.99, c


def test_full_window_includes_frame(spawn_window) -> None:
    hwnd = spawn_window(color="0,200,0", w=640, h=400)
    with ck.ScreenCapture(ck.WindowSource(hwnd)) as cap:
        full = cap.grab().data
    cr = _client_screen_rect(hwnd)
    w = ck.get_window(hwnd)
    assert full.shape[:2] == (w.rect.height, w.rect.width)
    assert full.shape[0] > cr.height + 20  # title bar is included
    assert color_fraction(full, GREEN) < 0.97


def test_frames_iterator_paced() -> None:
    with ck.ScreenCapture(ck.MonitorSource(0, region=ck.Rect(0, 0, 320, 240))) as cap:
        t = time.perf_counter()
        frames = list(cap.frames(fps=30, max_frames=30))
        elapsed = time.perf_counter() - t
    assert len(frames) == 30
    assert 0.9 < elapsed < 1.5
    assert all(f.data.shape == (240, 320, 4) for f in frames)
