"""Source specifications.

Sources are immutable descriptions of *what* to capture. They hold no native
resources; capture sessions and recorders open them.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from .types import AudioDevice, Monitor, Rect, Window


class MonitorBackend(enum.Enum):
    AUTO = "auto"
    #: Windows: DXGI Desktop Duplication (default).
    DXGI = "dxgi"
    #: Windows: Windows Graphics Capture of the whole monitor.
    WGC = "wgc"
    #: Linux: X11 GetImage/XShm.
    X11 = "x11"


@dataclass(frozen=True, slots=True)
class MonitorSource:
    """Capture a monitor, optionally cropped.

    ``monitor`` may be a :class:`Monitor`, a monitor index, or ``None`` for the
    primary monitor. ``region`` is relative to the monitor's top-left corner.
    """

    monitor: Monitor | int | None = None
    region: Rect | None = None
    include_cursor: bool = True
    backend: MonitorBackend = MonitorBackend.AUTO
    display: str | None = None


@dataclass(frozen=True, slots=True)
class WindowSource:
    """Capture one window's own content (never a crop of the desktop).

    ``window`` may be a :class:`Window` or a native handle. ``region`` is
    relative to the captured content: the whole window (including title bar)
    or, with ``client_area_only=True``, the client area.

    ``border``: Windows draws a yellow capture border by default. ``False``
    asks the OS to omit it where the OS permits (Windows 11); ``None`` keeps
    the OS default.
    """

    window: Window | int
    region: Rect | None = None
    client_area_only: bool = False
    include_cursor: bool = False
    border: bool | None = None
    display: str | None = None

    @property
    def handle(self) -> int:
        return self.window.handle if isinstance(self.window, Window) else int(self.window)


VideoSource = MonitorSource | WindowSource


@dataclass(frozen=True, slots=True)
class MicrophoneSource:
    """Audio input device (``None`` = system default input)."""

    device: AudioDevice | str | None = None
    name: str | None = None


@dataclass(frozen=True, slots=True)
class SystemAudioSource:
    """Everything played on an output device (``None`` = default output)."""

    device: AudioDevice | str | None = None
    name: str | None = None


@dataclass(frozen=True, slots=True)
class ProcessAudioSource:
    """Audio rendered by one process (and, by default, its child processes).

    Never falls back to whole-system audio: if per-process capture is not
    available, opening the source raises :class:`CapabilityUnavailableError`.
    On Linux ``stream_ids`` may pin specific PulseAudio sink-inputs.
    """

    pid: int
    include_children: bool = True
    name: str | None = None
    stream_ids: tuple[str, ...] = ()

    @classmethod
    def for_window(cls, window: Window, *, include_children: bool = True) -> ProcessAudioSource:
        """Audio of the process that owns ``window``.

        Note: the window's process is not always the process that plays the
        audio (launchers, browser helper processes, audio-server proxies).
        ``include_children`` covers the common child-process case; use
        :func:`capturekit.list_audio_sessions` to pick a PID explicitly.
        """
        if window.pid is None:
            from .errors import TargetNotFoundError

            raise TargetNotFoundError(f"process of window {window.handle:#x} is unknown")
        return cls(window.pid, include_children=include_children, name=window.process_name)


AudioSourceSpec = MicrophoneSource | SystemAudioSource | ProcessAudioSource


def audio_source_label(src: AudioSourceSpec) -> str:
    if src.name:
        return src.name
    if isinstance(src, MicrophoneSource):
        return "microphone"
    if isinstance(src, SystemAudioSource):
        return "system"
    return f"process {src.pid}"
