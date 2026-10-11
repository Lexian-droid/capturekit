"""FFmpeg discovery, encoder validation, command construction and process control."""

from __future__ import annotations

import collections
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ..config import Container, EncoderConfig
from ..errors import EncoderError, EncoderNotAvailableError

#: Order used by ``encoder="auto"`` (hardware first).
AUTO_H264_ORDER = ("h264_nvenc", "h264_qsv", "h264_amf", "libx264", "h264_mf")
HARDWARE_ENCODERS = {
    "h264_nvenc",
    "h264_qsv",
    "h264_amf",
    "h264_mf",
    "h264_vaapi",
    "hevc_nvenc",
    "hevc_qsv",
    "hevc_amf",
}
#: Encoders that accept BGRA input directly (colour conversion on the GPU).
BGRA_INPUT_ENCODERS = {"h264_nvenc", "hevc_nvenc", "av1_nvenc", "libx264rgb"}

_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def find_ffmpeg(explicit: str | None = None) -> str:
    """Locate an FFmpeg executable.

    Order: ``explicit`` → ``$CAPTUREKIT_FFMPEG`` → ``PATH`` → the optional
    ``imageio-ffmpeg`` bundled binary.
    """
    for cand in (explicit, os.environ.get("CAPTUREKIT_FFMPEG")):
        if cand:
            if Path(cand).is_file():
                return str(Path(cand).resolve())
            raise EncoderNotAvailableError(f"FFmpeg not found at {cand!r}")
    found = _which_no_cwd("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).is_file():
            return str(exe)
    except Exception:
        pass
    raise EncoderNotAvailableError(
        "FFmpeg executable not found: install FFmpeg, set CAPTUREKIT_FFMPEG, or "
        "`pip install capturekit[ffmpeg]`"
    )


def _which_no_cwd(name: str) -> str | None:
    """``shutil.which`` restricted to absolute PATH entries.

    On Windows ``shutil.which`` also searches the current directory first,
    which would execute a planted ``ffmpeg.exe`` from e.g. a downloads folder.
    """
    cwd = os.path.normcase(os.path.abspath(os.getcwd()))
    dirs = [
        d
        for d in os.environ.get("PATH", "").split(os.pathsep)
        if d and os.path.isabs(d) and os.path.normcase(os.path.abspath(d)) != cwd
    ]
    found = shutil.which(name, path=os.pathsep.join(dirs)) if dirs else None
    if found and os.path.normcase(os.path.dirname(os.path.abspath(found))) == cwd:
        return None
    return str(Path(found).resolve()) if found else None


def _run(args: Sequence[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args), capture_output=True, text=True, timeout=timeout,
        stdin=subprocess.DEVNULL, creationflags=_CREATE_NO_WINDOW, check=False,
    )  # fmt: skip


def ffmpeg_version(ffmpeg: str) -> str:
    out = _run([ffmpeg, "-hide_banner", "-version"], 15).stdout
    return out.splitlines()[0] if out else "unknown"


@dataclass(frozen=True, slots=True)
class EncoderProbe:
    name: str
    usable: bool
    hardware: bool
    reason: str = ""


_probe_cache: dict[tuple[str, float, str], EncoderProbe] = {}
_probe_lock = threading.Lock()


def probe_encoder(ffmpeg: str, encoder: str) -> EncoderProbe:
    """Validate an encoder by actually initialising it (a 3-frame encode).

    ``-encoders`` listing is not enough: e.g. ``h264_qsv`` is listed in most
    Windows builds but fails without an Intel GPU.
    """
    key = (ffmpeg, os.path.getmtime(ffmpeg), encoder)
    with _probe_lock:
        if key in _probe_cache:
            return _probe_cache[key]
    pix = (
        "bgra"
        if encoder in BGRA_INPUT_ENCODERS
        else ("nv12" if encoder in HARDWARE_ENCODERS else "yuv420p")
    )
    args = [
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=black:s=256x256:r=30",
        "-frames:v", "3", "-pix_fmt", pix, "-c:v", encoder, "-f", "null", "-",
    ]  # fmt: skip
    try:
        res = _run(args, 30)
        ok = res.returncode == 0
        reason = "" if ok else (res.stderr.strip().splitlines() or ["failed"])[-1]
    except subprocess.TimeoutExpired:
        ok, reason = False, "probe timed out"
    probe = EncoderProbe(encoder, ok, encoder in HARDWARE_ENCODERS, reason)
    with _probe_lock:
        _probe_cache[key] = probe
    return probe


def select_encoder(ffmpeg: str, cfg: EncoderConfig, size: tuple[int, int]) -> str:
    """Choose a validated encoder according to ``cfg``.

    Raises :class:`EncoderNotAvailableError` with every rejection reason when
    nothing works (never silently picks something the caller excluded).
    """
    if cfg.encoder == "auto":
        candidates: list[str] = list(AUTO_H264_ORDER)
    else:
        candidates = [cfg.encoder]
        if cfg.fallback:
            candidates += [e for e in AUTO_H264_ORDER if e != cfg.encoder]
    reasons = []
    for enc in candidates:
        # NVENC rejects tiny frames on some GPU generations.
        if enc.endswith("_nvenc") and (size[0] < 160 or size[1] < 64):
            reasons.append(f"{enc}: frame {size} below NVENC minimum")
            continue
        probe = probe_encoder(ffmpeg, enc)
        if probe.usable:
            return enc
        reasons.append(f"{enc}: {probe.reason}")
    raise EncoderNotAvailableError("no usable video encoder:\n  " + "\n  ".join(reasons))


@dataclass(frozen=True, slots=True)
class AudioInput:
    path: str  # pipe path FFmpeg reads from
    sample_rate: int
    channels: int
    title: str


def container_for(output: str, explicit: Container | None) -> Container:
    if explicit is not None:
        return explicit
    ext = Path(output).suffix.lower()
    if ext in (".mp4", ".m4v", ".mov"):
        return Container.MP4
    return Container.MKV


def _video_codec_args(encoder: str, cfg: EncoderConfig, fps: float) -> list[str]:
    gop = max(1, round(cfg.keyframe_interval_s * fps))
    args = ["-c:v", encoder, "-g", str(gop)]
    br = cfg.bitrate
    if encoder.endswith("_nvenc"):
        args += ["-preset", cfg.preset or "p4"]
        if br:
            args += [
                "-rc",
                "vbr",
                "-b:v",
                str(br),
                "-maxrate",
                str(int(br * 1.5)),
                "-bufsize",
                str(br * 2),
            ]
        else:
            args += ["-rc", "vbr", "-cq", str(cfg.quality), "-b:v", "0"]
    elif encoder.endswith("_qsv"):
        args += ["-pix_fmt", "nv12", "-preset", cfg.preset or "veryfast"]
        args += ["-b:v", str(br)] if br else ["-global_quality", str(cfg.quality)]
    elif encoder.endswith("_amf"):
        args += ["-pix_fmt", "nv12", "-quality", cfg.preset or "balanced"]
        if br:
            args += ["-rc", "vbr_peak", "-b:v", str(br), "-maxrate", str(int(br * 1.5))]
        else:
            args += ["-rc", "cqp", "-qp_i", str(cfg.quality), "-qp_p", str(cfg.quality)]
    elif encoder == "h264_mf":
        args += ["-pix_fmt", "nv12", "-b:v", str(br or 8_000_000)]
    else:  # libx264 and other software encoders
        args += ["-pix_fmt", "yuv420p", "-preset", cfg.preset or "veryfast"]
        if br:
            args += ["-b:v", str(br), "-maxrate", str(int(br * 1.5)), "-bufsize", str(br * 2)]
        else:
            args += ["-crf", str(cfg.quality)]
    return args


def build_command(
    ffmpeg: str,
    *,
    output: str,
    container: Container,
    encoder: str | None,
    cfg: EncoderConfig,
    video_size: tuple[int, int] | None,
    fps: float,
    audio_inputs: Sequence[AudioInput] = (),
    overwrite: bool = False,
) -> list[str]:
    """Build the FFmpeg argument list (never passed through a shell).

    The output is given as ``file:<absolute path>`` so names starting with
    ``-`` or containing ``proto:`` prefixes cannot be misinterpreted.
    """
    args = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "warning", "-nostats"]
    args.append("-y" if overwrite else "-n")
    inputs = 0
    if video_size is not None:
        w, h = video_size
        args += [
            "-thread_queue_size", "64", "-probesize", "32", "-analyzeduration", "0",
            "-f", "rawvideo", "-pix_fmt", "bgra", "-video_size", f"{w}x{h}",
            "-framerate", _fps_str(fps), "-i", "pipe:0",
        ]  # fmt: skip
        inputs += 1
    for a in audio_inputs:
        args += [
            "-thread_queue_size", "1024", "-probesize", "32", "-analyzeduration", "0",
            "-f", "f32le", "-ar", str(a.sample_rate), "-ac", str(a.channels), "-i", a.path,
        ]  # fmt: skip
        inputs += 1
    for i in range(inputs):
        args += ["-map", f"{i}"]
    if video_size is not None:
        assert encoder is not None
        # No colour-space override: NVENC's internal RGB->YUV and swscale's
        # default both use BT.601, which is what the encoders tag.
        args += _video_codec_args(encoder, cfg, fps)
    if audio_inputs:
        acodec = cfg.audio_codec
        args += ["-c:a", acodec]
        if acodec not in ("flac", "pcm_f32le", "pcm_s16le"):
            args += ["-b:a", str(cfg.audio_bitrate)]
        for i, a in enumerate(audio_inputs):
            args += [f"-metadata:s:a:{i}", f"title={a.title}"]
    if container is Container.MP4:
        # Fragmented MP4 stays playable if the process dies mid-recording.
        args += ["-f", "mp4", "-movflags", "+frag_keyframe+empty_moov+default_base_moof"]
    else:
        args += ["-f", "matroska"]
    args.append("file:" + str(Path(output).resolve()))
    return args


def _fps_str(fps: float) -> str:
    if abs(fps - round(fps)) < 1e-9:
        return str(round(fps))
    # NTSC-style rates such as 29.97 → 30000/1001
    for base in (24, 30, 60, 120):
        if abs(fps - base * 1000 / 1001) < 1e-3:
            return f"{base * 1000}/1001"
    return f"{fps:.6f}"


class FFmpegProcess:
    """A running FFmpeg process with continuous stderr draining."""

    def __init__(self, args: list[str]) -> None:
        self.args = args
        self._stderr_tail: collections.deque[str] = collections.deque(maxlen=200)
        try:
            self.proc = subprocess.Popen(
                args,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=_CREATE_NO_WINDOW,
                bufsize=0,
            )
        except OSError as exc:
            raise EncoderNotAvailableError(f"failed to start FFmpeg: {exc}") from exc
        self._stderr_thread = threading.Thread(
            target=self._drain, name="capturekit-ffmpeg-stderr", daemon=True
        )
        self._stderr_thread.start()

    def _drain(self) -> None:
        assert self.proc.stderr is not None
        for raw in iter(self.proc.stderr.readline, b""):
            self._stderr_tail.append(raw.decode("utf-8", "replace").rstrip())

    @property
    def stderr_tail(self) -> str:
        return "\n".join(self._stderr_tail)

    def write(self, data: memoryview | bytes) -> None:
        """Write all of ``data`` to stdin (raises EncoderError if FFmpeg died)."""
        stdin = self.proc.stdin
        assert stdin is not None
        mv = memoryview(data).cast("B")
        try:
            while mv:
                mv = mv[stdin.write(mv) :]
        except (BrokenPipeError, OSError, ValueError) as exc:
            self.proc.poll()
            raise EncoderError(
                f"FFmpeg stopped accepting video ({exc})",
                stderr_tail=self.stderr_tail,
                returncode=self.proc.returncode,
            ) from exc

    def close_stdin(self) -> None:
        if self.proc.stdin is not None and not self.proc.stdin.closed:
            try:
                self.proc.stdin.close()
            except OSError:
                pass

    def wait(self, timeout: float) -> int:
        """Wait for exit; terminate/kill on timeout. Returns the exit code."""
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            try:
                return self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                return self.proc.wait(timeout=5)
        finally:
            self._stderr_thread.join(timeout=2)

    def kill(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
