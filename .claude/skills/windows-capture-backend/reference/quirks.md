# Windows capture quirks (verified by experiment)

Each entry: observation → handling → evidence. Add new ones at the end.

1. **DXGI frames without a present carry no image.** `AcquireNextFrame` can
   return `LastPresentTime == 0` (pointer/metadata only); the texture is then
   all zeros (alpha 0). → Only frames with a present time are images; acquire
   loops until timeout. DWM presents a full frame 6–26 ms after
   `DuplicateOutput` even on a static desktop (measured 5×).
2. **DXGI output descriptors are DPI-virtualised** for non-DPI-aware processes
   (`DesktopCoordinates` 1536×864 on a 1920×1080 @125 % panel) while the
   duplicated surface is physical. → Match outputs by HMONITOR.
3. **WGC content size = DWM extended frame bounds** (not GetWindowRect).
   Client area offset = `ClientToScreen(0,0) − bounds.origin` (1 px left
   border observed on a standard overlapped window).
4. **WGC after resize:** exactly one frame arrives in the *old* pool texture
   with the new `ContentSize`; a static window then never presents again.
   → `Recreate` the pool; if no frame within 150 ms, restart the session
   (new sessions always deliver an initial frame). Full-size frame arrived
   ~330 ms after the resize in the trace.
5. **MTA teardown crash.** Capturing a window that is then destroyed, closing
   the session and letting the capture thread `RoUninitialize` crashed the
   process (access violation on a thread-pool thread) ~0.5–2 s later, every
   time. Holding `CoIncrementMTAUsage` fixed it (3/3). Root cause: the last
   MTA thread's uninitialise tears down the MTA / unloads WinRT DLLs while a
   window-closed callback is pending.
6. **COM objects implemented in Python must not free themselves inside
   Release.** The ctypes thunk is still executing; keep dead objects in a
   bounded graveyard.
7. **Minimized windows:** WGC delivers no frames while minimized (1 s
   observed with `grab(new_only=True)` timing out); frames resume on restore.
8. **`IsBorderRequired = false`** is accepted (S_OK) for an unpackaged Win32
   process on build 26200 without `GraphicsCaptureAccess.RequestAccessAsync`.
9. **WDA_EXCLUDEFROMCAPTURE windows** produce non-content (no green pixels of
   the fixture) through WGC; `GetWindowDisplayAffinity` reports 0x11.
10. **Python venv launcher:** `.venv\Scripts\python.exe` spawns the base
    interpreter as a child; audio sessions report the child PID. Process
    loopback on the launcher PID works thanks to tree inclusion.
11. **NVENC with BGRA input** produces yuv420p H.264 Main tagged BT.601
    (`bt470bg`); conversion happens on the GPU.
