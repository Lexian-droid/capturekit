"""Linux backend facade.

The X11/Xvfb and PulseAudio/PipeWire backends are not implemented yet (see
docs/PROGRESS.md). Every entry point raises CapabilityUnavailableError so
callers get an explicit, typed answer instead of partial behaviour.
"""

from __future__ import annotations

from typing import NoReturn

from ...capabilities import Capabilities, unsupported
from ...errors import CapabilityUnavailableError

PLATFORM = "linux"
_MSG = "the Linux X11/Xvfb backend is not implemented yet (see docs/guide/linux.md)"


def _missing(*_args: object, **_kwargs: object) -> NoReturn:
    raise CapabilityUnavailableError(_MSG)


thread_context = _missing
list_monitors = _missing
list_windows = _missing
get_window = _missing
open_video = _missing
list_audio_devices = _missing
list_audio_sessions = _missing
open_audio = _missing


def capabilities(display: str | None = None) -> Capabilities:
    import platform

    n = unsupported("Linux backend not implemented yet")
    return Capabilities(
        platform=PLATFORM,
        os_version=platform.release(),
        display_server=None,
        monitor_capture=n,
        window_capture=n,
        window_capture_borderless=n,
        window_capture_minimized=n,
        cursor_capture=n,
        microphone_audio=n,
        system_audio=n,
        process_audio=n,
        audio_session_enumeration=n,
        recording=n,
    )
