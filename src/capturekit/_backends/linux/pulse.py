"""Linux audio through the PulseAudio protocol (PulseAudio or pipewire-pulse).

* Enumeration: ``pactl -f json`` (PulseAudio >= 16 / pipewire-pulse).
* Microphone / system audio: one ``parec`` per source (``<sink>.monitor``
  for system audio).
* Application audio: one ``parec --monitor-stream=<sink-input>`` per stream
  of the target process tree. This records only that stream and does not
  change the user's routing; streams that appear later are attached, ended
  streams are dropped. Per-process capture never falls back to the sink
  monitor.

All streams are float32 / 48 kHz / stereo; the server resamples.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import shutil
import subprocess
import threading
from collections import deque
from collections.abc import Callable
from typing import Any

import numpy as np

from ..._pipeline.clock import NS_PER_S, now_ns
from ...capabilities import Capability, supported, unavailable
from ...errors import (
    AudioDeviceDisconnectedError,
    AudioDeviceError,
    CapabilityUnavailableError,
    TargetNotFoundError,
)
from ...sources import (
    AudioSourceSpec,
    MicrophoneSource,
    ProcessAudioSource,
    SystemAudioSource,
    audio_source_label,
)
from ...types import AudioDevice, AudioDeviceKind, AudioSession
from ..base import AudioCallback, AudioPacket

log = logging.getLogger("capturekit.linux.pulse")

CHUNK_FRAMES = 480  # 10 ms at 48 kHz
LATENCY_MS = 20
RESCAN_S = 0.5


def _tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise CapabilityUnavailableError(
            f"{name!r} not found: install pulseaudio-utils (works with PulseAudio and PipeWire)"
        )
    return path


def _pactl(*args: str) -> Any:
    res = subprocess.run(
        [_tool("pactl"), "-f", "json", *args],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if res.returncode != 0:
        raise AudioDeviceError(f"pactl {' '.join(args)} failed: {res.stderr.strip()}")
    return json.loads(res.stdout or "null")


def _pactl_text(*args: str) -> str:
    res = subprocess.run(
        [_tool("pactl"), *args], capture_output=True, text=True, timeout=10, check=False
    )
    if res.returncode != 0:
        raise AudioDeviceError(f"pactl {' '.join(args)} failed: {res.stderr.strip()}")
    return res.stdout.strip()


def audio_capabilities() -> dict[str, Capability]:
    try:
        _pactl_text("info")
        _tool("parec")
    except (
        CapabilityUnavailableError,
        AudioDeviceError,
        OSError,
        subprocess.TimeoutExpired,
    ) as exc:
        cap = unavailable(f"no reachable PulseAudio/PipeWire server: {exc}")
        return dict.fromkeys(("microphone", "system", "process", "sessions"), cap)
    return {
        "microphone": supported("parec on the source"),
        "system": supported("parec on <default sink>.monitor"),
        "process": supported("parec --monitor-stream per sink-input of the process tree"),
        "sessions": supported("pactl sink-inputs with application.process.id"),
    }


def list_devices() -> list[AudioDevice]:
    default_sink = _pactl_text("get-default-sink")
    default_source = _pactl_text("get-default-source")
    out: list[AudioDevice] = []
    for s in _pactl("list", "sources") or []:
        name = s.get("name", "")
        if s.get("monitor_of_sink") not in (None, "n/a") or name.endswith(".monitor"):
            continue  # monitors are reached through SystemAudioSource
        out.append(_device(s, AudioDeviceKind.INPUT, name == default_source))
    for s in _pactl("list", "sinks") or []:
        out.append(_device(s, AudioDeviceKind.OUTPUT, s.get("name") == default_sink))
    return out


def _device(s: dict[str, Any], kind: AudioDeviceKind, default: bool) -> AudioDevice:
    spec = str(s.get("sample_specification", ""))
    rate = next((int(p[:-2]) for p in spec.split() if p.endswith("Hz") and p[:-2].isdigit()), None)
    ch = next((int(p[:-2]) for p in spec.split() if p.endswith("ch") and p[:-2].isdigit()), None)
    return AudioDevice(
        id=s.get("name", ""),
        name=s.get("description") or s.get("name", ""),
        kind=kind,
        is_default=default,
        channels=ch,
        sample_rate=rate,
        backend="pulse",
    )


def _sink_inputs() -> list[dict[str, Any]]:
    data = _pactl("list", "sink-inputs") or []
    return [d for d in data if isinstance(d, dict)]


def _sink_names() -> dict[int, str]:
    return {int(s["index"]): str(s["name"]) for s in _pactl("list", "short", "sinks") or []}


def list_sessions() -> list[AudioSession]:
    sinks = _sink_names()
    out = []
    for si in _sink_inputs():
        props = si.get("properties", {})
        pid = props.get("application.process.id")
        out.append(
            AudioSession(
                pid=int(pid) if pid and str(pid).isdigit() else None,
                process_name=props.get("application.process.binary"),
                display_name=props.get("application.name") or props.get("media.name") or "",
                device_id=sinks.get(int(si.get("sink", -1))),
                is_active=not si.get("corked", False),
                stream_id=str(si.get("index")),
                backend="pulse",
            )
        )
    return out


def process_tree(pid: int) -> set[int]:
    parents: dict[int, int] = {}
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat", encoding="utf-8") as f:
                stat = f.read()
            # field 4 (ppid) follows the parenthesised command name
            parents[int(entry)] = int(stat.rsplit(")", 1)[1].split()[1])
        except (OSError, IndexError, ValueError):
            continue
    tree = {pid}
    changed = True
    while changed:
        changed = False
        for child, parent in parents.items():
            if parent in tree and child not in tree:
                tree.add(child)
                changed = True
    return tree


# --- parec streams ---------------------------------------------------------------


class ParecStream:
    """One ``parec`` process; delivers float32 stereo chunks to a callback."""

    def __init__(
        self,
        args: list[str],
        channels: int,
        on_chunk: Callable[[np.ndarray, int], None],
        on_end: Callable[[ParecStream, int | None], None],
    ) -> None:
        self.args = args
        self.channels = channels
        self._on_chunk = on_chunk
        self._on_end = on_end
        self.proc = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL
        )
        self._stopping = False
        self._thread = threading.Thread(target=self._run, name="capturekit-parec", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        assert self.proc.stdout is not None
        nbytes = CHUNK_FRAMES * self.channels * 4
        while True:
            data = self.proc.stdout.read(nbytes)
            if not data:
                break
            n = len(data) // (4 * self.channels)
            if n == 0:
                continue
            arr = (
                np.frombuffer(data[: n * 4 * self.channels], dtype=np.float32)
                .reshape(n, self.channels)
                .copy()
            )
            # A pipe carries no timestamps: estimate capture time of the first
            # sample from the read time, chunk length and requested latency.
            ts = now_ns() - n * NS_PER_S // 48_000 - LATENCY_MS * 1_000_000
            self._on_chunk(arr, ts)
        rc = self.proc.wait()
        if not self._stopping:
            self._on_end(self, rc)

    def stop(self) -> None:
        self._stopping = True
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self._thread.join(3)

    @property
    def stderr(self) -> str:
        if self.proc.stderr is None or self.proc.poll() is None:
            return ""
        return self.proc.stderr.read().decode("utf-8", "replace").strip()


def _parec_args(rate: int, channels: int, name: str) -> list[str]:
    return [
        _tool("parec"), "--raw", "--format=float32le", f"--rate={rate}",
        f"--channels={channels}", f"--latency-msec={LATENCY_MS}",
        f"--client-name=capturekit-{name}",
        # PipeWire: never re-link to another node (e.g. the microphone) when
        # the target disappears.
        "--property=node.dont-reconnect=true",
    ]  # fmt: skip


class PulseDeviceCapture:
    """Microphone or system audio (sink monitor)."""

    def __init__(
        self, kind: str, device: str | None, sample_rate: int, channels: int, name: str
    ) -> None:
        self.kind = kind
        self.device = device
        self.sample_rate = sample_rate
        self.channels = channels
        self.name = name
        self.device_name = ""
        self._stream: ParecStream | None = None

    def start(self, on_packet: AudioCallback, on_error: Callable[[BaseException], None]) -> None:
        if self.kind == "system":
            sink = self.device or _pactl_text("get-default-sink")
            source = sink if sink.endswith(".monitor") else f"{sink}.monitor"
        else:
            source = self.device or _pactl_text("get-default-source")
        names = {s.get("name") for s in _pactl("list", "short", "sources") or []}
        if source not in names:
            raise TargetNotFoundError(f"audio source {source!r} not found")
        self.device_name = source
        args = [*_parec_args(self.sample_rate, self.channels, self.kind), f"--device={source}"]

        def ended(stream: ParecStream, rc: int | None) -> None:
            on_error(
                AudioDeviceDisconnectedError(
                    f"recording from {source!r} ended (parec exit {rc}): {stream.stderr}"
                )
            )

        self._stream = ParecStream(
            args, self.channels, lambda a, ts: on_packet(AudioPacket(a, ts)), ended
        )

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream = None


class PulseProcessCapture:
    """Audio of a process tree: every matching sink-input, mixed, followed live."""

    def __init__(
        self,
        pid: int,
        include_children: bool,
        stream_ids: tuple[str, ...],
        sample_rate: int,
        channels: int,
        name: str,
    ) -> None:
        self.pid = pid
        self.include_children = include_children
        self.stream_ids = stream_ids
        self.sample_rate = sample_rate
        self.channels = channels
        self.name = name
        self.device_name = f"process {pid}"
        self._streams: dict[int, ParecStream] = {}
        self._fifos: dict[int, deque[np.ndarray]] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.streams_seen: set[int] = set()

    def _matching_inputs(self) -> list[int]:
        if self.stream_ids:
            wanted = {int(s) for s in self.stream_ids}
            return [int(si["index"]) for si in _sink_inputs() if int(si["index"]) in wanted]
        pids = process_tree(self.pid) if self.include_children else {self.pid}
        out = []
        for si in _sink_inputs():
            p = si.get("properties", {}).get("application.process.id")
            if p and str(p).isdigit() and int(p) in pids:
                out.append(int(si["index"]))
        return out

    def start(self, on_packet: AudioCallback, on_error: Callable[[BaseException], None]) -> None:
        if not os.path.exists(f"/proc/{self.pid}"):
            raise TargetNotFoundError(f"process {self.pid} does not exist")
        _tool("parec")
        self._on_packet = on_packet
        self._on_error = on_error
        self._rescan()
        for target in (self._scan_loop, self._mix_loop):
            t = threading.Thread(target=target, name="capturekit-pulse-process", daemon=True)
            t.start()
            self._threads.append(t)

    def _rescan(self) -> None:
        try:
            current = set(self._matching_inputs())
        except (AudioDeviceError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
            log.warning("sink-input scan failed: %s", exc)
            return
        with self._lock:
            for idx in current - set(self._streams):
                args = [
                    *_parec_args(self.sample_rate, self.channels, f"stream{idx}"),
                    f"--monitor-stream={idx}",
                ]
                fifo: deque[np.ndarray] = deque()
                self._fifos[idx] = fifo
                self._streams[idx] = ParecStream(
                    args,
                    self.channels,
                    functools.partial(self._push_chunk, fifo),
                    functools.partial(self._stream_ended, idx),
                )
                self.streams_seen.add(idx)
                log.debug("attached sink-input %d", idx)

    def _push_chunk(self, fifo: deque[np.ndarray], a: np.ndarray, _ts: int) -> None:
        self._push(fifo, a)

    def _stream_ended(self, idx: int, _stream: ParecStream, _rc: int | None) -> None:
        self._ended(idx)

    def _push(self, fifo: deque[np.ndarray], a: np.ndarray) -> None:
        with self._lock:
            fifo.append(a)
            # Bound the backlog (300 ms): drop the oldest audio.
            while sum(len(x) for x in fifo) > self.sample_rate * 3 // 10:
                fifo.popleft()

    def _ended(self, idx: int) -> None:
        with self._lock:
            self._streams.pop(idx, None)
        log.debug("sink-input %d ended", idx)

    def _scan_loop(self) -> None:
        while not self._stop.wait(RESCAN_S):
            self._rescan()

    def _pop(self, fifo: deque[np.ndarray], n: int) -> np.ndarray:
        out = np.zeros((n, self.channels), np.float32)
        filled = 0
        while filled < n and fifo:
            blk = fifo[0]
            take = min(n - filled, len(blk))
            out[filled : filled + take] = blk[:take]
            filled += take
            if take == len(blk):
                fifo.popleft()
            else:
                fifo[0] = blk[take:]
        return out

    def _mix_loop(self) -> None:
        """Clock-driven mixer: emits exactly real-time worth of samples, so a
        silent or absent process yields silence, never other audio."""
        delay_ns = 60_000_000  # jitter buffer for the parec pipes
        t0 = now_ns() + delay_ns
        emitted = 0
        while not self._stop.wait(0.01):
            due = (now_ns() - t0) * self.sample_rate // NS_PER_S - emitted
            if due <= 0:
                continue
            with self._lock:
                fifos = [f for i, f in self._fifos.items() if i in self._streams or f]
                mix = np.zeros((due, self.channels), np.float32)
                for f in fifos:
                    mix += self._pop(f, due)
                for i in [i for i, f in self._fifos.items() if i not in self._streams and not f]:
                    del self._fifos[i]
            np.clip(mix, -1.0, 1.0, out=mix)
            ts = t0 - delay_ns + emitted * NS_PER_S // self.sample_rate
            emitted += due
            try:
                self._on_packet(AudioPacket(mix, ts))
            except Exception as exc:
                self._on_error(exc)
                return

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(3)
        with self._lock:
            streams = list(self._streams.values())
            self._streams.clear()
        for s in streams:
            s.stop()


def open_audio(spec: AudioSourceSpec, sample_rate: int, channels: int) -> Any:
    name = audio_source_label(spec)
    dev = None
    if isinstance(spec, (MicrophoneSource, SystemAudioSource)) and spec.device is not None:
        dev = spec.device if isinstance(spec.device, str) else spec.device.id
    if isinstance(spec, MicrophoneSource):
        return PulseDeviceCapture("microphone", dev, sample_rate, channels, name)
    if isinstance(spec, SystemAudioSource):
        return PulseDeviceCapture("system", dev, sample_rate, channels, name)
    if isinstance(spec, ProcessAudioSource):
        return PulseProcessCapture(
            spec.pid, spec.include_children, spec.stream_ids, sample_rate, channels, name
        )
    raise TypeError(f"unknown audio source {spec!r}")
