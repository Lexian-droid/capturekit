# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/). Versioning: SemVer.

## [Unreleased]

### Added
- Windows monitor capture via DXGI Desktop Duplication (regions, software
  cursor, automatic recovery) and via Windows Graphics Capture.
- Windows exclusive window capture via Windows Graphics Capture (client area,
  window-relative crop, resize and closure handling, capture-exclusion
  reporting).
- WASAPI microphone, system loopback and per-process loopback audio; device
  and session enumeration.
- `Recorder`: synchronised video + multi-track audio to MKV/MP4 through
  FFmpeg with validated hardware encoder selection, pause/resume, statistics
  and lifecycle/error callbacks.
- `ScreenCapture` for frame grabbing without recording.
- Capability discovery (`get_capabilities`).
