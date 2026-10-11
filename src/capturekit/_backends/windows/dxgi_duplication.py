"""Monitor capture with DXGI Desktop Duplication."""

from __future__ import annotations

import ctypes
import logging
import time
from ctypes import Structure, byref, c_int64, c_long, c_uint, c_void_p
from ctypes import wintypes as wt

import numpy as np

from ..._pipeline.clock import now_ns
from ...errors import AccessDeniedError, CapabilityUnavailableError, NativeError
from ...types import Monitor, Rect, validate_region
from ..base import AcquiredFrame, Buffer, VideoBackendInfo, blit_clear, crop_box
from . import _d3d11
from ._com import ComPtr, check_hr, hr_u32
from .cursor import CursorShape, composite

log = logging.getLogger("capturekit.windows.dxgi")

DXGI_ERROR_ACCESS_LOST = -2005270490  # 0x887A0026
DXGI_ERROR_WAIT_TIMEOUT = -2005270489  # 0x887A0027
DXGI_ERROR_UNSUPPORTED = -2005270524  # 0x887A0004
DXGI_ERROR_NOT_CURRENTLY_AVAILABLE = -2005270494  # 0x887A0022
DXGI_ERROR_SESSION_DISCONNECTED = -2005270492  # 0x887A0024
E_ACCESSDENIED = -2147024891  # 0x80070005
DXGI_MODE_ROTATION_IDENTITY = 1
DXGI_MODE_ROTATION_UNSPECIFIED = 0

# IDXGIOutputDuplication
_DUP_GetDesc = 7
_DUP_AcquireNextFrame = 8
_DUP_GetFramePointerShape = 11
_DUP_ReleaseFrame = 14
# IDXGIOutput1
_OUT1_DuplicateOutput = 22


class DXGI_OUTDUPL_POINTER_POSITION(Structure):
    _fields_ = [("Position", wt.POINT), ("Visible", wt.BOOL)]


class DXGI_OUTDUPL_FRAME_INFO(Structure):
    _fields_ = [
        ("LastPresentTime", c_int64),
        ("LastMouseUpdateTime", c_int64),
        ("AccumulatedFrames", c_uint),
        ("RectsCoalesced", wt.BOOL),
        ("ProtectedContentMaskedOut", wt.BOOL),
        ("PointerPosition", DXGI_OUTDUPL_POINTER_POSITION),
        ("TotalMetadataBufferSize", c_uint),
        ("PointerShapeBufferSize", c_uint),
    ]


class DXGI_OUTDUPL_POINTER_SHAPE_INFO(Structure):
    _fields_ = [
        ("Type", c_uint),
        ("Width", c_uint),
        ("Height", c_uint),
        ("Pitch", c_uint),
        ("HotSpot", wt.POINT),
    ]


class DXGI_MODE_DESC(Structure):
    _fields_ = [
        ("Width", c_uint),
        ("Height", c_uint),
        ("RefreshNumerator", c_uint),
        ("RefreshDenominator", c_uint),
        ("Format", c_uint),
        ("ScanlineOrdering", c_uint),
        ("Scaling", c_uint),
    ]


class DXGI_OUTDUPL_DESC(Structure):
    _fields_ = [
        ("ModeDesc", DXGI_MODE_DESC),
        ("Rotation", c_uint),
        ("DesktopImageInSystemMemory", wt.BOOL),
    ]


_kernel32 = ctypes.WinDLL("kernel32")
_qpc_freq = c_int64()
_kernel32.QueryPerformanceFrequency(byref(_qpc_freq))


class DxgiDuplicationBackend:
    """Captures one monitor. Rotated outputs are rejected (use the WGC backend)."""

    def __init__(self, monitor: Monitor, region: Rect | None, include_cursor: bool) -> None:
        self.monitor = monitor
        self.region = region
        self.include_cursor = include_cursor
        self._adapter: ComPtr | None = None
        self._output1: ComPtr | None = None
        self._device: ComPtr | None = None
        self._context: ComPtr | None = None
        self._dup: ComPtr | None = None
        self._reader: _d3d11.StagingReader | None = None
        self._size = (monitor.width, monitor.height)
        self._crop = crop_box(Rect(0, 0, *self._size), region)
        self._have_image = False
        self._cursor_shape: CursorShape | None = None
        self._cursor_pos = (0, 0)
        self._cursor_visible = False
        self.protected_frames = 0
        self.access_lost_count = 0
        self.info = VideoBackendInfo("dxgi-duplication", self._crop.size)

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        outputs = _d3d11.enum_outputs()
        try:
            match = None
            for adapter, output, odesc, adesc in outputs:
                if int(odesc.Monitor or 0) == self.monitor.native_handle and match is None:
                    match = (adapter, output, adesc)
                else:
                    adapter.release()
                    output.release()
            if match is None:
                raise CapabilityUnavailableError(
                    f"no DXGI output for monitor {self.monitor.id}; the display may be driven "
                    "by an indirect/virtual driver — try MonitorBackend.WGC"
                )
            adapter, output, adesc = match
            self._adapter = adapter
            self.info.details["adapter"] = adesc.Description
            self._output1 = output.query(_d3d11.IID_IDXGIOutput1, "IDXGIOutput1")
            output.release()
            self._device, self._context = _d3d11.create_device(self._adapter)
            self._reader = _d3d11.StagingReader(self._device, self._context)
            self._duplicate()
        except BaseException:
            self.close()
            raise

    def _duplicate(self) -> None:
        assert self._output1 is not None and self._device is not None
        out = c_void_p()
        hr = self._output1.call(
            _OUT1_DuplicateOutput, c_long, (c_void_p, c_void_p), self._device.as_param, byref(out)
        )
        if hr == DXGI_ERROR_UNSUPPORTED:
            raise CapabilityUnavailableError(
                "DXGI Desktop Duplication is not supported for this output/adapter "
                "(hybrid-GPU or remote session) — try MonitorBackend.WGC"
            )
        if hr == DXGI_ERROR_NOT_CURRENTLY_AVAILABLE:
            raise CapabilityUnavailableError(
                "DXGI Desktop Duplication is currently unavailable (too many duplication "
                "sessions or a full-screen exclusive application)"
            )
        if hr == E_ACCESSDENIED:
            raise AccessDeniedError("desktop duplication denied (secure desktop / lock screen?)")
        check_hr(hr, "IDXGIOutput1::DuplicateOutput")
        self._dup = ComPtr(out.value, "IDXGIOutputDuplication")
        desc = DXGI_OUTDUPL_DESC()
        self._dup.call(_DUP_GetDesc, None, (c_void_p,), byref(desc))
        if desc.Rotation not in (DXGI_MODE_ROTATION_IDENTITY, DXGI_MODE_ROTATION_UNSPECIFIED):
            raise CapabilityUnavailableError(
                "rotated monitors are not supported by the DXGI backend; use MonitorBackend.WGC"
            )
        size = (int(desc.ModeDesc.Width), int(desc.ModeDesc.Height))
        if size != self._size:
            log.info("duplication size %s differs from monitor rect %s", size, self._size)
            self._size = size
        if self.region is not None:
            validate_region(self.region, self._size)
        self._crop = crop_box(Rect(0, 0, *self._size), self.region)
        self.info.initial_size = self._crop.size
        self.info.details["refresh"] = (
            desc.ModeDesc.RefreshNumerator / desc.ModeDesc.RefreshDenominator
            if desc.ModeDesc.RefreshDenominator else None
        )  # fmt: skip

    def close(self) -> None:
        if self._reader is not None:
            self._reader.release()
            self._reader = None
        for name in ("_dup", "_output1", "_context", "_device", "_adapter"):
            p = getattr(self, name)
            if p is not None:
                p.release()
                setattr(self, name, None)

    # -- capture -------------------------------------------------------------
    def _recover(self) -> None:
        self.access_lost_count += 1
        if self._dup is not None:
            self._dup.release()
            self._dup = None
        try:
            self._duplicate()
        except (AccessDeniedError, CapabilityUnavailableError, NativeError) as exc:
            # Secure desktop / mode switch in progress: retry on next acquire.
            log.debug("duplication not yet available: %s", exc)

    def acquire(self, out: Buffer | None, timeout_s: float) -> AcquiredFrame | None:
        # AcquireNextFrame may return pointer-only updates; keep waiting for an
        # image until the timeout expires.
        deadline = now_ns() + int(timeout_s * 1e9)
        while True:
            remaining = max(0.0, (deadline - now_ns()) / 1e9)
            frame = self._acquire_once(out, remaining)
            if frame is not None or remaining <= 0:
                return frame

    def _acquire_once(self, out: Buffer | None, timeout_s: float) -> AcquiredFrame | None:
        if self._dup is None:
            self._recover()
            if self._dup is None:
                if timeout_s > 0:
                    time.sleep(min(timeout_s, 0.05))
                return None
        dup = self._dup
        info = DXGI_OUTDUPL_FRAME_INFO()
        res = c_void_p()
        hr = dup.call(
            _DUP_AcquireNextFrame, c_long, (c_uint, c_void_p, c_void_p),
            max(0, int(timeout_s * 1000)), byref(info), byref(res),
        )  # fmt: skip
        if hr == DXGI_ERROR_WAIT_TIMEOUT:
            return None
        if hr in (DXGI_ERROR_ACCESS_LOST, E_ACCESSDENIED, DXGI_ERROR_SESSION_DISCONNECTED):
            log.info("duplication lost (0x%08X); recreating", hr_u32(hr))
            self._recover()
            return None
        check_hr(hr, "AcquireNextFrame")
        resource = ComPtr(res.value, "IDXGIResource")
        try:
            assert self._reader is not None
            # Frames with LastPresentTime == 0 carry only pointer/metadata updates;
            # their texture is not a valid desktop image (verified: all-zero).
            new_image = info.LastPresentTime != 0
            cursor_changed = False
            if self.include_cursor and info.LastMouseUpdateTime != 0:
                pos = (info.PointerPosition.Position.x, info.PointerPosition.Position.y)
                vis = bool(info.PointerPosition.Visible)
                cursor_changed = pos != self._cursor_pos or vis != self._cursor_visible
                self._cursor_pos, self._cursor_visible = pos, vis
                if info.PointerShapeBufferSize:
                    self._read_pointer_shape(info.PointerShapeBufferSize)
                    cursor_changed = True
            if info.ProtectedContentMaskedOut:
                self.protected_frames += 1
            if new_image:
                with resource.query(_d3d11.IID_ID3D11Texture2D, "ID3D11Texture2D") as tex:
                    c = self._crop
                    self._reader.copy_box(tex, c.x, c.y, c.width, c.height)
                self._have_image = True
            if not (new_image or (cursor_changed and self._have_image)):
                return None
            w, h = self._crop.size
            dest = out if out is not None else np.empty((h, w, 4), dtype=np.uint8)
            # Map waits for the GPU copy; done before ReleaseFrame so DWM cannot
            # overwrite the desktop surface while it is being copied.
            self._reader.read_into(dest)
            blit_clear(dest, min(w, dest.shape[1]), min(h, dest.shape[0]))
            if self.include_cursor and self._cursor_visible and self._cursor_shape is not None:
                composite(
                    dest,
                    self._cursor_shape,
                    self._cursor_pos[0] - self._crop.x,
                    self._cursor_pos[1] - self._crop.y,
                )
            ts = (info.LastPresentTime * 1_000_000_000 // _qpc_freq.value) if new_image else 0
            return AcquiredFrame(dest, ts or now_ns(), self._size)
        finally:
            resource.release()
            dup.call(_DUP_ReleaseFrame, c_long, ())

    def _read_pointer_shape(self, size: int) -> None:
        assert self._dup is not None
        buf = (ctypes.c_ubyte * size)()
        required = c_uint()
        sinfo = DXGI_OUTDUPL_POINTER_SHAPE_INFO()
        hr = self._dup.call(
            _DUP_GetFramePointerShape, c_long, (c_uint, c_void_p, c_void_p, c_void_p),
            size, buf, byref(required), byref(sinfo),
        )  # fmt: skip
        if hr < 0:
            log.debug("GetFramePointerShape failed 0x%08X", hr_u32(hr))
            return
        self._cursor_shape = CursorShape(
            int(sinfo.Type), int(sinfo.Width), int(sinfo.Height), int(sinfo.Pitch), bytes(buf)
        )
