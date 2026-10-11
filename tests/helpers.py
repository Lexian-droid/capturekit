"""Test helpers: window manipulation (Windows) and media inspection."""

from __future__ import annotations

import re
import subprocess
import sys
import time

import numpy as np
import numpy.typing as npt

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes as wt

    _u32 = ctypes.WinDLL("user32")
    _u32.SetWindowPos.argtypes = (
        wt.HWND,
        wt.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wt.UINT,
    )
    _u32.ShowWindow.argtypes = (wt.HWND, ctypes.c_int)
    _u32.PostMessageW.argtypes = (wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
    _u32.IsWindow.argtypes = (wt.HWND,)

    SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE = 0x2, 0x4, 0x10

    def resize_window(hwnd: int, w: int, h: int) -> None:
        _u32.SetWindowPos(hwnd, None, 0, 0, w, h, SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE)

    def minimize_window(hwnd: int) -> None:
        _u32.ShowWindow(hwnd, 6)  # SW_MINIMIZE

    def restore_window(hwnd: int) -> None:
        _u32.ShowWindow(hwnd, 9)  # SW_RESTORE

    def close_window(hwnd: int, timeout: float = 5.0) -> None:
        _u32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
        end = time.time() + timeout
        while _u32.IsWindow(hwnd) and time.time() < end:
            time.sleep(0.05)


def color_fraction(img: npt.NDArray[np.uint8], rgb: tuple[int, int, int], tol: int = 3) -> float:
    """Fraction of pixels in a BGRA image matching ``rgb`` within ``tol``."""
    bgr = np.array(rgb[::-1], dtype=np.int16)
    diff = np.abs(img[..., :3].astype(np.int16) - bgr)
    return float((diff <= tol).all(axis=-1).mean())


def media_info(ffmpeg: str, path: str) -> dict[str, object]:
    """Duration, stream count and per-stream frame/sample counts via FFmpeg."""
    res = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", path], capture_output=True, text=True, check=False
    )
    text = res.stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", text)
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else -1.0
    streams = re.findall(r"Stream #0:\d+.*?: (Video|Audio): (\S+)", text)
    return {"duration": dur, "streams": streams, "raw": text}


def count_video_frames(ffmpeg: str, path: str) -> int:
    res = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", path, "-map", "0:v:0", "-c", "copy", "-f", "null", "-"],
        capture_output=True,
        text=True,
        check=False,
    )
    found = re.findall(r"frame=\s*(\d+)", res.stderr)
    return int(found[-1]) if found else -1


def decode_audio(ffmpeg: str, path: str, stream: int) -> npt.NDArray[np.float32]:
    res = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            path,
            "-map",
            f"0:a:{stream}",
            "-ac",
            "1",
            "-ar",
            "48000",
            "-f",
            "f32le",
            "-",
        ],
        capture_output=True,
        check=True,
    )
    return np.frombuffer(res.stdout, dtype=np.float32)


def decode_video_gray(ffmpeg: str, path: str, w: int, h: int) -> npt.NDArray[np.uint8]:
    res = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            path,
            "-map",
            "0:v:0",
            "-vf",
            f"scale={w}:{h}",
            "-fps_mode",
            "passthrough",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        capture_output=True,
        check=True,
    )
    return np.frombuffer(res.stdout, dtype=np.uint8).reshape(-1, h, w)


def tone_energy(samples: npt.NDArray[np.float32], freq: float, rate: int = 48000) -> float:
    if len(samples) < 1024:
        return 0.0
    spec = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
    f = np.fft.rfftfreq(len(samples), 1 / rate)
    band = (f > freq - 15) & (f < freq + 15)
    return float(spec[band].max() / len(samples))
