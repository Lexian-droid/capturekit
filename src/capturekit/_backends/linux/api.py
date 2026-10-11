"""Linux backend facade: X11/Xvfb video, PulseAudio/PipeWire audio."""

from __future__ import annotations

import contextlib
import platform
from collections.abc import Iterator
from types import ModuleType

from ...capabilities import (
    Capabilities,
    Capability,
    experimental,
    supported,
    unavailable,
    unsupported,
)
from ...errors import CapabilityUnavailableError, TargetNotFoundError
from ...sources import (
    AudioSourceSpec,
    MonitorBackend,
    MonitorSource,
    WindowSource,
)
from ...types import AudioDevice, AudioSession, Monitor, Window
from ..base import AudioBackend, VideoBackend

PLATFORM = "linux"


@contextlib.contextmanager
def thread_context() -> Iterator[None]:
    # Each backend opens its own X connection on its own thread.
    yield


def _x11() -> ModuleType:
    from . import x11

    return x11


def list_monitors(display: str | None = None) -> list[Monitor]:
    result: list[Monitor] = _x11().list_monitors(display)
    return result


def list_windows(display: str | None = None, include_untitled: bool = False) -> list[Window]:
    result: list[Window] = _x11().list_windows(display, include_untitled)
    return result


def get_window(handle: int, display: str | None = None) -> Window:
    result: Window = _x11().get_window(handle, display)
    return result


def resolve_monitor(spec: Monitor | int | None, display: str | None) -> Monitor:
    if isinstance(spec, Monitor):
        display = spec.display if display is None else display
    monitors = list_monitors(display)
    if spec is None:
        return next((m for m in monitors if m.is_primary), monitors[0])
    if isinstance(spec, int):
        if not 0 <= spec < len(monitors):
            raise TargetNotFoundError(f"monitor index {spec} out of range (0..{len(monitors) - 1})")
        return monitors[spec]
    for m in monitors:
        if m.id == spec.id:
            return m
    raise TargetNotFoundError(f"monitor {spec.id} not found on {display}")


def open_video(source: MonitorSource | WindowSource) -> VideoBackend:
    x11 = _x11()
    if isinstance(source, WindowSource):
        display = source.display
        if display is None and isinstance(source.window, Window):
            display = source.window.display
        name, _explicit = x11.resolve_display(display)
        backend: VideoBackend = x11.CompositeWindowBackend(
            source.handle, source.region, source.client_area_only, name
        )
        return backend
    if source.backend not in (MonitorBackend.AUTO, MonitorBackend.X11):
        raise CapabilityUnavailableError(f"monitor backend {source.backend.value} is Windows-only")
    monitor = resolve_monitor(source.monitor, source.display)
    mon_backend: VideoBackend = x11.X11MonitorBackend(monitor, source.region, monitor.display)
    return mon_backend


def _pulse() -> ModuleType:
    from . import pulse

    return pulse


def list_audio_devices() -> list[AudioDevice]:
    result: list[AudioDevice] = _pulse().list_devices()
    return result


def list_audio_sessions() -> list[AudioSession]:
    result: list[AudioSession] = _pulse().list_sessions()
    return result


def open_audio(spec: AudioSourceSpec, sample_rate: int, channels: int = 2) -> AudioBackend:
    backend: AudioBackend = _pulse().open_audio(spec, sample_rate, channels)
    return backend


def capabilities(display: str | None = None) -> Capabilities:
    from ...errors import CaptureError

    notes: list[str] = []
    server = None
    try:
        x11 = _x11()
        with x11.open_display(display) as d:
            server = x11.server_kind(d)
            shm = d.has_extension("MIT-SHM")
            composite = d.has_extension("Composite")
            xwayland = server == "xwayland"
    except CaptureError as exc:
        reason = str(exc)
        monitor: Capability = unavailable(reason)
        window: Capability = unavailable(reason)
    else:
        monitor = (
            unsupported("XWayland root does not contain the Wayland desktop")
            if xwayland
            else supported("MIT-SHM" if shm else "XGetImage (no MIT-SHM)")
        )
        window = (
            supported("XComposite automatic redirection")
            if composite
            else unavailable("X server lacks the Composite extension")
        )
        if xwayland and composite:
            window = experimental("X11 clients on XWayland via XComposite")
    audio = _pulse().audio_capabilities()
    return Capabilities(
        platform=PLATFORM,
        os_version=platform.release(),
        display_server=server,
        monitor_capture=monitor,
        window_capture=window,
        window_capture_borderless=supported("no capture border on X11"),
        window_capture_minimized=unsupported(
            "unmapped/iconified windows have no Composite pixmap; the last frame is held"
        ),
        cursor_capture=unsupported("cursor capture on X11 is not implemented yet"),
        microphone_audio=audio["microphone"],
        system_audio=audio["system"],
        process_audio=audio["process"],
        audio_session_enumeration=audio["sessions"],
        recording=supported("FFmpeg subprocess (requires an FFmpeg executable)"),
        notes=tuple(notes),
    )
