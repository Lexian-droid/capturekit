"""Extra FFmpeg inputs: Windows named pipes and POSIX FIFOs.

The pipe is created before FFmpeg starts; a writer thread connects (blocking
until FFmpeg opens it) and streams data. ``abort()`` unblocks a writer stuck in
connect by connecting to the pipe ourselves.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import uuid

from ..errors import EncoderError


class OutputPipe:
    path: str

    def connect(self) -> None:
        raise NotImplementedError

    def write(self, data: bytes | memoryview) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def abort(self) -> None:
        raise NotImplementedError


if sys.platform == "win32":
    import ctypes
    from ctypes import byref, c_void_p
    from ctypes import wintypes as wt

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _adv = ctypes.WinDLL("advapi32", use_last_error=True)
    _k32.CreateNamedPipeW.argtypes = (
        wt.LPCWSTR,
        wt.DWORD,
        wt.DWORD,
        wt.DWORD,
        wt.DWORD,
        wt.DWORD,
        wt.DWORD,
        c_void_p,
    )
    _k32.CreateNamedPipeW.restype = wt.HANDLE
    _k32.ConnectNamedPipe.argtypes = (wt.HANDLE, c_void_p)
    _k32.ConnectNamedPipe.restype = wt.BOOL
    _k32.WriteFile.argtypes = (wt.HANDLE, c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), c_void_p)
    _k32.WriteFile.restype = wt.BOOL
    _k32.FlushFileBuffers.argtypes = (wt.HANDLE,)
    _k32.DisconnectNamedPipe.argtypes = (wt.HANDLE,)
    _k32.CloseHandle.argtypes = (wt.HANDLE,)
    _k32.CreateFileW.argtypes = (
        wt.LPCWSTR,
        wt.DWORD,
        wt.DWORD,
        c_void_p,
        wt.DWORD,
        wt.DWORD,
        wt.HANDLE,
    )
    _k32.CreateFileW.restype = wt.HANDLE
    _k32.LocalFree.argtypes = (c_void_p,)
    _adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = (
        wt.LPCWSTR,
        wt.DWORD,
        ctypes.POINTER(c_void_p),
        c_void_p,
    )
    _adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wt.BOOL

    class SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("nLength", wt.DWORD),
            ("lpSecurityDescriptor", c_void_p),
            ("bInheritHandle", wt.BOOL),
        ]

    INVALID_HANDLE_VALUE = wt.HANDLE(-1).value
    PIPE_ACCESS_OUTBOUND = 0x2
    FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000
    PIPE_TYPE_BYTE = 0x0
    PIPE_WAIT = 0x0
    PIPE_REJECT_REMOTE_CLIENTS = 0x8
    ERROR_PIPE_CONNECTED = 535
    GENERIC_READ = 0x80000000
    OPEN_EXISTING = 3
    #: Owner (current user), SYSTEM and Administrators only — other local
    #: users cannot connect and read the recorded audio.
    _PIPE_SDDL = "D:P(A;;GA;;;OW)(A;;GA;;;SY)(A;;GA;;;BA)"

    class NamedPipe(OutputPipe):
        def __init__(self, label: str, buffer_size: int = 1 << 20) -> None:
            self.path = rf"\\.\pipe\capturekit-{uuid.uuid4().hex}-{label}"
            sd = c_void_p()
            if not _adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                self._sddl(), 1, byref(sd), None
            ):
                raise EncoderError(f"security descriptor failed ({ctypes.get_last_error()})")
            sa = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), sd, False)
            try:
                self._h = _k32.CreateNamedPipeW(
                    self.path,
                    PIPE_ACCESS_OUTBOUND | FILE_FLAG_FIRST_PIPE_INSTANCE,
                    PIPE_TYPE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
                    1, buffer_size, buffer_size, 0, byref(sa),
                )  # fmt: skip
            finally:
                _k32.LocalFree(sd)
            if self._h in (None, INVALID_HANDLE_VALUE):
                raise EncoderError(f"CreateNamedPipe failed ({ctypes.get_last_error()})")
            self._lock = threading.Lock()
            self._connected = False
            self._closed = False

        @staticmethod
        def _sddl() -> str:
            return _PIPE_SDDL

        def connect(self) -> None:
            ok = _k32.ConnectNamedPipe(self._h, None)
            if not ok:
                err = ctypes.get_last_error()
                if err != ERROR_PIPE_CONNECTED:
                    raise EncoderError(f"ConnectNamedPipe failed ({err})")
            self._connected = True

        def write(self, data: bytes | memoryview) -> None:
            mv = memoryview(data).cast("B")
            written = wt.DWORD()
            while mv:
                chunk = mv[: 1 << 20]
                buf = (
                    (ctypes.c_char * len(chunk)).from_buffer_copy(chunk)
                    if chunk.readonly
                    else (ctypes.c_char * len(chunk)).from_buffer(chunk)
                )
                if not _k32.WriteFile(self._h, buf, len(chunk), byref(written), None):
                    raise EncoderError(
                        f"audio pipe write failed ({ctypes.get_last_error()}); "
                        "FFmpeg closed its input"
                    )
                mv = mv[written.value :]

        def close(self) -> None:
            with self._lock:
                if self._closed:
                    return
                self._closed = True
                # Plain CloseHandle: the client still reads buffered data, then
                # gets EOF. FlushFileBuffers would block until FFmpeg reads
                # everything (forever if FFmpeg is stalled).
                _k32.CloseHandle(self._h)

        def abort(self) -> None:
            """Unblock a pending ``connect()`` by connecting as a client."""
            h = _k32.CreateFileW(self.path, GENERIC_READ, 0, None, OPEN_EXISTING, 0, None)
            if h not in (None, INVALID_HANDLE_VALUE):
                _k32.CloseHandle(h)

    def create_pipe(label: str) -> OutputPipe:
        return NamedPipe(label)

else:

    class Fifo(OutputPipe):
        _dir: str | None = None

        def __init__(self, label: str) -> None:
            if Fifo._dir is None or not os.path.isdir(Fifo._dir):
                Fifo._dir = tempfile.mkdtemp(prefix="capturekit-")  # mode 0700
            self.path = os.path.join(Fifo._dir, f"{uuid.uuid4().hex}-{label}.f32")
            os.mkfifo(self.path, 0o600)
            self._fd: int | None = None

        def connect(self) -> None:
            self._fd = os.open(self.path, os.O_WRONLY)  # blocks until FFmpeg opens it

        def write(self, data: bytes | memoryview) -> None:
            assert self._fd is not None
            mv = memoryview(data).cast("B")
            try:
                while mv:
                    mv = mv[os.write(self._fd, mv) :]
            except BrokenPipeError as exc:
                raise EncoderError("FFmpeg closed its audio input") from exc

        def close(self) -> None:
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None
            try:
                os.unlink(self.path)
            except FileNotFoundError:
                pass

        def abort(self) -> None:
            try:
                fd = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK)
                os.close(fd)
            except OSError:
                pass

    def create_pipe(label: str) -> OutputPipe:
        return Fifo(label)
