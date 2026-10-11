"""Windows backend facade used by the public API."""

from __future__ import annotations

import contextlib
import platform
from collections.abc import Iterator

from ...capabilities import (
    Capabilities,
    Capability,
    experimental,
    supported,
    unavailable,
    unsupported,
)
from ...errors import (
    CapabilityUnavailableError,
    TargetNotFoundError,
    UnsupportedDisplayServerError,
)
from ...sources import (
    AudioSourceSpec,
    MicrophoneSource,
    MonitorBackend,
    MonitorSource,
    ProcessAudioSource,
    SystemAudioSource,
    WindowSource,
    audio_source_label,
)
from ...types import AudioDevice, AudioSession, Monitor, Window
from ..base import AudioBackend, VideoBackend
from . import _win32, wasapi
from ._winrt import RoApartment

PLATFORM = "windows"
WGC_WINDOW_MIN_BUILD = 18362  # CreateForWindow interop (1903)
WGC_BORDER_MIN_BUILD = 20348


def _no_display(display: str | None) -> None:
    if display is not None:
        raise UnsupportedDisplayServerError("display strings are only meaningful on Linux/X11")


@contextlib.contextmanager
def thread_context() -> Iterator[None]:
    """Per-thread runtime initialisation for backend worker threads."""
    with RoApartment():
        yield


def list_monitors(display: str | None = None) -> list[Monitor]:
    _no_display(display)
    return _win32.list_monitors()


def list_windows(display: str | None = None, include_untitled: bool = False) -> list[Window]:
    _no_display(display)
    return _win32.list_windows(include_untitled)


def get_window(handle: int, display: str | None = None) -> Window:
    _no_display(display)
    if not _win32.is_window(handle):
        raise TargetNotFoundError(f"window {handle:#x} does not exist")
    return _win32.describe_window(handle)


def resolve_monitor(spec: Monitor | int | None, display: str | None = None) -> Monitor:
    monitors = list_monitors(display)
    if not monitors:
        raise TargetNotFoundError("no monitors found")
    if spec is None:
        return next((m for m in monitors if m.is_primary), monitors[0])
    if isinstance(spec, int):
        if not 0 <= spec < len(monitors):
            raise TargetNotFoundError(f"monitor index {spec} out of range (0..{len(monitors) - 1})")
        return monitors[spec]
    for m in monitors:
        if m.id == spec.id or m.native_handle == spec.native_handle:
            return m
    raise TargetNotFoundError(f"monitor {spec.id} is no longer connected")


def open_video(source: MonitorSource | WindowSource) -> VideoBackend:
    """Create (not start) a video backend. Must be started on a thread inside
    :func:`thread_context`."""
    from .dxgi_duplication import DxgiDuplicationBackend
    from .wgc import WgcBackend

    if isinstance(source, WindowSource):
        _no_display(source.display)
        if _build() < WGC_WINDOW_MIN_BUILD:
            raise CapabilityUnavailableError("window capture needs Windows 10 1903 or newer")
        return WgcBackend(
            hwnd=source.handle,
            region=source.region,
            client_area_only=source.client_area_only,
            include_cursor=source.include_cursor,
            border=source.border,
        )
    _no_display(source.display)
    monitor = resolve_monitor(source.monitor)
    if source.backend is MonitorBackend.WGC:
        return WgcBackend(
            monitor=monitor, region=source.region, include_cursor=source.include_cursor
        )
    if source.backend not in (MonitorBackend.AUTO, MonitorBackend.DXGI):
        raise CapabilityUnavailableError(
            f"monitor backend {source.backend.value} is not available on Windows"
        )
    return DxgiDuplicationBackend(monitor, source.region, source.include_cursor)


def list_audio_devices() -> list[AudioDevice]:
    return wasapi.list_devices()


def list_audio_sessions() -> list[AudioSession]:
    return wasapi.list_sessions()


def _device_id(dev: AudioDevice | str | None) -> str | None:
    if isinstance(dev, AudioDevice):
        return dev.id
    return dev


def open_audio(spec: AudioSourceSpec, sample_rate: int, channels: int = 2) -> AudioBackend:
    name = audio_source_label(spec)
    if isinstance(spec, MicrophoneSource):
        return wasapi.WasapiCapture(
            "microphone",
            device_id=_device_id(spec.device),
            sample_rate=sample_rate,
            channels=channels,
            name=name,
        )
    if isinstance(spec, SystemAudioSource):
        return wasapi.WasapiCapture(
            "system",
            device_id=_device_id(spec.device),
            sample_rate=sample_rate,
            channels=channels,
            name=name,
        )
    if isinstance(spec, ProcessAudioSource):
        if spec.stream_ids:
            raise CapabilityUnavailableError(
                "stream_ids selection is Linux-only; on Windows select the process by PID"
            )
        return wasapi.WasapiCapture(
            "process",
            pid=spec.pid,
            include_tree=spec.include_children,
            sample_rate=sample_rate,
            channels=channels,
            name=name,
        )
    raise TypeError(f"unknown audio source {spec!r}")


def _build() -> int:
    return wasapi.windows_build()


def capabilities(display: str | None = None) -> Capabilities:
    _no_display(display)
    build = _build()
    from .wgc import is_supported

    with RoApartment():
        wgc_ok = is_supported()
    if not wgc_ok:
        window: Capability = unavailable("Windows Graphics Capture not supported on this system")
    elif build < WGC_WINDOW_MIN_BUILD:
        window = unavailable("window capture requires Windows 10 1903 (build 18362)+")
    else:
        window = supported("Windows Graphics Capture (CreateForWindow)")
    border = (
        experimental("IsBorderRequired=false accepted on this build; OS may still enforce it")
        if build >= WGC_BORDER_MIN_BUILD and wgc_ok
        else unavailable(f"requires build {WGC_BORDER_MIN_BUILD}+")
    )
    proc = (
        supported("WASAPI process loopback (includes child processes)")
        if wasapi.process_loopback_supported()
        else unavailable(f"requires build {wasapi.PROCESS_LOOPBACK_MIN_BUILD}+ (this: {build})")
    )
    return Capabilities(
        platform=PLATFORM,
        os_version=f"{platform.release()} build {build}",
        display_server="dwm",
        monitor_capture=supported("DXGI Desktop Duplication (WGC monitor capture optional)"),
        window_capture=window,
        window_capture_borderless=border,
        window_capture_minimized=unsupported(
            "minimized windows are not rendered by DWM; WGC delivers no new frames "
            "(the recording repeats the last frame)"
        ),
        cursor_capture=supported("DXGI: software-composited; WGC: IsCursorCaptureEnabled"),
        microphone_audio=supported("WASAPI capture endpoint"),
        system_audio=supported("WASAPI endpoint loopback"),
        process_audio=proc,
        audio_session_enumeration=supported("IAudioSessionManager2"),
        recording=supported("FFmpeg subprocess (requires an FFmpeg executable)"),
    )


__all__ = [
    "audio_source_label",
]
