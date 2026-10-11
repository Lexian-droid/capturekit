"""Direct3D 11 / DXGI bindings used by the capture backends."""

from __future__ import annotations

import ctypes
from ctypes import POINTER, Structure, byref, c_int, c_long, c_uint, c_void_p
from ctypes import wintypes as wt

import numpy as np
import numpy.typing as npt

from ...errors import NativeError
from ._com import GUID, ComPtr, check_hr

_d3d11 = ctypes.WinDLL("d3d11")
_dxgi = ctypes.WinDLL("dxgi")

_d3d11.D3D11CreateDevice.argtypes = (
    c_void_p, c_int, c_void_p, c_uint, c_void_p, c_uint, c_uint,
    POINTER(c_void_p), POINTER(c_int), POINTER(c_void_p),
)  # fmt: skip
_d3d11.D3D11CreateDevice.restype = c_long
_d3d11.CreateDirect3D11DeviceFromDXGIDevice.argtypes = (c_void_p, POINTER(c_void_p))
_d3d11.CreateDirect3D11DeviceFromDXGIDevice.restype = c_long
_dxgi.CreateDXGIFactory1.argtypes = (c_void_p, POINTER(c_void_p))
_dxgi.CreateDXGIFactory1.restype = c_long

IID_IDXGIFactory1 = GUID.from_str("770aae78-f26f-4dba-a829-253c83d1b387")
IID_IDXGIDevice = GUID.from_str("54ec77fa-1377-44e6-8c32-88fd5f44c84c")
IID_IDXGIOutput1 = GUID.from_str("00cddea8-939b-4b83-a340-a685226666cc")
IID_ID3D11Texture2D = GUID.from_str("6f15aaf2-d208-4e89-9ab4-489535d34f9c")
IID_ID3D10Multithread = GUID.from_str("9B7E4E00-342C-4106-A19F-4F2704F689F0")

D3D_DRIVER_TYPE_UNKNOWN = 0
D3D_DRIVER_TYPE_HARDWARE = 1
D3D11_CREATE_DEVICE_BGRA_SUPPORT = 0x20
D3D11_SDK_VERSION = 7
DXGI_FORMAT_B8G8R8A8_UNORM = 87
D3D11_USAGE_STAGING = 3
D3D11_CPU_ACCESS_READ = 0x20000
D3D11_MAP_READ = 1

# ID3D11Device
_DEV_CreateTexture2D = 5
_DEV_GetImmediateContext = 40
# ID3D11DeviceContext
_CTX_Map = 14
_CTX_Unmap = 15
_CTX_CopySubresourceRegion = 46
_CTX_Flush = 111
# ID3D11Texture2D
_TEX_GetDesc = 10
# ID3D10Multithread
_MT_SetMultithreadProtected = 5


class D3D11_TEXTURE2D_DESC(Structure):
    _fields_ = [
        ("Width", c_uint),
        ("Height", c_uint),
        ("MipLevels", c_uint),
        ("ArraySize", c_uint),
        ("Format", c_uint),
        ("SampleCount", c_uint),
        ("SampleQuality", c_uint),
        ("Usage", c_uint),
        ("BindFlags", c_uint),
        ("CPUAccessFlags", c_uint),
        ("MiscFlags", c_uint),
    ]


class D3D11_MAPPED_SUBRESOURCE(Structure):
    _fields_ = [("pData", c_void_p), ("RowPitch", c_uint), ("DepthPitch", c_uint)]


class D3D11_BOX(Structure):
    _fields_ = [
        ("left", c_uint),
        ("top", c_uint),
        ("front", c_uint),
        ("right", c_uint),
        ("bottom", c_uint),
        ("back", c_uint),
    ]


class LUID(Structure):
    _fields_ = [("LowPart", wt.DWORD), ("HighPart", wt.LONG)]


class DXGI_ADAPTER_DESC1(Structure):
    _fields_ = [
        ("Description", wt.WCHAR * 128),
        ("VendorId", c_uint),
        ("DeviceId", c_uint),
        ("SubSysId", c_uint),
        ("Revision", c_uint),
        ("DedicatedVideoMemory", ctypes.c_size_t),
        ("DedicatedSystemMemory", ctypes.c_size_t),
        ("SharedSystemMemory", ctypes.c_size_t),
        ("AdapterLuid", LUID),
        ("Flags", c_uint),
    ]


class DXGI_OUTPUT_DESC(Structure):
    _fields_ = [
        ("DeviceName", wt.WCHAR * 32),
        ("DesktopCoordinates", wt.RECT),
        ("AttachedToDesktop", wt.BOOL),
        ("Rotation", c_uint),
        ("Monitor", wt.HMONITOR),
    ]


def create_device(adapter: ComPtr | None = None) -> tuple[ComPtr, ComPtr]:
    """Create a BGRA-capable D3D11 device and its immediate context.

    With ``adapter`` the device is created on that adapter (required for DXGI
    duplication of outputs owned by it).
    """
    dev, ctx = c_void_p(), c_void_p()
    level = c_int()
    hr = _d3d11.D3D11CreateDevice(
        adapter.as_param if adapter else None,
        D3D_DRIVER_TYPE_UNKNOWN if adapter else D3D_DRIVER_TYPE_HARDWARE,
        None,
        D3D11_CREATE_DEVICE_BGRA_SUPPORT,
        None,
        0,
        D3D11_SDK_VERSION,
        byref(dev),
        byref(level),
        byref(ctx),
    )
    check_hr(hr, "D3D11CreateDevice")
    device = ComPtr(dev.value, "ID3D11Device")
    context = ComPtr(ctx.value, "ID3D11DeviceContext")
    mt = device.try_query(IID_ID3D10Multithread, "ID3D10Multithread")
    if mt is not None:
        # WGC's free-threaded frame pool uses the device from its own thread.
        mt.call(_MT_SetMultithreadProtected, c_int, (c_int,), 1)
        mt.release()
    return device, context


def create_winrt_device(device: ComPtr) -> ComPtr:
    """Wrap a D3D11 device as WinRT ``IDirect3DDevice`` (IInspectable)."""
    with device.query(IID_IDXGIDevice, "IDXGIDevice") as dxgi_dev:
        out = c_void_p()
        check_hr(
            _d3d11.CreateDirect3D11DeviceFromDXGIDevice(dxgi_dev.as_param, byref(out)),
            "CreateDirect3D11DeviceFromDXGIDevice",
        )
    return ComPtr(out.value, "IDirect3DDevice")


def create_dxgi_factory() -> ComPtr:
    out = c_void_p()
    check_hr(_dxgi.CreateDXGIFactory1(byref(IID_IDXGIFactory1), byref(out)), "CreateDXGIFactory1")
    return ComPtr(out.value, "IDXGIFactory1")


DXGI_ERROR_NOT_FOUND = -2005270526  # 0x887A0002


def enum_outputs() -> list[tuple[ComPtr, ComPtr, DXGI_OUTPUT_DESC, DXGI_ADAPTER_DESC1]]:
    """All (adapter, output, output_desc, adapter_desc) attached to the desktop.

    Caller owns (and must release) the returned ComPtrs.
    """
    result = []
    with create_dxgi_factory() as factory:
        a = 0
        while True:
            out = c_void_p()
            hr = factory.call(12, c_long, (c_uint, c_void_p), a, byref(out))  # EnumAdapters1
            if hr == DXGI_ERROR_NOT_FOUND:
                break
            check_hr(hr, "EnumAdapters1")
            adapter = ComPtr(out.value, "IDXGIAdapter1")
            adesc = DXGI_ADAPTER_DESC1()
            adapter.hcall(10, (c_void_p,), byref(adesc), what="GetDesc1")
            o = 0
            while True:
                oout = c_void_p()
                hr = adapter.call(7, c_long, (c_uint, c_void_p), o, byref(oout))  # EnumOutputs
                if hr == DXGI_ERROR_NOT_FOUND:
                    break
                check_hr(hr, "EnumOutputs")
                output = ComPtr(oout.value, "IDXGIOutput")
                odesc = DXGI_OUTPUT_DESC()
                output.hcall(7, (c_void_p,), byref(odesc), what="IDXGIOutput::GetDesc")
                if odesc.AttachedToDesktop:
                    result.append((adapter.add_ref(), output, odesc, adesc))
                else:
                    output.release()
                o += 1
            adapter.release()
            a += 1
    return result


def texture_desc(texture: ComPtr) -> D3D11_TEXTURE2D_DESC:
    desc = D3D11_TEXTURE2D_DESC()
    texture.call(_TEX_GetDesc, None, (c_void_p,), byref(desc))
    return desc


class StagingReader:
    """GPU→CPU readback of a sub-rectangle through a CPU-readable texture.

    Only the requested box is copied on the GPU; the CPU side performs exactly
    one row-pitch-aware copy into the destination numpy array.
    """

    def __init__(self, device: ComPtr, context: ComPtr) -> None:
        self._device = device
        self._context = context
        self._staging: ComPtr | None = None
        self._size = (0, 0)
        self._format = DXGI_FORMAT_B8G8R8A8_UNORM

    def _ensure(self, width: int, height: int, fmt: int) -> ComPtr:
        if self._staging is not None and self._size == (width, height) and self._format == fmt:
            return self._staging
        if self._staging is not None:
            self._staging.release()
            self._staging = None
        desc = D3D11_TEXTURE2D_DESC(
            Width=width,
            Height=height,
            MipLevels=1,
            ArraySize=1,
            Format=fmt,
            SampleCount=1,
            SampleQuality=0,
            Usage=D3D11_USAGE_STAGING,
            BindFlags=0,
            CPUAccessFlags=D3D11_CPU_ACCESS_READ,
            MiscFlags=0,
        )
        self._staging = self._device.out_ptr(
            _DEV_CreateTexture2D,
            (c_void_p, c_void_p),
            byref(desc),
            None,
            what="CreateTexture2D(staging)",
            name="staging",
        )
        self._size = (width, height)
        self._format = fmt
        return self._staging

    def copy_box(
        self,
        src_texture: ComPtr,
        x: int,
        y: int,
        width: int,
        height: int,
        fmt: int = DXGI_FORMAT_B8G8R8A8_UNORM,
    ) -> None:
        """Queue a GPU copy of ``src[x:x+w, y:y+h]`` into the staging texture."""
        staging = self._ensure(width, height, fmt)
        box = D3D11_BOX(x, y, 0, x + width, y + height, 1)
        self._context.call(
            _CTX_CopySubresourceRegion,
            None,
            (c_void_p, c_uint, c_uint, c_uint, c_uint, c_void_p, c_uint, c_void_p),
            staging.as_param, 0, 0, 0, 0, src_texture.as_param, 0, byref(box),
        )  # fmt: skip

    def read_into(self, out: npt.NDArray[np.uint8], dst_x: int = 0, dst_y: int = 0) -> None:
        """Map the staging texture and copy it into ``out[dst_y:, dst_x:]``.

        The copied area is clipped to ``out``; Map blocks until the GPU copy
        has completed.
        """
        if self._staging is None:
            raise NativeError("read_into before copy_box")
        w, h = self._size
        mapped = D3D11_MAPPED_SUBRESOURCE()
        self._context.hcall(
            _CTX_Map, (c_void_p, c_uint, c_uint, c_uint, c_void_p),
            self._staging.as_param, 0, D3D11_MAP_READ, 0, byref(mapped), what="Map(staging)",
        )  # fmt: skip
        try:
            pitch = int(mapped.RowPitch)
            if not mapped.pData or pitch < w * 4:
                raise NativeError(f"invalid mapped staging texture (pitch={pitch}, w={w})")
            cw = max(0, min(w, out.shape[1] - dst_x))
            ch = max(0, min(h, out.shape[0] - dst_y))
            if cw and ch:
                raw = (ctypes.c_ubyte * (pitch * (ch - 1) + cw * 4)).from_address(mapped.pData)
                src = np.lib.stride_tricks.as_strided(
                    np.frombuffer(raw, dtype=np.uint8), shape=(ch, cw, 4), strides=(pitch, 4, 1)
                )
                out[dst_y : dst_y + ch, dst_x : dst_x + cw] = src
        finally:
            self._context.call(_CTX_Unmap, None, (c_void_p, c_uint), self._staging.as_param, 0)

    def release(self) -> None:
        if self._staging is not None:
            self._staging.release()
            self._staging = None
