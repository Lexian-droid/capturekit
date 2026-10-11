"""Win32 helpers: DPI context, monitor and window enumeration, process info."""

from __future__ import annotations

import contextlib
import ctypes
import os
from collections.abc import Iterator
from ctypes import POINTER, Structure, byref, c_int, c_long, c_uint, c_void_p, sizeof
from ctypes import wintypes as wt

from ...types import Monitor, Rect, Window

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")
try:
    shcore: ctypes.WinDLL | None = ctypes.WinDLL("shcore")
except OSError:  # pragma: no cover - Windows 7
    shcore = None


class MONITORINFOEXW(Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("rcMonitor", wt.RECT),
        ("rcWork", wt.RECT),
        ("dwFlags", wt.DWORD),
        ("szDevice", wt.WCHAR * 32),
    ]


class DISPLAY_DEVICEW(Structure):
    _fields_ = [
        ("cb", wt.DWORD),
        ("DeviceName", wt.WCHAR * 32),
        ("DeviceString", wt.WCHAR * 128),
        ("StateFlags", wt.DWORD),
        ("DeviceID", wt.WCHAR * 128),
        ("DeviceKey", wt.WCHAR * 128),
    ]


class DEVMODEW(Structure):
    _fields_ = [
        ("dmDeviceName", wt.WCHAR * 32),
        ("dmSpecVersion", wt.WORD),
        ("dmDriverVersion", wt.WORD),
        ("dmSize", wt.WORD),
        ("dmDriverExtra", wt.WORD),
        ("dmFields", wt.DWORD),
        ("dmPositionX", wt.LONG),
        ("dmPositionY", wt.LONG),
        ("dmDisplayOrientation", wt.DWORD),
        ("dmDisplayFixedOutput", wt.DWORD),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", wt.WCHAR * 32),
        ("dmLogPixels", wt.WORD),
        ("dmBitsPerPel", wt.DWORD),
        ("dmPelsWidth", wt.DWORD),
        ("dmPelsHeight", wt.DWORD),
        ("dmDisplayFlags", wt.DWORD),
        ("dmDisplayFrequency", wt.DWORD),
        ("dmICMMethod", wt.DWORD),
        ("dmICMIntent", wt.DWORD),
        ("dmMediaType", wt.DWORD),
        ("dmDitherType", wt.DWORD),
        ("dmReserved1", wt.DWORD),
        ("dmReserved2", wt.DWORD),
        ("dmPanningWidth", wt.DWORD),
        ("dmPanningHeight", wt.DWORD),
    ]


MONITORENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HMONITOR, wt.HDC, POINTER(wt.RECT), wt.LPARAM)
WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

user32.EnumDisplayMonitors.argtypes = (wt.HDC, c_void_p, MONITORENUMPROC, wt.LPARAM)
user32.EnumDisplayMonitors.restype = wt.BOOL
user32.GetMonitorInfoW.argtypes = (wt.HMONITOR, c_void_p)
user32.GetMonitorInfoW.restype = wt.BOOL
user32.EnumDisplaySettingsW.argtypes = (wt.LPCWSTR, wt.DWORD, c_void_p)
user32.EnumDisplaySettingsW.restype = wt.BOOL
user32.EnumDisplayDevicesW.argtypes = (wt.LPCWSTR, wt.DWORD, c_void_p, wt.DWORD)
user32.EnumDisplayDevicesW.restype = wt.BOOL
user32.EnumWindows.argtypes = (WNDENUMPROC, wt.LPARAM)
user32.EnumWindows.restype = wt.BOOL
user32.IsWindow.argtypes = (wt.HWND,)
user32.IsWindow.restype = wt.BOOL
user32.IsWindowVisible.argtypes = (wt.HWND,)
user32.IsWindowVisible.restype = wt.BOOL
user32.IsIconic.argtypes = (wt.HWND,)
user32.IsIconic.restype = wt.BOOL
user32.GetWindowTextLengthW.argtypes = (wt.HWND,)
user32.GetWindowTextLengthW.restype = c_int
user32.GetWindowTextW.argtypes = (wt.HWND, wt.LPWSTR, c_int)
user32.GetWindowTextW.restype = c_int
user32.GetClassNameW.argtypes = (wt.HWND, wt.LPWSTR, c_int)
user32.GetClassNameW.restype = c_int
user32.GetWindowThreadProcessId.argtypes = (wt.HWND, POINTER(wt.DWORD))
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.GetWindowLongPtrW.argtypes = (wt.HWND, c_int)
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetAncestor.argtypes = (wt.HWND, c_uint)
user32.GetAncestor.restype = wt.HWND
user32.GetWindowRect.argtypes = (wt.HWND, POINTER(wt.RECT))
user32.GetWindowRect.restype = wt.BOOL
user32.GetClientRect.argtypes = (wt.HWND, POINTER(wt.RECT))
user32.GetClientRect.restype = wt.BOOL
user32.ClientToScreen.argtypes = (wt.HWND, POINTER(wt.POINT))
user32.ClientToScreen.restype = wt.BOOL
user32.GetWindowDisplayAffinity.argtypes = (wt.HWND, POINTER(wt.DWORD))
user32.GetWindowDisplayAffinity.restype = wt.BOOL
user32.MonitorFromWindow.argtypes = (wt.HWND, wt.DWORD)
user32.MonitorFromWindow.restype = wt.HMONITOR
user32.SetThreadDpiAwarenessContext.argtypes = (c_void_p,)
user32.SetThreadDpiAwarenessContext.restype = c_void_p
dwmapi.DwmGetWindowAttribute.argtypes = (wt.HWND, wt.DWORD, c_void_p, wt.DWORD)
dwmapi.DwmGetWindowAttribute.restype = c_long
kernel32.OpenProcess.argtypes = (wt.DWORD, wt.BOOL, wt.DWORD)
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.CloseHandle.argtypes = (wt.HANDLE,)
kernel32.CloseHandle.restype = wt.BOOL
kernel32.QueryFullProcessImageNameW.argtypes = (wt.HANDLE, wt.DWORD, wt.LPWSTR, POINTER(wt.DWORD))
kernel32.QueryFullProcessImageNameW.restype = wt.BOOL
if shcore is not None:
    shcore.GetDpiForMonitor.argtypes = (wt.HMONITOR, c_int, POINTER(c_uint), POINTER(c_uint))
    shcore.GetDpiForMonitor.restype = c_long

MONITORINFOF_PRIMARY = 1
ENUM_CURRENT_SETTINGS = 0xFFFFFFFF
GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_CHILD = 0x40000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOREDIRECTIONBITMAP = 0x00200000
GA_ROOT = 2
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
WDA_NONE = 0
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = c_void_p(-4)


@contextlib.contextmanager
def per_monitor_dpi_aware() -> Iterator[None]:
    """Use physical-pixel coordinates on this thread only.

    The process-wide DPI awareness belongs to the host application, so we
    switch the *thread* context temporarily instead.
    """
    old = user32.SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
    try:
        yield
    finally:
        if old:
            user32.SetThreadDpiAwarenessContext(c_void_p(old))


def _rect(r: wt.RECT) -> Rect:
    return Rect.from_ltrb(r.left, r.top, r.right, r.bottom)


# --- monitors ----------------------------------------------------------------


def list_monitors() -> list[Monitor]:
    handles: list[int] = []

    def cb(hmon: int, _hdc: int, _rc: object, _lp: int) -> bool:
        handles.append(hmon)
        return True

    proc = MONITORENUMPROC(cb)
    monitors: list[Monitor] = []
    with per_monitor_dpi_aware():
        user32.EnumDisplayMonitors(None, None, proc, 0)
        for hmon in handles:
            info = MONITORINFOEXW()
            info.cbSize = sizeof(info)
            if not user32.GetMonitorInfoW(hmon, byref(info)):
                continue
            device = info.szDevice
            scale = 1.0
            if shcore is not None:
                dx, dy = c_uint(), c_uint()
                if shcore.GetDpiForMonitor(hmon, 0, byref(dx), byref(dy)) == 0:
                    scale = dx.value / 96.0
            mode = DEVMODEW()
            mode.dmSize = sizeof(mode)
            refresh = None
            if user32.EnumDisplaySettingsW(device, ENUM_CURRENT_SETTINGS, byref(mode)):
                refresh = float(mode.dmDisplayFrequency) or None
            dd = DISPLAY_DEVICEW()
            dd.cb = sizeof(dd)
            name = device
            if user32.EnumDisplayDevicesW(device, 0, byref(dd), 0) and dd.DeviceString:
                name = dd.DeviceString
            monitors.append(
                Monitor(
                    id=device,
                    index=0,
                    name=name,
                    rect=_rect(info.rcMonitor),
                    is_primary=bool(info.dwFlags & MONITORINFOF_PRIMARY),
                    scale=scale,
                    refresh_rate=refresh,
                    native_handle=int(hmon),
                )
            )
    # Stable order: primary first, then left-to-right, top-to-bottom.
    monitors.sort(key=lambda m: (not m.is_primary, m.rect.x, m.rect.y))
    return [
        Monitor(
            m.id, i, m.name, m.rect, m.is_primary, m.scale, m.refresh_rate, None, m.native_handle
        )
        for i, m in enumerate(monitors)
    ]


# --- windows -------------------------------------------------------------------


def is_window(hwnd: int) -> bool:
    return bool(user32.IsWindow(hwnd))


def window_title(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def window_class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def window_pid(hwnd: int) -> int | None:
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, byref(pid))
    return pid.value or None


def process_image_path(pid: int) -> str | None:
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        size = wt.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, byref(size)):
            return buf.value
        return None
    finally:
        kernel32.CloseHandle(h)


def process_name(pid: int | None) -> str | None:
    if not pid:
        return None
    path = process_image_path(pid)
    return os.path.basename(path) if path else None


def is_cloaked(hwnd: int) -> bool:
    cloaked = wt.DWORD()
    hr = dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, byref(cloaked), sizeof(cloaked))
    return hr == 0 and cloaked.value != 0


def window_bounds(hwnd: int) -> Rect:
    """Visible window bounds (DWM extended frame bounds, physical pixels).

    This matches the content that Windows Graphics Capture returns for a
    window (no invisible resize borders / drop shadow).
    """
    r = wt.RECT()
    with per_monitor_dpi_aware():
        hr = dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, byref(r), sizeof(r))
        if hr != 0:
            user32.GetWindowRect(hwnd, byref(r))
    return _rect(r)


def client_rect_in_window(hwnd: int) -> Rect:
    """Client area relative to the visible window bounds (physical pixels)."""
    with per_monitor_dpi_aware():
        bounds = window_bounds(hwnd)
        rc = wt.RECT()
        user32.GetClientRect(hwnd, byref(rc))
        pt = wt.POINT(0, 0)
        user32.ClientToScreen(hwnd, byref(pt))
    return Rect(pt.x - bounds.x, pt.y - bounds.y, rc.right - rc.left, rc.bottom - rc.top)


def display_affinity(hwnd: int) -> int:
    aff = wt.DWORD()
    if user32.GetWindowDisplayAffinity(hwnd, byref(aff)):
        return int(aff.value)
    return WDA_NONE


def describe_window(hwnd: int) -> Window:
    pid = window_pid(hwnd)
    return Window(
        handle=int(hwnd),
        title=window_title(hwnd),
        class_name=window_class(hwnd),
        pid=pid,
        process_name=process_name(pid),
        rect=window_bounds(hwnd),
        is_visible=bool(user32.IsWindowVisible(hwnd)),
        is_minimized=bool(user32.IsIconic(hwnd)),
        display=None,
        capture_excluded=display_affinity(hwnd) != WDA_NONE,
    )


def _is_capturable(hwnd: int, include_untitled: bool) -> bool:
    if not user32.IsWindowVisible(hwnd):
        return False
    style = user32.GetWindowLongPtrW(hwnd, GWL_STYLE)
    exstyle = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    if style & WS_CHILD or exstyle & WS_EX_TOOLWINDOW:
        return False
    if user32.GetAncestor(hwnd, GA_ROOT) != hwnd:
        return False
    if is_cloaked(hwnd):  # hidden UWP frames, windows on other virtual desktops
        return False
    if not include_untitled and user32.GetWindowTextLengthW(hwnd) <= 0:
        return False
    return True


def list_windows(include_untitled: bool = False) -> list[Window]:
    hwnds: list[int] = []

    def cb(hwnd: int, _lp: int) -> bool:
        hwnds.append(hwnd)
        return True

    proc = WNDENUMPROC(cb)
    user32.EnumWindows(proc, 0)
    out: list[Window] = []
    for hwnd in hwnds:
        if not hwnd or not _is_capturable(hwnd, include_untitled):
            continue
        w = describe_window(hwnd)
        if w.rect.is_empty() and not w.is_minimized:
            continue
        out.append(w)
    return out


# --- process tree --------------------------------------------------------------


class PROCESSENTRY32W(Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("cntUsage", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", c_long),
        ("dwFlags", wt.DWORD),
        ("szExeFile", wt.WCHAR * 260),
    ]


kernel32.CreateToolhelp32Snapshot.argtypes = (wt.DWORD, wt.DWORD)
kernel32.CreateToolhelp32Snapshot.restype = wt.HANDLE
kernel32.Process32FirstW.argtypes = (wt.HANDLE, POINTER(PROCESSENTRY32W))
kernel32.Process32FirstW.restype = wt.BOOL
kernel32.Process32NextW.argtypes = (wt.HANDLE, POINTER(PROCESSENTRY32W))
kernel32.Process32NextW.restype = wt.BOOL
TH32CS_SNAPPROCESS = 0x2


def process_parents() -> dict[int, int]:
    """Map pid -> parent pid for all processes (snapshot)."""
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == wt.HANDLE(-1).value:
        return {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = sizeof(entry)
        result: dict[int, int] = {}
        ok = kernel32.Process32FirstW(snap, byref(entry))
        while ok:
            result[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            ok = kernel32.Process32NextW(snap, byref(entry))
        return result
    finally:
        kernel32.CloseHandle(snap)


def process_tree(pid: int) -> set[int]:
    """``pid`` and all its descendants (the set WASAPI process loopback covers)."""
    parents = process_parents()
    tree = {pid}
    changed = True
    while changed:
        changed = False
        for child, parent in parents.items():
            if parent in tree and child not in tree and child != parent:
                tree.add(child)
                changed = True
    return tree
