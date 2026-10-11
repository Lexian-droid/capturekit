---
name: ffmpeg-encoding
description: FFmpeg discovery, hardware encoder probing (NVENC/QSV/AMF/x264/MediaFoundation), command construction, named-pipe audio inputs, process lifecycle and failure handling for capturekit recordings. Use when editing src/capturekit/encoding/ or diagnosing encoder failures.
---

# FFmpeg and hardware encoding

Code: `src/capturekit/encoding/ffmpeg.py` (discovery, probe, command builder,
`FFmpegProcess`), `encoding/pipes.py` (named pipes / FIFOs).

## Discovery order
explicit `EncoderConfig.ffmpeg_path` → `$CAPTUREKIT_FFMPEG` → `PATH` →
`imageio_ffmpeg.get_ffmpeg_exe()` (optional dep). Record version via
`ffmpeg -version`.

## Encoder validation (never trust `-encoders` alone)
```
ffmpeg -hide_banner -loglevel error -f lavfi -i color=c=black:s=256x256:r=30
       -frames:v 3 -c:v <encoder> -f null -
```
Exit 0 → usable. Cache per (ffmpeg path, mtime). Measured 2026-10-11 on RTX
4050 laptop: `h264_nvenc` OK; `h264_qsv`, `h264_amf` listed but fail.
NVENC minimum size is ~145x49 on some generations — probe at 256x256 and
reject smaller recordings for NVENC (fall back to libx264).

## Command shape
```
ffmpeg -hide_banner -nostdin -loglevel warning -stats_period 1 -progress pipe:2?
  -f rawvideo -pix_fmt bgra -video_size WxH -framerate FPS -i pipe:0
  [-f f32le -ar 48000 -ac 2 -i \\.\pipe\capturekit-<uuid>-a0]...
  -map 0:v [-map 1:a ...] -c:v <enc> <enc opts> -c:a aac -b:a 192k
  -metadata:s:a:i title=<track name> <out.mkv>
```
- Hardware encoders take BGRA directly (GPU colour conversion). libx264 needs
  `-pix_fmt yuv420p` output (swscale on CPU — measure cost).
- Always `-pix_fmt yuv420p` (or nv12 for hw) on output for player compatibility.
- Rate control: NVENC `-rc vbr -b:v B -maxrate 1.5B -bufsize 2B -preset p4`;
  x264 `-preset veryfast -b:v B -maxrate -bufsize`; or CQP/CRF when bitrate=None.
- MKV is the default (survives crashes). MP4 gets `-movflags +faststart`? No:
  faststart rewrites the whole file at the end — omit by default for long
  recordings; offer fragmented MP4 (`+frag_keyframe+empty_moov`) as option.

## Process handling
- `subprocess.Popen(list_args, stdin=PIPE, stderr=PIPE, stdout=DEVNULL,
  creationflags=CREATE_NO_WINDOW)` — never `shell=True`.
- Drain stderr on a thread into a bounded deque (last 200 lines).
- Shutdown: close stdin and audio pipes → wait(timeout) → on timeout send
  `q`? (stdin closed so no) → `terminate()` → `kill()`. Report exit code and
  stderr tail in `EncoderError`.
- `BrokenPipeError` on write = FFmpeg died: collect stderr, raise EncoderError.

## Named pipes (Windows)
`CreateNamedPipeW(r"\\.\pipe\capturekit-<uuid4>", PIPE_ACCESS_OUTBOUND,
PIPE_TYPE_BYTE|PIPE_WAIT, 1, 1 MiB, 1 MiB, 0, NULL)` → start FFmpeg →
`ConnectNamedPipe` in the writer thread (blocks until FFmpeg opens it). FFmpeg
opens inputs in order, so every writer thread must be running before FFmpeg
starts probing. Linux: `os.mkfifo` in a private temp dir (mode 0600).

## Troubleshooting
| Symptom | Cause |
| --- | --- |
| hangs at start | an input pipe never connected/written; check writer threads started |
| `Invalid argument` with nvenc | odd width/height → we round crop to even dimensions |
| A/V offset constant | wrong timeline start for audio; see av-sync skill |
| file unplayable after crash | MP4 without fragmentation; use MKV |
