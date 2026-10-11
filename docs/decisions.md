# Decision log

ADR-style; newest last. Each entry: context → decision → consequences.

## D1 (2026-10-11) Native access via ctypes, no compiled extension
Context: DXGI, WGC (WinRT), WASAPI and Xlib are all C/COM ABIs.
Decision: bind them with ctypes (`_com.ComPtr` vtable calls, `ComObject` for
callbacks). No Rust/C++.
Consequences: zero build toolchain, wheels are pure Python. Per-call overhead
(~µs) is irrelevant at ≤ 240 calls/s. Wrong vtable indices crash the
interpreter → every index is verified against SDK headers and tested.

## D2 Runtime dependency: numpy only
Frames are numpy arrays (zero-copy friendly, universal in Python media code).
`imageio-ffmpeg` is an optional extra providing an FFmpeg binary.

## D3 FFmpeg as a subprocess (not PyAV/in-process)
Isolation (encoder crash cannot kill the host), no binary ABI coupling,
hardware encoders available in stock builds. Cost: raw BGRA through a pipe
(1080p60 ≈ 500 MB/s) — measured ~1.3 ms per 1080p frame write on the test
machine. Encoders are validated by real initialisation because `-encoders`
lists unusable ones (QSV/AMF on an NVIDIA-only laptop).

## D4 Pull-model video + CFR pacing at absolute deadlines
WGC/DXGI only deliver frames on change. The pacer samples the newest frame at
`t0 + n/fps`, duplicates when unchanged and accounts late/dropped slots.
CFR output makes FFmpeg timestamps = slot counts, so A/V sync is fully
determined by our clock, not by pipe timing.

## D5 Audio: OS-side format conversion, Python-side alignment
All sources run at float32/48 kHz/stereo (WASAPI AUTOCONVERTPCM). An aligner
maps QPC timestamps to media time: gap fill, overlap trim, exact re-anchor on
start/resume, ≤ 0.2 % resampling for drift. Audio reaches FFmpeg through
per-track named pipes (owner-only DACL, random names).

## D6 Raw-input probing disabled
FFmpeg's default `analyzeduration` waits ~5 s of real-time audio per raw pipe
before consuming video (measured: 229 dropped frames at start). All raw
inputs use `-probesize 32 -analyzeduration 0`.

## D7 No colour-space override
NVENC's internal RGB→YUV and swscale both use BT.601 and tag it; forcing
BT.709 tags would mislabel the data. Revisit with an explicit GPU colour
conversion (D3D11 VideoProcessor) later.

## D8 Process-wide MTA usage cookie
`CoIncrementMTAUsage` is called once and never released. Without it, the last
MTA thread's uninitialisation could unload WinRT capture DLLs while thread-pool
callbacks were pending (reproduced crash after capturing a window that was then
destroyed).

## D9 Window resize: recreate pool, refresh session if static
After a resize WGC delivers one frame in the old-size texture; static windows
never present again. The backend recreates the pool and, if no full-size
frame arrives within 150 ms, restarts the capture session (a new session
always delivers an initial frame).

## D10 Thread-scoped DPI awareness
The library never changes process DPI awareness (host application's choice);
it switches the *thread* DPI context to per-monitor-v2 around coordinate APIs.
DXGI output descriptors are still DPI-virtualised for unaware processes, so
outputs are matched by HMONITOR, never by coordinates.
