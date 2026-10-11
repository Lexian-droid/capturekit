# Encoding

capturekit runs FFmpeg as a subprocess (never through a shell) and streams
raw BGRA video on stdin and float audio through private named pipes.

## Finding FFmpeg

1. `EncoderConfig(ffmpeg_path=...)`
2. `CAPTUREKIT_FFMPEG` environment variable
3. `ffmpeg` on `PATH`
4. `imageio-ffmpeg`'s bundled binary (`pip install capturekit[ffmpeg]`)

## Encoder selection

`EncoderConfig(encoder="auto")` tries `h264_nvenc → h264_qsv → h264_amf →
libx264 → h264_mf` and **validates each by encoding 3 test frames** — FFmpeg
builds list hardware encoders that cannot work on the current machine
(measured: QSV and AMF are listed but fail on an NVIDIA-only laptop).
Results are cached per FFmpeg binary.

An explicit encoder (`EncoderConfig("libx264")`) is tried first; with
`fallback=False` an unusable encoder raises `EncoderNotAvailableError`
instead of silently switching.

| Encoder | Input | Rate control (`bitrate=None` → quality mode) | Tested |
| --- | --- | --- | --- |
| `h264_nvenc` | BGRA (GPU colour conversion) | `-cq quality` / VBR `bitrate` | yes (RTX 4050) |
| `libx264` | yuv420p (CPU conversion) | `-crf quality` / `bitrate` | yes |
| `h264_qsv` | nv12 | `-global_quality` / `bitrate` | no hardware |
| `h264_amf` | nv12 | CQP / VBR peak | no hardware |
| `h264_mf` | nv12 | bitrate | no |

## Containers

* **MKV** (default for unknown extensions): resilient; a crash leaves a
  playable file.
* **MP4**: written as fragmented MP4 (`frag_keyframe+empty_moov`) so it is
  also playable after an interrupted recording.

Video is constant frame rate: each output frame is one pacer slot.

## Failure handling

* FFmpeg exits or stops reading → `EncoderError` (with the last FFmpeg
  output in `.stderr_tail`), `on_error` callback, recorder stops itself and
  `stop()` re-raises. Tested by killing FFmpeg mid-recording.
* Output files are never overwritten unless `RecordingConfig(overwrite=True)`.
