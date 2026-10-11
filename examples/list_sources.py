"""Print capabilities, monitors, windows, audio devices and audio sessions."""

import capturekit as ck

print(ck.get_capabilities().summary())
print("\nMonitors:")
for m in ck.list_monitors():
    print(f"  [{m.index}] {m.name} {m.rect} primary={m.is_primary} scale={m.scale}")
print("\nWindows:")
for w in ck.list_windows():
    print(f"  {w.handle:#010x} pid={w.pid} {w.process_name} {w.title[:60]!r}")
print("\nAudio devices:")
for d in ck.list_audio_devices():
    print(f"  {d.kind.value:6s} default={d.is_default!s:5s} {d.name}")
print("\nAudio sessions:")
for s in ck.list_audio_sessions():
    print(f"  pid={s.pid} {s.process_name} {s.display_name!r} active={s.is_active}")
