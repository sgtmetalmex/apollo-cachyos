#!/usr/bin/env python3
"""
evdi_pageflip_probe.py — standalone EVDI capture rate probe.

PURPOSE
-------
Apollo's user is hitting ~50-57 fps when streaming a STATIC desktop from
KDE Plasma 6 Wayland to a Steam Deck LCD via the Apollo+EVDI virtual
display. The full stream stack (capture loop, encoder, network, decoder)
makes it hard to see where the cap is. This script removes every layer
except libevdi and kwin so we can MEASURE the cap directly:

  - Creates a fresh EVDI device (1280x800@60Hz).
  - Connects it (with a sane EDID) and asks kscreen-doctor to make it
    the primary output so kwin actually pageflips into it.
  - Registers one BGRA buffer.
  - For 10 seconds, runs the same request_update/poll/grab_pixels loop
    Apollo's evdi_events_loop() runs, but with TIGHT 250us iteration
    sleeps and a CLOCK_MONOTONIC time stamp on every grab_pixels event.

For each 1-second window it reports:
  - kwin pageflips delivered (events_per_sec) — the ACTUAL rate kwin
    commits to the EVDI device when nothing visual is changing.
  - grab_pixels invocations (== events; should match).
  - Mean grab_pixels latency in microseconds (kernel ioctl + uaccess).
  - Inner loop iterations (sanity check — we never block-sleep).

WHAT THE RESULTS MEAN
---------------------
  events_per_sec ~= 60 → kwin IS pageflipping at 60Hz. The fps cap
    is downstream of libevdi (capture copy_to, encoder, network).
  events_per_sec ~= 30 → kwin is committing at half rate (the classic
    KWIN_DRM_USE_MODIFIERS=0 / KWIN_DRM_NO_AMS bug). Apply that
    workaround at the kwin level — it's not an Apollo bug.
  events_per_sec ~= 50-57 → kwin is throttling pageflips for some
    OTHER reason (clock source, vrr negotiation, etc.).
  events_per_sec ~= 0 → kwin never enabled the EVDI output. Check
    kscreen-doctor output and the EDID we shipped.

USAGE
-----
  sudo modprobe evdi    # if not already loaded
  python3 evdi_pageflip_probe.py [seconds]
"""

import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
from ctypes import POINTER, Structure, c_bool, c_char_p, c_int, c_int32, c_size_t, c_uint, c_uint8, c_uint16, c_uint32, c_void_p, pointer


# ---------------------------------------------------------------- libevdi ABI

class EvdiRect(Structure):
    _fields_ = [("x1", c_int), ("y1", c_int), ("x2", c_int), ("y2", c_int)]


class EvdiMode(Structure):
    _fields_ = [
        ("width", c_int), ("height", c_int), ("refresh_rate", c_int),
        ("bits_per_pixel", c_int), ("pixel_format", c_uint32),
    ]


class EvdiBuffer(Structure):
    _fields_ = [
        ("id", c_int), ("buffer", c_void_p),
        ("width", c_int), ("height", c_int), ("stride", c_int),
        ("rects", POINTER(EvdiRect)), ("rect_count", c_int),
    ]


class EvdiCursorSet(Structure):
    _fields_ = [
        ("hot_x", c_int32), ("hot_y", c_int32),
        ("width", c_uint32), ("height", c_uint32),
        ("enabled", c_uint8),
        ("buffer_length", c_uint32),
        ("buffer", POINTER(c_uint32)),
        ("pixel_format", c_uint32),
        ("stride", c_uint32),
    ]


class EvdiCursorMove(Structure):
    _fields_ = [("x", c_int32), ("y", c_int32)]


class EvdiDdcciData(Structure):
    _fields_ = [
        ("address", c_uint16), ("flags", c_uint16),
        ("buffer_length", c_uint32),
        ("buffer", POINTER(c_uint8)),
    ]


DpmsHandler = ctypes.CFUNCTYPE(None, c_int, c_void_p)
ModeChangedHandler = ctypes.CFUNCTYPE(None, EvdiMode, c_void_p)
UpdateReadyHandler = ctypes.CFUNCTYPE(None, c_int, c_void_p)
CrtcStateHandler = ctypes.CFUNCTYPE(None, c_int, c_void_p)
CursorSetHandler = ctypes.CFUNCTYPE(None, EvdiCursorSet, c_void_p)
CursorMoveHandler = ctypes.CFUNCTYPE(None, EvdiCursorMove, c_void_p)
DdcciDataHandler = ctypes.CFUNCTYPE(None, EvdiDdcciData, c_void_p)


class EvdiEventContext(Structure):
    _fields_ = [
        ("dpms_handler", DpmsHandler),
        ("mode_changed_handler", ModeChangedHandler),
        ("update_ready_handler", UpdateReadyHandler),
        ("crtc_state_handler", CrtcStateHandler),
        ("cursor_set_handler", CursorSetHandler),
        ("cursor_move_handler", CursorMoveHandler),
        ("ddcci_data_handler", DdcciDataHandler),
        ("user_data", c_void_p),
    ]


def load_libevdi():
    lib = ctypes.CDLL("libevdi.so.1", use_errno=True)
    lib.evdi_add_device.restype = c_int
    lib.evdi_open.argtypes = [c_int]
    lib.evdi_open.restype = c_void_p
    lib.evdi_close.argtypes = [c_void_p]
    lib.evdi_connect.argtypes = [c_void_p, c_char_p, c_uint, c_uint]
    # NOTE: evdi_connect width/height/sku_area_limit signature; many libevdi
    # versions take (handle, edid, edid_length, sku_area_limit). We'll use
    # connect2 if available since it takes pixel_format explicitly.
    lib.evdi_disconnect.argtypes = [c_void_p]
    lib.evdi_register_buffer.argtypes = [c_void_p, EvdiBuffer]
    lib.evdi_unregister_buffer.argtypes = [c_void_p, c_int]
    lib.evdi_request_update.argtypes = [c_void_p, c_int]
    lib.evdi_request_update.restype = c_bool
    lib.evdi_grab_pixels.argtypes = [c_void_p, POINTER(EvdiRect), POINTER(c_int)]
    lib.evdi_handle_events.argtypes = [c_void_p, POINTER(EvdiEventContext)]
    lib.evdi_get_event_ready.argtypes = [c_void_p]
    lib.evdi_get_event_ready.restype = c_int
    return lib


# Apollo's default 1920x1080 EDID copied verbatim from
# src/platform/linux/virtual_display.cpp `default_edid`. This is the same
# EDID Apollo hands to libevdi when it creates a virtual display via
# `createVirtualDisplay()`. Using it here means the probe sees exactly
# the same kwin behavior Apollo does. (The streamed resolution doesn't
# affect the libevdi pageflip rate we're measuring.)
EDID = bytes([
    0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00,
    0x1E, 0x6D, 0x00, 0x00, 0x01, 0x01, 0x01, 0x01,
    0x00, 0x1D, 0x01, 0x04, 0xB5, 0x3C, 0x22, 0x78, 0x3A,
    0xFC, 0x81, 0xA4, 0x55, 0x4D, 0x9D, 0x25, 0x12, 0x50, 0x54,
    0x21, 0x08, 0x00,
    0xD1, 0xC0, 0x81, 0x80, 0x81, 0xC0,
    0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01,
    0x02, 0x3A, 0x80, 0x18, 0x71, 0x38, 0x2D, 0x40,
    0x58, 0x2C, 0x45, 0x00, 0x56, 0x50, 0x21, 0x00, 0x00, 0x1E,
    0x00, 0x00, 0x00, 0xFC, 0x00,
    ord('A'), ord('P'), ord('O'), ord('L'), ord('L'), ord('O'), ord(' '),
    ord('V'), ord('D'), ord('I'), ord('S'), ord('P'), ord('\n'),
    0x00, 0x00, 0x00, 0xFD, 0x00,
    0x32, 0x4B, 0x1E, 0x51, 0x11, 0x00, 0x0A, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20,
    0x00, 0x00,
])
# Pad to 128 bytes if short (the above is meant to be exactly 128; this is
# defensive in case of byte-count drift).
if len(EDID) < 128:
    EDID = EDID + bytes(128 - len(EDID))
elif len(EDID) > 128:
    EDID = EDID[:128]
# Recompute checksum (last byte such that sum mod 256 == 0).
_edid = bytearray(EDID)
_edid[-1] = (256 - (sum(_edid[:-1]) % 256)) % 256
EDID = bytes(_edid)
assert len(EDID) == 128, f"EDID is {len(EDID)} bytes, expected 128"


# ---------------------------------------------------------------- helpers

def run(cmd, check=True, timeout=10):
    """Run a shell command, return (returncode, stdout, stderr)."""
    p = subprocess.run(cmd, shell=isinstance(cmd, str),
                       capture_output=True, text=True, timeout=timeout)
    if check and p.returncode != 0:
        print(f"[probe] command failed: {cmd}", file=sys.stderr)
        print(p.stdout, file=sys.stderr)
        print(p.stderr, file=sys.stderr)
    return p.returncode, p.stdout, p.stderr


def find_evdi_card():
    """Scan /sys/class/drm/cardN/device/driver for the most recently added evdi node."""
    candidates = []
    for entry in os.listdir("/sys/class/drm"):
        if not entry.startswith("card") or "-" in entry:
            continue
        drv_link = os.path.realpath(f"/sys/class/drm/{entry}/device/driver")
        if "evdi" in drv_link:
            try:
                ctime = os.path.getctime(f"/sys/class/drm/{entry}")
            except OSError:
                ctime = 0
            candidates.append((ctime, entry))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def kscreen_enable(output_name, promote_primary):
    """Enable `output_name` via kscreen-doctor. If promote_primary, also set
    priority 1 (this makes it the PRIMARY display - your main screen will
    likely go dark for the duration of the test)."""
    rc, _, _ = run(["kscreen-doctor", f"output.{output_name}.enable"], check=False)
    if rc != 0:
        print(f"[probe] kscreen-doctor enable failed (kscreen may not be installed)")
        return False
    if promote_primary:
        run(["kscreen-doctor", f"output.{output_name}.priority.1"], check=False)
    return True


def kscreen_disable(output_name):
    """Disable `output_name` via kscreen-doctor to restore the previous setup."""
    run(["kscreen-doctor", f"output.{output_name}.disable"], check=False)


def find_kscreen_output_for_card(card_name):
    """kscreen-doctor outputs JSON listing. Return the kscreen output name that
    matches the just-created EVDI cardN."""
    rc, out, err = run(["kscreen-doctor", "-j"], check=False)
    if rc != 0:
        return None
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return None
    for o in data.get("outputs", []):
        # Match heuristic: name contains "VIRTUAL" or "EVDI" or is the only
        # currently-disconnected slot that just appeared.
        name = o.get("name", "")
        if name.upper().startswith(("VIRTUAL", "EVDI")):
            return name
    return None


# ---------------------------------------------------------------- main probe

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("seconds", nargs="?", type=int, default=10,
                        help="how long to measure (default: 10s)")
    parser.add_argument("--no-kscreen", action="store_true",
                        help="skip kscreen-doctor enable (probe won't get any pageflips)")
    parser.add_argument("--promote", action="store_true",
                        help="promote the new EVDI output to primary (will black out your main screen for the duration of the test; only use if your stream session does this anyway)")
    args = parser.parse_args()

    print(f"[probe] libevdi probe — {args.seconds}s pageflip rate measurement")

    # Pre-flight: kernel module loaded?
    if not os.path.exists("/sys/module/evdi"):
        print("[probe] ERROR: evdi kernel module not loaded. Run: sudo modprobe evdi")
        return 2

    libevdi = load_libevdi()

    # 1. Create a new EVDI device and open it.
    print("[probe] evdi_add_device()...")
    rc = libevdi.evdi_add_device()
    if rc < 0:
        print(f"[probe] evdi_add_device failed (rc={rc}). Are you in the 'video' group "
              "or running as root? Check /dev/dri/card* permissions.")
        return 2
    # The new card index isn't returned directly. We re-scan and pick the newest.
    time.sleep(0.2)
    card = find_evdi_card()
    if not card:
        print("[probe] couldn't locate newly created EVDI card under /sys/class/drm")
        return 2
    card_index = int(card[len("card"):])
    print(f"[probe] new EVDI card = /dev/dri/{card} (index {card_index})")

    handle = libevdi.evdi_open(card_index)
    if not handle:
        print(f"[probe] evdi_open({card_index}) returned NULL")
        return 2

    # 2. Connect with our 1280x800@60 EDID.
    # sku_area_limit is arbitrary; large number just says "any resolution".
    libevdi.evdi_connect(handle, EDID, len(EDID), 1280 * 800 * 4)
    print("[probe] evdi_connect() done — kwin should now see a 1280x800 display")

    # 3. Ask kscreen-doctor to enable it so kwin pageflips into it.
    kscreen_output = None
    if not args.no_kscreen:
        time.sleep(0.5)  # let DRM uevent settle
        kscreen_output = find_kscreen_output_for_card(card)
        if kscreen_output:
            print(f"[probe] kscreen output for this EVDI card: {kscreen_output}")
            kscreen_enable(kscreen_output, args.promote)
            if args.promote:
                print("[probe] promoted to PRIMARY — your physical screen will be dark "
                      "for the duration of this run")
            else:
                print("[probe] enabled (not promoted) — kwin will compose to BOTH your "
                      "physical screen and the EVDI device")
        else:
            print("[probe] WARNING: couldn't find a matching kscreen output. "
                  "kwin may not pageflip; events_per_sec will likely be 0.")

    # 4. Register a destination buffer.
    width, height = 1280, 800
    stride = width * 4
    buf_data = (c_uint8 * (stride * height))()
    rects = (EvdiRect * 16)()
    evdi_buf = EvdiBuffer(
        id=42,
        buffer=ctypes.cast(buf_data, c_void_p),
        width=width, height=height, stride=stride,
        rects=ctypes.cast(rects, POINTER(EvdiRect)),
        rect_count=0,
    )
    libevdi.evdi_register_buffer(handle, evdi_buf)
    print(f"[probe] registered buffer id=42 ({width}x{height} BGRA8888)")

    # 5. Run the same poll/grab/request loop Apollo uses, but with tighter
    #    timings and per-second statistics.
    ctx = EvdiEventContext()
    # No-op callbacks
    @UpdateReadyHandler
    def _on_update_ready(buffer_id, user_data):
        pass
    ctx.update_ready_handler = _on_update_ready

    import select
    poller = select.poll()
    fd = libevdi.evdi_get_event_ready(handle)
    if fd < 0:
        print(f"[probe] evdi_get_event_ready returned {fd}")
        return 2
    poller.register(fd, select.POLLIN)

    libevdi.evdi_request_update(handle, 42)

    end_t = time.monotonic() + args.seconds
    window_t = time.monotonic() + 1.0
    events = grabs = polls = 0
    win_events = win_grabs = win_polls = 0
    grab_us_total = 0
    win_grab_us = 0
    rect_count = c_int(16)

    print(f"[probe] measuring for {args.seconds}s...")
    print("[probe]   second  events  grabs  polls  avg_grab_us")
    while time.monotonic() < end_t:
        polls += 1
        win_polls += 1
        ready = poller.poll(1)  # 1 ms timeout
        if ready:
            libevdi.evdi_handle_events(handle, pointer(ctx))
            t0 = time.monotonic_ns()
            libevdi.evdi_grab_pixels(handle, rects, ctypes.byref(rect_count))
            t1 = time.monotonic_ns()
            grab_us = (t1 - t0) // 1000
            grab_us_total += grab_us
            win_grab_us += grab_us
            grabs += 1
            events += 1
            win_grabs += 1
            win_events += 1
            libevdi.evdi_request_update(handle, 42)
        # else: poll timeout — no event. Loop again.

        if time.monotonic() >= window_t:
            avg = (win_grab_us // win_grabs) if win_grabs else 0
            elapsed_s = int(args.seconds - (end_t - time.monotonic()))
            print(f"[probe]   {elapsed_s:>5}  {win_events:>6}  {win_grabs:>5}  "
                  f"{win_polls:>5}  {avg:>11}")
            win_events = win_grabs = win_polls = win_grab_us = 0
            window_t = time.monotonic() + 1.0

    # 6. Aggregate report.
    duration = args.seconds
    avg_total = (grab_us_total // grabs) if grabs else 0
    print()
    print(f"[probe] ── summary over {duration}s ───────────────────────────")
    print(f"[probe]  total events:      {events}")
    print(f"[probe]  total grab_pixels: {grabs}")
    print(f"[probe]  total poll iters:  {polls}")
    print(f"[probe]  mean pageflip Hz:  {events / duration:.2f}")
    print(f"[probe]  mean grab_pixels:  {avg_total} us")
    print()
    if events / duration < 30:
        print("[probe] DIAGNOSIS: kwin is NOT pageflipping into this EVDI device.")
        print("[probe]   - confirm kscreen-doctor promoted it: kscreen-doctor -o")
        print("[probe]   - confirm kwin is on Wayland (env XDG_SESSION_TYPE=wayland)")
    elif 25 <= events / duration <= 35:
        print("[probe] DIAGNOSIS: kwin is pageflipping at HALF refresh rate.")
        print("[probe]   - try: KWIN_DRM_USE_MODIFIERS=0 plasmashell --replace")
        print("[probe]   - or rebuild kwin with no atomic modesetting (KWIN_DRM_NO_AMS=1)")
    elif 55 <= events / duration <= 62:
        print("[probe] DIAGNOSIS: kwin IS pageflipping at ~60Hz. The 50-57fps cap")
        print("[probe]   the user sees in moonlight is DOWNSTREAM of libevdi —")
        print("[probe]   likely capture copy_to throughput, encoder latency,")
        print("[probe]   or network packet loss. Check apollo's [FPS-CAP]/[FPS-ENC] logs.")
    else:
        print(f"[probe] DIAGNOSIS: kwin pageflip rate is {events / duration:.1f} Hz — unusual.")
        print("[probe]   This is between 'pageflipping normally' and 'no events at all'.")
        print("[probe]   Look for VRR negotiation, EDID-rejected, or kwin DRM driver bugs.")

    # 7. Cleanup.
    if kscreen_output and not args.no_kscreen:
        kscreen_disable(kscreen_output)
    libevdi.evdi_unregister_buffer(handle, 42)
    libevdi.evdi_disconnect(handle)
    libevdi.evdi_close(handle)
    # Best-effort: remove the EVDI card from the kernel. Will silently fail
    # without write access to /sys/devices/evdi/remove_all.
    try:
        with open("/sys/devices/evdi/remove_all", "w") as f:
            f.write("1\n")
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[probe] interrupted")
        sys.exit(130)
