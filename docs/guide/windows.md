# Windows

## Requirements

| Feature | Minimum |
| --- | --- |
| Monitor capture (DXGI Desktop Duplication) | Windows 8+ (Windows 10/11 supported) |
| Window capture (Windows Graphics Capture, `CreateForWindow`) | Windows 10 1903 (build 18362) |
| WGC cursor toggle | Windows 10 2004 (19041) |
| Capture border removal (`border=False`) | Windows 11 / build 20348+ (OS may refuse) |
| Per-process audio (WASAPI process loopback) | Windows 10 build 20348 / Windows 11 |
| GPU | Any D3D11 GPU with a WDDM driver |

No administrator rights are required. capturekit does **not** change your
process DPI awareness; all coordinates it returns are physical pixels.

## Monitor capture

`MonitorSource(monitor, region=None, include_cursor=True, backend=AUTO)`

* `monitor`: `Monitor`, index into `list_monitors()` (primary first), or
  `None` for the primary monitor.
* `region` is relative to the monitor's top-left, in physical pixels; it is
  validated (`InvalidRegionError`).
* DXGI only copies the region on the GPU before readback.
* Cursor: DXGI excludes the hardware cursor; capturekit composites the pointer
  shape in software (experimental). `MonitorBackend.WGC` uses the OS cursor
  rendering instead.
* Rotated monitors: use `MonitorBackend.WGC` (DXGI raises
  `CapabilityUnavailableError`).
* DXGI duplication is interrupted by UAC prompts, the lock screen, mode
  changes and exclusive full-screen apps; capturekit recreates it
  automatically and repeats the last frame meanwhile.

## Window capture

`WindowSource(window, region=None, client_area_only=False, include_cursor=False, border=None)`

Uses Windows Graphics Capture on the HWND: frames are the window's own
composition surface. **Overlapping windows never appear** (verified by pixel
tests with partially and fully covering windows).

* Content size = the window's visible bounds (DWM extended frame bounds,
  i.e. without the invisible resize border/shadow). `client_area_only=True`
  crops to the client area; `region` is relative to whichever base you chose.
* Resizes are followed. Without a fixed region, `ScreenCapture.grab()` returns
  frames of the new size; recordings keep their output size (content anchored
  top-left, padded/cropped).
* Closing the window raises `WindowClosedError` (recordings stop and finalise
  the file, or continue with the last frame under
  `TargetLostPolicy.CONTINUE`).

### Minimized windows (measured on build 26200)

DWM does not render minimized windows, and WGC delivers **no new frames**
while a window is minimized. `grab()` returns the last frame;
`grab(new_only=True)` times out; a recording repeats the last frame. After
restore, capture resumes. capturekit never substitutes desktop pixels.

### Windows that exclude themselves from capture

Windows using `SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)` (password
managers, DRM players) are reported with `Window.capture_excluded=True`; their
captured content is blank. This is intentional and not bypassed.

### UWP / packaged apps

Many UWP apps render into a child `CoreWindow` hosted by an
`ApplicationFrameWindow`. Capture the top-level `ApplicationFrameWindow`
returned by `list_windows()`.
