"""WASAPI capture: microphones, endpoint loopback and process loopback.

All streams are opened in shared mode as float32 / 48 kHz / stereo with
``AUTOCONVERTPCM`` so the audio engine performs format conversion and
resampling (no Python resampling except drift correction).
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
from collections.abc import Callable
from ctypes import POINTER, Structure, byref, c_int, c_long, c_uint, c_uint32, c_uint64, c_void_p
from ctypes import wintypes as wt

import numpy as np

from ..._pipeline.clock import NS_PER_S, hns_to_ns, now_ns
from ...errors import (
    AudioDeviceDisconnectedError,
    AudioDeviceError,
    CapabilityUnavailableError,
    NativeError,
    TargetNotFoundError,
)
from ...types import AudioDevice, AudioDeviceKind, AudioSession
from ..base import AudioCallback, AudioPacket
from . import _win32
from ._com import (
    GUID,
    ComApartment,
    ComObject,
    ComPtr,
    check_hr,
    co_create_instance,
    co_task_mem_free,
    hr_u32,
    prop_variant_clear,
)

log = logging.getLogger("capturekit.windows.wasapi")

CLSID_MMDeviceEnumerator = GUID.from_str("BCDE0395-E52F-467C-8E3D-C4579291692E")
IID_IMMDeviceEnumerator = GUID.from_str("A95664D2-9614-4F35-A746-DE8DB63617E6")
IID_IAudioClient = GUID.from_str("1CB9AD4C-DBFA-4c32-B178-C2F568A703B2")
IID_IAudioCaptureClient = GUID.from_str("C8ADBD64-E71E-48a0-A4DE-185C395CD317")
IID_IAudioSessionManager2 = GUID.from_str("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F")
IID_IAudioSessionControl2 = GUID.from_str("bfb7ff88-7239-4fc9-8fa2-07c950be9c6d")
IID_IActivateAudioInterfaceCompletionHandler = GUID.from_str("41D949AB-9862-444A-80F6-C261334DA5EB")
PKEY_Device_FriendlyName_fmtid = GUID.from_str("a45c254e-df1c-4efd-8020-67d146a850e0")

eRender, eCapture = 0, 1
eConsole = 0
DEVICE_STATE_ACTIVE = 1
CLSCTX_ALL = 0x17
STGM_READ = 0
AUDCLNT_SHAREMODE_SHARED = 0
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_STREAMFLAGS_EVENTCALLBACK = 0x00040000
AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM = 0x80000000
AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY = 0x08000000
AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY = 0x1
AUDCLNT_BUFFERFLAGS_SILENT = 0x2
AUDCLNT_BUFFERFLAGS_TIMESTAMP_ERROR = 0x4
AUDCLNT_E_DEVICE_INVALIDATED = -2004287484  # 0x88890004
AUDCLNT_E_SERVICE_NOT_RUNNING = -2004287472  # 0x88890010
E_NOTFOUND = -2147023728  # 0x80070490
WAVE_FORMAT_IEEE_FLOAT = 3
VT_LPWSTR = 31
VT_BLOB = 65
VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK = "VAD\\Process_Loopback"
AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK = 1
PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE = 0
PROCESS_LOOPBACK_MIN_BUILD = 20348
BUFFER_HNS = 2_000_000  # 200 ms engine buffer

# IAudioClient
_AC_Initialize = 3
_AC_Start = 10
_AC_Stop = 11
_AC_SetEventHandle = 13
_AC_GetService = 14
# IAudioCaptureClient
_CC_GetBuffer = 3
_CC_ReleaseBuffer = 4
_CC_GetNextPacketSize = 5


class WAVEFORMATEX(Structure):
    _pack_ = 1
    _fields_ = [
        ("wFormatTag", wt.WORD),
        ("nChannels", wt.WORD),
        ("nSamplesPerSec", wt.DWORD),
        ("nAvgBytesPerSec", wt.DWORD),
        ("nBlockAlign", wt.WORD),
        ("wBitsPerSample", wt.WORD),
        ("cbSize", wt.WORD),
    ]


class PROPERTYKEY(Structure):
    _fields_ = [("fmtid", GUID), ("pid", wt.DWORD)]


class PROPVARIANT(Structure):
    _fields_ = [
        ("vt", wt.WORD),
        ("r1", wt.WORD),
        ("r2", wt.WORD),
        ("r3", wt.WORD),
        ("val", c_void_p),  # pwszVal / blob cbSize (low 4 bytes)
        ("ptr2", c_void_p),  # blob pBlobData
    ]


class AUDIOCLIENT_ACTIVATION_PARAMS(Structure):
    _fields_ = [
        ("ActivationType", c_int),
        ("TargetProcessId", wt.DWORD),
        ("ProcessLoopbackMode", c_int),
    ]


_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateEventW.argtypes = (c_void_p, wt.BOOL, wt.BOOL, wt.LPCWSTR)
_k32.CreateEventW.restype = wt.HANDLE
_k32.WaitForSingleObject.argtypes = (wt.HANDLE, wt.DWORD)
_k32.WaitForSingleObject.restype = wt.DWORD
_k32.CloseHandle.argtypes = (wt.HANDLE,)
_mmdevapi = ctypes.WinDLL("Mmdevapi")
_mmdevapi.ActivateAudioInterfaceAsync.argtypes = (
    wt.LPCWSTR,
    c_void_p,
    c_void_p,
    c_void_p,
    POINTER(c_void_p),
)
_mmdevapi.ActivateAudioInterfaceAsync.restype = c_long


def windows_build() -> int:
    return int(sys.getwindowsversion().build)


def process_loopback_supported() -> bool:
    return windows_build() >= PROCESS_LOOPBACK_MIN_BUILD


def float_format(rate: int, channels: int) -> WAVEFORMATEX:
    return WAVEFORMATEX(
        WAVE_FORMAT_IEEE_FLOAT, channels, rate, rate * channels * 4, channels * 4, 32, 0
    )


# --- enumeration -------------------------------------------------------------


def _enumerator() -> ComPtr:
    return co_create_instance(
        CLSID_MMDeviceEnumerator, IID_IMMDeviceEnumerator, "IMMDeviceEnumerator"
    )


def _device_id(dev: ComPtr) -> str:
    out = c_void_p()
    dev.hcall(5, (c_void_p,), byref(out), what="IMMDevice::GetId")
    try:
        return ctypes.wstring_at(out.value) if out.value else ""
    finally:
        co_task_mem_free(out.value)


def _friendly_name(dev: ComPtr) -> str:
    store = dev.out_ptr(4, (c_uint,), STGM_READ, what="OpenPropertyStore", name="IPropertyStore")
    with store:
        key = PROPERTYKEY(PKEY_Device_FriendlyName_fmtid, 14)
        pv = PROPVARIANT()
        store.hcall(5, (c_void_p, c_void_p), byref(key), byref(pv), what="GetValue")
        try:
            return ctypes.wstring_at(pv.val) if pv.vt == VT_LPWSTR and pv.val else ""
        finally:
            prop_variant_clear(pv)


def _default_id(enum: ComPtr, flow: int) -> str | None:
    out = c_void_p()
    hr = enum.call(4, c_long, (c_int, c_int, c_void_p), flow, eConsole, byref(out))
    if hr == E_NOTFOUND or not out.value:
        return None
    check_hr(hr, "GetDefaultAudioEndpoint")
    with ComPtr(out.value, "IMMDevice") as dev:
        return _device_id(dev)


def _devices(enum: ComPtr, flow: int) -> list[ComPtr]:
    coll = enum.out_ptr(
        3,
        (c_int, c_uint),
        flow,
        DEVICE_STATE_ACTIVE,
        what="EnumAudioEndpoints",
        name="IMMDeviceCollection",
    )
    with coll:
        count = c_uint()
        coll.hcall(3, (c_void_p,), byref(count), what="GetCount")
        return [
            coll.out_ptr(4, (c_uint,), i, what="Item", name="IMMDevice") for i in range(count.value)
        ]


def list_devices() -> list[AudioDevice]:
    result: list[AudioDevice] = []
    with ComApartment(), _enumerator() as enum:
        for flow, kind in ((eCapture, AudioDeviceKind.INPUT), (eRender, AudioDeviceKind.OUTPUT)):
            default = _default_id(enum, flow)
            for dev in _devices(enum, flow):
                with dev:
                    did = _device_id(dev)
                    result.append(
                        AudioDevice(
                            did, _friendly_name(dev), kind, did == default, backend="wasapi"
                        )
                    )
    return result


def list_sessions() -> list[AudioSession]:
    """Audio sessions on all active render endpoints with their owning PIDs."""
    sessions: list[AudioSession] = []
    with ComApartment(), _enumerator() as enum:
        for dev in _devices(enum, eRender):
            with dev:
                did = _device_id(dev)
                mgr = dev.out_ptr(
                    3,
                    (c_void_p, c_uint, c_void_p),
                    byref(IID_IAudioSessionManager2),
                    CLSCTX_ALL,
                    None,
                    what="Activate(IAudioSessionManager2)",
                    name="IAudioSessionManager2",
                )
                with mgr:
                    sen = mgr.out_ptr(
                        5, (), what="GetSessionEnumerator", name="IAudioSessionEnumerator"
                    )
                    with sen:
                        count = c_int()
                        sen.hcall(3, (c_void_p,), byref(count), what="GetCount")
                        for i in range(count.value):
                            ctl = sen.out_ptr(
                                4, (c_int,), i, what="GetSession", name="IAudioSessionControl"
                            )
                            with ctl:
                                s = _session_info(ctl, did)
                                if s is not None:
                                    sessions.append(s)
    return sessions


def _session_info(ctl: ComPtr, device_id: str) -> AudioSession | None:
    ctl2 = ctl.try_query(IID_IAudioSessionControl2, "IAudioSessionControl2")
    if ctl2 is None:
        return None
    with ctl2:
        pid = wt.DWORD()
        ctl2.call(14, c_long, (c_void_p,), byref(pid))  # GetProcessId (S_FALSE: multi-process)
        state = c_int()
        ctl2.hcall(3, (c_void_p,), byref(state), what="GetState")
        name_ptr = c_void_p()
        ctl2.call(4, c_long, (c_void_p,), byref(name_ptr))
        display = ctypes.wstring_at(name_ptr.value) if name_ptr.value else ""
        co_task_mem_free(name_ptr.value)
        is_system = ctl2.call(15, c_long, ()) == 0  # IsSystemSoundsSession: S_OK = yes
    p = int(pid.value) or None
    pname = _win32.process_name(p)
    return AudioSession(
        pid=p,
        process_name=pname,
        display_name=display or ("System sounds" if is_system else (pname or "")),
        device_id=device_id,
        is_active=state.value == 1,
        backend="wasapi",
    )


# --- capture -----------------------------------------------------------------


class _CompletionHandler(ComObject):
    _methods_ = (("ActivateCompleted", c_long, (c_void_p,)),)

    def __init__(self) -> None:
        super().__init__((IID_IActivateAudioInterfaceCompletionHandler,))
        self.done = threading.Event()

    def ActivateCompleted(self, _op: int) -> None:
        self.done.set()


def _activate_process_loopback(pid: int, include_tree: bool) -> ComPtr:
    params = AUDIOCLIENT_ACTIVATION_PARAMS(
        AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK,
        pid,
        PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE if include_tree else 0,
    )
    if not include_tree:
        # Mode 1 (EXCLUDE_TARGET_PROCESS_TREE) means "everything except" —
        # it is NOT "target without children", so it is never used here.
        raise CapabilityUnavailableError(
            "WASAPI process loopback always includes child processes; "
            "include_children=False is not available on Windows"
        )
    pv = PROPVARIANT()
    pv.vt = VT_BLOB
    pv.val = ctypes.sizeof(params)
    pv.ptr2 = ctypes.addressof(params)
    handler = _CompletionHandler()
    op = c_void_p()
    try:
        hr = _mmdevapi.ActivateAudioInterfaceAsync(
            VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK, byref(IID_IAudioClient), byref(pv),
            c_void_p(handler.ptr), byref(op),
        )  # fmt: skip
        check_hr(hr, "ActivateAudioInterfaceAsync(process loopback)")
        with ComPtr(op.value, "IActivateAudioInterfaceAsyncOperation") as operation:
            if not handler.done.wait(10):
                raise AudioDeviceError("process loopback activation timed out")
            act_hr = c_long()
            client = c_void_p()
            operation.hcall(
                3, (c_void_p, c_void_p), byref(act_hr), byref(client), what="GetActivateResult"
            )
            check_hr(act_hr.value, f"process loopback activation for PID {pid}")
            return ComPtr(client.value, "IAudioClient(process loopback)")
    finally:
        handler.release()


class WasapiCapture:
    """One WASAPI capture stream running on its own MTA thread."""

    def __init__(
        self,
        kind: str,
        *,
        device_id: str | None = None,
        pid: int | None = None,
        include_tree: bool = True,
        sample_rate: int = 48_000,
        channels: int = 2,
        name: str = "",
    ) -> None:
        if kind not in ("microphone", "system", "process"):
            raise ValueError(kind)
        self.kind = kind
        self.device_id = device_id
        self.pid = pid
        self.include_tree = include_tree
        self.sample_rate = sample_rate
        self.channels = channels
        self.name = name or kind
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._init_error: BaseException | None = None
        self.device_name = ""
        self.packets = 0
        self.glitches = 0

    def _validate(self) -> None:
        if self.kind == "process":
            if not process_loopback_supported():
                raise CapabilityUnavailableError(
                    f"process audio capture needs Windows build >= {PROCESS_LOOPBACK_MIN_BUILD} "
                    f"(this is {windows_build()}); refusing to fall back to system audio"
                )
            if not self.pid or not _pid_exists(self.pid):
                raise TargetNotFoundError(f"process {self.pid} does not exist")

    def start(self, on_packet: AudioCallback, on_error: Callable[[BaseException], None]) -> None:
        self._validate()
        self._thread = threading.Thread(
            target=self._run, args=(on_packet, on_error), name=f"capturekit-audio-{self.name}",
            daemon=True,
        )  # fmt: skip
        self._thread.start()
        self._ready.wait(15)
        if self._init_error is not None:
            self._thread.join(5)
            raise self._init_error
        if not self._ready.is_set():
            self._stop.set()
            raise AudioDeviceError(f"audio source {self.name!r} did not start")

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(5)
            if self._thread.is_alive():
                log.warning("audio thread %r did not stop within 5 s", self.name)
                return  # keep the reference; it is a daemon thread
            self._thread = None

    # -- thread ------------------------------------------------------------
    def _open_client(self) -> ComPtr:
        if self.kind == "process":
            assert self.pid is not None
            self.device_name = f"process {self.pid} ({_win32.process_name(self.pid)})"
            return _activate_process_loopback(self.pid, self.include_tree)
        flow = eCapture if self.kind == "microphone" else eRender
        with _enumerator() as enum:
            out = c_void_p()
            if self.device_id:
                hr = enum.call(5, c_long, (wt.LPCWSTR, c_void_p), self.device_id, byref(out))
                if hr < 0:
                    raise TargetNotFoundError(f"audio device {self.device_id!r} not found")
            else:
                hr = enum.call(4, c_long, (c_int, c_int, c_void_p), flow, eConsole, byref(out))
                if hr == E_NOTFOUND or not out.value:
                    raise TargetNotFoundError(
                        f"no default {'input' if flow == eCapture else 'output'} audio device"
                    )
                check_hr(hr, "GetDefaultAudioEndpoint")
            with ComPtr(out.value, "IMMDevice") as dev:
                self.device_name = _friendly_name(dev)
                return dev.out_ptr(
                    3,
                    (c_void_p, c_uint, c_void_p),
                    byref(IID_IAudioClient),
                    CLSCTX_ALL,
                    None,
                    what="Activate(IAudioClient)",
                    name="IAudioClient",
                )

    def _open_stream(self) -> tuple[ComPtr, ComPtr, int]:
        client = self._open_client()
        capture: ComPtr | None = None
        event = None
        try:
            flags = (
                AUDCLNT_STREAMFLAGS_EVENTCALLBACK
                | AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM
                | AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY
            )
            if self.kind != "microphone":
                flags |= AUDCLNT_STREAMFLAGS_LOOPBACK
            fmt = float_format(self.sample_rate, self.channels)
            client.hcall(
                _AC_Initialize,
                (c_int, c_uint, ctypes.c_int64, ctypes.c_int64, c_void_p, c_void_p),
                AUDCLNT_SHAREMODE_SHARED,
                flags,
                BUFFER_HNS,
                0,
                byref(fmt),
                None,
                what=f"IAudioClient::Initialize({self.name})",
            )
            event = _k32.CreateEventW(None, False, False, None)
            client.hcall(_AC_SetEventHandle, (wt.HANDLE,), event, what="SetEventHandle")
            capture = client.out_ptr(
                _AC_GetService,
                (c_void_p,),
                byref(IID_IAudioCaptureClient),
                what="GetService(IAudioCaptureClient)",
                name="IAudioCaptureClient",
            )
            client.hcall(_AC_Start, (), what="IAudioClient::Start")
            return client, capture, event
        except BaseException:
            if capture is not None:
                capture.release()
            client.release()
            if event:
                _k32.CloseHandle(event)
            raise

    def _run(self, on_packet: AudioCallback, on_error: Callable[[BaseException], None]) -> None:
        with ComApartment():
            try:
                client, capture, event = self._open_stream()
            except NativeError as exc:
                self._init_error = _translate(exc, self.name)
                self._ready.set()
                return
            except BaseException as exc:
                self._init_error = exc
                self._ready.set()
                return
            self._ready.set()
            try:
                self._loop(capture, event, on_packet)
            except NativeError as exc:
                on_error(_translate(exc, self.name))
            except BaseException as exc:
                on_error(exc)
            finally:
                client.call(_AC_Stop, c_long, ())
                capture.release()
                client.release()
                _k32.CloseHandle(event)

    def _loop(self, capture: ComPtr, event: int, on_packet: AudioCallback) -> None:
        data = c_void_p()
        frames = c_uint32()
        flags = wt.DWORD()
        devpos = c_uint64()
        qpcpos = c_uint64()
        size = c_uint32()
        bpf = 4 * self.channels
        get_next = capture.method(_CC_GetNextPacketSize, c_long, (c_void_p,))
        get_buf = capture.method(
            _CC_GetBuffer, c_long, (c_void_p, c_void_p, c_void_p, c_void_p, c_void_p)
        )
        rel_buf = capture.method(_CC_ReleaseBuffer, c_long, (c_uint32,))
        ptr = capture.ptr
        while not self._stop.is_set():
            _k32.WaitForSingleObject(event, 50)
            while True:
                hr = get_next(ptr, byref(size))
                if hr < 0:
                    raise NativeError("GetNextPacketSize", hr)
                if size.value == 0:
                    break
                hr = get_buf(
                    ptr, byref(data), byref(frames), byref(flags), byref(devpos), byref(qpcpos)
                )
                if hr < 0:
                    raise NativeError("GetBuffer", hr)
                n = frames.value
                f = flags.value
                if f & AUDCLNT_BUFFERFLAGS_SILENT or not data.value:
                    arr = np.zeros((n, self.channels), dtype=np.float32)
                else:
                    raw = (ctypes.c_ubyte * (n * bpf)).from_address(data.value)
                    arr = np.frombuffer(raw, dtype=np.float32).reshape(n, self.channels).copy()
                rel_buf(ptr, n)
                if qpcpos.value and not f & AUDCLNT_BUFFERFLAGS_TIMESTAMP_ERROR:
                    ts = hns_to_ns(qpcpos.value)
                else:
                    ts = now_ns() - n * NS_PER_S // self.sample_rate
                disc = bool(f & AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY)
                if disc:
                    self.glitches += 1
                self.packets += 1
                if n:
                    on_packet(AudioPacket(arr, ts, disc))


def _pid_exists(pid: int) -> bool:
    h = _win32.kernel32.OpenProcess(_win32.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if h:
        _win32.kernel32.CloseHandle(h)
        return True
    return ctypes.get_last_error() == 5  # access denied → exists


def _translate(exc: NativeError, name: str) -> BaseException:
    code = exc.code or 0
    if code == AUDCLNT_E_DEVICE_INVALIDATED:
        return AudioDeviceDisconnectedError(f"audio device for {name!r} was removed/invalidated")
    if code == AUDCLNT_E_SERVICE_NOT_RUNNING:
        return AudioDeviceError("Windows Audio service is not running")
    return AudioDeviceError(f"{name}: {exc} (0x{hr_u32(code):08X})")
