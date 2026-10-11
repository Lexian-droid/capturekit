"""Minimal COM support on top of ctypes.

* :class:`ComPtr` calls interface methods by vtable index (indices are listed
  in ``.claude/skills/windows-capture-backend/reference/vtables.md``).
* :class:`ComObject` implements COM interfaces in Python (event delegates,
  completion handlers).
"""

from __future__ import annotations

import collections
import ctypes
import logging
import sys
import threading
import uuid
from collections.abc import Callable, Sequence
from ctypes import POINTER, Structure, byref, c_long, c_ubyte, c_ulong, c_ushort, c_void_p
from typing import Any, ClassVar

from ...errors import NativeError

log = logging.getLogger("capturekit.windows")

if sys.platform != "win32":  # pragma: no cover - guarded by _platform
    raise ImportError("capturekit._backends.windows requires Windows")

WINFUNCTYPE = ctypes.WINFUNCTYPE

S_OK = 0
S_FALSE = 1
E_NOINTERFACE = -2147467262  # 0x80004002
E_POINTER = -2147467261  # 0x80004003
E_FAIL = -2147467259  # 0x80004005
RPC_E_CHANGED_MODE = -2147417850  # 0x80010106


class GUID(Structure):
    _fields_ = [
        ("Data1", c_ulong),
        ("Data2", c_ushort),
        ("Data3", c_ushort),
        ("Data4", c_ubyte * 8),
    ]

    @classmethod
    def from_str(cls, text: str) -> GUID:
        return cls.from_buffer_copy(uuid.UUID(text).bytes_le)

    def to_uuid(self) -> uuid.UUID:
        return uuid.UUID(bytes_le=bytes(self))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, GUID) and bytes(self) == bytes(other)

    def __hash__(self) -> int:
        return hash(bytes(self))

    def __repr__(self) -> str:
        return f"GUID('{self.to_uuid()}')"


IID_IUnknown = GUID.from_str("00000000-0000-0000-C000-000000000046")
IID_IAgileObject = GUID.from_str("94ea2b94-e9cc-49e0-c0ff-ee64ca8f5b90")
IID_IInspectable = GUID.from_str("AF86E2E0-B12D-4c6a-9C5A-D7AA65101E90")


def hr_u32(hr: int) -> int:
    return hr & 0xFFFFFFFF


def check_hr(hr: int, what: str) -> None:
    if hr < 0:
        raise NativeError(what, hr)


_proto_cache: dict[tuple[Any, tuple[Any, ...]], Any] = {}


def _prototype(restype: Any, argtypes: tuple[Any, ...]) -> Any:
    key = (restype, argtypes)
    proto = _proto_cache.get(key)
    if proto is None:
        proto = WINFUNCTYPE(restype, c_void_p, *argtypes)
        _proto_cache[key] = proto
    return proto


class ComPtr:
    """Owning reference to a COM interface pointer.

    Release is explicit (``release()`` / context manager). ``__del__`` is a
    safety net only; correct code releases deterministically on the owning
    thread.
    """

    __slots__ = ("_ptr", "name")

    def __init__(self, ptr: int | None, name: str = "") -> None:
        if not ptr:
            raise NativeError(f"null {name or 'COM'} pointer")
        self._ptr: int | None = int(ptr)
        self.name = name

    @property
    def ptr(self) -> int:
        if self._ptr is None:
            raise NativeError(f"use of released {self.name or 'COM'} pointer")
        return self._ptr

    @property
    def as_param(self) -> c_void_p:
        return c_void_p(self.ptr)

    def method(self, index: int, restype: Any, argtypes: Sequence[Any]) -> Any:
        ptr = self.ptr
        vtbl = c_void_p.from_address(ptr).value
        assert vtbl is not None
        fn_addr = c_void_p.from_address(vtbl + index * ctypes.sizeof(c_void_p)).value
        return _prototype(restype, tuple(argtypes))(fn_addr)

    def call(self, index: int, restype: Any, argtypes: Sequence[Any], *args: Any) -> Any:
        return self.method(index, restype, argtypes)(self.ptr, *args)

    def hcall(self, index: int, argtypes: Sequence[Any], *args: Any, what: str = "") -> int:
        """Call a method returning HRESULT; raise on failure, return the HRESULT."""
        hr: int = self.method(index, c_long, argtypes)(self.ptr, *args)
        if hr < 0:
            raise NativeError(what or f"{self.name}[{index}]", hr)
        return hr

    def out_ptr(
        self, index: int, argtypes: Sequence[Any], *args: Any, what: str = "", name: str = ""
    ) -> ComPtr:
        """Call a method whose last parameter is ``void**`` and wrap the result."""
        out = c_void_p()
        self.hcall(index, (*argtypes, c_void_p), *args, byref(out), what=what)
        return ComPtr(out.value, name or what)

    def query(self, iid: GUID, name: str = "") -> ComPtr:
        out = c_void_p()
        hr = self.call(0, c_long, (c_void_p, c_void_p), byref(iid), byref(out))
        check_hr(hr, f"QueryInterface({name or iid!r})")
        return ComPtr(out.value, name)

    def try_query(self, iid: GUID, name: str = "") -> ComPtr | None:
        out = c_void_p()
        hr = self.call(0, c_long, (c_void_p, c_void_p), byref(iid), byref(out))
        if hr < 0 or not out.value:
            return None
        return ComPtr(out.value, name)

    def add_ref(self) -> ComPtr:
        self.call(1, c_ulong, ())
        return ComPtr(self.ptr, self.name)

    def release(self) -> None:
        if self._ptr is not None:
            ptr, self._ptr = self._ptr, None
            vtbl = c_void_p.from_address(ptr).value
            assert vtbl is not None
            fn = c_void_p.from_address(vtbl + 2 * ctypes.sizeof(c_void_p)).value
            _prototype(c_ulong, ())(fn)(ptr)

    def __enter__(self) -> ComPtr:
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()

    def __del__(self) -> None:
        if self._ptr is not None and not sys.is_finalizing():
            try:
                self.release()
            except Exception:  # pragma: no cover - last-resort cleanup
                pass

    def __repr__(self) -> str:
        return f"<ComPtr {self.name} {self._ptr and hex(self._ptr)}>"


def release_all(*ptrs: ComPtr | None) -> None:
    for p in ptrs:
        if p is not None:
            p.release()


class _ComObjectStruct(Structure):
    _fields_ = [("lpVtbl", c_void_p)]


class ComObject:
    """Base class for COM objects implemented in Python.

    Subclasses set ``_iids_`` (interfaces they implement besides IUnknown /
    IAgileObject) and ``_methods_``: a list of ``(name, restype, argtypes)``
    for the vtable slots after IUnknown, each implemented as a Python method
    taking the raw arguments (without ``this``).

    The object keeps itself alive while its COM refcount is > 0, so native
    code can never call into freed memory. Exceptions raised by methods are
    logged and converted to ``E_FAIL``.
    """

    _iids_: ClassVar[Sequence[GUID]] = ()
    _methods_: ClassVar[Sequence[tuple[str, Any, Sequence[Any]]]] = ()
    _alive: ClassVar[set[ComObject]] = set()
    _alive_lock: ClassVar[threading.Lock] = threading.Lock()
    _graveyard: ClassVar[collections.deque[ComObject]] = collections.deque(maxlen=256)

    def __init__(self, extra_iids: Sequence[GUID] = ()) -> None:
        self._refs = 1
        self._ref_lock = threading.Lock()
        qi = WINFUNCTYPE(c_long, c_void_p, POINTER(GUID), POINTER(c_void_p))(self._qi)
        addref = WINFUNCTYPE(c_ulong, c_void_p)(self._addref)
        release = WINFUNCTYPE(c_ulong, c_void_p)(self._release)
        funcs: list[Any] = [qi, addref, release]
        for name, restype, argtypes in self._methods_:
            impl = getattr(self, name)
            funcs.append(WINFUNCTYPE(restype, c_void_p, *argtypes)(self._guard(impl, restype)))
        self._funcs = funcs  # keep callbacks alive
        self._vtbl = (c_void_p * len(funcs))(*[ctypes.cast(f, c_void_p) for f in funcs])
        self._struct = _ComObjectStruct(ctypes.cast(self._vtbl, c_void_p))
        self._supported = {bytes(IID_IUnknown), bytes(IID_IAgileObject)} | {
            bytes(i) for i in (*self._iids_, *extra_iids)
        }
        with ComObject._alive_lock:
            ComObject._alive.add(self)

    @property
    def ptr(self) -> int:
        return ctypes.addressof(self._struct)

    def _guard(self, fn: Callable[..., Any], restype: Any) -> Callable[..., Any]:
        def wrapper(_this: int, *args: Any) -> Any:
            try:
                res = fn(*args)
                return S_OK if res is None and restype is c_long else res
            except Exception:
                log.exception("exception in COM callback %s", fn.__name__)
                return E_FAIL if restype is c_long else 0

        return wrapper

    def _qi(self, _this: int, riid: Any, ppv: Any) -> int:
        if not ppv:
            return E_POINTER
        if bytes(riid.contents) in self._supported:
            ppv[0] = self.ptr
            self._addref(_this)
            return S_OK
        ppv[0] = None
        return E_NOINTERFACE

    def _addref(self, _this: int) -> int:
        with self._ref_lock:
            self._refs += 1
            return self._refs

    def _release(self, _this: int) -> int:
        with self._ref_lock:
            self._refs -= 1
            refs = self._refs
        if refs == 0:
            with ComObject._alive_lock:
                ComObject._alive.discard(self)
                # Do not free now: this method may be running inside our own
                # ctypes thunk (native Release call); freeing the thunk before
                # it returns crashes the process. Keep dead objects in a
                # bounded graveyard so they die long after their last call.
                ComObject._graveyard.append(self)
        return refs

    def release(self) -> None:
        """Drop the Python-side initial reference."""
        self._release(0)


# --- COM apartment ---------------------------------------------------------

_ole32 = ctypes.WinDLL("ole32", use_last_error=True)
_ole32.CoInitializeEx.argtypes = (c_void_p, ctypes.c_uint)
_ole32.CoInitializeEx.restype = c_long
_ole32.CoUninitialize.argtypes = ()
_ole32.CoUninitialize.restype = None
_ole32.CoCreateInstance.argtypes = (c_void_p, c_void_p, ctypes.c_uint, c_void_p, c_void_p)
_ole32.CoCreateInstance.restype = c_long
_ole32.CoTaskMemFree.argtypes = (c_void_p,)
_ole32.CoTaskMemFree.restype = None
_ole32.PropVariantClear.argtypes = (c_void_p,)
_ole32.PropVariantClear.restype = c_long

_ole32.CoIncrementMTAUsage.argtypes = (c_void_p,)
_ole32.CoIncrementMTAUsage.restype = c_long

COINIT_MULTITHREADED = 0x0
CLSCTX_ALL = 0x17

_mta_cookie = c_void_p()
_mta_lock = threading.Lock()


def ensure_mta_usage() -> None:
    """Keep the process MTA alive for the rest of the process lifetime.

    Without this, the last MTA thread's CoUninitialize/RoUninitialize tears
    down the MTA and may unload WinRT capture DLLs while thread-pool
    callbacks are still pending (reproduced: access violation shortly after
    closing a WGC session whose window had been destroyed). The cookie is
    intentionally never released.
    """
    with _mta_lock:
        if not _mta_cookie.value:
            check_hr(_ole32.CoIncrementMTAUsage(byref(_mta_cookie)), "CoIncrementMTAUsage")


class ComApartment:
    """Context manager: join the MTA on this thread for the duration."""

    def __enter__(self) -> ComApartment:
        ensure_mta_usage()
        hr = _ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
        # S_FALSE = already initialised (balanced by CoUninitialize too).
        # RPC_E_CHANGED_MODE = caller's thread is STA: usable, but don't uninit.
        self._uninit = hr >= 0
        if hr < 0 and hr != RPC_E_CHANGED_MODE:
            check_hr(hr, "CoInitializeEx")
        return self

    def __exit__(self, *exc: object) -> None:
        if self._uninit:
            _ole32.CoUninitialize()


def co_create_instance(clsid: GUID, iid: GUID, name: str = "") -> ComPtr:
    out = c_void_p()
    check_hr(
        _ole32.CoCreateInstance(byref(clsid), None, CLSCTX_ALL, byref(iid), byref(out)),
        f"CoCreateInstance({name})",
    )
    return ComPtr(out.value, name)


def co_task_mem_free(ptr: int | None) -> None:
    if ptr:
        _ole32.CoTaskMemFree(ptr)


def prop_variant_clear(pv: Any) -> None:
    _ole32.PropVariantClear(byref(pv))
