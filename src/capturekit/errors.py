"""Exception hierarchy.

Every error raised by capturekit derives from :class:`CaptureError` so callers
can catch library failures with one ``except`` clause while still being able
to distinguish specific conditions.
"""

from __future__ import annotations


class CaptureError(Exception):
    """Base class for all capturekit errors."""


class UnsupportedPlatformError(CaptureError):
    """The operating system is not supported (e.g. macOS)."""


class UnsupportedDisplayServerError(CaptureError):
    """The targeted display server is unsupported (e.g. a Wayland-only session)."""


class CapabilityUnavailableError(CaptureError):
    """A capability exists in capturekit but is unavailable on this system.

    Examples: process audio loopback on Windows builds older than 20348, or
    XComposite missing on the targeted X server.
    """


class TargetNotFoundError(CaptureError):
    """A requested monitor, window, device or process does not exist."""


class TargetLostError(CaptureError):
    """A capture target disappeared during capture."""


class WindowClosedError(TargetLostError):
    """The captured window was closed or its handle became invalid."""


class InvalidRegionError(CaptureError, ValueError):
    """A crop region is empty or lies outside the capture source."""


class CaptureTimeoutError(CaptureError, TimeoutError):
    """No frame or audio data arrived within the requested time."""


class AccessDeniedError(CaptureError, PermissionError):
    """The OS refused capture (secure desktop, protected content, policy)."""


class AudioDeviceError(CaptureError):
    """Audio device failure."""


class AudioDeviceDisconnectedError(AudioDeviceError, TargetLostError):
    """The audio device was removed or invalidated during capture."""


class EncoderError(CaptureError):
    """FFmpeg/encoder failure. ``stderr_tail`` holds recent encoder output."""

    def __init__(self, message: str, *, stderr_tail: str = "", returncode: int | None = None):
        super().__init__(
            message if not stderr_tail else f"{message}\n--- ffmpeg ---\n{stderr_tail}"
        )
        self.stderr_tail = stderr_tail
        self.returncode = returncode


class EncoderNotAvailableError(EncoderError):
    """No usable encoder/FFmpeg executable was found."""


class RecorderStateError(CaptureError, RuntimeError):
    """An operation is invalid in the recorder's current state."""


class NativeError(CaptureError, OSError):
    """A native API call failed. ``code`` holds the HRESULT/errno when known."""

    def __init__(self, what: str, code: int | None = None):
        self.what = what
        self.code = code
        text = what if code is None else f"{what} failed (0x{code & 0xFFFFFFFF:08X})"
        super().__init__(text)


__all__ = [
    "AccessDeniedError",
    "AudioDeviceDisconnectedError",
    "AudioDeviceError",
    "CapabilityUnavailableError",
    "CaptureError",
    "CaptureTimeoutError",
    "EncoderError",
    "EncoderNotAvailableError",
    "InvalidRegionError",
    "NativeError",
    "RecorderStateError",
    "TargetLostError",
    "TargetNotFoundError",
    "UnsupportedDisplayServerError",
    "UnsupportedPlatformError",
    "WindowClosedError",
]
