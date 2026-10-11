"""Windows Graphics Capture (WGC) through the raw WinRT ABI.

Window capture targets the HWND itself via
``IGraphicsCaptureItemInterop::CreateForWindow``: frames contain the
window's own composition surface, so overlapping windows never appear.
"""

from __future__ import annotations

import logging
import threading
from ctypes import Structure, byref, c_int, c_int32, c_int64, c_long, c_ubyte, c_void_p

import numpy as np

from ..._pipeline.clock import hns_to_ns, now_ns
from ...errors import (
    AccessDeniedError,
    CapabilityUnavailableError,
    NativeError,
    TargetNotFoundError,
    WindowClosedError,
)
from ...types import Monitor, Rect, validate_region
from ..base import AcquiredFrame, Buffer, VideoBackendInfo, blit_clear, crop_box
from . import _d3d11, _win32
from ._com import GUID, ComPtr, check_hr
from ._winrt import (
    EventDelegate,
    EventRegistration,
    activation_factory,
    close_winrt,
    typed_event_handler_iid,
)

log = logging.getLogger("capturekit.windows.wgc")

IID_IGraphicsCaptureItemInterop = GUID.from_str("3628E81B-3CAC-4C60-B7F4-23CE0E0C3356")
IID_IGraphicsCaptureItem = GUID.from_str("79C3F95B-31F7-4EC2-A464-632EF5D30760")
IID_IDirect3D11CaptureFramePoolStatics2 = GUID.from_str("589B103F-6BBC-5DF5-A991-02E28B3B66D5")
IID_IGraphicsCaptureSession2 = GUID.from_str("2C39AE40-7D2E-5044-804E-8B6799D4CF9E")
IID_IGraphicsCaptureSession3 = GUID.from_str("F2CDD966-22AE-5EA1-9596-3A289344C3BE")
IID_IGraphicsCaptureSessionStatics = GUID.from_str("2224A540-5974-49AA-B232-0882536F4CB5")
IID_IDirect3DDxgiInterfaceAccess = GUID.from_str("A9B3D012-3DF2-4EE3-B8D1-8695F457D3C1")

FRAME_ARRIVED_IID = typed_event_handler_iid(
    "Windows.Graphics.Capture.Direct3D11CaptureFramePool", "24eb6d22-1975-422e-82e7-780dbd8ddf24"
)
ITEM_CLOSED_IID = typed_event_handler_iid(
    "Windows.Graphics.Capture.GraphicsCaptureItem", "79c3f95b-31f7-4ec2-a464-632ef5d30760"
)

# vtable slots (IInspectable occupies 0..5)
_INTEROP_CreateForWindow = 3
_INTEROP_CreateForMonitor = 4
_ITEM_get_Size = 7
_ITEM_add_Closed = 8
_ITEM_remove_Closed = 9
_POOLSTATICS2_CreateFreeThreaded = 6
_POOL_Recreate = 6
_POOL_TryGetNextFrame = 7
_POOL_add_FrameArrived = 8
_POOL_remove_FrameArrived = 9
_POOL_CreateCaptureSession = 10
_SESSION_StartCapture = 6
_SESSION2_put_IsCursorCaptureEnabled = 7
_SESSION3_put_IsBorderRequired = 7
_FRAME_get_Surface = 6
_FRAME_get_SystemRelativeTime = 7
_FRAME_get_ContentSize = 8
_SESSIONSTATICS_IsSupported = 6
_DXGIACCESS_GetInterface = 3

E_ACCESSDENIED = -2147024891
E_INVALIDARG = -2147024809

PIXEL_FORMAT_B8G8R8A8 = _d3d11.DXGI_FORMAT_B8G8R8A8_UNORM
POOL_BUFFERS = 2
REFRESH_AFTER_RESIZE_NS = 150_000_000


class SizeInt32(Structure):
    _fields_ = [("Width", c_int32), ("Height", c_int32)]


def is_supported() -> bool:
    """``GraphicsCaptureSession.IsSupported()`` (False if WinRT type missing)."""
    try:
        statics = activation_factory(
            "Windows.Graphics.Capture.GraphicsCaptureSession", IID_IGraphicsCaptureSessionStatics
        )
    except NativeError:
        return False
    with statics:
        val = c_ubyte()
        statics.hcall(_SESSIONSTATICS_IsSupported, (c_void_p,), byref(val), what="IsSupported")
        return bool(val.value)


class WgcBackend:
    """WGC capture of a window (``hwnd``) or a monitor (``monitor``)."""

    def __init__(
        self,
        *,
        hwnd: int | None = None,
        monitor: Monitor | None = None,
        region: Rect | None = None,
        client_area_only: bool = False,
        include_cursor: bool = False,
        border: bool | None = None,
    ) -> None:
        if (hwnd is None) == (monitor is None):
            raise ValueError("exactly one of hwnd or monitor is required")
        self.hwnd = hwnd
        self.monitor = monitor
        self.region = region
        self.client_area_only = client_area_only
        self.include_cursor = include_cursor
        self.border = border
        self._device: ComPtr | None = None
        self._context: ComPtr | None = None
        self._rt_device: ComPtr | None = None
        self._item: ComPtr | None = None
        self._pool: ComPtr | None = None
        self._session: ComPtr | None = None
        self._reader: _d3d11.StagingReader | None = None
        self._frame_reg: EventRegistration | None = None
        self._closed_reg: EventRegistration | None = None
        self._frame_event = threading.Event()
        self._closed = threading.Event()
        self._pool_size = (0, 0)
        self._out_size = (0, 0)
        self.border_applied: bool | None = None
        self.cursor_applied: bool | None = None
        self.resize_count = 0
        self.session_restarts = 0
        self._refresh_deadline = 0
        self.info = VideoBackendInfo("wgc-window" if hwnd is not None else "wgc-monitor", (0, 0))

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        try:
            self._start()
        except BaseException:
            self.close()
            raise

    def _start(self) -> None:
        if self.hwnd is not None:
            if not _win32.is_window(self.hwnd):
                raise TargetNotFoundError(f"window {self.hwnd:#x} does not exist")
            if _win32.display_affinity(self.hwnd) != 0:
                log.warning(
                    "window %#x excludes itself from capture (display affinity); "
                    "frames will be blank — capturekit does not bypass this",
                    self.hwnd,
                )
        if not is_supported():
            raise CapabilityUnavailableError("Windows Graphics Capture is not supported here")
        self._device, self._context = _d3d11.create_device()
        self._rt_device = _d3d11.create_winrt_device(self._device)
        self._reader = _d3d11.StagingReader(self._device, self._context)
        self._item = self._create_item()

        size = SizeInt32()
        self._item.hcall(_ITEM_get_Size, (c_void_p,), byref(size), what="GraphicsCaptureItem.Size")
        if size.Width <= 0 or size.Height <= 0:
            raise CapabilityUnavailableError(
                "capture item has zero size (window minimized at start or not renderable)"
            )
        self._open_session((size.Width, size.Height))
        closed_delegate = EventDelegate(ITEM_CLOSED_IID, lambda s, a: self._on_closed())
        self._closed_reg = EventRegistration.add(
            self._item, _ITEM_add_Closed, _ITEM_remove_Closed, closed_delegate, "Closed"
        )
        base = self._base_rect(self._pool_size)
        if self.region is not None:
            validate_region(self.region, base.size)
        self._out_size = crop_box(base, self.region).size
        self.info.initial_size = self._out_size
        self.info.details.update(border=self.border_applied, cursor=self.cursor_applied)

    def _open_session(self, size: tuple[int, int]) -> None:
        """Create frame pool + capture session for ``size`` and start capturing."""
        assert self._rt_device is not None and self._item is not None
        self._pool_size = size
        with activation_factory(
            "Windows.Graphics.Capture.Direct3D11CaptureFramePool",
            IID_IDirect3D11CaptureFramePoolStatics2,
        ) as statics:
            self._pool = statics.out_ptr(
                _POOLSTATICS2_CreateFreeThreaded, (c_void_p, c_int, c_int32, SizeInt32),
                self._rt_device.as_param, PIXEL_FORMAT_B8G8R8A8, POOL_BUFFERS, SizeInt32(*size),
                what="Direct3D11CaptureFramePool.CreateFreeThreaded", name="FramePool",
            )  # fmt: skip
        self._session = self._pool.out_ptr(
            _POOL_CreateCaptureSession, (c_void_p,), self._item.as_param,
            what="CreateCaptureSession", name="GraphicsCaptureSession",
        )  # fmt: skip
        self._configure_session()
        frame_delegate = EventDelegate(FRAME_ARRIVED_IID, lambda s, a: self._frame_event.set())
        self._frame_reg = EventRegistration.add(
            self._pool, _POOL_add_FrameArrived, _POOL_remove_FrameArrived, frame_delegate,
            "FrameArrived",
        )  # fmt: skip
        self._session.hcall(_SESSION_StartCapture, (), what="StartCapture")

    def _close_session(self) -> None:
        if self._frame_reg is not None:
            try:
                self._frame_reg.remove()
            except NativeError:
                pass
            self._frame_reg = None
        close_winrt(self._session)
        self._session = None
        close_winrt(self._pool)
        self._pool = None

    def _create_item(self) -> ComPtr:
        with activation_factory(
            "Windows.Graphics.Capture.GraphicsCaptureItem", IID_IGraphicsCaptureItemInterop
        ) as interop:
            out = c_void_p()
            if self.hwnd is not None:
                hr = interop.call(
                    _INTEROP_CreateForWindow, c_long, (c_void_p, c_void_p, c_void_p),
                    c_void_p(self.hwnd), byref(IID_IGraphicsCaptureItem), byref(out),
                )  # fmt: skip
                what = f"CreateForWindow({self.hwnd:#x})"
            else:
                assert self.monitor is not None
                hr = interop.call(
                    _INTEROP_CreateForMonitor, c_long, (c_void_p, c_void_p, c_void_p),
                    c_void_p(self.monitor.native_handle), byref(IID_IGraphicsCaptureItem),
                    byref(out),
                )  # fmt: skip
                what = f"CreateForMonitor({self.monitor.id})"
            if hr == E_ACCESSDENIED:
                raise AccessDeniedError(f"{what}: access denied")
            if hr == E_INVALIDARG:
                raise CapabilityUnavailableError(
                    f"{what}: target cannot be captured (invalid, non-top-level or system window)"
                )
            check_hr(hr, what)
            return ComPtr(out.value, "GraphicsCaptureItem")

    def _configure_session(self) -> None:
        assert self._session is not None
        s2 = self._session.try_query(IID_IGraphicsCaptureSession2, "IGraphicsCaptureSession2")
        if s2 is not None:
            with s2:
                hr = s2.call(
                    _SESSION2_put_IsCursorCaptureEnabled,
                    c_long,
                    (c_ubyte,),
                    1 if self.include_cursor else 0,
                )
                self.cursor_applied = hr >= 0 and self.include_cursor
        if self.border is not None:
            s3 = self._session.try_query(IID_IGraphicsCaptureSession3, "IGraphicsCaptureSession3")
            if s3 is None:
                log.info("IsBorderRequired not available on this Windows build")
                self.border_applied = None
            else:
                with s3:
                    hr = s3.call(
                        _SESSION3_put_IsBorderRequired, c_long, (c_ubyte,), 1 if self.border else 0
                    )
                    self.border_applied = self.border if hr >= 0 else None
                    if hr < 0:
                        log.info(
                            "IsBorderRequired=%s rejected (0x%08X)", self.border, hr & 0xFFFFFFFF
                        )

    def _on_closed(self) -> None:
        self._closed.set()
        self._frame_event.set()

    def close(self) -> None:
        self._close_session()
        if self._closed_reg is not None:
            try:
                self._closed_reg.remove()
            except NativeError:
                pass
            self._closed_reg = None
        if self._reader is not None:
            self._reader.release()
            self._reader = None
        for name in ("_item", "_rt_device", "_context", "_device"):
            p = getattr(self, name)
            if p is not None:
                p.release()
                setattr(self, name, None)

    # -- capture -----------------------------------------------------------
    @property
    def target_closed(self) -> bool:
        return self._closed.is_set() or (self.hwnd is not None and not _win32.is_window(self.hwnd))

    def _base_rect(self, content: tuple[int, int]) -> Rect:
        full = Rect(0, 0, *content)
        if self.hwnd is not None and self.client_area_only:
            return _win32.client_rect_in_window(self.hwnd).intersect(full)
        return full

    def _next_frame(self) -> ComPtr | None:
        """Drain the pool, keeping only the newest frame."""
        assert self._pool is not None
        latest: ComPtr | None = None
        while True:
            out = c_void_p()
            self._pool.hcall(_POOL_TryGetNextFrame, (c_void_p,), byref(out), what="TryGetNextFrame")
            if not out.value:
                return latest
            if latest is not None:
                close_winrt(latest)
            latest = ComPtr(out.value, "Direct3D11CaptureFrame")

    def acquire(self, out: Buffer | None, timeout_s: float) -> AcquiredFrame | None:
        if self.target_closed:
            raise WindowClosedError(f"capture target {self.hwnd or self.monitor} was closed")
        self._frame_event.clear()
        frame = self._next_frame()
        if frame is None and timeout_s > 0:
            self._frame_event.wait(timeout_s)
            if self.target_closed:
                raise WindowClosedError(f"capture target {self.hwnd or self.monitor} was closed")
            frame = self._next_frame()
        if frame is None:
            if self._refresh_deadline and now_ns() > self._refresh_deadline:
                # Resized window has not presented a full-size frame (static
                # content): a new session always delivers an initial frame.
                self._refresh_deadline = 0
                self.session_restarts += 1
                self._close_session()
                self._open_session(self._pool_size)
            return None
        try:
            return self._process(frame, out)
        finally:
            close_winrt(frame)

    def _process(self, frame: ComPtr, out: Buffer | None) -> AcquiredFrame:
        assert self._reader is not None and self._pool is not None
        ts = c_int64()
        frame.hcall(
            _FRAME_get_SystemRelativeTime, (c_void_p,), byref(ts), what="SystemRelativeTime"
        )
        cs = SizeInt32()
        frame.hcall(_FRAME_get_ContentSize, (c_void_p,), byref(cs), what="ContentSize")
        content = (max(0, cs.Width), max(0, cs.Height))

        surface = frame.out_ptr(_FRAME_get_Surface, (), what="Frame.Surface", name="Surface")
        with surface, surface.query(IID_IDirect3DDxgiInterfaceAccess, "DxgiAccess") as access:
            tex = access.out_ptr(
                _DXGIACCESS_GetInterface, (c_void_p,), byref(_d3d11.IID_ID3D11Texture2D),
                what="GetInterface(ID3D11Texture2D)", name="ID3D11Texture2D",
            )  # fmt: skip
        with tex:
            desc = _d3d11.texture_desc(tex)
            visible = (min(content[0], desc.Width), min(content[1], desc.Height))
            box = crop_box(self._base_rect(visible), self.region)
            if out is None:
                w, h = box.size if not box.is_empty() else self._out_size
                dest = np.empty((max(1, h), max(1, w), 4), dtype=np.uint8)
            else:
                dest = out
            if box.is_empty():
                dest[...] = 0
            else:
                self._reader.copy_box(tex, box.x, box.y, box.width, box.height)
                self._reader.read_into(dest)
                blit_clear(dest, min(box.width, dest.shape[1]), min(box.height, dest.shape[0]))

        if content != self._pool_size and content[0] > 0 and content[1] > 0:
            # Window resized: new frames need a pool matching the new size.
            assert self._rt_device is not None
            self._pool.hcall(
                _POOL_Recreate, (c_void_p, c_int, c_int32, SizeInt32),
                self._rt_device.as_param, PIXEL_FORMAT_B8G8R8A8, POOL_BUFFERS,
                SizeInt32(*content), what="FramePool.Recreate",
            )  # fmt: skip
            self._pool_size = content
            self.resize_count += 1
            self._refresh_deadline = now_ns() + REFRESH_AFTER_RESIZE_NS
        elif (desc.Width, desc.Height) == content:
            self._refresh_deadline = 0
        stamp = hns_to_ns(ts.value) if ts.value > 0 else now_ns()
        return AcquiredFrame(dest, stamp, content)
