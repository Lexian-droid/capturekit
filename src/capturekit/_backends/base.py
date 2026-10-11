"""Internal backend protocols (not public API)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np
import numpy.typing as npt

from ..types import Rect

Buffer = npt.NDArray[np.uint8]
Samples = npt.NDArray[np.float32]


@dataclass(slots=True)
class AcquiredFrame:
    data: Buffer
    timestamp_ns: int
    content_size: tuple[int, int]


@dataclass(slots=True)
class VideoBackendInfo:
    name: str
    #: Output size when ``acquire(out=None)`` is used right after start.
    initial_size: tuple[int, int]
    details: dict[str, object] = field(default_factory=dict)


class VideoBackend(Protocol):
    """Pull-model video source. All methods are called from one thread."""

    info: VideoBackendInfo

    def start(self) -> None: ...

    def acquire(self, out: Buffer | None, timeout_s: float) -> AcquiredFrame | None:
        """Return the newest frame since the last call, or ``None``.

        With ``out`` the frame is written at its top-left corner; the rest of
        ``out`` is cleared. Raises ``TargetLostError`` when the target is gone.
        """
        ...

    def close(self) -> None: ...


@dataclass(slots=True)
class AudioPacket:
    data: Samples  # (frames, channels) float32
    timestamp_ns: int
    discontinuity: bool = False


AudioCallback = Callable[[AudioPacket], None]


class AudioBackend(Protocol):
    """Push-model audio source running its own capture thread."""

    name: str
    sample_rate: int
    channels: int

    def start(
        self, on_packet: AudioCallback, on_error: Callable[[BaseException], None]
    ) -> None: ...

    def stop(self) -> None: ...


def blit_clear(out: Buffer, copied_w: int, copied_h: int) -> None:
    """Clear the parts of ``out`` not covered by a top-left copy."""
    h, w = out.shape[0], out.shape[1]
    if copied_h < h:
        out[copied_h:, :] = 0
    if copied_w < w:
        out[: min(copied_h, h), copied_w:] = 0


def crop_box(base: Rect, region: Rect | None) -> Rect:
    """Region (relative to ``base``) translated into absolute coordinates and
    clipped to ``base``. Without a region the whole base is used."""
    if region is None:
        return base
    return region.translate(base.x, base.y).intersect(base)
