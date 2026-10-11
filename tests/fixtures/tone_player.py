"""Play a sine tone from this process (test fixture for audio capture).

Usage: python tone_player.py --freq 440 --seconds 3 [--amplitude 0.3]

Windows: winsound.PlaySound from memory (goes through the audio engine as
this process's session, so process loopback can isolate it).
Linux: writes s16le to `paplay`/`pw-play` via stdin when available.
"""

from __future__ import annotations

import argparse
import io
import math
import shutil
import struct
import subprocess
import sys
import wave


def make_wav(freq: float, seconds: float, amplitude: float, rate: int = 48_000) -> bytes:
    n = int(rate * seconds)
    frames = bytearray()
    fade = int(rate * 0.01)
    for i in range(n):
        env = min(1.0, i / fade, (n - i) / fade) if fade else 1.0
        v = int(32767 * amplitude * env * math.sin(2 * math.pi * freq * i / rate))
        frames += struct.pack("<hh", v, v)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return buf.getvalue()


def play_sequence(spec: str, amplitude: float) -> int:
    import time

    segments = [(float(f), float(d)) for f, d in (x.split(":") for x in spec.split(","))]
    print("playing", flush=True)
    for i, (freq, dur) in enumerate(segments):
        print(f"segment {i}", flush=True)
        if freq <= 0:
            time.sleep(dur)
        elif sys.platform == "win32":
            import winsound

            winsound.PlaySound(make_wav(freq, dur, amplitude), winsound.SND_MEMORY)
        else:
            return 2
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--freq", type=float, default=440.0)
    ap.add_argument("--seconds", type=float, default=3.0)
    ap.add_argument("--amplitude", type=float, default=0.08)
    ap.add_argument("--sequence", help="freq:seconds,... (freq 0 = silence); prints 'segment i'")
    args = ap.parse_args()
    if args.sequence:
        return play_sequence(args.sequence, args.amplitude)
    data = make_wav(args.freq, args.seconds, args.amplitude)
    print("playing", flush=True)
    if sys.platform == "win32":
        import winsound

        winsound.PlaySound(data, winsound.SND_MEMORY)
        return 0
    player = shutil.which("paplay") or shutil.which("pw-play")
    if player is None:
        print("no audio player available", file=sys.stderr)
        return 2
    subprocess.run([player, "/dev/stdin"], input=data, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
