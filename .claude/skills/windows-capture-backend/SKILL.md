---
name: windows-capture-backend
description: How to develop, debug and test capturekit's Windows video backends — DXGI Desktop Duplication (monitor capture) and Windows Graphics Capture (exclusive HWND window capture) via ctypes COM/WinRT, D3D11 staging copies, DPI handling, and window enumeration. Use when editing src/capturekit/_backends/windows/ video code.
---

# Windows capture backend

Files: `src/capturekit/_backends/windows/`
- `_com.py` GUID, HRESULT checking, `ComPtr` vtable calls, COM object
  implementation helper (`ComObject`) for delegates/callbacks.
- `_winrt.py` HSTRING, `RoGetActivationFactory`, pinterface IIDs, delegates.
- `_d3d11.py` device creation, staging texture ring, map/copy to numpy.
- `_win32.py` monitors (EnumDisplayMonitors), windows (EnumWindows), DPI,
  process names, DWM attributes.
- `dxgi_duplication.py` monitor capture. `wgc.py` window (and monitor) capture.

Vtable indices and GUIDs: `reference/vtables.md` (verified list — extend it).
Quirks: `reference/quirks.md`.

## Rules
- All coordinates are **physical pixels**. Wrap enumeration in
  `_win32.per_monitor_dpi_aware()` (thread DPI context). Never call
  `SetProcessDpiAwareness*` — that is the host application's decision.
- Window capture = WGC `IGraphicsCaptureItemInterop::CreateForWindow`. No
  desktop cropping, no BitBlt, no PrintWindow fallback.
- COM objects are thread-affine in our design: create, use and release them
  on the backend's capture thread (MTA, `RoInitialize(1)`).
- Enable `ID3D10Multithread::SetMultithreadProtected(TRUE)` on the device —
  WGC's free-threaded frame pool uses it from a thread-pool thread.
- DXGI duplication device must be created on the adapter that owns the output
  (`D3D11CreateDevice(adapter, D3D_DRIVER_TYPE_UNKNOWN, ...)`); hybrid
  laptops may have the panel on the iGPU.
- Copy only the crop box (`CopySubresourceRegion` with `D3D11_BOX`) into a
  staging texture sized to the box; map; copy rows honouring `RowPitch`.
- Frame pool content size changes on window resize → `Recreate` the pool; if
  no full-size frame follows within 150 ms, restart the session (quirk 4).
- Any thread that initialises COM/WinRT goes through `ComApartment` /
  `RoApartment`, which hold a process-wide `CoIncrementMTAUsage` cookie —
  never bypass them (quirk 5: MTA teardown crash).
- COM callbacks implemented in Python (`ComObject`) must never free
  themselves inside `Release` (graveyard; quirk 6).
- Read `reference/quirks.md` before changing acquire/resize/teardown logic.

## Error handling
| HRESULT | Meaning | Action |
| --- | --- | --- |
| `0x887A0027` DXGI_ERROR_WAIT_TIMEOUT | no new desktop frame | not an error, reuse last frame |
| `0x887A0026` DXGI_ERROR_ACCESS_LOST | mode change, UAC/secure desktop, fullscreen switch | release duplication, recreate (retry with backoff) |
| `0x887A0022` DXGI_ERROR_NOT_CURRENTLY_AVAILABLE | too many duplications / session | raise CaptureUnavailableError |
| `0x887A0004` DXGI_ERROR_UNSUPPORTED | output on another adapter or unsupported | retry on owning adapter |
| `0x887A0005` DXGI_ERROR_DEVICE_REMOVED | GPU reset/driver update | recreate device |
| `0x80070005` E_ACCESSDENIED | secure desktop / capture not allowed | report |

WGC window closure: the `GraphicsCaptureItem.Closed` event fires and
`IsWindow(hwnd)` becomes false → raise `WindowClosedError`.

## Testing
- `tests/fixtures/color_window.py`: deterministic solid-colour/pattern window
  (Win32 via ctypes) — use it for occlusion, resize, minimize, close tests.
- Occlusion test: place an occluder window over the target, capture the target
  and assert the target colour is everywhere in the client area.
- Run: `.venv/Scripts/python -m pytest -m windows -v`.

## Debugging procedure
1. Reproduce with `scripts/diag_windows.py` (enumerates outputs/windows,
   grabs one frame per backend, prints HRESULTs).
2. Crash in ctypes (access violation) → wrong vtable index or argtypes; check
   `reference/vtables.md`, run with `python -X faulthandler`.
3. Black frames from WGC → check display affinity (`GetWindowDisplayAffinity`),
   DRM, minimized state, and whether the window renders via a different HWND
   (e.g. UWP `ApplicationFrameWindow` host vs `CoreWindow`).
