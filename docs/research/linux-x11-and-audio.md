# Linux backend research: X11 (Xlib via ctypes) and PulseAudio/PipeWire audio

Scope: X11 only (Xorg, Xvfb, Xwayland-as-X-server-only). No Wayland capture. Python-first, `ctypes`,
no compiled extensions. Every X entry point must accept an explicit display string such as `":99"`.

Labels used below:
- **UNVERIFIED**: no authoritative source found, or not tested locally. Confirm with a test before relying on it.
- **(source)**: checked against the linked source code or spec during this research.

---

## 0. Shared libraries and packages

| Purpose | soname (ctypes) | Debian/Ubuntu pkg | Fedora pkg |
|---|---|---|---|
| Core Xlib | `libX11.so.6` | `libx11-6` | `libX11` |
| MIT-SHM (XShm*) | `libXext.so.6` | `libxext6` | `libXext` |
| Composite | `libXcomposite.so.1` | `libxcomposite1` | `libXcomposite` |
| XFixes (cursor) | `libXfixes.so.3` | `libxfixes3` | `libXfixes` |
| Damage | `libXdamage.so.1` | `libxdamage1` | `libXdamage` |
| RandR | `libXrandr.so.2` | `libxrandr2` | `libXrandr` |
| X-Resource (PIDs) | `libXRes.so.1` (capital R) | `libxres1` | `libXres` |
| Xinerama | `libXinerama.so.1` | `libxinerama1` | `libXinerama` |
| SysV shm | `libc.so.6` | (libc6) | (glibc) |
| PulseAudio client | `libpulse.so.0`, `libpulse-simple.so.0` | `libpulse0` | `pulseaudio-libs` |

Tools for tests and subprocess backends:
- Debian/Ubuntu: `xvfb`, `x11-utils` (xdpyinfo, xwininfo, xprop), `x11-xserver-utils` (xrandr), `ffmpeg`, `pulseaudio-utils` (pactl, parec), `pipewire-bin` (pw-record, pw-dump, pw-cli).
- Fedora: `xorg-x11-server-Xvfb`, `xdpyinfo`, `xwininfo`, `xprop`, `xrandr`, `pulseaudio-utils`, `pipewire-utils`, `ffmpeg-free` (or RPM Fusion `ffmpeg`).
- Fedora split of `xorg-x11-utils` into per-tool packages: **UNVERIFIED** for every Fedora release.

Load with `ctypes.CDLL("libX11.so.6")`. Use the versioned soname. The unversioned `.so` symlinks only ship in `-dev` packages. `ctypes.util.find_library("X11")` works without `-dev` because it reads ldconfig.

---

## 1. Monitor enumeration

### 1.1 RandR 1.5 `GetMonitors` (preferred)
RandR 1.5 added the monitor request as opcode 42 (https://cgit.freedesktop.org/~keithp/randrproto/commit, xcb man page https://man.archlinux.org/man/xcb_randr_get_monitors.3.en).

```c
Bool  XRRQueryExtension(Display*, int *event_base, int *error_base);
Status XRRQueryVersion(Display*, int *major, int *minor);      // need >= 1.5
XRRMonitorInfo *XRRGetMonitors(Display*, Window root, Bool get_active, int *nmonitors);
void  XRRFreeMonitors(XRRMonitorInfo*);
typedef struct { Atom name; Bool primary; Bool automatic; int noutput;
                 int x, y, width, height; int mwidth, mheight; RROutput *outputs; } XRRMonitorInfo;
```
ctypes types: `Atom`, `RROutput`, `Window` and `XID` are `c_ulong`. `Bool` is `c_int`. Get the monitor name with `XGetAtomName(dpy, info.name)` and free it with `XFree`.

Server behaviour, read from `randr/rrmonitor.c` (https://ftp.kaist.ac.kr/NetBSD/NetBSD-release-11/xsrc/external/mit/xorg-server/dist/randr/rrmonitor.c) (source):
- If no client has defined monitors, the server creates one automatic monitor per active CRTC. A CRTC is active when its mode is non-NULL.
- The monitor takes the name of the CRTC's first output. A CRTC without outputs is named `Monitor-%08lx`.
- The primary output's monitor comes first in the list.
- With `get_active=True`, monitors with zero width or height are skipped.
- The server never synthesises a whole-screen monitor. With zero CRTCs the request may return an empty list or `BadAlloc`. The client must fall back (1.4).

### 1.2 Fallback: RandR 1.2+ CRTC enumeration
`XRRGetScreenResourcesCurrent(dpy, root)` returns `XRRScreenResources*`. For each CRTC, call `XRRGetCrtcInfo(dpy, res, crtc)` and keep it if `mode != None` and `width > 0`. Free with `XRRFreeCrtcInfo` and `XRRFreeScreenResources`. Use `XRRGetOutputPrimary(dpy, root)` to find the primary. `...Current` does not re-probe hardware, so it is cheap.

### 1.3 Xinerama fallback
```c
Bool XineramaQueryExtension(Display*, int*, int*);  Bool XineramaIsActive(Display*);
XineramaScreenInfo *XineramaQueryScreens(Display*, int *number);   // XFree() result
typedef struct { int screen_number; short x_org, y_org, width, height; } XineramaScreenInfo;
```
Note that the struct fields are `c_short`.

### 1.4 Final fallback
Return one monitor covering `DisplayWidth(dpy, s) x DisplayHeight(dpy, s)`. These are macros, so call the functions `XDisplayWidth` and `XDisplayHeight` instead. Also enumerate `XScreenCount(dpy)`, because separate X screens (`:99.0`, `:99.1`) are separate roots.

### 1.5 Xvfb and RandR
Xvfb implements RandR (`vfbRandRInit` in `hw/vfb/InitOutput.c`, read from the X11Libre fork mirror https://raw.githubusercontent.com/X11Libre/xserver/master/hw/vfb/InitOutput.c) (source):
- One CRTC by default with one output named **`screen`**. Further outputs are named `screen_%d`. Rotation is not supported.
- `RRScreenSetSizeRange(pScreen, 1, 1, width, height)`: the screen can shrink but cannot grow past its startup size.
- Defaults: 1280x1024, depth 24.
- `GetMonitors` on Xvfb therefore returns one monitor named `screen` at 0,0. The server reports RandR protocol 1.6 on recent builds (**UNVERIFIED**; run `xrandr --version` against Xvfb).
- The `-crtcs n[@WxH]` option is in the X11Libre source. Whether upstream xorg/xserver Xvfb has it is **UNVERIFIED**. Upstream gitlab blocked automated fetches (Anubis).
- For multi-monitor tests, use `Xvfb :99 -screen 0 3840x1080x24 +xinerama`, or multiple `-screen`s with `+xinerama`. With Xinerama active over more than one screen, RandR may be disabled by the server (**UNVERIFIED**), so test the Xinerama path separately.

---

## 2. Fast capture: MIT-SHM, with XGetImage as fallback

### 2.1 Requirements and caveats
- The client and X server must share the same host and the same SysV IPC namespace. In Docker the X server needs `--ipc=host` or a shared namespace. Remote `DISPLAY=host:0` and ssh-forwarded displays fail at attach time.
- `XShmQueryExtension` can return True for a remote display while the attach still fails. Always attach, `XSync`, and check for an error before trusting SHM.
- The X server checks that the client may access the segment. Create it `0600` as the same uid. If the X server runs as another user, `0600` fails and `0666` is needed. **UNVERIFIED** detail; see the server's `shm.c` access check.
- ShmGetImage needs the requested rectangle to lie fully inside the drawable. Otherwise the server returns BadMatch, the same as core GetImage.

### 2.2 ctypes signatures
(XShm(3): https://www.mankier.com/3/XShm, https://man.openbsd.org/man3/XShm.3)
```c
// libXext
Bool   XShmQueryExtension(Display*);
Bool   XShmQueryVersion(Display*, int *major, int *minor, Bool *pixmaps);
XImage*XShmCreateImage(Display*, Visual*, unsigned int depth, int format /*ZPixmap=2*/,
                       char *data /*NULL*/, XShmSegmentInfo*, unsigned int width, unsigned int height);
Bool   XShmAttach(Display*, XShmSegmentInfo*);
Bool   XShmDetach(Display*, XShmSegmentInfo*);
Bool   XShmGetImage(Display*, Drawable, XImage*, int x, int y, unsigned long plane_mask /*AllPlanes=~0UL*/);
typedef struct { ShmSeg shmseg /*c_ulong*/; int shmid; char *shmaddr; Bool readOnly; } XShmSegmentInfo;
// libc
int   shmget(key_t key /*IPC_PRIVATE=0*/, size_t size, int shmflg /*IPC_CREAT|0o600 = 0o1600*/);
void *shmat(int shmid, const void *addr /*NULL*/, int shmflg /*0*/);   // error == (void*)-1
int   shmdt(const void *addr);
int   shmctl(int shmid, int cmd /*IPC_RMID=0*/, struct shmid_ds *buf /*NULL*/);
// libX11 fallback / helpers
XImage *XGetImage(Display*, Drawable, int x, int y, unsigned int w, unsigned int h,
                  unsigned long plane_mask, int format /*ZPixmap=2*/);
int XDestroyImage(XImage*);   // exported as a real function by libX11 as well as a macro
int XFree(void*);  Status XSync(Display*, Bool discard);
```
Notes:
- Set `shmat.restype = c_void_p` and compare the result with `c_void_p(-1).value`.
- `XImage` must be declared in full so ctypes can read the fields. The field order is: `width, height, xoffset, format, data(c_char_p→use c_void_p), byte_order, bitmap_unit, bitmap_bit_order, bitmap_pad, depth, bytes_per_line, bits_per_pixel, red_mask, green_mask, blue_mask (c_ulong), obdata (c_void_p), funcs (6 fn ptrs: create_image, destroy_image, get_pixel, put_pixel, sub_image, add_pixel)`.
- Before calling `XDestroyImage` on an SHM image, set `image.data = None`. Otherwise Xlib `free()`s the shm address.

### 2.3 Sequence
1. Create the image: `img = XShmCreateImage(dpy, DefaultVisual, DefaultDepth, ZPixmap, NULL, &seg, w, h)`.
2. Allocate the segment: `seg.shmid = shmget(IPC_PRIVATE, img.bytes_per_line*img.height, IPC_CREAT|0o600)`.
3. Map it: `seg.shmaddr = img.data = shmat(seg.shmid, NULL, 0)` and set `seg.readOnly = False`.
4. Attach it: `XShmAttach(dpy,&seg)` then `XSync(dpy, False)`. If the error handler saw an error, fall back to `XGetImage`.
5. Call `shmctl(seg.shmid, IPC_RMID, NULL)` right after the successful sync. On Linux an attached segment survives until its last detach, so this avoids leaked segments when the process crashes.
6. Per frame, call `XShmGetImage(dpy, drawable, img, x, y, AllPlanes)`. The call is synchronous because it waits for the reply. Then copy `bytes_per_line*h` bytes out of `img.data`, for example with `ctypes.string_at` or `memoryview` via `(c_ubyte*n).from_address`.
7. Teardown: `XShmDetach`, `XSync`, `shmdt`, set `img.data=None`, then `XDestroyImage`.

The image must be recreated whenever the capture size changes.

### 2.4 Pixel format
- Depth 24 TrueColor ZPixmap on Xorg and Xvfb uses `bits_per_pixel=32`, `red_mask=0xff0000`, `green_mask=0xff00`, `blue_mask=0xff`. On x86 `byte_order=LSBFirst`, so the bytes in memory are **B,G,R,X**, the same as Windows BGRA. The X byte is undefined: often 0, sometimes 0xff.
- Never assume the layout. Check `bits_per_pixel==32`, the masks, and `byte_order`.
- Depth-32 ARGB visuals (windows using alpha, see section 3) give premultiplied alpha in the top byte (**UNVERIFIED** premultiplication; it follows the Render convention).
- Depth 16 (`Xvfb -screen 0 WxHx16`) gives RGB565 at 16 bpp. Convert it or raise UnsupportedFormat.
- `bytes_per_line` can exceed `width*4`. Respect the stride.

### 2.5 Prior art
python-mss implements Xlib-via-ctypes XGetImage on Linux. Newer versions add an XShm/XCB path. Version details are **UNVERIFIED**. It is a useful reference for the ctypes structs.

---

## 3. Window-exclusive capture with Composite

Spec: compositeproto.txt (https://xorg.freedesktop.org/archive/current/doc/compositeproto/compositeproto.txt) (source).

### 3.1 API (libXcomposite)
```c
Bool   XCompositeQueryExtension(Display*, int *event_base, int *error_base);
Status XCompositeQueryVersion(Display*, int *major /*in/out*/, int *minor /*in/out*/); // need >= 0.2
void   XCompositeRedirectWindow(Display*, Window, int update);   // CompositeRedirectAutomatic=0, Manual=1
void   XCompositeRedirectSubwindows(Display*, Window, int update);
void   XCompositeUnredirectWindow(Display*, Window, int update);
Pixmap XCompositeNameWindowPixmap(Display*, Window);   // 0.2+; free with XFreePixmap
Window XCompositeGetOverlayWindow(Display*, Window);    // not needed for capture
```

### 3.2 Semantics, quoted from the spec
- **Automatic vs Manual:** with Automatic, the server itself paints the redirected contents into the parent. With Manual, "the parent window contents can be completely determined by an external application", which is a compositing manager.
- **Only one Manual client:** "Only one client may specify an update type of Manual, another attempt will result in an Access error."
  - A running compositing manager (mutter, kwin, picom, xfwm4 with compositing on) holds Manual redirection of the root's subwindows. The library must **never** use Manual.
  - Automatic redirection may be requested by any number of clients. The server counts them per client and drops the redirection when the client unredirects or disconnects.
  - Redirecting a client window that already sits inside a frame the compositor redirected is legal (nested redirection).
- **Occlusion:** the purpose of Composite is to make "pixel contents of window hierarchies available without respect to sibling and antecedent clipping". The pixels "are available whenever it is viewable". Redirected content is therefore occlusion-free and includes off-screen parts, but only while the window is **viewable**.
- **NameWindowPixmap:**
  - "A new pixmap is created each time the window is mapped or resized." The client must call NameWindowPixmap again to get the new storage. The old named pixmap "will remain allocated until freed, even if 'window' is unmapped, reconfigured or destroyed", and it keeps stale contents.
  - It returns a Match error "if 'window' is not redirected or is not visible."
  - Storage covers "the window contents, its borders and the contents of all descendants."

### 3.3 Consequences
- **Resize:** select `StructureNotifyMask` on the target and watch for `ConfigureNotify` with a new width or height. On a resize, `XFreePixmap` the old pixmap, rename, and recreate the SHM image. A cheaper alternative is to poll `XGetGeometry(pixmap)` against `XGetWindowAttributes(win)` each frame.
- **Unmapped or minimized (iconified) windows:**
  - ICCCM WMs unmap iconified client windows. The window is then not viewable, NameWindowPixmap fails with BadMatch, and no new content is produced.
  - A pixmap named before the unmap still holds the **last frame**, which the library can return as stale, marked `is_stale=True`.
  - Whether the server keeps the backing pixmap of an unmapped redirected window alive is not specified. Assume it does not: the spec says a new pixmap is allocated on map.
  - Some compositing WMs keep minimized windows mapped and only hide them (**UNVERIFIED**, WM-specific). Use `_NET_WM_STATE_HIDDEN` together with `map_state` to report the state.
- **Frames and decorations:**
  - Reparenting WMs put the client window inside a frame window, which is a child of root. `_NET_CLIENT_LIST` lists **client** windows.
  - Redirecting and naming the client window gives the content with no decorations. Redirecting the frame gives content with decorations.
  - To find the frame, walk `XQueryTree` parents until the parent is root.
  - `_NET_FRAME_EXTENTS` (left, right, top, bottom; https://specifications.freedesktop.org/wm/latest/ar01s05.html) gives the decoration size. Client-side-decorated GTK windows also carry `_GTK_FRAME_EXTENTS` for shadows (**UNVERIFIED**, not EWMH).
- **Capture from the pixmap:**
  - `XShmGetImage(dpy, pixmap, img, 0, 0, AllPlanes)` or `XGetImage(dpy, pixmap, ...)`.
  - The pixmap depth equals the window depth, which can be 32 for ARGB windows. Create the SHM image with the window's visual and depth, taken from `XGetWindowAttributes` fields `visual` and `depth`, not the root's.
- **Cost:** redirecting a window that was not redirected before (no compositor) moves its rendering off-screen. With Automatic redirection the server still paints it on screen, so it stays visible. Unredirect on stop.
- **Simpler variant:** after an Automatic redirect, `XGetImage(window)` reads the window's own backing pixmap, so naming a pixmap is unnecessary for reads. This is observed behaviour in practice and is **UNVERIFIED** against the spec. Prefer NameWindowPixmap, whose semantics are documented.

### 3.4 Damage (change notification; libXdamage)
```c
Bool   XDamageQueryExtension(Display*, int *event_base, int *error_base);
Status XDamageQueryVersion(Display*, int *major, int *minor);
Damage XDamageCreate(Display*, Drawable, int level);  // RawRectangles=0, DeltaRectangles=1, BoundingBox=2, NonEmpty=3
void   XDamageSubtract(Display*, Damage, XserverRegion repair /*None*/, XserverRegion parts /*None*/);
void   XDamageDestroy(Display*, Damage);
```
- Events arrive with `type == damage_event_base + XDamageNotify(0)`.
- Use `XDamageReportNonEmpty` and call `XDamageSubtract(dpy, dmg, 0, 0)` after each capture. This gives a cheap "frame changed" signal and lets the library skip identical frames.
- Create the Damage object on the redirected window, not on the pixmap. Drain events with `XPending`/`XNextEvent` on the capture thread's own Display.

### 3.5 Cursor (libXfixes)
```c
Bool  XFixesQueryExtension(Display*, int*, int*);  Status XFixesQueryVersion(Display*, int*, int*);  // call first
XFixesCursorImage *XFixesGetCursorImage(Display*);  // XFree() result
typedef struct { short x, y; unsigned short width, height, xhot, yhot;
                 unsigned long cursor_serial; unsigned long *pixels;  /* v2+: */ Atom atom; const char *name; } XFixesCursorImage;
```
**Gotcha:** `pixels` is an array of `unsigned long`, which is 8 bytes on LP64. Each element holds one premultiplied ARGB32 value in its low 32 bits, so read it as `c_ulong * (w*h)` and mask with `& 0xffffffff`.

`x` and `y` are root coordinates of the hotspot. The top-left is `x-xhot, y-yhot`. Cache the image by `cursor_serial`, and use `XFixesSelectCursorInput(dpy, root, XFixesDisplayCursorNotifyMask)` for change events.

### 3.6 Extensions in Xvfb
- Composite, DAMAGE, XFIXES, MIT-SHM, RANDR, RENDER and X-Resource are expected in a stock Xorg-built Xvfb (**UNVERIFIED locally**: Xvfb is not installed in the WSL test distro). Verify with `xdpyinfo -display :99 | sed -n '/extensions:/,$p'`.
- The Xvfb source shows `-render` style options that can also disable Composite (`noCompositeExtension = TRUE`) (source, X11Libre fork). Do not pass such flags in tests.
- SHM in Xvfb depends on the build flag (`CONFIG_MITSHM`). Distro builds enable it (**UNVERIFIED**).
- Xvfb has no WM and no compositor. Composite Automatic redirection is what makes overlapping test windows capturable without occlusion.

---

## 4. Window enumeration

EWMH reference: https://specifications.freedesktop.org/wm/latest/ (root properties ar01s03.html; application properties ar01s05.html).

1. **WM present.** Check that `_NET_SUPPORTING_WM_CHECK` on root points to a live child window that carries the same property.
   - Read `_NET_CLIENT_LIST` (`WINDOW[]`, mapping order) or `_NET_CLIENT_LIST_STACKING` (bottom-to-top) from root.
   - Use `XGetWindowProperty(dpy, root, atom, 0, 0x7fffffff, False, XA_WINDOW(33), ...)`. The returned items are `c_ulong` (32-bit format data is returned as `long` by Xlib, another LP64 gotcha).
2. **Per window:**
   - Title: `_NET_WM_NAME` (type `UTF8_STRING`, "the title of the window in UTF-8 encoding"). Fall back to `WM_NAME` via `XGetWMName` + `Xutf8TextPropertyToTextList`, or decode Latin-1/COMPOUND_TEXT best-effort.
   - `_NET_WM_PID` (CARDINAL). The spec says it "MUST contain the process ID of the client owning this window", but only when the client sets it. Compare `WM_CLIENT_MACHINE` with the local hostname; a remote client's PID is meaningless.
   - `WM_CLASS`: two NUL-separated strings, instance then class, via `XGetClassHint`.
   - Geometry: `XGetWindowAttributes` (map_state: IsUnmapped=0, IsUnviewable=1, IsViewable=2; class; override_redirect; depth; visual) plus `XTranslateCoordinates(dpy, w, root, 0,0, &x,&y,&child)`.
   - State: `_NET_WM_STATE` contains `_NET_WM_STATE_HIDDEN` for minimized windows.
3. **Reliable PID via X-Resource 1.2** (libXRes; spec: https://www.x.org/releases/current/doc/resourceproto/resproto.txt, prototypes below were not fetched and are **UNVERIFIED** in exact form):
   ```c
   Bool XResQueryExtension(Display*, int*, int*);  Status XResQueryVersion(Display*, int*, int*);  // need >= 1.2
   typedef struct { XID client; unsigned int mask; } XResClientIdSpec;        // mask = XRES_CLIENT_ID_PID_MASK (1<<1 = 2)
   typedef struct { XResClientIdSpec spec; long length; void *value; } XResClientIdValue;
   Status XResQueryClientIds(Display*, long num_specs, XResClientIdSpec*, long *num_ids, XResClientIdValue **ids);
   pid_t  XResGetClientPid(XResClientIdValue*);    // -1 if unavailable
   void   XResClientIdsDestroy(long num_ids, XResClientIdValue*);
   ```
   Pass the window XID as `client`, because the server maps any resource ID to its owning client. The server obtains the PID from the socket peer credentials, so it works only for local clients and does not depend on the client setting `_NET_WM_PID`. Prefer XRes, fall back to `_NET_WM_PID`, and treat both as possibly absent.
4. **No EWMH WM (bare Xvfb):**
   - Call `XQueryTree(root)` and keep children where `map_state == IsViewable`, `class == InputOutput(1)`, `override_redirect == False`, and size > 1x1. Optionally also require `WM_NAME` or `WM_CLASS`, which filters out helper windows.
   - `XQueryTree` children come in bottom-to-top stacking order. Free them with `XFree`.
5. **Non-EWMH reparenting WM:** descend from root children to the first descendant that has the ICCCM `WM_STATE` property. This is the XmuClientWindow approach and finds the client inside its frame.
6. Windows can vanish at any moment between these calls. Each per-window query must tolerate BadWindow (section 5).

---

## 5. Error handling and threading

- Xlib's **default error handler prints the error and calls `exit()`**. See the Xlib manual "Using the Default Error Handlers" (https://www.x.org/releases/X11R7.7/doc/libX11/libX11/libX11.html).
  - Install `XSetErrorHandler(handler)` before any request that may fail. The handler signature is `int (*)(Display*, XErrorEvent*)`, and its return value is ignored (https://xorg.freedesktop.org/archive/current/doc/man/man3/XSetErrorHandler.3.xhtml).
  - `XErrorEvent`: `int type; Display *display; XID resourceid; unsigned long serial; unsigned char error_code, request_code, minor_code;`.
  - ctypes: `HANDLER = CFUNCTYPE(c_int, c_void_p, POINTER(XErrorEvent))`. **Keep a module-global reference** so the callback is not garbage-collected.
  - The handler is **process-global**, not per Display. Record errors into a lock-protected dict keyed by `(display_ptr, serial)` or into thread-local state, and never call Xlib from inside the handler.
  - Pattern: `serial = XNextRequest(dpy)`, issue the request, `XSync(dpy, False)`, then check for errors with `serial >= start`. Be careful when Python code shares the handler with other libraries (Tk, Qt). Chain to the previous handler only for unknown displays.
- **IO errors** (server died, Xvfb killed): `XSetIOErrorHandler` handlers "should not return"; if one does, Xlib exits the process (same man page). libX11 1.7+ has `XSetIOErrorExitHandler(dpy, fn, data)` to avoid `exit()` (**UNVERIFIED** version). Feature-detect it with `hasattr(libX11, "XSetIOErrorExitHandler")`. Otherwise run capture against a server that may die in a subprocess, or accept the risk.
- **BadWindow** from destroyed windows (error_code 3), and BadMatch (8) from GetImage or NameWindowPixmap on an unviewable window or an out-of-bounds rectangle, are routine. Map them to `WindowGone` and `WindowNotViewable` exceptions.
- **Threads:**
  - `XInitThreads` "must be the first Xlib function a multi-threaded program calls" (https://xorg.freedesktop.org/archive/current/doc/man/man3/XInitThreads.3.xhtml).
  - libX11 **1.8+** calls `XInitThreads()` from a library constructor by default (announce: https://lists.freedesktop.org/archives/xorg/2022-April/060979.html). Still call it once at import, before `XOpenDisplay`, for older libX11.
  - Recommendation: **one `Display*` per capture thread**, never shared, opened with `XOpenDisplay(b":99")`. Close it on that same thread.

---

## 6. Session detection (Wayland, Xwayland, Xvfb)

- **Wayland session:** `XDG_SESSION_TYPE == "wayland"` or `WAYLAND_DISPLAY` set. If the user passed **no** display, raise `UnsupportedSession("Wayland")`. On Wayland, `DISPLAY` points to Xwayland, which sees only X11 clients.
  - If the user passed an explicit display (`":99"`), **ignore** these variables.
  - Example where this matters: WSLg sets `WAYLAND_DISPLAY=wayland-0` and `DISPLAY=:0` (Xwayland) even in a plain shell (observed locally).
- **Xwayland:** the `XWAYLAND` extension exists "so clients can tell whether they're talking to Xwayland". Clients only need `XQueryExtension(dpy, b"XWAYLAND", ...)` or `XListExtensions` (https://cgit.xenocara.org/proto/xorgproto/tree/xwaylandproto.txt?h=master).
  - It was added to xorgproto in 2022. The first Xwayland release that has it is **UNVERIFIED** (likely 23.1).
  - Fallback heuristic for older Xwayland: RandR output names start with `XWAYLAND` (**UNVERIFIED**).
- **Xvfb:** `XServerVendor()` is "The X.Org Foundation" for Xorg, Xvfb and Xwayland alike, so it does not decide anything.
  - Heuristic: no `XWAYLAND` extension, RandR output named `screen` (vfb source, §1.5), no `_NET_SUPPORTING_WM_CHECK`.
  - The only practical difference between Xvfb and Xorg for this library is the missing WM/compositor. Detection should be informational only.

---

## 7. FFmpeg x11grab `-window_id` (libavdevice/xcbgrab.c)

Source: https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/libavdevice/xcbgrab.c (source).
- `window_id` defaults to `XCB_NONE`, in which case the root is used. Otherwise `select_region` and `follow_mouse` are disabled.
- Both paths pass `drawable = c->window_id` straight to `xcb_get_image(..., XCB_IMAGE_FORMAT_Z_PIXMAP, x, y, w, h)` or `xcb_shm_get_image(...)`.
- **No Composite calls exist in the file.** Geometry is read once with `xcb_get_geometry` at startup.
  - No resize handling: if the window shrinks, GetImage fails and the error path returns `AVERROR(EACCES)`.
  - The cursor is drawn via XFixes when `draw_mouse` is set.
- Implication, from core protocol GetImage (https://www.x.org/releases/X11R7.7/doc/xproto/x11protocol.html, GetImage):
  - The window must be viewable, otherwise Match.
  - Regions obscured by non-inferior windows return backing-store contents if available, otherwise they are **undefined**. In practice they contain whatever is on screen there.
  - So `-window_id` is **not** occlusion-free on a non-compositing server such as bare Xvfb.
  - When a compositor has the window's frame redirected, GetImage reads the off-screen pixmap and looks occlusion-free. That is a side effect of someone else's redirection, not something ffmpeg guarantees.
- A minimized (unmapped) window fails outright.
- Conclusion: use our own Composite Automatic redirection (§3). FFmpeg is useful only as a CLI baseline or encoder.

---

## 8. Linux audio (PulseAudio and pipewire-pulse; PipeWire native)

### 8.1 Enumeration
- PulseAudio protocol (works on real PulseAudio and pipewire-pulse):
  - `pactl -f json list sinks|sources|sink-inputs`. JSON output arrived in PulseAudio 16 (**UNVERIFIED** version); fall back to parsing `pactl list` text.
  - Sink-input properties include `application.process.id`, `application.process.binary`, `application.name` and `media.name`.
  - Each sink has a monitor source `<sink_name>.monitor`. The `@DEFAULT_MONITOR@` alias exists (**UNVERIFIED** on pipewire-pulse).
  - Under pipewire-pulse the sink-input index equals the PipeWire node global id.
- PipeWire native: `pw-dump` emits a JSON array.
  - Filter `type == "PipeWire:Interface:Node"` and `info.props["media.class"] == "Stream/Output/Audio"`. Read `application.process.id`, `object.serial` and `node.name` from `info.props`.
  - Properties are documented at https://docs.pipewire.org/page_man_pipewire-props_7.html: `application.process.id` is the "process id (pid)", and `target.object` is a "node.name or an object.serial".
- A process can own **several** streams, for example browsers create per-tab or per-media streams. Streams also appear only while the app is producing audio, so per-app capture means "all current and future sink-inputs whose PID (or child PID) matches".

### 8.2 Per-application capture options
**(a) PulseAudio monitor-stream (recommended):**
- C API: `int pa_stream_set_monitor_stream(pa_stream*, uint32_t sink_input_idx)`. It must be called **before** `pa_stream_connect_record()` on a record stream from the sink's monitor source. It records the output of that single sink-input. Added in PulseAudio 0.9.11 (**UNVERIFIED** version); background in https://bugs.freedesktop.org/show_bug.cgi?id=66326.
- CLI: `parec --monitor-stream=INDEX --raw --format=s16le --rate=48000 --channels=2 --latency-msec=20` writes raw PCM to stdout ("Record from the sink input with index INDEX": https://manpages.debian.org/bookworm/pulseaudio-utils/pacat.1.en.html).
- **pipewire-pulse supports it**, read from `module-protocol-pulse/pulse-server.c` (https://raw.githubusercontent.com/pipewire/pipewire/master/src/modules/module-protocol-pulse/pulse-server.c) (source):
  - For protocol >= 13, `direct_on_input_idx` is parsed. If it is valid, `source_index = direct_on_input_idx` and the stream gets `target.object = <idx>`.
  - PipeWire then links the capture stream directly to the application's output node. The stream does not use the sink monitor and does not change the user's routing.
  - Not tested end-to-end here: **UNVERIFIED in practice**.
  - Known quirk: `find_device()` runs before the override, so the reported device may be the sink or monitor.
- Behaviour when the sink-input disappears: PulseAudio kills the record stream and parec exits (**UNVERIFIED**).
  - On PipeWire, set `node.dont-reconnect=true` ("when the target is destroyed, destroy the node as well") so the stream does not silently relink to the default source, which would be the **microphone**.
  - With parec, pass it as `--property=node.dont-reconnect=true` (**UNVERIFIED** that pipewire-pulse forwards it).
  - In every case, treat EOF from the subprocess as "stream ended".

**(b) Null sink + move-sink-input:**
- `pactl load-module module-null-sink sink_name=cap_X`, then `pactl move-sink-input <idx> cap_X`, then record `cap_X.monitor`.
- The app becomes **inaudible** to the user unless a loopback is added. This is disruptive, the change is user-visible in the mixer, and the old routing must be restored even after a crash. Avoid it, or offer it only as an explicit opt-in.

**(c) PipeWire native:**
- Per-app: `pw-record --target <object.serial|node.name> -P '{ "node.dont-reconnect": true }' --raw --format s16 --rate 48000 --channels 2 -`.
  - This links the recorder to the app's output stream node. Linking to a stream node is the same mechanism pipewire-pulse uses for (a). It is **UNVERIFIED** by local test, and the props doc does not state that stream nodes are valid targets.
  - `--target` accepts a serial or node.name (https://docs.pipewire.org/page_man_pw-cat_1.html), not the global id.
  - Without a container, `-` (stdout) writes AU (Sun audio) per the man page. Pass `--raw`/`-a` explicitly, or parse the AU header (magic `.snd`, big-endian header with a data-offset field).
  - Whether `--raw` exists in older 0.3.x releases is **UNVERIFIED**.
- System mix: target the sink with `-P '{ "stream.capture.sink": true }'`. That property comes from PipeWire stream docs, was not found in pipewire-props(7), and is **UNVERIFIED**. The portable alternative is to record `<sink>.monitor` via parec.

**(d) libpulse via ctypes:**
- `pa_simple_new(server, name, PA_STREAM_RECORD=2, dev, stream_name, &pa_sample_spec{format,rate,channels}, NULL, &attr, &err)` plus `pa_simple_read(s, buf, n, &err)` is easy in ctypes and covers **monitor source** capture (system or device).
- The Simple API cannot call `pa_stream_set_monitor_stream`. Per-app capture through ctypes needs the async API: `pa_threaded_mainloop` + context + stream callbacks as `CFUNCTYPE`. That is feasible but heavier. Defer it, because the parec subprocess gives the same result.

### 8.3 Recommendation
1. Backend detection:
   - `pactl info` shows "Server Name: PulseAudio (on PipeWire x.y)" on PipeWire. Prefer that string over checking whether `pw-cli` exists.
   - Otherwise check whether the native socket `$XDG_RUNTIME_DIR/pulse/native` or `$PULSE_SERVER` is reachable.
2. System or device loopback: libpulse-simple via ctypes on `<sink>.monitor`. Fall back to a `parec -d <sink>.monitor` subprocess.
3. Per-app: a `parec --monitor-stream=<idx>` subprocess for each matching sink-input. This works on PulseAudio and, per the source, on pipewire-pulse. The Python side mixes multiple streams with float32 add and clip.
4. Use `pw-record` only if `pactl` or parec is missing (PipeWire-only installs).
5. Track streams with a `pactl subscribe` subprocess, whose lines look like `Event 'new' on sink-input #N` / `Event 'remove' on sink-input #N`. Attach or detach per-stream recorders accordingly. `pw-dump --monitor` is the PipeWire equivalent.
6. Timestamps: a pipe carries no timestamps. Stamp each chunk with `time.monotonic()` minus the buffered duration, and keep `--latency-msec` small (10–20 ms).
7. Use fixed output formats: s16le or float32le at 48 kHz stereo. Let the server resample.

---

## 9. WSL2 test environment (read-only check, 2026-10-11)

Command run: `wsl -d Ubuntu -- bash -lc "which Xvfb xdpyinfo python3 ffmpeg pactl pw-cli; python3 --version; ls /usr/lib/x86_64-linux-gnu | grep -E 'libX(11|ext|composite|fixes|damage|randr|res)\.so'"`
```
/usr/bin/python3
Python 3.14.4
libX11.so.6  libX11.so.6.4.0  libXcomposite.so.1  libXcomposite.so.1.0.0  libXdamage.so.1  libXdamage.so.1.1.0
libXext.so.6  libXext.so.6.4.0  libXfixes.so.3  libXfixes.so.3.1.0  libXrandr.so.2  libXrandr.so.2.2.0
```
- Distro: **Ubuntu 26.04.1 LTS**.
- `Xvfb`, `xdpyinfo`, `ffmpeg`, `pactl` and `pw-cli` are **not installed**.
- The grep pattern missed XRes because the soname is `libXRes.so.1` (capital R). It **is** installed (`libxres1 2:1.2.1`), as are `libXinerama.so.1`, `libXtst.so.6`, `libxcb-shm.so.0` and `libxcb-randr.so.0`.
- Installed versions: libx11-6 2:1.8.13, libxext6 1.3.4, libxcomposite1 0.4.6, libxfixes3 6.0.0, libxdamage1 1.1.7, libxrandr2 1.5.4.
- Not installed: `libpulse0`, `pipewire`, `pipewire-pulse`, `pipewire-bin`, `pulseaudio-utils`, `xvfb`, `x11-utils`.
- Environment: `DISPLAY=:0` (WSLg Xwayland), `WAYLAND_DISPLAY=wayland-0`, `XDG_SESSION_TYPE` empty, `PULSE_SERVER=unix:/mnt/wslg/PulseServer`.
  - WSLg runs a real PulseAudio server with an RDP sink and source. Whether it exposes a usable `.monitor` and supports `--monitor-stream` is **UNVERIFIED**.
  - Tests should start their own Xvfb on `:99`, and optionally their own `pipewire`, `pipewire-pulse` and `wireplumber` user daemons with an isolated `XDG_RUNTIME_DIR`.

Packages to install (the user decides; nothing was installed):
```
sudo apt install xvfb x11-utils x11-xserver-utils ffmpeg \
     libpulse0 pulseaudio-utils pipewire pipewire-pulse pipewire-bin wireplumber \
     x11-apps xterm            # optional: test clients (xeyes, xterm) to create windows
```
Test audio sources: `pw-play`/`paplay` of a sine file, or `ffmpeg -f lavfi -i sine=f=440 -f pulse default`, which needs an ffmpeg built with pulse.

---

## 10. Recommended implementation plan

1. **`_x11/lib.py`: ctypes bindings.**
   - Load the sonames from §0. Declare `argtypes`/`restype` for every function, because missing restypes silently truncate 64-bit pointers.
   - Define the structs `XImage`, `XShmSegmentInfo`, `XWindowAttributes`, `XErrorEvent`, `XRRMonitorInfo`, `XRRScreenResources`, `XRRCrtcInfo`, `XineramaScreenInfo`, `XFixesCursorImage`, `XResClientIdSpec`/`Value`.
   - Optional libs (Xcomposite, Xdamage, XRes, Xinerama, Xrandr) load lazily, and features degrade when they are missing.
   - At import, call `XInitThreads()` and install the global error handler (§5).
2. **`Connection(display: str | None)`: one per thread.**
   - If `display is None`, run the Wayland check (§6) before using `$DISPLAY`. Then `XOpenDisplay(display.encode())`, raising on NULL.
   - Probe extensions once and cache their versions: `XShmQueryVersion`, `XCompositeQueryVersion` (≥0.2), `XDamageQueryVersion`, `XFixesQueryVersion`, `XRRQueryVersion`, `XResQueryVersion`, `XQueryExtension("XWAYLAND")`.
   - Install `XSetIOErrorExitHandler` if it is available.
3. **Monitors:**
   - RandR ≥1.5: `XRRGetMonitors(dpy, root, True, &n)`, then name via `XGetAtomName`, then `XRRFreeMonitors`.
   - Otherwise RandR ≥1.2: `XRRGetScreenResourcesCurrent` → `XRRGetCrtcInfo` for active CRTCs, plus `XRRGetOutputPrimary`.
   - Otherwise `XineramaIsActive` → `XineramaQueryScreens`.
   - Otherwise one monitor from `XDisplayWidth`/`XDisplayHeight` for each `XScreenCount` screen.
4. **Screen grabber (root drawable):**
   - SHM path (§2.3): `XShmCreateImage` → `shmget` → `shmat` → `XShmAttach` → `XSync`+error check → `shmctl(IPC_RMID)`.
   - Per frame: `XShmGetImage(root, x, y)`, then copy with the stride.
   - On attach failure, use `XGetImage(root, x, y, w, h, AllPlanes, ZPixmap)` → copy → `XDestroyImage`.
   - Validate `bits_per_pixel`, the masks and `byte_order`. Emit BGRA. Composite the cursor from `XFixesGetCursorImage` (cached by serial) on request.
5. **Window list:**
   - If `_NET_SUPPORTING_WM_CHECK` is valid, read `_NET_CLIENT_LIST_STACKING`. Otherwise use `XQueryTree(root)` filtered (§4.4), plus the `WM_STATE` descent for reparenting WMs.
   - Per window: `_NET_WM_NAME`→`WM_NAME`, `WM_CLASS`, PID via `XResQueryClientIds(PID mask)` → `_NET_WM_PID`, geometry via `XGetWindowAttributes` + `XTranslateCoordinates`, plus `_NET_WM_STATE_HIDDEN`, `_NET_FRAME_EXTENTS`.
   - Every call goes through the error-checked wrapper. BadWindow skips the window.
6. **Window grabber (Composite):**
   - Setup: `XSelectInput(win, StructureNotifyMask)` → `XCompositeRedirectWindow(win, Automatic)` → `XSync`.
   - Then `pix = XCompositeNameWindowPixmap(win)` → `XDamageCreate(win, NonEmpty)` → SHM image with the window's visual and depth.
   - Per frame:
     - Drain events. On `ConfigureNotify` with a size change, `XFreePixmap(pix)`, rename, and rebuild the image. On `UnmapNotify`, mark the window not viewable and return the last frame with `is_stale=True`. On `DestroyNotify`, raise `WindowGone`.
     - If Damage fired (or always, at low rates), `XShmGetImage(pix, 0, 0)` → `XDamageSubtract(dmg, 0, 0)`.
   - Teardown: `XDamageDestroy` → `XFreePixmap` → `XCompositeUnredirectWindow(win, Automatic)` → `XSync`.
   - Option `include_frame=True` resolves the frame (ancestor below root) and redirects that instead.
   - Without Composite, fall back to root capture of the window rectangle and flag `occlusion_free=False`.
7. **Audio backend:**
   - Detect with `pactl info`.
   - Loopback: libpulse-simple via ctypes on `<default sink>.monitor`.
   - Per-app:
     - Enumerate `pactl -f json list sink-inputs` filtered by PID (and descendants via `/proc/<pid>/task/*/children`).
     - Spawn one `parec --monitor-stream=<idx> --raw --format=float32le --rate=48000 --channels=2 --latency-msec=20 --property=node.dont-reconnect=true` per stream.
     - Mix in Python and follow `pactl subscribe` for new and removed streams.
   - If only PipeWire tools exist, fall back to `pw-dump` + `pw-record --target <serial> --raw -P '{"node.dont-reconnect":true}' -`.
   - Do not use the null-sink approach (b) unless the user explicitly opts in.
8. **Tests (WSL2/CI):**
   - Start `Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp` and assert the extensions via the library's own probe.
   - Spawn `xterm`/`xeyes` windows. Overlap them to test that Composite capture is occlusion-free, and unmap one to test stale-frame handling.
   - Kill a window mid-capture to test BadWindow handling, and kill Xvfb to test IO error handling.
   - Audio: run a private `pipewire`+`wireplumber`+`pipewire-pulse` with a temp `XDG_RUNTIME_DIR`, play a sine with `pw-play`, and capture it with `--monitor-stream`.
