"""Deterministic X11 test window (pure ctypes, no toolkit).

    python3 x11_color_window.py --display :99 --color 0,200,0 --x 100 --y 100 --w 640 --h 360
    python3 x11_color_window.py --display :99 --pattern quad --title target

Prints ``WINDOW <decimal id>`` once mapped. Repaints on Expose. Without a
window manager (bare Xvfb) the position is honoured exactly and the most
recently mapped/raised window is on top.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import sys
from ctypes import POINTER, byref, c_char_p, c_int, c_long, c_uint, c_ulong, c_void_p

QUAD = ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0))  # TL, TR, BL, BR
Expose, ConfigureNotify = 12, 22
ExposureMask, StructureNotifyMask = 1 << 15, 1 << 17


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--display", required=True)
    ap.add_argument("--color", default="0,200,0")
    ap.add_argument("--pattern", choices=("solid", "quad"), default="solid")
    ap.add_argument("--title", default="capturekit-x11-test")
    ap.add_argument("--x", type=int, default=100)
    ap.add_argument("--y", type=int, default=100)
    ap.add_argument("--w", type=int, default=640)
    ap.add_argument("--h", type=int, default=360)
    args = ap.parse_args()

    x = ctypes.CDLL("libX11.so.6")
    x.XOpenDisplay.restype = c_void_p
    x.XOpenDisplay.argtypes = (c_char_p,)
    x.XDefaultScreen.argtypes = (c_void_p,)
    x.XRootWindow.restype = c_ulong
    x.XRootWindow.argtypes = (c_void_p, c_int)
    x.XCreateSimpleWindow.restype = c_ulong
    x.XCreateSimpleWindow.argtypes = (
        c_void_p,
        c_ulong,
        c_int,
        c_int,
        c_uint,
        c_uint,
        c_uint,
        c_ulong,
        c_ulong,
    )
    x.XStoreName.argtypes = (c_void_p, c_ulong, c_char_p)
    x.XSetClassHint.argtypes = (c_void_p, c_ulong, c_void_p)
    x.XSelectInput.argtypes = (c_void_p, c_ulong, c_long)
    x.XMapRaised.argtypes = (c_void_p, c_ulong)
    x.XCreateGC.restype = c_void_p
    x.XCreateGC.argtypes = (c_void_p, c_ulong, c_ulong, c_void_p)
    x.XSetForeground.argtypes = (c_void_p, c_void_p, c_ulong)
    x.XFillRectangle.argtypes = (c_void_p, c_ulong, c_void_p, c_int, c_int, c_uint, c_uint)
    x.XNextEvent.argtypes = (c_void_p, c_void_p)
    x.XFlush.argtypes = (c_void_p,)
    x.XSync.argtypes = (c_void_p, c_int)

    dpy = x.XOpenDisplay(args.display.encode())
    if not dpy:
        print("cannot open display", file=sys.stderr)
        return 1
    root = x.XRootWindow(dpy, x.XDefaultScreen(dpy))

    def pixel(c: tuple[int, ...]) -> int:
        return (c[0] << 16) | (c[1] << 8) | c[2]

    color = tuple(int(v) for v in args.color.split(","))
    win = x.XCreateSimpleWindow(dpy, root, args.x, args.y, args.w, args.h, 0, 0, pixel(color))
    x.XStoreName(dpy, win, args.title.encode())

    class XClassHint(ctypes.Structure):
        _fields_ = [("res_name", c_char_p), ("res_class", c_char_p)]

    hint = XClassHint(b"capturekit-test", b"CapturekitTestWindow")
    x.XSetClassHint(dpy, win, byref(hint))
    x.XSelectInput(dpy, win, ExposureMask | StructureNotifyMask)
    gc = x.XCreateGC(dpy, win, 0, None)
    size = [args.w, args.h]

    def paint() -> None:
        w, h = size
        if args.pattern == "quad":
            mx, my = w // 2, h // 2
            for (rx, ry, rw, rh), c in zip(
                (
                    (0, 0, mx, my),
                    (mx, 0, w - mx, my),
                    (0, my, mx, h - my),
                    (mx, my, w - mx, h - my),
                ),
                QUAD,
                strict=True,
            ):
                x.XSetForeground(dpy, gc, pixel(c))
                x.XFillRectangle(dpy, win, gc, rx, ry, rw, rh)
        else:
            x.XSetForeground(dpy, gc, pixel(color))
            x.XFillRectangle(dpy, win, gc, 0, 0, w, h)
        x.XFlush(dpy)

    x.XMapRaised(dpy, win)
    x.XSync(dpy, 0)
    paint()
    print(f"WINDOW {win}", flush=True)
    ev = (c_long * 24)()
    while True:
        x.XNextEvent(dpy, ev)
        etype = ctypes.cast(ev, POINTER(c_int))[0]
        if etype == Expose:
            paint()
        elif etype == ConfigureNotify:
            # XConfigureEvent: width/height at int offsets 14/15 on LP64.
            ints = ctypes.cast(ev, POINTER(c_int))
            size[:] = [ints[14], ints[15]]
            paint()


if __name__ == "__main__":
    raise SystemExit(main())
