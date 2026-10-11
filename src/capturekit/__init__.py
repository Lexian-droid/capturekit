"""capturekit — monitor, exclusive-window and audio capture for Windows and Linux X11.

Quick start::

    import capturekit as ck

    print(ck.get_capabilities().summary())
    win = next(w for w in ck.list_windows() if "Notepad" in w.title)
    with ck.ScreenCapture(ck.WindowSource(win)) as cap:
        frame = cap.grab()                      # BGRA numpy array in frame.data

    rec = ck.Recorder(ck.WindowSource(win), [ck.ProcessAudioSource.for_window(win)],
                      "notepad.mkv", ck.RecordingConfig(fps=30))
    rec.start(); ...; rec.stop()
"""

from __future__ import annotations

from . import _platform
from ._pipeline.clock import now_ns
from ._version import __version__
from .capabilities import Capabilities, Capability, Support
from .capture import ScreenCapture, screenshot
from .config import AudioMixMode, Container, EncoderConfig, RecordingConfig, TargetLostPolicy
from .errors import (
    AccessDeniedError,
    AudioDeviceDisconnectedError,
    AudioDeviceError,
    CapabilityUnavailableError,
    CaptureError,
    CaptureTimeoutError,
    EncoderError,
    EncoderNotAvailableError,
    InvalidRegionError,
    NativeError,
    RecorderStateError,
    TargetLostError,
    TargetNotFoundError,
    UnsupportedDisplayServerError,
    UnsupportedPlatformError,
    WindowClosedError,
)
from .recording import (
    AudioTrackStats,
    Recorder,
    RecorderState,
    RecordingResult,
    RecordingStats,
    VideoStats,
)
from .sources import (
    MicrophoneSource,
    MonitorBackend,
    MonitorSource,
    ProcessAudioSource,
    SystemAudioSource,
    WindowSource,
)
from .types import (
    AudioChunk,
    AudioDevice,
    AudioDeviceKind,
    AudioSession,
    Frame,
    Monitor,
    PixelFormat,
    Rect,
    Window,
)


def list_monitors(display: str | None = None) -> list[Monitor]:
    """Enumerate monitors (``display`` selects an X server on Linux, e.g. ``":99"``)."""
    result: list[Monitor] = _platform.backend().list_monitors(display)
    return result


def list_windows(display: str | None = None, *, include_untitled: bool = False) -> list[Window]:
    """Enumerate capturable top-level windows."""
    result: list[Window] = _platform.backend().list_windows(display, include_untitled)
    return result


def get_window(handle: int, display: str | None = None) -> Window:
    """Describe the window with native ``handle`` (raises TargetNotFoundError)."""
    result: Window = _platform.backend().get_window(handle, display)
    return result


def list_audio_devices() -> list[AudioDevice]:
    """Enumerate audio input and output devices."""
    result: list[AudioDevice] = _platform.backend().list_audio_devices()
    return result


def list_audio_sessions() -> list[AudioSession]:
    """Enumerate application audio sessions/streams with owning process IDs."""
    result: list[AudioSession] = _platform.backend().list_audio_sessions()
    return result


def get_capabilities(display: str | None = None) -> Capabilities:
    """Describe what this machine/display server supports."""
    result: Capabilities = _platform.backend().capabilities(display)
    return result


__all__ = [
    "AccessDeniedError",
    "AudioChunk",
    "AudioDevice",
    "AudioDeviceDisconnectedError",
    "AudioDeviceError",
    "AudioDeviceKind",
    "AudioMixMode",
    "AudioSession",
    "AudioTrackStats",
    "Capabilities",
    "Capability",
    "CapabilityUnavailableError",
    "CaptureError",
    "CaptureTimeoutError",
    "Container",
    "EncoderConfig",
    "EncoderError",
    "EncoderNotAvailableError",
    "Frame",
    "InvalidRegionError",
    "MicrophoneSource",
    "Monitor",
    "MonitorBackend",
    "MonitorSource",
    "NativeError",
    "PixelFormat",
    "ProcessAudioSource",
    "Recorder",
    "RecorderState",
    "RecorderStateError",
    "RecordingConfig",
    "RecordingResult",
    "RecordingStats",
    "Rect",
    "ScreenCapture",
    "Support",
    "SystemAudioSource",
    "TargetLostError",
    "TargetLostPolicy",
    "TargetNotFoundError",
    "UnsupportedDisplayServerError",
    "UnsupportedPlatformError",
    "VideoStats",
    "Window",
    "WindowClosedError",
    "WindowSource",
    "__version__",
    "get_capabilities",
    "get_window",
    "list_audio_devices",
    "list_audio_sessions",
    "list_monitors",
    "list_windows",
    "now_ns",
    "screenshot",
]
