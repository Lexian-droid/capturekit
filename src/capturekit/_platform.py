"""Lazy selection of the platform backend module."""

from __future__ import annotations

import sys
from types import ModuleType

from .errors import UnsupportedPlatformError


def backend() -> ModuleType:
    """Return the platform ``api`` module (imported on first use)."""
    plat: str = sys.platform  # a variable, so type checkers keep every branch reachable
    if plat == "win32":
        from ._backends.windows import api as win_api

        return win_api
    if plat.startswith("linux"):
        from ._backends.linux import api as linux_api

        return linux_api
    raise UnsupportedPlatformError(
        f"capturekit supports Windows 10/11 and Linux X11; {sys.platform!r} is not supported"
    )
