"""Record a window with its application's audio.

python examples/record_window.py "Notepad" out.mkv --seconds 10 --fps 60 --mic
"""

import argparse

import capturekit as ck

ap = argparse.ArgumentParser()
ap.add_argument("title", help="substring of the window title")
ap.add_argument("output")
ap.add_argument("--seconds", type=float, default=10)
ap.add_argument("--fps", type=float, default=30)
ap.add_argument("--mic", action="store_true", help="also record the default microphone")
args = ap.parse_args()

win = next((w for w in ck.list_windows() if args.title.lower() in w.title.lower()), None)
if win is None:
    raise SystemExit(f"no window matching {args.title!r}")

audio: list[ck.MicrophoneSource | ck.ProcessAudioSource] = []
if ck.get_capabilities().process_audio:
    audio.append(ck.ProcessAudioSource.for_window(win))
if args.mic:
    audio.append(ck.MicrophoneSource())

rec = ck.Recorder(
    ck.WindowSource(win),
    audio,
    args.output,
    ck.RecordingConfig(fps=args.fps),
    on_error=lambda e: print("error:", e),
)
rec.start()
print(f"recording {win.title!r} for {args.seconds}s ...")
rec.wait(args.seconds)  # returns early if the window is closed
result = rec.stop()
print(result.stats.video)
for track in result.stats.audio:
    print(track)
