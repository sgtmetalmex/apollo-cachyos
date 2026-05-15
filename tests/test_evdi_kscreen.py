#!/usr/bin/env python3
"""
End-to-end test for the apollo EVDI -> kscreen-doctor integration.

Simulates exactly what Apollo's patched createVirtualDisplay does:
  1. Snapshot current kscreen outputs (before).
  2. evdi_add_device() via sysfs (libevdi does the same internally).
  3. evdi_open() the new device.
  4. evdi_connect() with a 128-byte EDID for 1280x800 (Steam Deck native).
  5. Poll kscreen-doctor -j up to ~3s for a new connected output.
  6. Run: kscreen-doctor output.<new>.enable output.<new>.priority.1
                          output.<old_primary>.priority.2
  7. Verify the new output is now priority 1.
  8. Restore the old primary, disconnect, remove the EVDI device.

Reports pass/fail at each step so we can pinpoint where things break.
"""
import ctypes
import json
import subprocess
import sys
import time
from pathlib import Path

OK = "\033[32m[OK]\033[0m"
FAIL = "\033[31m[FAIL]\033[0m"
INFO = "\033[36m[..]\033[0m"


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def kscreen_outputs() -> list[dict]:
    cp = run(["kscreen-doctor", "-j"])
    if cp.returncode != 0:
        return []
    return json.loads(cp.stdout).get("outputs", [])


def connected_names() -> set[str]:
    return {o["name"] for o in kscreen_outputs() if o.get("connected")}


def current_primary() -> str:
    for o in kscreen_outputs():
        if o.get("priority") == 1 and o.get("enabled") and o.get("connected"):
            return o["name"]
    return ""


# Same EDID Apollo generates for 1280x800 (Steam Deck) — minimal subset.
# We only need a valid 128-byte EDID that has a "connected monitor" signature.
def make_edid_1280x800() -> bytes:
    edid = bytearray(128)
    # Header
    edid[0:8] = b"\x00\xff\xff\xff\xff\xff\xff\x00"
    # Manufacturer "APO" (Apollo) — actually irrelevant for the test
    edid[8:10] = b"\x05\x10"
    # Product code / serial
    edid[10:18] = b"\x01\x00\x01\x00\x01\x00\x01\x00"
    edid[18] = 0x1d  # Week
    edid[19] = 0x01  # Year
    edid[20] = 0x04  # EDID 1.4
    edid[21] = 0xb5  # Digital, 8bpc, DisplayPort
    edid[22] = 0x3c  # Width cm
    edid[23] = 0x22  # Height cm
    edid[24] = 0x78  # Gamma
    edid[25] = 0x3a  # Features
    # Chromaticity (placeholder values, KDE doesn't care for detection)
    edid[26:36] = b"\xfc\x81\xa4\x55\x4d\x9d\x25\x12\x50\x54"
    edid[36:39] = b"\x21\x08\x00"
    # Standard timings - 8 slots, fill with 0x01 0x01 (unused)
    for i in range(38, 54):
        edid[i] = 0x01
    # Detailed Timing Descriptor #1: 1280x800@60Hz (CVT-RB)
    # Pixel clock: 71 MHz / 10 kHz = 7100 → 0x1bbc
    edid[54:56] = b"\xbc\x1b"
    edid[56] = 1280 & 0xff
    edid[57] = 0  # h-blank low
    edid[58] = ((1280 >> 4) & 0xf0) | (0 & 0x0f)
    edid[59] = 800 & 0xff
    edid[60] = 0
    edid[61] = ((800 >> 4) & 0xf0)
    edid[62] = 48  # h-front
    edid[63] = 32  # h-sync
    edid[64] = (3 << 4) | 6
    edid[65] = 0
    edid[66] = 0xa0  # widthmm low (just to fill)
    edid[67] = 0x69
    edid[68] = 0
    edid[69] = 0
    edid[70] = 0
    edid[71] = 0x18  # digital separate sync
    # The remaining descriptors filled with zeros; not strictly valid but
    # KDE+EVDI generally accepts incomplete EDIDs for hotplug detection.
    edid[126] = 0  # Extension flag
    # Checksum
    cks = (-sum(edid[:127])) & 0xff
    edid[127] = cks
    return bytes(edid)


def main() -> int:
    # Pre-flight
    if not Path("/sys/devices/evdi/add").exists():
        print(f"{FAIL} EVDI sysfs not present — is the kernel module loaded?")
        return 2

    # Load libevdi.so.1
    try:
        evdi = ctypes.CDLL("libevdi.so.1")
    except OSError as e:
        print(f"{FAIL} Cannot load libevdi.so.1: {e}")
        return 2

    evdi.evdi_add_device.restype = ctypes.c_int
    evdi.evdi_add_device.argtypes = []
    evdi.evdi_check_device.restype = ctypes.c_int
    evdi.evdi_check_device.argtypes = [ctypes.c_int]
    evdi.evdi_open.restype = ctypes.c_void_p
    evdi.evdi_open.argtypes = [ctypes.c_int]
    evdi.evdi_close.restype = None
    evdi.evdi_close.argtypes = [ctypes.c_void_p]
    evdi.evdi_connect.restype = None
    evdi.evdi_connect.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                  ctypes.c_uint, ctypes.c_uint32]
    evdi.evdi_disconnect.restype = None
    evdi.evdi_disconnect.argtypes = [ctypes.c_void_p]
    EVDI_AVAILABLE = 0

    print(f"{INFO} Step 1: snapshot 'before' kscreen outputs")
    before = connected_names()
    original_primary = current_primary()
    print(f"     before: {sorted(before)}")
    print(f"     original primary: {original_primary!r}")

    print(f"{INFO} Step 2: evdi_add_device()")
    rc = evdi.evdi_add_device()
    if rc < 1:
        print(f"{FAIL} evdi_add_device returned {rc} (need >=1). "
              f"Are /sys/devices/evdi/{{add,remove_all}} writable?")
        return 1
    print(f"     evdi_add_device returned {rc} (bytes written, as expected)")

    # Mimic the patched find_available_evdi_device(): poll for new index
    print(f"{INFO} Step 3: scan for newly-AVAILABLE EVDI index (the patch)")
    new_index = -1
    for retry in range(50):
        for i in range(16):
            if evdi.evdi_check_device(i) == EVDI_AVAILABLE:
                new_index = i
                break
        if new_index >= 0:
            break
        time.sleep(0.05)
    if new_index < 0:
        print(f"{FAIL} no EVDI_AVAILABLE slot after 2.5s")
        run(["bash", "-c", "echo 1 > /sys/devices/evdi/remove_all"])
        return 1
    print(f"     new index: {new_index} -> /dev/dri/card{new_index}")

    print(f"{INFO} Step 4: evdi_open + evdi_connect with 1280x800 EDID")
    handle = evdi.evdi_open(new_index)
    if not handle:
        print(f"{FAIL} evdi_open({new_index}) returned NULL")
        run(["bash", "-c", "echo 1 > /sys/devices/evdi/remove_all"])
        return 1
    edid = make_edid_1280x800()
    evdi.evdi_connect(handle, edid, len(edid), 0)
    print(f"     connected with {len(edid)}-byte EDID")

    print(f"{INFO} Step 5: poll kscreen-doctor for new output (up to 3s)")
    new_name = None
    for _ in range(30):
        time.sleep(0.1)
        diff = connected_names() - before
        if diff:
            new_name = sorted(diff)[0]
            break

    if not new_name:
        print(f"{FAIL} kscreen did not enumerate the new EVDI output within 3s")
        print(f"     current connected: {sorted(connected_names())}")
        evdi.evdi_disconnect(handle)
        evdi.evdi_close(handle)
        run(["bash", "-c", "echo 1 > /sys/devices/evdi/remove_all"])
        return 1
    print(f"     {OK} new output detected: {new_name}")

    print(f"{INFO} Step 6: run the same kscreen-doctor command Apollo would")
    cmd = ["kscreen-doctor",
           f"output.{new_name}.enable",
           f"output.{new_name}.priority.1"]
    if original_primary:
        cmd.append(f"output.{original_primary}.priority.2")
    print(f"     $ {' '.join(cmd)}")
    cp = run(cmd)
    if cp.returncode != 0:
        print(f"{FAIL} kscreen-doctor failed (rc={cp.returncode}): {cp.stderr.strip()}")
    else:
        print(f"     kscreen-doctor returned 0")

    time.sleep(0.5)
    print(f"{INFO} Step 7: verify state after promote")
    for o in kscreen_outputs():
        marker = " <-- new EVDI" if o["name"] == new_name else ""
        print(f"     {o['name']:12} connected={o.get('connected')} "
              f"enabled={o.get('enabled')} priority={o.get('priority')}{marker}")
    new_primary = current_primary()
    if new_primary == new_name:
        print(f"     {OK} EVDI is now the priority-1 output")
        promote_ok = True
    else:
        print(f"     {FAIL} primary is still {new_primary!r}, expected {new_name!r}")
        promote_ok = False

    print(f"{INFO} Step 8: restore (mimics on_evdi_removed)")
    if original_primary:
        run(["kscreen-doctor", f"output.{original_primary}.priority.1"])
    evdi.evdi_disconnect(handle)
    evdi.evdi_close(handle)
    run(["bash", "-c", "echo 1 > /sys/devices/evdi/remove_all"])
    time.sleep(0.3)
    final_primary = current_primary()
    print(f"     primary after restore: {final_primary!r}")
    if final_primary == original_primary:
        print(f"     {OK} primary restored to original ({original_primary})")
        restore_ok = True
    else:
        print(f"     {FAIL} primary did not restore (got {final_primary!r})")
        restore_ok = False

    print()
    if promote_ok and restore_ok:
        print(f"{OK} ALL CHECKS PASSED — Apollo's kscreen integration should work end-to-end.")
        return 0
    else:
        print(f"{FAIL} something is wrong. See output above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
