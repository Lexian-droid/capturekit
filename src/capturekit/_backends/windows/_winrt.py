"""WinRT ABI helpers: HSTRING, activation factories, pinterface IIDs, delegates."""

from __future__ import annotations

import ctypes
import uuid
from collections.abc import Callable
from ctypes import POINTER, byref, c_int64, c_long, c_uint, c_void_p, c_wchar_p

from ._com import GUID, ComObject, ComPtr, check_hr, ensure_mta_usage

_combase = ctypes.WinDLL("combase")
_combase.RoInitialize.argtypes = (c_uint,)
_combase.RoInitialize.restype = c_long
_combase.RoUninitialize.argtypes = ()
_combase.RoUninitialize.restype = None
_combase.WindowsCreateString.argtypes = (c_wchar_p, c_uint, POINTER(c_void_p))
_combase.WindowsCreateString.restype = c_long
_combase.WindowsDeleteString.argtypes = (c_void_p,)
_combase.WindowsDeleteString.restype = c_long
_combase.WindowsGetStringRawBuffer.argtypes = (c_void_p, POINTER(c_uint))
_combase.WindowsGetStringRawBuffer.restype = c_wchar_p
_combase.RoGetActivationFactory.argtypes = (c_void_p, c_void_p, POINTER(c_void_p))
_combase.RoGetActivationFactory.restype = c_long

RO_INIT_MULTITHREADED = 1
RPC_E_CHANGED_MODE = -2147417850


class RoApartment:
    """Initialise the Windows Runtime (MTA) on the current thread."""

    def __enter__(self) -> RoApartment:
        ensure_mta_usage()
        hr = _combase.RoInitialize(RO_INIT_MULTITHREADED)
        self._uninit = hr >= 0
        if hr < 0 and hr != RPC_E_CHANGED_MODE:
            check_hr(hr, "RoInitialize")
        return self

    def __exit__(self, *exc: object) -> None:
        if self._uninit:
            _combase.RoUninitialize()


class HString:
    def __init__(self, text: str) -> None:
        self.handle = c_void_p()
        check_hr(
            _combase.WindowsCreateString(text, len(text), byref(self.handle)),
            "WindowsCreateString",
        )

    def close(self) -> None:
        if self.handle:
            _combase.WindowsDeleteString(self.handle)
            self.handle = c_void_p()

    def __enter__(self) -> HString:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def hstring_to_str(handle: int | None) -> str:
    """Read and delete an HSTRING returned by a WinRT method."""
    if not handle:
        return ""
    length = c_uint()
    text = _combase.WindowsGetStringRawBuffer(handle, byref(length)) or ""
    _combase.WindowsDeleteString(handle)
    return text[: length.value]


def activation_factory(class_name: str, iid: GUID) -> ComPtr:
    with HString(class_name) as hs:
        out = c_void_p()
        check_hr(
            _combase.RoGetActivationFactory(hs.handle, byref(iid), byref(out)),
            f"RoGetActivationFactory({class_name})",
        )
    return ComPtr(out.value, f"{class_name} factory")


_PINTERFACE_NS = uuid.UUID("11f47ad5-7b73-42c0-abae-878b1e16adee")
_TYPED_EVENT_HANDLER = "{9de1c534-6ae1-11e0-84e1-18a905bcc53f}"


def pinterface_iid(signature: str) -> GUID:
    """IID of a parameterised WinRT interface (UUIDv5 over the signature)."""
    return GUID.from_buffer_copy(uuid.uuid5(_PINTERFACE_NS, signature).bytes_le)


def typed_event_handler_iid(sender_class: str, sender_default_iid: str) -> GUID:
    """IID of ``TypedEventHandler<sender_class, IInspectable>``."""
    sig = (
        f"pinterface({_TYPED_EVENT_HANDLER};"
        f"rc({sender_class};{{{sender_default_iid.lower()}}});cinterface(IInspectable))"
    )
    return pinterface_iid(sig)


class EventDelegate(ComObject):
    """``TypedEventHandler<TSender, IInspectable>`` implemented in Python.

    ``callback(sender_ptr, args_ptr)`` runs on a WinRT thread-pool thread; keep
    it short (signal an event).
    """

    _methods_ = (("Invoke", c_long, (c_void_p, c_void_p)),)

    def __init__(self, iid: GUID, callback: Callable[[int | None, int | None], None]) -> None:
        self._callback = callback
        super().__init__((iid,))

    def Invoke(self, sender: int | None, args: int | None) -> None:
        self._callback(sender, args)


class EventRegistration:
    """Holds a delegate registered with ``add_X``; ``remove()`` unregisters."""

    def __init__(self, source: ComPtr, remove_index: int, token: int, delegate: ComObject):
        self._source = source
        self._remove_index = remove_index
        self._token = token
        self._delegate: ComObject | None = delegate

    @staticmethod
    def add(
        source: ComPtr, add_index: int, remove_index: int, delegate: ComObject, what: str
    ) -> EventRegistration:
        token = c_int64()
        source.hcall(
            add_index, (c_void_p, c_void_p), c_void_p(delegate.ptr), byref(token), what=what
        )
        return EventRegistration(source, remove_index, token.value, delegate)

    def remove(self) -> None:
        if self._delegate is not None:
            try:
                self._source.call(self._remove_index, c_long, (c_int64,), self._token)
            finally:
                self._delegate.release()
                self._delegate = None


def close_winrt(obj: ComPtr | None) -> None:
    """``IClosable::Close`` then release (ignores objects without IClosable)."""
    if obj is None:
        return
    closable = obj.try_query(IID_IClosable, "IClosable")
    if closable is not None:
        try:
            closable.call(6, c_long, ())
        finally:
            closable.release()
    obj.release()


IID_IClosable = GUID.from_str("30D5A829-7FA4-4026-83BB-D75BAE4EA99E")
