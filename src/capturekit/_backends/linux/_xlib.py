"""Xlib + extensions through ctypes.

Reference: docs/research/linux-x11-and-audio.md. Key safety rules:

* Xlib's default error handler calls ``exit()``: a process-global handler is
  installed that records errors per Display; requests that may fail go
  through :meth:`XDisplay.checked` (issue, ``XSync``, inspect).
* The default IO-error path also exits; ``XSetIOErrorExitHandler`` (libX11
  >= 1.7) is used so a dying X server becomes a Python exception.
* One :class:`XDisplay` per thread; never shared.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import threading
from collections.abc import Callable
from ctypes import (
    CFUNCTYPE,
    POINTER,
    Structure,
    byref,
    c_char_p,
    c_int,
    c_long,
    c_short,
    c_ubyte,
    c_uint,
    c_ulong,
    c_ushort,
    c_void_p,
)
from typing import Any, TypeVar

from ...errors import CapabilityUnavailableError, NativeError, TargetLostError

T = TypeVar("T")

XID = c_ulong
Window = c_ulong
Atom = c_ulong
Drawable = c_ulong
Pixmap = c_ulong
Bool = c_int
Status = c_int

ZPixmap = 2
AllPlanes = 0xFFFFFFFFFFFFFFFF
LSBFirst = 0
IsUnmapped, IsUnviewable, IsViewable = 0, 1, 2
InputOutput = 1
StructureNotifyMask = 1 << 17
PropertyChangeMask = 1 << 22
DestroyNotify, UnmapNotify, MapNotify, ConfigureNotify = 17, 18, 19, 22
CompositeRedirectAutomatic = 0
XDamageReportNonEmpty = 3
XA_WINDOW = 33
XA_CARDINAL = 6
AnyPropertyType = 0
BadWindow, BadMatch, BadDrawable, BadAccess = 3, 8, 9, 10
XRES_CLIENT_ID_PID_MASK = 1 << 1


def _load(name: str, soname: str, required: bool = True) -> ctypes.CDLL | None:
    for cand in (soname, ctypes.util.find_library(name)):
        if cand:
            try:
                return ctypes.CDLL(cand)
            except OSError:
                continue
    if required:
        raise CapabilityUnavailableError(
            f"{soname} not found (install the X11 client library package, e.g. libx11-6)"
        )
    return None


# --- structures ------------------------------------------------------------------


class XImage(Structure):
    _fields_ = [
        ("width", c_int),
        ("height", c_int),
        ("xoffset", c_int),
        ("format", c_int),
        ("data", c_void_p),
        ("byte_order", c_int),
        ("bitmap_unit", c_int),
        ("bitmap_bit_order", c_int),
        ("bitmap_pad", c_int),
        ("depth", c_int),
        ("bytes_per_line", c_int),
        ("bits_per_pixel", c_int),
        ("red_mask", c_ulong),
        ("green_mask", c_ulong),
        ("blue_mask", c_ulong),
        ("obdata", c_void_p),
        ("funcs", c_void_p * 6),
    ]


class Visual(Structure):
    _fields_ = [
        ("ext_data", c_void_p),
        ("visualid", c_ulong),
        ("class_", c_int),
        ("red_mask", c_ulong),
        ("green_mask", c_ulong),
        ("blue_mask", c_ulong),
        ("bits_per_rgb", c_int),
        ("map_entries", c_int),
    ]


class XShmSegmentInfo(Structure):
    _fields_ = [("shmseg", c_ulong), ("shmid", c_int), ("shmaddr", c_void_p), ("readOnly", Bool)]


class XWindowAttributes(Structure):
    _fields_ = [
        ("x", c_int),
        ("y", c_int),
        ("width", c_int),
        ("height", c_int),
        ("border_width", c_int),
        ("depth", c_int),
        ("visual", c_void_p),
        ("root", Window),
        ("class_", c_int),
        ("bit_gravity", c_int),
        ("win_gravity", c_int),
        ("backing_store", c_int),
        ("backing_planes", c_ulong),
        ("backing_pixel", c_ulong),
        ("save_under", Bool),
        ("colormap", c_ulong),
        ("map_installed", Bool),
        ("map_state", c_int),
        ("all_event_masks", c_long),
        ("your_event_mask", c_long),
        ("do_not_propagate_mask", c_long),
        ("override_redirect", Bool),
        ("screen", c_void_p),
    ]


class XErrorEvent(Structure):
    _fields_ = [
        ("type", c_int),
        ("display", c_void_p),
        ("resourceid", XID),
        ("serial", c_ulong),
        ("error_code", c_ubyte),
        ("request_code", c_ubyte),
        ("minor_code", c_ubyte),
    ]


class XAnyEvent(Structure):
    _fields_ = [
        ("type", c_int),
        ("serial", c_ulong),
        ("send_event", Bool),
        ("display", c_void_p),
        ("window", Window),
    ]


class XConfigureEvent(Structure):
    _fields_ = [
        ("type", c_int),
        ("serial", c_ulong),
        ("send_event", Bool),
        ("display", c_void_p),
        ("event", Window),
        ("window", Window),
        ("x", c_int),
        ("y", c_int),
        ("width", c_int),
        ("height", c_int),
        ("border_width", c_int),
        ("above", Window),
        ("override_redirect", Bool),
    ]


class XStructureEvent(Structure):  # Destroy/Unmap/Map share event+window at the start
    _fields_ = [
        ("type", c_int),
        ("serial", c_ulong),
        ("send_event", Bool),
        ("display", c_void_p),
        ("event", Window),
        ("window", Window),
    ]


XEvent = c_long * 24


class XRRMonitorInfo(Structure):
    _fields_ = [
        ("name", Atom),
        ("primary", Bool),
        ("automatic", Bool),
        ("noutput", c_int),
        ("x", c_int),
        ("y", c_int),
        ("width", c_int),
        ("height", c_int),
        ("mwidth", c_int),
        ("mheight", c_int),
        ("outputs", POINTER(c_ulong)),
    ]


class XRRScreenResources(Structure):
    _fields_ = [
        ("timestamp", c_ulong),
        ("configTimestamp", c_ulong),
        ("ncrtc", c_int),
        ("crtcs", POINTER(c_ulong)),
        ("noutput", c_int),
        ("outputs", POINTER(c_ulong)),
        ("nmode", c_int),
        ("modes", c_void_p),
    ]


class XRRCrtcInfo(Structure):
    _fields_ = [
        ("timestamp", c_ulong),
        ("x", c_int),
        ("y", c_int),
        ("width", c_uint),
        ("height", c_uint),
        ("mode", c_ulong),
        ("rotation", c_ushort),
        ("noutput", c_int),
        ("outputs", POINTER(c_ulong)),
        ("rotations", c_ushort),
        ("npossible", c_int),
        ("possible", POINTER(c_ulong)),
    ]


class XineramaScreenInfo(Structure):
    _fields_ = [
        ("screen_number", c_int),
        ("x_org", c_short),
        ("y_org", c_short),
        ("width", c_short),
        ("height", c_short),
    ]


class XResClientIdSpec(Structure):
    _fields_ = [("client", XID), ("mask", c_uint)]


class XResClientIdValue(Structure):
    _fields_ = [("spec", XResClientIdSpec), ("length", c_long), ("value", c_void_p)]


class XClassHint(Structure):
    _fields_ = [("res_name", c_void_p), ("res_class", c_void_p)]


# --- library setup ------------------------------------------------------------------


def _sig(lib: ctypes.CDLL | None, name: str, restype: Any, *argtypes: Any) -> None:
    if lib is None or not hasattr(lib, name):
        return
    fn = getattr(lib, name)
    fn.restype = restype
    fn.argtypes = argtypes


_x11_lib = _load("X11", "libX11.so.6")
assert _x11_lib is not None  # required=True raises otherwise
x11: ctypes.CDLL = _x11_lib
xext = _load("Xext", "libXext.so.6", required=False)
xcomposite = _load("Xcomposite", "libXcomposite.so.1", required=False)
xdamage = _load("Xdamage", "libXdamage.so.1", required=False)
xfixes = _load("Xfixes", "libXfixes.so.3", required=False)
xrandr = _load("Xrandr", "libXrandr.so.2", required=False)
xres = _load("XRes", "libXRes.so.1", required=False)
xinerama = _load("Xinerama", "libXinerama.so.1", required=False)
libc = ctypes.CDLL(None, use_errno=True)

_sig(x11, "XInitThreads", Status)
_sig(x11, "XOpenDisplay", c_void_p, c_char_p)
_sig(x11, "XCloseDisplay", c_int, c_void_p)
_sig(x11, "XDefaultScreen", c_int, c_void_p)
_sig(x11, "XScreenCount", c_int, c_void_p)
_sig(x11, "XRootWindow", Window, c_void_p, c_int)
_sig(x11, "XDefaultVisual", c_void_p, c_void_p, c_int)
_sig(x11, "XDefaultDepth", c_int, c_void_p, c_int)
_sig(x11, "XDisplayWidth", c_int, c_void_p, c_int)
_sig(x11, "XDisplayHeight", c_int, c_void_p, c_int)
_sig(x11, "XSync", c_int, c_void_p, Bool)
_sig(x11, "XFlush", c_int, c_void_p)
_sig(x11, "XFree", c_int, c_void_p)
_sig(x11, "XNextRequest", c_ulong, c_void_p)
_sig(x11, "XSetErrorHandler", c_void_p, c_void_p)
_sig(x11, "XSetIOErrorExitHandler", None, c_void_p, c_void_p, c_void_p)
_sig(
    x11, "XQueryExtension", Bool, c_void_p, c_char_p, POINTER(c_int), POINTER(c_int), POINTER(c_int)
)
_sig(x11, "XInternAtom", Atom, c_void_p, c_char_p, Bool)
_sig(x11, "XGetAtomName", c_void_p, c_void_p, Atom)
_sig(
    x11,
    "XGetWindowProperty",
    c_int,
    c_void_p,
    Window,
    Atom,
    c_long,
    c_long,
    Bool,
    Atom,
    POINTER(Atom),
    POINTER(c_int),
    POINTER(c_ulong),
    POINTER(c_ulong),
    POINTER(c_void_p),
)
_sig(x11, "XGetWindowAttributes", Status, c_void_p, Window, POINTER(XWindowAttributes))
_sig(
    x11,
    "XTranslateCoordinates",
    Bool,
    c_void_p,
    Window,
    Window,
    c_int,
    c_int,
    POINTER(c_int),
    POINTER(c_int),
    POINTER(Window),
)
_sig(
    x11,
    "XQueryTree",
    Status,
    c_void_p,
    Window,
    POINTER(Window),
    POINTER(Window),
    POINTER(POINTER(Window)),
    POINTER(c_uint),
)
_sig(x11, "XGetClassHint", Status, c_void_p, Window, POINTER(XClassHint))
_sig(x11, "XFetchName", Status, c_void_p, Window, POINTER(c_void_p))
_sig(
    x11,
    "XGetImage",
    POINTER(XImage),
    c_void_p,
    Drawable,
    c_int,
    c_int,
    c_uint,
    c_uint,
    c_ulong,
    c_int,
)
_sig(x11, "XDestroyImage", c_int, POINTER(XImage))
_sig(x11, "XSelectInput", c_int, c_void_p, Window, c_long)
_sig(x11, "XPending", c_int, c_void_p)
_sig(x11, "XNextEvent", c_int, c_void_p, POINTER(XEvent))
_sig(x11, "XFreePixmap", c_int, c_void_p, Pixmap)
_sig(x11, "XServerVendor", c_char_p, c_void_p)
_sig(x11, "XVendorRelease", c_int, c_void_p)

_sig(xext, "XShmQueryExtension", Bool, c_void_p)
_sig(
    xext,
    "XShmCreateImage",
    POINTER(XImage),
    c_void_p,
    c_void_p,
    c_uint,
    c_int,
    c_void_p,
    POINTER(XShmSegmentInfo),
    c_uint,
    c_uint,
)
_sig(xext, "XShmAttach", Bool, c_void_p, POINTER(XShmSegmentInfo))
_sig(xext, "XShmDetach", Bool, c_void_p, POINTER(XShmSegmentInfo))
_sig(xext, "XShmGetImage", Bool, c_void_p, Drawable, POINTER(XImage), c_int, c_int, c_ulong)

_sig(xcomposite, "XCompositeQueryExtension", Bool, c_void_p, POINTER(c_int), POINTER(c_int))
_sig(xcomposite, "XCompositeQueryVersion", Status, c_void_p, POINTER(c_int), POINTER(c_int))
_sig(xcomposite, "XCompositeRedirectWindow", None, c_void_p, Window, c_int)
_sig(xcomposite, "XCompositeUnredirectWindow", None, c_void_p, Window, c_int)
_sig(xcomposite, "XCompositeNameWindowPixmap", Pixmap, c_void_p, Window)

_sig(xdamage, "XDamageQueryExtension", Bool, c_void_p, POINTER(c_int), POINTER(c_int))
_sig(xdamage, "XDamageCreate", XID, c_void_p, Drawable, c_int)
_sig(xdamage, "XDamageDestroy", None, c_void_p, XID)
_sig(xdamage, "XDamageSubtract", None, c_void_p, XID, XID, XID)

_sig(xrandr, "XRRQueryExtension", Bool, c_void_p, POINTER(c_int), POINTER(c_int))
_sig(xrandr, "XRRQueryVersion", Status, c_void_p, POINTER(c_int), POINTER(c_int))
_sig(xrandr, "XRRGetMonitors", POINTER(XRRMonitorInfo), c_void_p, Window, Bool, POINTER(c_int))
_sig(xrandr, "XRRFreeMonitors", None, POINTER(XRRMonitorInfo))
_sig(xrandr, "XRRGetScreenResourcesCurrent", POINTER(XRRScreenResources), c_void_p, Window)
_sig(xrandr, "XRRFreeScreenResources", None, POINTER(XRRScreenResources))
_sig(xrandr, "XRRGetCrtcInfo", POINTER(XRRCrtcInfo), c_void_p, POINTER(XRRScreenResources), c_ulong)
_sig(xrandr, "XRRFreeCrtcInfo", None, POINTER(XRRCrtcInfo))
_sig(xrandr, "XRRGetOutputPrimary", c_ulong, c_void_p, Window)

_sig(xinerama, "XineramaIsActive", Bool, c_void_p)
_sig(xinerama, "XineramaQueryScreens", POINTER(XineramaScreenInfo), c_void_p, POINTER(c_int))

_sig(xres, "XResQueryExtension", Bool, c_void_p, POINTER(c_int), POINTER(c_int))
_sig(
    xres,
    "XResQueryClientIds",
    Status,
    c_void_p,
    c_long,
    POINTER(XResClientIdSpec),
    POINTER(c_long),
    POINTER(POINTER(XResClientIdValue)),
)
_sig(xres, "XResGetClientPid", c_int, POINTER(XResClientIdValue))
_sig(xres, "XResClientIdsDestroy", None, c_long, POINTER(XResClientIdValue))

_sig(libc, "shmget", c_int, c_int, ctypes.c_size_t, c_int)
_sig(libc, "shmat", c_void_p, c_int, c_void_p, c_int)
_sig(libc, "shmdt", c_int, c_void_p)
_sig(libc, "shmctl", c_int, c_int, c_int, c_void_p)

IPC_PRIVATE, IPC_CREAT, IPC_RMID = 0, 0o1000, 0

# libX11 >= 1.8 calls XInitThreads itself; calling it again is harmless and
# needed for older versions (must precede any other Xlib call).
x11.XInitThreads()


# --- error handling -------------------------------------------------------------------


class XError(NativeError):
    def __init__(self, what: str, error_code: int, request_code: int = 0) -> None:
        self.error_code = error_code
        self.request_code = request_code
        super().__init__(f"{what}: X error {error_code} (request {request_code})", error_code)


class XConnectionLostError(TargetLostError):
    """The X server connection died (server exited, display closed)."""


_errors_lock = threading.Lock()
_errors: dict[int, list[tuple[int, int, int]]] = {}
_dead: set[int] = set()
_ours: set[int] = set()  # Display pointers opened by capturekit

_ERROR_HANDLER = CFUNCTYPE(c_int, c_void_p, POINTER(XErrorEvent))
_IO_ERROR_HANDLER = CFUNCTYPE(c_int, c_void_p)
_IO_EXIT_HANDLER = CFUNCTYPE(None, c_void_p, c_void_p)

_sig(x11, "XSetIOErrorHandler", c_void_p, c_void_p)

# Handlers are process-global. Errors on displays capturekit does not own are
# forwarded to whatever handler was installed before (e.g. Tk/Qt), so other
# X clients in the same process keep their behaviour.
_prev_error: Any = None
_prev_io_error: Any = None


def _on_error(dpy: int | None, ev: Any) -> int:
    key = int(dpy or 0)
    with _errors_lock:
        mine = key in _ours
        if mine:
            e = ev.contents
            _errors.setdefault(key, []).append(
                (int(e.error_code), int(e.request_code), int(e.resourceid))
            )
    if not mine and _prev_error is not None:
        return int(_prev_error(dpy, ev))
    return 0


def _on_io_error(dpy: int | None) -> int:
    # Xlib's default IO error handler calls exit(1) *before* the per-display
    # exit handler runs (libX11 XlibInt.c, verified 2026-10-11), so ours must
    # return for capturekit's displays.
    key = int(dpy or 0)
    with _errors_lock:
        mine = key in _ours
        if mine:
            _dead.add(key)
    if not mine and _prev_io_error is not None:
        return int(_prev_io_error(dpy))
    return 0


def _on_io_exit(dpy: int | None, _data: int | None) -> None:
    # Must not call Xlib here. Mark the display dead; Python code checks it.
    with _errors_lock:
        _dead.add(int(dpy or 0))


_error_handler_ref = _ERROR_HANDLER(_on_error)
_io_error_ref = _IO_ERROR_HANDLER(_on_io_error)
_io_exit_ref = _IO_EXIT_HANDLER(_on_io_exit)
_old = x11.XSetErrorHandler(ctypes.cast(_error_handler_ref, c_void_p))
_prev_error = _ERROR_HANDLER(_old) if _old else None
#: libX11 >= 1.7: a dying X server can be survived. Older versions exit the
#: process once the IO error handler returns, so we leave their default alone.
HAS_IO_EXIT_HANDLER = hasattr(x11, "XSetIOErrorExitHandler")
if HAS_IO_EXIT_HANDLER:
    _old_io = x11.XSetIOErrorHandler(ctypes.cast(_io_error_ref, c_void_p))
    _prev_io_error = _IO_ERROR_HANDLER(_old_io) if _old_io else None


class XDisplay:
    """A private X connection (one per thread)."""

    def __init__(self, name: str) -> None:
        self.name = name
        ptr = x11.XOpenDisplay(name.encode())
        if not ptr:
            raise CapabilityUnavailableError(f"cannot open X display {name!r}")
        self.ptr: int = int(ptr)
        with _errors_lock:
            _ours.add(self.ptr)
        if HAS_IO_EXIT_HANDLER:
            x11.XSetIOErrorExitHandler(self.ptr, ctypes.cast(_io_exit_ref, c_void_p), None)
        self.screen = x11.XDefaultScreen(self.ptr)
        self.root = int(x11.XRootWindow(self.ptr, self.screen))
        self._atoms: dict[str, int] = {}
        self._ext: dict[str, bool] = {}
        self.thread = threading.get_ident()

    # -- lifecycle -------------------------------------------------------------
    def close(self) -> None:
        if self.ptr:
            ptr, self.ptr = self.ptr, 0
            with _errors_lock:
                _errors.pop(ptr, None)
            # Even a dead connection must be closed to free Xlib's structures;
            # XCloseDisplay on a broken connection does not touch the socket.
            x11.XCloseDisplay(ptr)
            with _errors_lock:
                _dead.discard(ptr)
                _ours.discard(ptr)

    def __enter__(self) -> XDisplay:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def alive(self) -> bool:
        with _errors_lock:
            return bool(self.ptr) and self.ptr not in _dead

    def ensure_alive(self) -> None:
        if not self.alive:
            raise XConnectionLostError(f"connection to X display {self.name!r} was lost")

    # -- error-checked calls -----------------------------------------------------
    def _take_errors(self) -> list[tuple[int, int, int]]:
        with _errors_lock:
            return _errors.pop(self.ptr, [])

    def sync(self) -> None:
        self.ensure_alive()
        x11.XSync(self.ptr, False)
        self.ensure_alive()

    def checked(self, what: str, fn: Callable[..., T], *args: Any) -> T:
        """Call ``fn``, round-trip with XSync and raise :class:`XError` on errors."""
        self.ensure_alive()
        self._take_errors()
        res = fn(*args)
        x11.XSync(self.ptr, False)
        self.ensure_alive()
        errs = self._take_errors()
        if errs:
            code, req, _rid = errs[0]
            raise XError(what, code, req)
        return res

    # -- helpers ----------------------------------------------------------------------
    def atom(self, name: str) -> int:
        a = self._atoms.get(name)
        if a is None:
            a = int(x11.XInternAtom(self.ptr, name.encode(), False))
            self._atoms[name] = a
        return a

    def has_extension(self, name: str) -> bool:
        if name not in self._ext:
            op, ev, er = c_int(), c_int(), c_int()
            self._ext[name] = bool(
                x11.XQueryExtension(self.ptr, name.encode(), byref(op), byref(ev), byref(er))
            )
        return self._ext[name]

    def extension_event_base(self, name: str) -> int | None:
        op, ev, er = c_int(), c_int(), c_int()
        if x11.XQueryExtension(self.ptr, name.encode(), byref(op), byref(ev), byref(er)):
            return int(ev.value)
        return None

    def get_property(
        self,
        window: int,
        name: str,
        req_type: int = AnyPropertyType,
    ) -> tuple[int, int, bytes, int] | None:
        """(type, format, raw bytes, item count) or None. 32-bit items are C longs."""
        actual_type, actual_format = Atom(), c_int()
        nitems, after = c_ulong(), c_ulong()
        data = c_void_p()
        status = self.checked(
            f"XGetWindowProperty({name})", x11.XGetWindowProperty, self.ptr, window,
            self.atom(name), 0, 0x7FFFFFFF, False, req_type, byref(actual_type),
            byref(actual_format), byref(nitems), byref(after), byref(data),
        )  # fmt: skip
        if status != 0 or not data.value:
            return None
        try:
            fmt = actual_format.value
            item_size = {8: 1, 16: ctypes.sizeof(c_short), 32: ctypes.sizeof(c_long)}.get(fmt, 1)
            raw = ctypes.string_at(data.value, nitems.value * item_size)
            return int(actual_type.value), fmt, raw, int(nitems.value)
        finally:
            x11.XFree(data)

    def get_longs(self, window: int, name: str, req_type: int = AnyPropertyType) -> list[int]:
        prop = self.get_property(window, name, req_type)
        if prop is None or prop[1] != 32:
            return []
        arr = (c_ulong * prop[3]).from_buffer_copy(prop[2])
        return [int(v) for v in arr]

    def get_string(self, window: int, name: str) -> str | None:
        prop = self.get_property(window, name)
        if prop is None or prop[1] != 8:
            return None
        return prop[2].decode("utf-8", "replace").rstrip("\x00")
