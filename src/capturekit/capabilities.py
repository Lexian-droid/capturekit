"""Programmatic capability discovery."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class Support(enum.Enum):
    #: Implemented and tested on this platform.
    SUPPORTED = "supported"
    #: Implemented; limited testing or known gaps (see ``reason``).
    EXPERIMENTAL = "experimental"
    #: Implemented for this platform, but this machine/OS build/server lacks
    #: a requirement (``reason`` says which).
    UNAVAILABLE = "unavailable"
    #: Not implemented or out of scope.
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class Capability:
    status: Support
    reason: str = ""

    def __bool__(self) -> bool:
        return self.status in (Support.SUPPORTED, Support.EXPERIMENTAL)


def supported(reason: str = "") -> Capability:
    return Capability(Support.SUPPORTED, reason)


def experimental(reason: str) -> Capability:
    return Capability(Support.EXPERIMENTAL, reason)


def unavailable(reason: str) -> Capability:
    return Capability(Support.UNAVAILABLE, reason)


def unsupported(reason: str) -> Capability:
    return Capability(Support.UNSUPPORTED, reason)


@dataclass(frozen=True, slots=True)
class Capabilities:
    platform: str
    os_version: str
    display_server: str | None
    monitor_capture: Capability
    window_capture: Capability
    window_capture_borderless: Capability
    window_capture_minimized: Capability
    cursor_capture: Capability
    microphone_audio: Capability
    system_audio: Capability
    process_audio: Capability
    audio_session_enumeration: Capability
    recording: Capability
    encoders: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    def summary(self) -> str:
        lines = [
            f"capturekit on {self.platform} {self.os_version} "
            f"(display server: {self.display_server or 'n/a'})"
        ]
        for name in (
            "monitor_capture", "window_capture", "window_capture_borderless",
            "window_capture_minimized", "cursor_capture", "microphone_audio", "system_audio",
            "process_audio", "audio_session_enumeration", "recording",
        ):  # fmt: skip
            cap: Capability = getattr(self, name)
            lines.append(f"  {name:28s} {cap.status.value:12s} {cap.reason}")
        if self.encoders:
            lines.append(f"  validated encoders: {', '.join(self.encoders)}")
        lines.extend(f"  note: {n}" for n in self.notes)
        return "\n".join(lines)
