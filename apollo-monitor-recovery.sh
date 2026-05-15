#!/bin/bash
# apollo-monitor-recovery — restore the physical primary monitor after apollo exits.
#
# WHY
# ---
# Apollo's EVDI virtual display flow disables the user's physical primary
# monitor for the duration of a stream so games can't spawn on it. On a
# clean shutdown apollo restores the physical output itself. But if apollo
# is killed before it can clean up (SIGKILL from the OOM killer, hard
# power-off, kernel panic), the physical monitor stays disabled and the
# user is left without a working desktop until they SSH in.
#
# This script is installed as ExecStopPost on the apollo systemd user
# service. systemd runs ExecStopPost AFTER the main process exits, even
# if that exit was a SIGKILL. The script reads the saved_primary state
# file (written by apollo when the stream starts) and asks kscreen-doctor
# to re-enable + promote it as priority 1. On success it deletes the state
# file so the next apollo startup's recover_on_startup() is a no-op.
#
# Also runs harmlessly when apollo exits cleanly (state file already
# deleted by apollo's own on_evdi_removed() — we just see nothing to do).
#
# Exit code is always 0 so systemd doesn't get confused on clean stops.

set -u

# Locate the state file the same way apollo's virtual_display.cpp does.
if [[ -n "${XDG_STATE_HOME:-}" ]]; then
    state_dir="${XDG_STATE_HOME}/apollo"
elif [[ -n "${HOME:-}" ]]; then
    state_dir="${HOME}/.local/state/apollo"
else
    echo "[apollo-monitor-recovery] neither XDG_STATE_HOME nor HOME set; nothing to do" >&2
    exit 0
fi

state_file="${state_dir}/saved-primary"

if [[ ! -f "$state_file" ]]; then
    # Apollo cleaned up itself, or never created a stream. Nothing to do.
    exit 0
fi

primary=$(< "$state_file")
# Strip any whitespace defensively.
primary="${primary//[$'\t\r\n ']/}"

if [[ -z "$primary" ]]; then
    echo "[apollo-monitor-recovery] state file empty; removing" >&2
    rm -f -- "$state_file"
    exit 0
fi

# Strict charset validation (mirrors is_safe_name in virtual_display.cpp).
# Connector names are like DP-3, HDMI-A-1, DVI-I-2. Refuse anything outside
# this set so we never shell-interpolate something exotic.
if [[ ! "$primary" =~ ^[A-Za-z0-9_-]+$ ]]; then
    echo "[apollo-monitor-recovery] state file contains unsafe name '$primary'; removing" >&2
    rm -f -- "$state_file"
    exit 0
fi

# kscreen-doctor needs the user's Wayland session bus. In systemd --user
# units those env vars are usually already set (DBUS_SESSION_BUS_ADDRESS,
# WAYLAND_DISPLAY, XDG_RUNTIME_DIR). If not, the kscreen-doctor call will
# fail; that's OK — we delete the state file and let the next apollo
# startup retry the restore.
if command -v kscreen-doctor >/dev/null 2>&1; then
    echo "[apollo-monitor-recovery] restoring $primary as primary via kscreen-doctor" >&2
    if kscreen-doctor "output.${primary}.enable" "output.${primary}.priority.1" >/dev/null 2>&1; then
        rm -f -- "$state_file"
        echo "[apollo-monitor-recovery] success; state file removed" >&2
    else
        echo "[apollo-monitor-recovery] kscreen-doctor failed; leaving state file in place for next startup" >&2
    fi
else
    echo "[apollo-monitor-recovery] kscreen-doctor not found; leaving state file in place" >&2
fi

exit 0
