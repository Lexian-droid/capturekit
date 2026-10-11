"""A single thread that owns a video backend and executes calls on it."""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FuturesTimeout
from typing import Any, TypeVar

from . import _platform
from ._backends.base import VideoBackend
from .errors import CaptureError, CaptureTimeoutError
from .sources import VideoSource

T = TypeVar("T")


class BackendThread:
    """Creates, starts and drives a backend on one dedicated thread.

    Native video APIs (COM/WinRT on Windows, an Xlib ``Display`` on Linux) are
    used from this thread only; callers interact via :meth:`call`.
    """

    def __init__(self, source: VideoSource, name: str = "capturekit-video") -> None:
        self._source = source
        self._calls: queue.Queue[tuple[Callable[[VideoBackend], Any], Future[Any]] | None] = (
            queue.Queue()
        )
        self._ready: Future[VideoBackend] = Future()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._closed = threading.Event()

    def start(self, timeout_s: float = 15.0) -> VideoBackend:
        self._thread.start()
        try:
            return self._ready.result(timeout_s)
        except FuturesTimeout:
            # The backend may still finish starting later: make it exit and
            # close itself instead of leaking a live capture session.
            self._calls.put(None)
            raise CaptureTimeoutError(
                f"capture backend did not start within {timeout_s}s"
            ) from None

    def _run(self) -> None:
        api = _platform.backend()
        try:
            with api.thread_context():
                backend: VideoBackend = api.open_video(self._source)
                try:
                    backend.start()
                except BaseException as exc:
                    backend.close()
                    self._ready.set_exception(exc)
                    return
                self._ready.set_result(backend)
                try:
                    while True:
                        item = self._calls.get()
                        if item is None:
                            break
                        fn, fut = item
                        if not fut.set_running_or_notify_cancel():
                            continue
                        try:
                            fut.set_result(fn(backend))
                        except BaseException as exc:
                            fut.set_exception(exc)
                finally:
                    backend.close()
        except BaseException as exc:
            if not self._ready.done():
                self._ready.set_exception(exc)
        finally:
            self._closed.set()
            # Never leave a caller waiting on a call that will not run.
            while True:
                try:
                    pending = self._calls.get_nowait()
                except queue.Empty:
                    break
                if pending is not None and not pending[1].done():
                    pending[1].set_exception(CaptureError("capture was closed"))

    def call(self, fn: Callable[[VideoBackend], T], timeout_s: float | None = None) -> T:
        if self._closed.is_set() or not self._thread.is_alive():
            raise CaptureError("capture is closed")
        fut: Future[T] = Future()
        self._calls.put((fn, fut))
        if self._closed.is_set() and not fut.done():  # raced with shutdown
            fut.set_exception(CaptureError("capture was closed"))
        return fut.result(timeout_s)

    def stop(self, timeout_s: float = 10.0) -> None:
        if self._thread.is_alive():
            self._calls.put(None)
            self._thread.join(timeout_s)
