"""A single thread that owns a video backend and executes calls on it."""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from concurrent.futures import Future
from typing import Any, TypeVar

from . import _platform
from ._backends.base import VideoBackend
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

    def start(self, timeout_s: float = 15.0) -> VideoBackend:
        self._thread.start()
        return self._ready.result(timeout_s)

    def _run(self) -> None:
        api = _platform.backend()
        try:
            with api.thread_context():
                backend: VideoBackend = api.open_video(self._source)
                try:
                    backend.start()
                except BaseException as exc:
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

    def call(self, fn: Callable[[VideoBackend], T], timeout_s: float | None = None) -> T:
        if not self._thread.is_alive():
            raise RuntimeError("capture backend thread is not running")
        fut: Future[T] = Future()
        self._calls.put((fn, fut))
        return fut.result(timeout_s)

    def stop(self, timeout_s: float = 10.0) -> None:
        if self._thread.is_alive():
            self._calls.put(None)
            self._thread.join(timeout_s)
