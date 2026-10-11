"""Plain data types shared by all backends.

All coordinates are physical pixels. Monitor rectangles are in virtual-desktop
coordinates (may be negative). Crop regions are always relative to the
selected source (monitor or window content).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .errors import InvalidRegionError

if TYPE_CHECKING:
    import numpy as np
    import numpy.typing as npt


@dataclass(frozen=True, slots=True)
class Rect:
    """Axis-aligned rectangle ``(x, y, width, height)``."""

    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    @property
    def size(self) -> tuple[int, int]:
        return (self.width, self.height)

    def is_empty(self) -> bool:
        return self.width <= 0 or self.height <= 0

    def intersect(self, other: Rect) -> Rect:
        x = max(self.x, other.x)
        y = max(self.y, other.y)
        r = min(self.right, other.right)
        b = min(self.bottom, other.bottom)
        return Rect(x, y, max(0, r - x), max(0, b - y))

    def contains(self, other: Rect) -> bool:
        return (
            other.x >= self.x
            and other.y >= self.y
            and other.right <= self.right
            and other.bottom <= self.bottom
        )

    def translate(self, dx: int, dy: int) -> Rect:
        return Rect(self.x + dx, self.y + dy, self.width, self.height)

    @classmethod
    def from_ltrb(cls, left: int, top: int, right: int, bottom: int) -> Rect:
        return cls(left, top, right - left, bottom - top)


def validate_region(region: Rect, bounds_size: tuple[int, int]) -> Rect:
    """Validate a crop region against a source of ``bounds_size``.

    Raises :class:`InvalidRegionError` for empty or out-of-bounds regions.
    """
    if region.is_empty():
        raise InvalidRegionError(f"crop region {region} is empty")
    w, h = bounds_size
    if region.x < 0 or region.y < 0 or region.right > w or region.bottom > h:
        raise InvalidRegionError(f"crop region {region} lies outside the source ({w}x{h})")
    return region


class PixelFormat(enum.Enum):
    """Pixel layout of :attr:`Frame.data`. Only 8-bit BGRA is produced today."""

    BGRA = "bgra"


@dataclass(frozen=True, slots=True)
class Monitor:
    """A display output.

    ``id`` is stable for the lifetime of the display configuration (Windows:
    GDI device name such as ``\\\\.\\DISPLAY1``; X11: RandR monitor/output name).
    """

    id: str
    index: int
    name: str
    rect: Rect
    is_primary: bool
    scale: float = 1.0
    refresh_rate: float | None = None
    display: str | None = None
    native_handle: int = 0

    @property
    def width(self) -> int:
        return self.rect.width

    @property
    def height(self) -> int:
        return self.rect.height


@dataclass(frozen=True, slots=True)
class Window:
    """A top-level window that can be targeted for exclusive capture.

    ``handle`` is the native identifier (HWND on Windows, X11 window ID on
    Linux). ``rect`` is the visible window bounds in desktop coordinates.
    ``capture_excluded`` is True when the window asked the OS to exclude it
    from capture (Windows display affinity); capturekit honours that.
    """

    handle: int
    title: str
    class_name: str | None
    pid: int | None
    process_name: str | None
    rect: Rect
    is_visible: bool
    is_minimized: bool
    display: str | None = None
    capture_excluded: bool = False


class AudioDeviceKind(enum.Enum):
    INPUT = "input"  #: microphones / capture endpoints / Pulse sources
    OUTPUT = "output"  #: speakers / render endpoints (loopback-capable)


@dataclass(frozen=True, slots=True)
class AudioDevice:
    id: str
    name: str
    kind: AudioDeviceKind
    is_default: bool
    channels: int | None = None
    sample_rate: int | None = None
    backend: str = ""


@dataclass(frozen=True, slots=True)
class AudioSession:
    """An application audio stream/session and its owning process.

    One process may own several sessions (several streams, several devices).
    ``stream_id`` identifies the native stream where one exists (PulseAudio
    sink-input index); Windows sessions are identified by ``pid``.
    """

    pid: int | None
    process_name: str | None
    display_name: str
    device_id: str | None
    is_active: bool
    stream_id: str | None = None
    backend: str = ""


@dataclass(slots=True)
class Frame:
    """A captured video frame.

    ``data`` is a ``(height, width, 4)`` uint8 BGRA array owned by the caller.
    ``timestamp_ns`` is on the capturekit clock (see :func:`capturekit.now_ns`).
    ``content_size`` is the source content size when the frame was produced
    (useful to detect window resizes when a fixed output size is used).
    """

    data: npt.NDArray[np.uint8]
    timestamp_ns: int
    index: int
    content_size: tuple[int, int]
    pixel_format: PixelFormat = PixelFormat.BGRA

    @property
    def width(self) -> int:
        return int(self.data.shape[1])

    @property
    def height(self) -> int:
        return int(self.data.shape[0])


@dataclass(slots=True)
class AudioChunk:
    """Interleaved float32 audio, shape ``(frames, channels)``."""

    data: npt.NDArray[np.float32]
    timestamp_ns: int
    sample_rate: int
    source: str = ""
    flags: frozenset[str] = field(default_factory=frozenset)

    @property
    def frames(self) -> int:
        return int(self.data.shape[0])

    @property
    def channels(self) -> int:
        return int(self.data.shape[1])
