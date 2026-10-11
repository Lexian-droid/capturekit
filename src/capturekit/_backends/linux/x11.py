"""X11 video: display resolution, monitors, windows, XShm and XComposite capture."""

from __future__ import annotations

import ctypes
import logging
import os
from ctypes import POINTER, byref, c_int, c_uint

import numpy as np

from ..._pipeline.clock import now_ns
from ...errors import (
    CapabilityUnavailableError,
    NativeError,
    TargetNotFoundError,
    UnsupportedDisplayServerError,
    WindowClosedError,
)
from ...types import Monitor, Rect, Window, validate_region
from ..base import AcquiredFrame, Buffer, VideoBackendInfo, blit_clear, crop_box
from . import _xlib as X
from ._xlib import XDisplay, XError

log = logging.getLogger("capturekit.linux.x11")


# --- display / session ------------------------------------------------------------


def resolve_display(display: str | None) -> tuple[str, bool]:
    """Return ``(display_name, explicit)``.

    Without an explicit display a Wayland session is rejected: ``$DISPLAY``
    would point at XWayland, which only contains X11 clients, not the desktop.
    """
    if display is not None:
        return display, True
    if os.environ.get("WAYLAND_DISPLAY") or os.environ.get("XDG_SESSION_TYPE") == "wayland":
        raise UnsupportedDisplayServerError(
            "Wayland session detected: native Wayland capture is not supported. Pass an "
            "explicit X11 display (e.g. display=':99' for an Xvfb server)."
        )
    name = os.environ.get("DISPLAY")
    if not name:
        raise UnsupportedDisplayServerError("no X11 display: $DISPLAY is not set")
    return name, False


def open_display(display: str | None) -> XDisplay:
    name, explicit = resolve_display(display)
    d = XDisplay(name)
    if d.has_extension("XWAYLAND") and not explicit:
        d.close()
        raise UnsupportedDisplayServerError(
            f"{name} is XWayland (Wayland desktop); pass an explicit X11 display to capture "
            "X11 clients on it"
        )
    return d


def server_kind(d: XDisplay) -> str:
    if d.has_extension("XWAYLAND"):
        return "xwayland"
    names = [m.name for m in _monitors(d)]
    if names == ["screen"] and _wm_window(d) is None:
        return "xvfb"  # heuristic: vfb's single RandR output is named "screen"
    return "x11"


# --- monitors -------------------------------------------------------------------------


def _atom_name(d: XDisplay, atom: int) -> str:
    p = X.x11.XGetAtomName(d.ptr, atom)
    if not p:
        return f"atom-{atom}"
    try:
        return ctypes.string_at(p).decode("utf-8", "replace")
    finally:
        X.x11.XFree(p)


def _monitors(d: XDisplay) -> list[Monitor]:
    found: list[tuple[str, Rect, bool]] = []
    if X.xrandr is not None and d.has_extension("RANDR"):
        major, minor = c_int(), c_int()
        X.xrandr.XRRQueryVersion(d.ptr, byref(major), byref(minor))
        if (major.value, minor.value) >= (1, 5):
            n = c_int()
            mons = d.checked(
                "XRRGetMonitors", X.xrandr.XRRGetMonitors, d.ptr, d.root, True, byref(n)
            )
            if mons:
                try:
                    for i in range(n.value):
                        m = mons[i]
                        found.append(
                            (
                                _atom_name(d, m.name),
                                Rect(m.x, m.y, m.width, m.height),
                                bool(m.primary),
                            )
                        )
                finally:
                    X.xrandr.XRRFreeMonitors(mons)
        if not found and (major.value, minor.value) >= (1, 2):
            res = X.xrandr.XRRGetScreenResourcesCurrent(d.ptr, d.root)
            if res:
                try:
                    primary_out = X.xrandr.XRRGetOutputPrimary(d.ptr, d.root)
                    r = res.contents
                    for i in range(r.ncrtc):
                        ci = X.xrandr.XRRGetCrtcInfo(d.ptr, res, r.crtcs[i])
                        if not ci:
                            continue
                        try:
                            c = ci.contents
                            if c.mode and c.width > 0 and c.height > 0:
                                outs = [c.outputs[k] for k in range(c.noutput)]
                                found.append(
                                    (
                                        f"crtc-{i}",
                                        Rect(c.x, c.y, c.width, c.height),
                                        primary_out in outs,
                                    )
                                )
                        finally:
                            X.xrandr.XRRFreeCrtcInfo(ci)
                finally:
                    X.xrandr.XRRFreeScreenResources(res)
    if (
        not found
        and X.xinerama is not None
        and d.has_extension("XINERAMA")
        and X.xinerama.XineramaIsActive(d.ptr)
    ):
        n = c_int()
        scr = X.xinerama.XineramaQueryScreens(d.ptr, byref(n))
        if scr:
            try:
                for i in range(n.value):
                    s = scr[i]
                    found.append(
                        (
                            f"xinerama-{s.screen_number}",
                            Rect(s.x_org, s.y_org, s.width, s.height),
                            i == 0,
                        )
                    )
            finally:
                X.x11.XFree(scr)
    if not found:
        w = X.x11.XDisplayWidth(d.ptr, d.screen)
        h = X.x11.XDisplayHeight(d.ptr, d.screen)
        found.append(("root", Rect(0, 0, w, h), True))
    if not any(p for _, _, p in found):
        found[0] = (found[0][0], found[0][1], True)
    found.sort(key=lambda t: (not t[2], t[1].x, t[1].y))
    return [
        Monitor(id=name, index=i, name=name, rect=rect, is_primary=primary, display=d.name)
        for i, (name, rect, primary) in enumerate(found)
    ]


def list_monitors(display: str | None) -> list[Monitor]:
    with open_display(display) as d:
        return _monitors(d)


# --- windows --------------------------------------------------------------------------


def _wm_window(d: XDisplay) -> int | None:
    vals = d.get_longs(d.root, "_NET_SUPPORTING_WM_CHECK", X.XA_WINDOW)
    if not vals:
        return None
    try:
        child = d.get_longs(vals[0], "_NET_SUPPORTING_WM_CHECK", X.XA_WINDOW)
    except XError:
        return None
    return vals[0] if child and child[0] == vals[0] else None


def _attributes(d: XDisplay, w: int) -> X.XWindowAttributes:
    attrs = X.XWindowAttributes()
    d.checked("XGetWindowAttributes", X.x11.XGetWindowAttributes, d.ptr, w, byref(attrs))
    return attrs


def _query_tree(d: XDisplay, w: int) -> tuple[int, list[int]]:
    root, parent = X.Window(), X.Window()
    children = POINTER(X.Window)()
    n = c_uint()
    d.checked(
        "XQueryTree",
        X.x11.XQueryTree,
        d.ptr,
        w,
        byref(root),
        byref(parent),
        byref(children),
        byref(n),
    )
    try:
        return int(parent.value), [int(children[i]) for i in range(n.value)]
    finally:
        if children:
            X.x11.XFree(children)


def _toplevel_frame(d: XDisplay, w: int) -> int:
    """The ancestor of ``w`` directly below the root (the WM frame, if any)."""
    cur = w
    for _ in range(64):
        parent, _ = _query_tree(d, cur)
        if parent in (0, d.root):
            return cur
        cur = parent
    return cur


def _client_candidates(d: XDisplay) -> list[int]:
    if _wm_window(d) is not None:
        stacking = d.get_longs(d.root, "_NET_CLIENT_LIST_STACKING", X.XA_WINDOW)
        if stacking:
            return stacking
        return d.get_longs(d.root, "_NET_CLIENT_LIST", X.XA_WINDOW)
    # No EWMH window manager (bare Xvfb): mapped top-level InputOutput windows.
    _, children = _query_tree(d, d.root)
    out = []
    for w in children:
        try:
            a = _attributes(d, w)
        except XError:
            continue
        if a.class_ == X.InputOutput and not a.override_redirect and a.width > 1 and a.height > 1:
            out.append(w)
    return out


def _window_pid(d: XDisplay, w: int) -> int | None:
    if X.xres is not None and d.has_extension("X-Resource"):
        spec = X.XResClientIdSpec(w, X.XRES_CLIENT_ID_PID_MASK)
        num = ctypes.c_long()
        ids = POINTER(X.XResClientIdValue)()
        try:
            d.checked(
                "XResQueryClientIds",
                X.xres.XResQueryClientIds,
                d.ptr,
                1,
                byref(spec),
                byref(num),
                byref(ids),
            )
        except XError:
            num.value = 0
        if ids:
            try:
                for i in range(num.value):
                    pid = X.xres.XResGetClientPid(byref(ids[i]))
                    if pid > 0:
                        return int(pid)
            finally:
                X.xres.XResClientIdsDestroy(num, ids)
    vals = d.get_longs(w, "_NET_WM_PID", X.XA_CARDINAL)
    return vals[0] if vals else None


def _process_name(pid: int | None) -> str | None:
    if not pid:
        return None
    try:
        with open(f"/proc/{pid}/comm", encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def describe_window(d: XDisplay, w: int) -> Window:
    try:
        a = _attributes(d, w)
        title = d.get_string(w, "_NET_WM_NAME") or d.get_string(w, "WM_NAME") or ""
        cls = d.get_property(w, "WM_CLASS")
        class_name = None
        if cls is not None:
            parts = cls[2].split(b"\x00")
            class_name = (parts[1] if len(parts) > 1 and parts[1] else parts[0]).decode(
                "utf-8", "replace"
            ) or None
        x, y, child = c_int(), c_int(), X.Window()
        X.x11.XTranslateCoordinates(d.ptr, w, d.root, 0, 0, byref(x), byref(y), byref(child))
        states = d.get_longs(w, "_NET_WM_STATE")
        hidden = d.atom("_NET_WM_STATE_HIDDEN") in states
        pid = _window_pid(d, w)
    except XError as exc:
        if exc.error_code in (X.BadWindow, X.BadDrawable):
            raise TargetNotFoundError(f"X window {w:#x} does not exist") from exc
        raise
    return Window(
        handle=int(w),
        title=title,
        class_name=class_name,
        pid=pid,
        process_name=_process_name(pid),
        rect=Rect(x.value, y.value, a.width, a.height),
        is_visible=a.map_state == X.IsViewable,
        is_minimized=hidden or a.map_state != X.IsViewable,
        display=d.name,
    )


def list_windows(display: str | None, include_untitled: bool) -> list[Window]:
    with open_display(display) as d:
        out = []
        for w in _client_candidates(d):
            try:
                info = describe_window(d, w)
            except TargetNotFoundError:
                continue  # vanished while enumerating
            if not include_untitled and not info.title:
                continue
            out.append(info)
        return out


def get_window(handle: int, display: str | None) -> Window:
    with open_display(display) as d:
        return describe_window(d, handle)


# --- image grabbing -------------------------------------------------------------------


class Grabber:
    """Reads a rectangle of a drawable into BGRA, via MIT-SHM when possible."""

    def __init__(self, d: XDisplay, visual: int, depth: int) -> None:
        self.d = d
        self.visual = visual
        self.depth = depth
        # Validate the pixel layout from the Visual: images read from pixmaps
        # carry zero colour masks (the GetImage reply has no visual for a
        # pixmap), so the returned XImage cannot be used for this check.
        vis = X.Visual.from_address(visual)
        masks = (int(vis.red_mask), int(vis.green_mask), int(vis.blue_mask))
        if depth not in (24, 32) or masks != (0xFF0000, 0xFF00, 0xFF):
            raise CapabilityUnavailableError(
                f"unsupported X visual: depth {depth}, masks "
                f"{masks[0]:#x}/{masks[1]:#x}/{masks[2]:#x} (only 24/32-bit BGRX is supported)"
            )
        self.use_shm = X.xext is not None and d.has_extension("MIT-SHM")
        self._img: ctypes._Pointer[X.XImage] | None = None
        self._seg = X.XShmSegmentInfo()
        self._size = (0, 0)
        self.shm_failed_reason = ""

    def _create_shm(self, w: int, h: int) -> bool:
        assert X.xext is not None
        self._free_shm()
        img = X.xext.XShmCreateImage(
            self.d.ptr, self.visual, self.depth, X.ZPixmap, None, byref(self._seg), w, h
        )
        if not img:
            return False
        size = img.contents.bytes_per_line * h
        shmid = X.libc.shmget(X.IPC_PRIVATE, size, X.IPC_CREAT | 0o600)
        if shmid < 0:
            X.x11.XDestroyImage(img)
            self.shm_failed_reason = f"shmget failed (errno {ctypes.get_errno()})"
            return False
        addr = X.libc.shmat(shmid, None, 0)
        if addr in (None, ctypes.c_void_p(-1).value):
            X.libc.shmctl(shmid, X.IPC_RMID, None)
            X.x11.XDestroyImage(img)
            self.shm_failed_reason = "shmat failed"
            return False
        self._seg.shmid = shmid
        self._seg.shmaddr = addr
        self._seg.readOnly = False
        img.contents.data = addr
        try:
            self.d.checked("XShmAttach", X.xext.XShmAttach, self.d.ptr, byref(self._seg))
        except XError as exc:
            # Remote display or different IPC namespace: use XGetImage.
            self.shm_failed_reason = str(exc)
            X.libc.shmdt(addr)
            X.libc.shmctl(shmid, X.IPC_RMID, None)
            img.contents.data = None
            X.x11.XDestroyImage(img)
            self._seg = X.XShmSegmentInfo()
            return False
        # Mark for removal now: the segment lives until the last detach, so a
        # crash cannot leak it.
        X.libc.shmctl(shmid, X.IPC_RMID, None)
        self._img = img
        self._size = (w, h)
        return True

    def _free_shm(self) -> None:
        if self._img is not None:
            assert X.xext is not None
            if self.d.alive:
                X.xext.XShmDetach(self.d.ptr, byref(self._seg))
                X.x11.XSync(self.d.ptr, False)
            X.libc.shmdt(self._seg.shmaddr)
            self._img.contents.data = None
            X.x11.XDestroyImage(self._img)
            self._img = None
            self._seg = X.XShmSegmentInfo()
            self._size = (0, 0)

    def close(self) -> None:
        self._free_shm()

    def grab(self, drawable: int, x: int, y: int, w: int, h: int, out: Buffer) -> None:
        """Copy ``drawable[x:x+w, y:y+h]`` into the top-left of ``out``."""
        if self.use_shm and self._size != (w, h) and not self._create_shm(w, h):
            log.info("MIT-SHM unavailable (%s); using XGetImage", self.shm_failed_reason)
            self.use_shm = False
        if self.use_shm:
            assert X.xext is not None and self._img is not None
            self.d.checked(
                "XShmGetImage",
                X.xext.XShmGetImage,
                self.d.ptr,
                drawable,
                self._img,
                x,
                y,
                X.AllPlanes,
            )
            self._copy(self._img.contents, out)
            return
        img = self.d.checked(
            "XGetImage", X.x11.XGetImage, self.d.ptr, drawable, x, y, w, h, X.AllPlanes, X.ZPixmap
        )
        if not img:
            raise NativeError("XGetImage returned NULL")
        try:
            self._copy(img.contents, out)
        finally:
            X.x11.XDestroyImage(img)

    @staticmethod
    def _copy(img: X.XImage, out: Buffer) -> None:
        if img.bits_per_pixel != 32 or img.byte_order != X.LSBFirst:
            raise CapabilityUnavailableError(
                f"unsupported X image format: {img.bits_per_pixel} bpp, byte order "
                f"{img.byte_order} (only 32 bpp little-endian is supported)"
            )
        w, h, stride = img.width, img.height, img.bytes_per_line
        cw, ch = min(w, out.shape[1]), min(h, out.shape[0])
        raw = (ctypes.c_ubyte * (stride * (h - 1) + w * 4)).from_address(img.data)
        src = np.lib.stride_tricks.as_strided(
            np.frombuffer(raw, dtype=np.uint8), shape=(h, w, 4), strides=(stride, 4, 1)
        )
        out[:ch, :cw] = src[:ch, :cw]
        if img.depth != 32:
            out[:ch, :cw, 3] = 255  # X byte of depth-24 pixels is undefined


# --- monitor backend -----------------------------------------------------------------


class X11MonitorBackend:
    """Root-window capture of one monitor (MIT-SHM, XGetImage fallback)."""

    def __init__(self, monitor: Monitor, region: Rect | None, display: str | None) -> None:
        self.monitor = monitor
        self.region = region
        self.display = display if display is not None else monitor.display
        self._d: XDisplay | None = None
        self._grabber: Grabber | None = None
        self._crop = crop_box(Rect(0, 0, monitor.width, monitor.height), region)
        self.info = VideoBackendInfo("x11-shm", self._crop.size)

    def start(self) -> None:
        name, explicit = resolve_display(self.display)
        d = open_display(name if explicit else None)
        if d.has_extension("XWAYLAND"):
            d.close()
            raise UnsupportedDisplayServerError(
                "monitor capture on XWayland would only show X11 clients, not the Wayland "
                "desktop; not supported (window capture of X11 clients is)"
            )
        self._d = d
        if self.region is not None:
            validate_region(self.region, (self.monitor.width, self.monitor.height))
        visual = X.x11.XDefaultVisual(d.ptr, d.screen)
        depth = X.x11.XDefaultDepth(d.ptr, d.screen)
        self._grabber = Grabber(d, visual, depth)
        self.info.name = "x11-shm" if self._grabber.use_shm else "x11-getimage"
        self.info.details["server"] = server_kind(d)

    def acquire(self, out: Buffer | None, timeout_s: float) -> AcquiredFrame | None:
        assert self._d is not None and self._grabber is not None
        c = self._crop.translate(self.monitor.rect.x, self.monitor.rect.y)
        dest = out if out is not None else np.empty((c.height, c.width, 4), np.uint8)
        self._grabber.grab(self._d.root, c.x, c.y, c.width, c.height, dest)
        blit_clear(dest, min(c.width, dest.shape[1]), min(c.height, dest.shape[0]))
        if not self._grabber.use_shm:
            self.info.name = "x11-getimage"
        # Root capture has no change notification: every poll is a new frame.
        return AcquiredFrame(dest, now_ns(), (self.monitor.width, self.monitor.height))

    def close(self) -> None:
        if self._grabber is not None:
            self._grabber.close()
            self._grabber = None
        if self._d is not None:
            self._d.close()
            self._d = None


# --- window backend (XComposite) -----------------------------------------------------


class CompositeWindowBackend:
    """Exclusive window capture: the window is redirected off-screen with
    XComposite (Automatic mode, shareable with compositing managers) and
    read from its own pixmap, so overlapping windows never appear."""

    def __init__(
        self, window: int, region: Rect | None, client_area_only: bool, display: str | None
    ) -> None:
        self.window = window
        self.region = region
        self.client_area_only = client_area_only
        self.display = display
        self._d: XDisplay | None = None
        self._target = window
        self._pixmap = 0
        self._damage = 0
        self._damage_event = -1
        self._dirty = True
        self._viewable = False
        self._closed = False
        self._size = (0, 0)
        self._grabber: Grabber | None = None
        self._redirected = False
        self.info = VideoBackendInfo("x11-composite", (0, 0))

    def start(self) -> None:
        if self.display is None:
            raise CapabilityUnavailableError("X11 window capture needs the window's display")
        d = open_display(self.display)
        self._d = d
        try:
            if X.xcomposite is None or not d.has_extension("Composite"):
                raise CapabilityUnavailableError(
                    f"the X server {d.name} lacks the Composite extension; exclusive window "
                    "capture is unavailable (capturekit does not fall back to screen cropping)"
                )
            major, minor = c_int(0), c_int(4)
            X.xcomposite.XCompositeQueryVersion(d.ptr, byref(major), byref(minor))
            if (major.value, minor.value) < (0, 2):
                raise CapabilityUnavailableError("Composite >= 0.2 required")
            try:
                self._target = (
                    self.window if self.client_area_only else _toplevel_frame(d, self.window)
                )
                attrs = _attributes(d, self._target)
            except XError as exc:
                raise TargetNotFoundError(f"X window {self.window:#x} does not exist") from exc
            d.checked(
                "XSelectInput", X.x11.XSelectInput, d.ptr, self._target, X.StructureNotifyMask
            )
            d.checked(
                "XCompositeRedirectWindow",
                X.xcomposite.XCompositeRedirectWindow,
                d.ptr,
                self._target,
                X.CompositeRedirectAutomatic,
            )
            self._redirected = True
            if X.xdamage is not None and d.has_extension("DAMAGE"):
                self._damage = int(
                    d.checked(
                        "XDamageCreate",
                        X.xdamage.XDamageCreate,
                        d.ptr,
                        self._target,
                        X.XDamageReportNonEmpty,
                    )
                )
                base = d.extension_event_base("DAMAGE")
                self._damage_event = base if base is not None else -1
            self._grabber = Grabber(d, attrs.visual, attrs.depth)
            self._size = (attrs.width, attrs.height)
            self._viewable = attrs.map_state == X.IsViewable
            if self._viewable:
                self._name_pixmap()
            base_rect = Rect(0, 0, *self._size)
            if self.region is not None:
                validate_region(self.region, base_rect.size)
            self.info.initial_size = crop_box(base_rect, self.region).size
            self.info.details.update(
                server=server_kind(d),
                damage=bool(self._damage),
                shm=self._grabber.use_shm,
                depth=attrs.depth,
            )
        except BaseException:
            self.close()
            raise

    def _name_pixmap(self) -> None:
        assert self._d is not None and X.xcomposite is not None
        self._free_pixmap()
        try:
            self._pixmap = int(
                self._d.checked(
                    "XCompositeNameWindowPixmap",
                    X.xcomposite.XCompositeNameWindowPixmap,
                    self._d.ptr,
                    self._target,
                )
            )
        except XError as exc:
            if exc.error_code == X.BadMatch:  # not viewable (unmapped / iconified)
                self._viewable = False
                return
            if exc.error_code in (X.BadWindow, X.BadDrawable):
                self._closed = True
                return
            raise
        self._dirty = True

    def _free_pixmap(self) -> None:
        if self._pixmap and self._d is not None and self._d.alive:
            X.x11.XFreePixmap(self._d.ptr, self._pixmap)
        self._pixmap = 0

    def _drain_events(self) -> None:
        d = self._d
        assert d is not None
        ev = X.XEvent()
        while X.x11.XPending(d.ptr) > 0:
            X.x11.XNextEvent(d.ptr, byref(ev))
            etype = ctypes.cast(byref(ev), POINTER(c_int))[0]
            if etype == X.ConfigureNotify:
                ce = ctypes.cast(byref(ev), POINTER(X.XConfigureEvent))[0]
                if ce.window == self._target and (ce.width, ce.height) != self._size:
                    self._size = (ce.width, ce.height)
                    if self._viewable:
                        self._name_pixmap()  # storage is reallocated on resize
            elif etype in (X.UnmapNotify, X.MapNotify, X.DestroyNotify):
                se = ctypes.cast(byref(ev), POINTER(X.XStructureEvent))[0]
                if se.window != self._target:
                    continue
                if etype == X.DestroyNotify:
                    self._closed = True
                elif etype == X.UnmapNotify:
                    self._viewable = False
                    self._free_pixmap()
                else:
                    self._viewable = True
                    self._name_pixmap()
            elif etype == self._damage_event:
                self._dirty = True
        d.ensure_alive()

    def acquire(self, out: Buffer | None, timeout_s: float) -> AcquiredFrame | None:
        d = self._d
        assert d is not None and self._grabber is not None
        self._drain_events()
        if self._closed:
            raise WindowClosedError(f"X window {self.window:#x} was destroyed")
        if not self._viewable or not self._pixmap:
            return None  # unmapped/minimized: no content, never substitute the screen
        if self._damage and not self._dirty:
            return None
        base = Rect(0, 0, *self._size)
        box = crop_box(base, self.region)
        if out is None:
            dest = np.empty((max(1, box.height), max(1, box.width), 4), np.uint8)
        else:
            dest = out
        if box.is_empty():
            dest[...] = 0
        else:
            if self._damage:
                assert X.xdamage is not None
                X.xdamage.XDamageSubtract(d.ptr, self._damage, 0, 0)
                self._dirty = False
            try:
                self._grabber.grab(self._pixmap, box.x, box.y, box.width, box.height, dest)
            except XError as exc:
                # Window resized/unmapped between the event drain and the read.
                log.debug("pixmap read failed (%s); renaming", exc)
                self._drain_events()
                if self._closed:
                    raise WindowClosedError(f"X window {self.window:#x} was destroyed") from exc
                if self._viewable:
                    self._name_pixmap()
                self._dirty = True
                return None
            blit_clear(dest, min(box.width, dest.shape[1]), min(box.height, dest.shape[0]))
        return AcquiredFrame(dest, now_ns(), self._size)

    def close(self) -> None:
        d = self._d
        if d is None:
            return
        if self._grabber is not None:
            self._grabber.close()
            self._grabber = None
        if d.alive:
            if self._damage and X.xdamage is not None:
                X.xdamage.XDamageDestroy(d.ptr, self._damage)
            self._free_pixmap()
            if self._redirected and X.xcomposite is not None and not self._closed:
                X.xcomposite.XCompositeUnredirectWindow(
                    d.ptr, self._target, X.CompositeRedirectAutomatic
                )
            X.x11.XSync(d.ptr, False)
        d.close()
        self._d = None
