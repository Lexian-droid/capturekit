"""Recording configuration."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class AudioMixMode(enum.Enum):
    #: One track per audio source (default).
    SEPARATE = "separate"
    #: A single track containing the sum of all sources.
    MIXED = "mixed"
    #: Separate tracks followed by a mixed track.
    BOTH = "both"


class TargetLostPolicy(enum.Enum):
    #: Stop and finalise the recording, report via ``on_error``.
    STOP = "stop"
    #: Keep recording, repeating the last frame (window closed / device gone).
    CONTINUE = "continue"


class Container(enum.Enum):
    MKV = "matroska"
    MP4 = "mp4"


@dataclass(frozen=True, slots=True)
class EncoderConfig:
    """Video/audio encoder settings.

    ``encoder``: ``"auto"`` probes hardware encoders (NVENC → QSV → AMF) and
    falls back to ``libx264``. Explicit FFmpeg encoder names are accepted
    (``"h264_nvenc"``, ``"libx264"``, ...). ``fallback`` allows trying the next
    candidate when an explicitly requested encoder fails validation.

    ``bitrate`` in bits/s; ``None`` uses constant-quality mode with
    ``quality`` (CRF/CQ, lower is better, default 23).
    """

    encoder: str = "auto"
    codec: str = "h264"
    bitrate: int | None = None
    quality: int = 23
    preset: str | None = None
    keyframe_interval_s: float = 2.0
    fallback: bool = True
    audio_codec: str = "aac"
    audio_bitrate: int = 192_000
    ffmpeg_path: str | None = None


@dataclass(frozen=True, slots=True)
class RecordingConfig:
    """Recording settings.

    ``output_size``: fixed output resolution ``(w, h)``. ``None`` uses the
    source size at start. When a window is resized during recording its
    content is placed at the top-left and padded with black / cropped.
    Odd sizes are rounded down to even (required for 4:2:0 encoding).

    ``queue_frames`` bounds raw frames waiting for the encoder (memory ≈
    ``queue_frames * w * h * 4`` bytes).
    """

    fps: float = 30.0
    output_size: tuple[int, int] | None = None
    container: Container | None = None
    encoder: EncoderConfig = field(default_factory=EncoderConfig)
    audio_mix: AudioMixMode = AudioMixMode.SEPARATE
    audio_sample_rate: int = 48_000
    queue_frames: int = 8
    overwrite: bool = False
    on_target_lost: TargetLostPolicy = TargetLostPolicy.STOP
    start_timeout_s: float = 10.0

    def __post_init__(self) -> None:
        if not (1.0 <= self.fps <= 240.0):
            raise ValueError(f"fps must be within 1..240, got {self.fps}")
        if self.queue_frames < 2:
            raise ValueError("queue_frames must be >= 2")
        if self.output_size is not None and (self.output_size[0] < 2 or self.output_size[1] < 2):
            raise ValueError(f"invalid output_size {self.output_size}")
