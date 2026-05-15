#!/bin/bash
# Launch gamescope from apollo's `detached` array with stdout/stderr captured
# to ~/.local/state/apollo/gamescope.log.
#
# Why this script exists instead of inline bash in apps.json: apollo's
# `$(VAR)` env-var substitution in apps.json is GREEDY — it intercepts every
# `$(...)` pattern at apps.json parse time, including bash's own command
# substitutions like `$(date)` and `$(dirname "$LOG")`, replacing each with
# an empty string. That makes any non-trivial inline bash silently broken.
# A standalone script bypasses apollo's substitution entirely.

set -u

LOG_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/apollo"
LOG="$LOG_DIR/gamescope.log"
mkdir -p "$LOG_DIR"

# Strip the decimal Apollo appends to APOLLO_CLIENT_FPS ("60.000" -> "60").
FPS="${APOLLO_CLIENT_FPS%.*}"

{
    echo
    echo "--- $(date) launch ---"
    echo "WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-} XDG_SESSION_TYPE=${XDG_SESSION_TYPE:-}"
    echo "APOLLO_CLIENT_WIDTH=${APOLLO_CLIENT_WIDTH:-} APOLLO_CLIENT_HEIGHT=${APOLLO_CLIENT_HEIGHT:-} FPS=$FPS"
    exec gamescope \
        -W "${APOLLO_CLIENT_WIDTH:-1280}" \
        -H "${APOLLO_CLIENT_HEIGHT:-800}" \
        -r "${FPS:-60}" \
        -f -e \
        -F fsr \
        --fsr-sharpness 4 \
        -- steam -bigpicture
} >> "$LOG" 2>&1
