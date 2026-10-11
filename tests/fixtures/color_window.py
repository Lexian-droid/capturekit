"""Deterministic test window (Windows, pure ctypes).

Paints its client area with a solid colour or a four-quadrant pattern and
prints ``HWND <decimal>`` to stdout once visible. Used to verify that window
capture returns the window's own pixels (not whatever covers it on screen).

    python color_window.py --color 0,200,0 --x 100 --y 100 --w 640 --h 360
    python color_window.py --pattern quad --title target
    python color_window.py --color 200,0,0 --topmost        # occluder
"""

from __future__ import annotations

import argparse
import ctypes
import sys
from ctypes import wintypes as wt

if sys.platform != "win32":
    raise SystemExit("Windows only")

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32")
kernel32 = ctypes.WinDLL("kernel32")

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
        ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR),
    ]  # fmt: skip


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [
        ("hdc", wt.HDC),
        ("fErase", wt.BOOL),
        ("rcPaint", wt.RECT),
        ("fRestore", wt.BOOL),
        ("fIncUpdate", wt.BOOL),
        ("rgbReserved", ctypes.c_byte * 32),
    ]


user32.DefWindowProcW.argtypes = (wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
user32.DefWindowProcW.restype = LRESULT
user32.RegisterClassW.argtypes = (ctypes.POINTER(WNDCLASSW),)
user32.CreateWindowExW.argtypes = (
    wt.DWORD,
    wt.LPCWSTR,
    wt.LPCWSTR,
    wt.DWORD,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wt.HWND,
    wt.HMENU,
    wt.HINSTANCE,
    wt.LPVOID,
)
user32.CreateWindowExW.restype = wt.HWND
user32.BeginPaint.argtypes = (wt.HWND, ctypes.POINTER(PAINTSTRUCT))
user32.BeginPaint.restype = wt.HDC
user32.EndPaint.argtypes = (wt.HWND, ctypes.POINTER(PAINTSTRUCT))
user32.FillRect.argtypes = (wt.HDC, ctypes.POINTER(wt.RECT), wt.HBRUSH)
user32.GetClientRect.argtypes = (wt.HWND, ctypes.POINTER(wt.RECT))
user32.InvalidateRect.argtypes = (wt.HWND, ctypes.c_void_p, wt.BOOL)
user32.GetMessageW.argtypes = (ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT)
user32.TranslateMessage.argtypes = (ctypes.POINTER(wt.MSG),)
user32.DispatchMessageW.argtypes = (ctypes.POINTER(wt.MSG),)
user32.SetProcessDpiAwarenessContext.argtypes = (ctypes.c_void_p,)
user32.SetTimer.argtypes = (wt.HWND, ctypes.c_size_t, wt.UINT, ctypes.c_void_p)
user32.SetWindowDisplayAffinity.argtypes = (wt.HWND, wt.DWORD)
gdi32.CreateSolidBrush.argtypes = (wt.COLORREF,)
gdi32.CreateSolidBrush.restype = wt.HBRUSH

WM_DESTROY, WM_PAINT, WM_SIZE, WM_TIMER, WM_ERASEBKGND = 0x2, 0xF, 0x5, 0x113, 0x14
WS_OVERLAPPEDWINDOW, WS_VISIBLE = 0x00CF0000, 0x10000000
WS_EX_TOPMOST = 0x8

QUAD = ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0))  # TL, TR, BL, BR


def _click_wav() -> bytes:
    """40 ms 2 kHz burst, 48 kHz stereo s16."""
    import io
    import math
    import struct
    import wave

    rate, n = 48000, 1920
    frames = b"".join(
        struct.pack("<hh", v, v)
        for v in (int(12000 * math.sin(2 * math.pi * 2000 * i / rate)) for i in range(n))
    )
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames)
    return buf.getvalue()


def rgb(c: tuple[int, int, int]) -> int:
    return c[0] | (c[1] << 8) | (c[2] << 16)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--color", default="0,200,0")
    ap.add_argument("--pattern", choices=("solid", "quad", "flash"), default="solid")
    ap.add_argument("--title", default="capturekit-test-window")
    ap.add_argument("--x", type=int, default=100)
    ap.add_argument("--y", type=int, default=100)
    ap.add_argument("--w", type=int, default=640)
    ap.add_argument("--h", type=int, default=360)
    ap.add_argument("--topmost", action="store_true")
    ap.add_argument("--flash-ms", type=int, default=500)
    ap.add_argument(
        "--exclude", action="store_true", help="SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)"
    )
    ap.add_argument("--beep", action="store_true", help="click on every black->white flash")
    args = ap.parse_args()
    click = ""
    if args.beep:
        import os
        import tempfile

        fd, click = tempfile.mkstemp(suffix=".wav", prefix="ck-click-")
        os.write(fd, _click_wav())
        os.close(fd)

    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # physical pixels
    color = tuple(int(v) for v in args.color.split(","))
    brushes = {
        c: gdi32.CreateSolidBrush(rgb(c)) for c in (color, *QUAD, (0, 0, 0), (255, 255, 255))
    }
    state = {"flash": False}

    def paint(hwnd: int) -> None:
        ps = PAINTSTRUCT()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
        rc = wt.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(rc))
        if args.pattern == "quad":
            mx, my = rc.right // 2, rc.bottom // 2
            for (x0, t, r, b), c in zip(
                (
                    (0, 0, mx, my),
                    (mx, 0, rc.right, my),
                    (0, my, mx, rc.bottom),
                    (mx, my, rc.right, rc.bottom),
                ),
                QUAD,
                strict=False,
            ):
                user32.FillRect(hdc, ctypes.byref(wt.RECT(x0, t, r, b)), brushes[c])
        elif args.pattern == "flash":
            c = (255, 255, 255) if state["flash"] else (0, 0, 0)
            user32.FillRect(hdc, ctypes.byref(rc), brushes[c])
        else:
            user32.FillRect(hdc, ctypes.byref(rc), brushes[color])
        user32.EndPaint(hwnd, ctypes.byref(ps))

    def wndproc(hwnd: int, msg: int, wp: int, lp: int) -> int:
        if msg == WM_PAINT:
            paint(hwnd)
            return 0
        if msg == WM_ERASEBKGND:
            return 1
        if msg == WM_SIZE:
            user32.InvalidateRect(hwnd, None, False)
            return 0
        if msg == WM_TIMER:
            state["flash"] = not state["flash"]
            user32.InvalidateRect(hwnd, None, False)
            user32.UpdateWindow(hwnd)
            if click and state["flash"]:
                import winsound

                winsound.PlaySound(click, winsound.SND_FILENAME | winsound.SND_ASYNC)
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return int(user32.DefWindowProcW(hwnd, msg, wp, lp))

    proc = WNDPROC(wndproc)
    hinst = kernel32.GetModuleHandleW(None)
    cls = WNDCLASSW(0, proc, 0, 0, hinst, None, None, None, None, "CapturekitTestWindow")
    user32.RegisterClassW(ctypes.byref(cls))
    hwnd = user32.CreateWindowExW(
        WS_EX_TOPMOST if args.topmost else 0, "CapturekitTestWindow", args.title,
        WS_OVERLAPPEDWINDOW | WS_VISIBLE, args.x, args.y, args.w, args.h, None, None, hinst, None,
    )  # fmt: skip
    if not hwnd:
        print("CreateWindowExW failed", ctypes.get_last_error(), file=sys.stderr)
        return 1
    if args.exclude:
        user32.SetWindowDisplayAffinity(hwnd, 0x11)  # WDA_EXCLUDEFROMCAPTURE
    if args.pattern == "flash":
        user32.SetTimer(hwnd, 1, args.flash_ms, None)
    user32.UpdateWindow(hwnd)
    print(f"HWND {int(hwnd)}", flush=True)
    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
