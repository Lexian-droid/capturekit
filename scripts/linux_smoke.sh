#!/usr/bin/env bash
# Smoke-test the X11 backend against a private Xvfb (run inside Linux/WSL).
#   bash scripts/linux_smoke.sh [display]
set -euo pipefail
cd "$(dirname "$0")/.."
DISP="${1:-:97}"
Xvfb "$DISP" -screen 0 1280x720x24 -nolisten tcp >/tmp/capturekit-xvfb.log 2>&1 &
XVFB=$!
trap 'kill $XVFB 2>/dev/null || true; pkill -f "x11_color_window.py --display $DISP" || true' EXIT
for _ in $(seq 50); do xdpyinfo -display "$DISP" >/dev/null 2>&1 && break; sleep 0.1; done
PYTHONPATH=src python3 - "$DISP" <<'PY'
import subprocess, sys, time
import numpy as np
import capturekit as ck
disp = sys.argv[1]
print(ck.get_capabilities(disp).summary())
mons = ck.list_monitors(disp)
print("monitors", mons)
def spawn(*a):
    p = subprocess.Popen([sys.executable, "tests/fixtures/x11_color_window.py", "--display", disp, *a],
                         stdout=subprocess.PIPE, text=True)
    wid = int(p.stdout.readline().split()[1]); time.sleep(0.3); return p, wid
tp, target = spawn("--color", "0,200,0", "--x", "100", "--y", "100", "--w", "400", "--h", "300", "--title", "target")
op, occl = spawn("--color", "200,0,0", "--x", "200", "--y", "150", "--w", "400", "--h", "300", "--title", "occluder")
wins = ck.list_windows(disp)
for w in wins: print(w)
desk = ck.screenshot(mons[0], ck.Rect(100, 100, 400, 300))
def frac(img, rgb):
    return float((np.abs(img[..., :3].astype(int) - rgb[::-1]) <= 3).all(-1).mean())
print("desktop red fraction", frac(desk, (200, 0, 0)))
win = ck.get_window(target, disp)
with ck.ScreenCapture(ck.WindowSource(win)) as cap:
    img = cap.grab().data
    print("window", img.shape, "green", frac(img, (0, 200, 0)), "red", frac(img, (200, 0, 0)), cap.backend_name)
tp.kill(); op.kill()
PY
