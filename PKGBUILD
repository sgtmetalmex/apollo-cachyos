# Maintainer: Local build (Apollo-Linux fork w/ EVDI virtual display)
# Based on the AUR `apollo` PKGBUILD by xiota; source pointed at MrOz59/Apollo-Linux
# instead of upstream ClassicOldSong/Apollo so we get the Linux EVDI virtual
# display support that upstream doesn't have yet.

## options
: ${_use_sodeps:=false}

: ${_use_cuda:=false} # nvenc
# Only pin gcc to CUDA's required version if CUDA is actually installed locally.
# Querying `pacman -Si cuda` always succeeds (it's in the sync DB), which would
# force gcc15 onto users who don't have CUDA — breaking the build whenever
# CUDA's gcc requirement is newer than what they have. Gate on `-Qi` (local).
if pacman -Qi cuda &> /dev/null; then
  : ${_cuda_gcc_version:=$(LC_ALL=C pacman -Si cuda 2>/dev/null | grep -Pom1 '^Depends On\s*:.*\bgcc\K[0-9]+\b')}
fi

_pkgname="apollo"
pkgname="$_pkgname"
pkgver=0.4.8.r4.evdi
pkgrel=99
pkgdesc="A self-hosted game stream server (MrOz59 fork w/ EVDI Linux virtual display)"
url="https://github.com/MrOz59/Apollo-Linux"
license=('GPL-3.0-only')
arch=('x86_64')

depends=(
  'boost-libs'
  'evdi-dkms'   # provides libevdi.so + kernel module for virtual display
  'gtk3'
  'icu'
  'libayatana-appindicator'
  'libcap'
  'libdrm'
  'libevdev'
  'libnotify'
  'libpulse'
  'libva'
  'miniupnpc'
  'numactl'
  'openssl'
  'opus'
  'wayland'
)
makedepends=(
  "gcc${_cuda_gcc_version:-}"
  'boost'
  'cmake'
  'git'
  'ninja'
  # npm is satisfied by ~/.local/bin/npm shim -> pnpm (set via PATH in build())
)
optdepends=(
  'intel-media-driver: Intel GPU encoding support'
  'libva-mesa-driver: AMD GPU encoding support'
  'kscreen: needed on KDE Plasma to auto-enable the new EVDI output as primary during streaming sessions'
  'gamescope: enables the "Gamescope Steam Session" app entry (SteamOS Gaming Mode-style streaming with FSR upscaling)'
)

if pacman -Qi cuda &> /dev/null; then
  _use_cuda=true
fi

if [[ "${_use_cuda::1}" == "t" ]]; then
  makedepends+=('cuda')
  checkdepends+=('nvidia-utils')
  optdepends+=(
    'cuda: Nvidia GPU encoding support'
    'nvidia-utils: Nvidia GPU encoding support'
  )
fi

install="$_pkgname.install"

_pkgsrc="$_pkgname"
# Source:
#   - MrOz59/Apollo-Linux main branch (adds src/platform/linux/virtual_display.{cpp,h} for EVDI)
#   - 0001-fix-evdi-device-index.patch: fixes find_available_evdi_device() returning
#     evdi_add_device()'s byte count instead of the real DRM card index — without this
#     patch Apollo always opens the wrong card (the dGPU) and silently falls back to passthrough.
#   - 0002-promote-evdi-to-kscreen-primary.patch: after libevdi connects, asks
#     kscreen-doctor to enable the new output and make it priority-1. Without this
#     kwin leaves the new EVDI output disabled, no compositor draws to it, and
#     Apollo's KMS screencast captures the physical display instead of the
#     virtual one. Degrades gracefully on non-KDE systems.
#   - 0003-drop-spurious-drm-open.patch: removes the dead-code ::open() in
#     createVirtualDisplay that grabs an unused fd to /dev/dri/cardN. That fd
#     is never read/written, but it locks kwin out of the EVDI card (EBUSY) so
#     kwin can never enumerate the connector — without this fix, patch 0002's
#     polling never sees the new output and the stream silently mirrors the
#     physical display.
#   - 0004-kmsgrab-fallback-for-hotplugged-evdi.patch: kmsgrab.cpp's card
#     lookup bails with "shouldn't have happened" if the EVDI card was
#     hotplugged after startup (since card_descriptors is built once and
#     never refreshed). This makes the KMS capture use crtc geometry as a
#     fallback in that case, so the encoder can actually initialize on the
#     virtual display.
#   - evdi-perms.service: makes /sys/devices/evdi/{add,remove_all} writable by the video
#     group, required because Apollo has cap_sys_admin but NOT cap_dac_override and those
#     sysfs files are mode 0200 root-only by default.
#
#   - 0007-evdi-cpu-buffer-capture-backend.patch: Apollo's kmsgrab capture path
#     SIGSEGVs in gbm_create_device when pointed at an EVDI card (no render node).
#     This patch adds a parallel display_evdi_t backend that reads BGRA pixels
#     from a libevdi-filled CPU buffer instead — and absorbs the libevdi event
#     pump + stale-card cleanup that patches 0005+0006 (archived) tried to provide.
#   - 0008-disable-physical-primary-during-stream.patch: extends patch 0002 to
#     DISABLE (not just demote) the physical primary monitor during the EVDI
#     streaming session. Without this, leftover dual-GPU-era setups in
#     ~/.config/kwinoutputconfig.json can re-promote the physical monitor
#     and Steam/games spawn windows there instead of on the EVDI display.
#   - 0009-edid-exact-60hz-1280x800.patch: adds hand-tuned CVT-RB timing for
#     1280x800@60Hz (Steam Deck native). The generic CVT fallback produced
#     ~59Hz, causing kwin to pageflip at 59Hz and the Steam Deck to drop
#     effective fps to 54-56. Exact 60Hz timing matches the panel.
#   - 0010-unhide-resolution-scale-factor-on-linux.patch: the web UI hid the
#     Resolution Scale Factor slider with v-if="platform === 'windows'",
#     but the backend supports it on Linux too. Drop the gate.
#   - 0011-edid-advertise-only-requested-resolution.patch: zero out the
#     EDID established + standard timing slots so games see ONLY the
#     requested resolution in their display-settings menus.
#   - 0012-dxvk-hdr-off-when-client-sdr.patch: inject DXVK_HDR=0 +
#     PROTON_ENABLE_HDR=0 into the spawned game's environment when the
#     client requested SDR. Defeats DXVK's HDR-availability detection
#     via kwin's global wp_color_manager_v1 advertisement.
#   - 0013-vaapi-vbr-default-on-amd.patch: force VBR + single-frame VBV
#     on AMD VAAPI (default was uncontrolled CQP → 60-80 Mbps bitrate
#     spikes that stall the Steam Deck decoder).
#   - 0014-vaapi-quality-and-forced-idr.patch: add `quality=4` (balanced
#     low-latency on AMD RDNA encoders) and `forced_idr=1` (real IDR
#     frames on moonlight's mid-stream IDR requests) to all VAAPI
#     encoder profiles.
#   - 0015-add-gamescope-steam-session-app.patch: add "Gamescope Steam
#     Session" entry to the default apps.json template. Runs gamescope
#     wrapping Steam Big Picture with FSR upscaling + immediate-flips
#     for SteamOS Gaming Mode-style streaming experience.
#   - 0016-host-side-default-scale-factor.patch: add a global
#     `default_scale_factor` config field in sunshine.conf so the user
#     sets one number and every app inherits it (per-app override still
#     wins). Eliminates having to set scale-factor per-game.
#   - 0017 archived (two-PBO ring caused 1-frame latency on AMD UMA;
#     reverted to synchronous glTexSubImage2D which already runs at
#     near-zero cost on shared-memory GPUs).
#   - 0018-event-driven-evdi-capture.patch: hybrid rate-limited
#     condvar wait in display_evdi_t::capture — wakes early on a
#     fresh EVDI frame, still caps capture rate at the configured fps
#     so the encoder isn't overrun.
#   - 0019-vaapi-quality-configurable.patch: expose `vaapi_quality`
#     in sunshine.conf so user can lower encoder quality preset
#     (0=fastest, 8=slowest) to trade compression for encode speed
#     when the VAAPI encoder is the framerate bottleneck.
#   - 0020-fps-instrumentation-and-tighter-evdi-pump.patch: adds
#     per-second rate stats at every pipeline stage (EVDI pump,
#     capture loop, encoder loop). Logged every 5s so we can see
#     exactly where the fps cap is. Also drops the EVDI pump
#     usleep from 2000us to 500us for tighter kwin pageflip latency.
#   - 0021-monitor-recovery-safety-net.patch: NEVER leave the user
#     with a dark physical monitor. Writes a state file when apollo
#     disables a physical output, and recovers from it on next
#     startup AND via systemd ExecStopPost. Survives SIGSEGV /
#     SIGKILL / OOM / power-loss.
source=(
  "$_pkgsrc"::"git+https://github.com/MrOz59/Apollo-Linux.git#branch=main"
  "patches/0001-fix-evdi-device-index.patch"
  "patches/0002-promote-evdi-to-kscreen-primary.patch"
  "patches/0003-drop-spurious-drm-open.patch"
  "patches/0004-kmsgrab-fallback-for-hotplugged-evdi.patch"
  "patches/0007-evdi-cpu-buffer-capture-backend.patch"
  "patches/0008-disable-physical-primary-during-stream.patch"
  "patches/0009-edid-exact-60hz-1280x800.patch"
  "patches/0010-unhide-resolution-scale-factor-on-linux.patch"
  "patches/0011-edid-advertise-only-requested-resolution.patch"
  "patches/0012-dxvk-hdr-off-when-client-sdr.patch"
  "patches/0013-vaapi-vbr-default-on-amd.patch"
  "patches/0014-vaapi-quality-and-forced-idr.patch"
  "patches/0015-add-gamescope-steam-session-app.patch"
  "patches/0016-host-side-default-scale-factor.patch"
  "patches/0018-event-driven-evdi-capture.patch"
  "patches/0019-vaapi-quality-configurable.patch"
  "patches/0020-fps-instrumentation-and-tighter-evdi-pump.patch"
  "patches/0021-monitor-recovery-safety-net.patch"
  "patches/0022-evdi-search-range-to-64.patch"
  "apollo-monitor-recovery.sh"
  "apollo-gamescope-launch.sh"
  "evdi-perms.service"
)
sha256sums=('SKIP' 'SKIP' 'SKIP' 'SKIP' 'SKIP' 'SKIP' 'SKIP' 'SKIP' 'SKIP' 'SKIP' 'SKIP' 'SKIP' 'SKIP' 'SKIP' 'SKIP' 'SKIP' 'SKIP' 'SKIP' 'SKIP' 'SKIP' 'SKIP' 'SKIP' 'SKIP')

prepare() {
  cd "$_pkgsrc"

  local i _unwanted=(
    packaging/linux/flatpak/deps/flatpak-builder-tools
    packaging/linux/flatpak/deps/shared-modules
    third-party/doxyconfig
    third-party/nv-codec-headers
  )

  for i in "${_unwanted[@]}"; do
    if [ -e "$i" ]; then
      git rm -r "$i" 2>/dev/null || rm -rf "$i"
    fi
  done

  git submodule update --init --depth 1
  git -C third-party/moonlight-common-c submodule update --init --depth 1

  ## fix some names (sunshine -> apollo)
  sed -E -e 's&\bsunshine\b&"'${_pkgname}'"&g' -i cmake/prep/init.cmake cmake/packaging/unix.cmake
  sed -E -e '/set\(PROJECT_FQDN/s&^.*$&set(PROJECT_FQDN "'${_pkgname}'")&' -i cmake/compile_definitions/linux.cmake
  sed -E -e 's&\bsunshine\b&'${_pkgname}'&g' -i cmake/targets/common.cmake

  ## disable unwanted macros
  sed 's&macro(find_package)&macro(_disable_find_package)&' -i cmake/macros/common.cmake

  ## fix for Boost 1.89+
  sed -E -e 's&\b(Boost::)?(system)\b&&' -i third-party/Simple-Web-Server/CMakeLists.txt

  ## Patch MrOz59's broken find_available_evdi_device() so virtual display actually works
  ## on systems where the GPU isn't at /dev/dri/card0.
  patch -p1 -i "${srcdir}/0001-fix-evdi-device-index.patch"

  ## Tell KDE Plasma to actually enable the new EVDI output and make it primary,
  ## otherwise kwin leaves it disabled and Apollo's KMS screencast falls back to
  ## the physical display (so the Steam Deck sees the host's main monitor, not a
  ## separate virtual desktop).
  patch -p1 -i "${srcdir}/0002-promote-evdi-to-kscreen-primary.patch"

  ## Drop the spurious ::open() of /dev/dri/cardN that competes with kwin for
  ## DRM master — without this, kwin can never open the EVDI card (EBUSY) and
  ## patch 0002's poll for the new output silently times out.
  patch -p1 -i "${srcdir}/0003-drop-spurious-drm-open.patch"

  ## kmsgrab.cpp returns -1 (fatal "Couldn't find ... shouldn't have happened")
  ## when the EVDI card isn't in its startup-built card_descriptors list. Make
  ## it fall back to crtc geometry so the encoder can init on hotplugged EVDI.
  patch -p1 -i "${srcdir}/0004-kmsgrab-fallback-for-hotplugged-evdi.patch"

  # Patches 0005 + 0006 are archived under build/archive/. Replaced by patch 0007.

  ## EVDI CPU-buffer capture backend. Adds display_evdi_t (no GBM/EGL on the
  ## EVDI card, reads CPU buffer libevdi fills from kwin pageflips), the
  ## libevdi event pump (drains pixels so kwin pageflips complete), and
  ## /dev/dri/cardN cleanup on driver open/close.
  patch -p1 -i "${srcdir}/0007-evdi-cpu-buffer-capture-backend.patch"

  ## Disable the physical primary entirely during the stream so games can't
  ## spawn there. Restores on session end.
  patch -p1 -i "${srcdir}/0008-disable-physical-primary-during-stream.patch"

  ## Exact 60Hz EDID timing for Steam Deck native 1280x800 (fixes 59Hz drift).
  patch -p1 -i "${srcdir}/0009-edid-exact-60hz-1280x800.patch"

  ## Web UI: unhide Resolution Scale Factor slider on Linux (backend supports it).
  patch -p1 -i "${srcdir}/0010-unhide-resolution-scale-factor-on-linux.patch"

  ## EDID: advertise only the requested resolution so games don't see ghost modes.
  patch -p1 -i "${srcdir}/0011-edid-advertise-only-requested-resolution.patch"

  ## DXVK/Proton HDR off in spawned game env when client is SDR.
  patch -p1 -i "${srcdir}/0012-dxvk-hdr-off-when-client-sdr.patch"

  ## VAAPI: VBR by default on AMD so bitrate setting actually constrains output.
  patch -p1 -i "${srcdir}/0013-vaapi-vbr-default-on-amd.patch"

  ## VAAPI: quality=4 (low-latency preset) + forced_idr=1 (real IDR on request).
  patch -p1 -i "${srcdir}/0014-vaapi-quality-and-forced-idr.patch"

  ## Add "Gamescope Steam Session" entry to the default apps.json.
  patch -p1 -i "${srcdir}/0015-add-gamescope-steam-session-app.patch"

  ## Host-side global default_scale_factor in sunshine.conf.
  patch -p1 -i "${srcdir}/0016-host-side-default-scale-factor.patch"

  ## Hybrid rate-limited condvar wait in display_evdi_t (jitter-free fps cap).
  patch -p1 -i "${srcdir}/0018-event-driven-evdi-capture.patch"

  ## Expose vaapi_quality config knob (0-8, lower = faster encode).
  patch -p1 -i "${srcdir}/0019-vaapi-quality-configurable.patch"

  ## Per-second [FPS-EVDI]/[FPS-CAP]/[FPS-ENC] rate stats in the log every 5s
  ## so we can pinpoint where the framerate cap lives + tighten EVDI pump
  ## usleep from 2000us to 500us.
  patch -p1 -i "${srcdir}/0020-fps-instrumentation-and-tighter-evdi-pump.patch"

  ## Crash-safe monitor recovery: state file + ExecStopPost so the physical
  ## monitor always comes back even on SIGSEGV/SIGKILL/OOM/power-loss.
  patch -p1 -i "${srcdir}/0021-monitor-recovery-safety-net.patch"

  ## Scan EVDI device indices 0..63 (was 0..15) so freshly-created EVDI cards
  ## with high DRM minors are visible to apollo (otherwise we silently fall
  ## back to physical-display passthrough).
  patch -p1 -i "${srcdir}/0022-evdi-search-range-to-64.patch"

  ## Work around Apollo's phantom dependencies on `bootstrap` and `@fortawesome/fontawesome-free`.
  ## Their HTML/JS imports those packages but they're not in package.json — npm silently hoists
  ## them from a transitive dep (@lizardbyte/shared-web), pnpm doesn't. Tell pnpm to behave
  ## like npm for this build so `import { Dropdown } from 'bootstrap/dist/js/bootstrap'` resolves.
  install -Dm644 /dev/stdin .npmrc << 'END'
shamefully-hoist=true
END

  install -Dm644 /dev/stdin cmake/dependencies/Boost_Sunshine.cmake << END
include_guard(GLOBAL)
find_package(Boost COMPONENTS filesystem locale log program_options)
message(STATUS "Boost include dirs: \${Boost_INCLUDE_DIRS}")
message(STATUS "Boost libraries: \${Boost_LIBRARIES}")
END
}

build() (
  # Ensure the npm shim (-> pnpm) and pnpm itself are findable inside makepkg.
  # Prepend ~/.local/bin (the shim) and nvm node bin (real pnpm) to PATH.
  _nvm_node_bin="$(ls -1d "$HOME/.nvm/versions/node/v"*/bin 2>/dev/null | sort -V | tail -1)"
  export PATH="$HOME/.local/bin:${_nvm_node_bin}:$PATH"

  export BRANCH="main"
  export BUILD_VERSION="${pkgver}"
  export COMMIT="$(git -C "$_pkgsrc" rev-parse HEAD)"

  export CC="gcc${_cuda_gcc_version:+-$_cuda_gcc_version}"
  export CXX="g++${_cuda_gcc_version:+-$_cuda_gcc_version}"

  export CUDA_PATH=/opt/cuda
  export NVCC_CCBIN="/usr/bin/g++${_cuda_gcc_version:+-$_cuda_gcc_version}"

  local _cmake_options=(
    -B build
    -S "$_pkgsrc"
    -G Ninja
    -DCMAKE_BUILD_TYPE=None
    -DCMAKE_INSTALL_PREFIX='/usr'
    -DBUILD_DOCS=OFF
    -DBUILD_TESTS=OFF
    -Wno-dev

    # Explicitly point cmake at the npm shim so pnpm handles the web UI build.
    -DNPM="$HOME/.local/bin/npm"

    -DSUNSHINE_ASSETS_DIR="share/$_pkgname"
    -DSUNSHINE_EXECUTABLE_PATH="/usr/bin/$_pkgname"

    -DSUNSHINE_PUBLISHER_NAME="Local"
    -DSUNSHINE_PUBLISHER_WEBSITE="https://github.com/MrOz59/Apollo-Linux"
    -DSUNSHINE_PUBLISHER_ISSUE_URL="https://github.com/MrOz59/Apollo-Linux/issues"

    -DSUNSHINE_ENABLE_CUDA=ON
    -DSUNSHINE_ENABLE_DRM=ON
    -DSUNSHINE_ENABLE_TRAY=ON
    -DSUNSHINE_ENABLE_VAAPI=ON
    -DSUNSHINE_ENABLE_WAYLAND=ON
    -DSUNSHINE_ENABLE_X11=ON
  )

  if [[ "${_use_cuda::1}" == "t" ]]; then
    _cmake_options+=(-DCUDA_FAIL_ON_MISSING=ON)
  else
    _cmake_options+=(-DCUDA_FAIL_ON_MISSING=OFF)
  fi

  cmake "${_cmake_options[@]}"
  cmake --build build
)

package() {
  depends+=(
    'avahi'
    'libx11'
    'libxcb'
    'libxfixes'
    'libxrandr'
    'mesa' # libgbm
  )

  if [[ "${_use_sodeps::1}" == "t" ]]; then
    eval "depends+=(
      'libboost_filesystem.so'
      'libboost_locale.so'
      'libboost_log.so'
      'libboost_program_options.so'
      'libboost_thread.so'
      'libcap.so'
      'libcrypto.so'
      'libcurl.so'
      'libevdev.so'
      'libglib-2.0.so'
      'libgobject-2.0.so'
      'libgtk-3.so'
      'libminiupnpc.so'
      'libnotify.so'
      'libnuma.so'
      'libopus.so'
      'libpulse-simple.so'
      'libpulse.so'
      'libssl.so'
      'libva-drm.so'
      'libva.so'
      'libwayland-client.so'
    )"
  fi

  DESTDIR="$pkgdir" cmake --install build

  # prevent conflict
  mv "$pkgdir"/usr/lib/modules-load.d/60-{sunshine,apollo}.conf 2>/dev/null || true
  mv "$pkgdir"/usr/lib/udev/rules.d/60-{sunshine,apollo}.rules 2>/dev/null || true

  # unwanted
  rm -rf "$pkgdir/usr/lib/systemd"
  rm -rf "$pkgdir/usr/share/applications"
  rm -rf "$pkgdir/usr/share/metainfo"

  install -Dm644 /dev/stdin "$pkgdir/usr/lib/systemd/user/$_pkgname.service" << END
[Unit]
Description=$pkgdesc
StartLimitIntervalSec=500
StartLimitBurst=5

[Service]
# Avoid starting ${_pkgname^} before the desktop is fully initialized.
ExecStartPre=/bin/sleep 5
ExecStart=/usr/bin/apollo

# Patch 0021: ALWAYS run the monitor-recovery script after apollo exits,
# even after SIGKILL/OOM. The "-" prefix tells systemd to ignore the exit
# code so a clean shutdown (where there's nothing to restore) doesn't
# leave the unit in a "failed" state.
ExecStopPost=-/usr/bin/apollo-monitor-recovery

Restart=on-failure
RestartSec=5s

[Install]
WantedBy=xdg-desktop-autostart.target
END

  # Patch 0021: monitor-recovery script invoked by ExecStopPost above.
  # Reads ~/.local/state/apollo/saved-primary (written by apollo when it
  # disables a physical output) and re-enables it via kscreen-doctor.
  install -Dm755 "${srcdir}/apollo-monitor-recovery.sh" "$pkgdir/usr/bin/apollo-monitor-recovery"

  # Gamescope wrapper for the "Gamescope Steam Session" app entry. Pure
  # bash script that reads APOLLO_CLIENT_WIDTH/HEIGHT/FPS from the env at
  # runtime, normalises FPS to integer, and execs gamescope -> steam
  # bigpicture. Lives outside apollo because apollo's $(VAR) substitution
  # in apps.json clashes with bash's $(...) syntax.
  install -Dm755 "${srcdir}/apollo-gamescope-launch.sh" "$pkgdir/usr/bin/apollo-gamescope-launch"

  # System-level helper unit: lets the video group write /sys/devices/evdi/{add,remove_all}.
  install -Dm644 "${srcdir}/evdi-perms.service" "$pkgdir/usr/lib/systemd/system/evdi-perms.service"

  install -Dm644 /dev/stdin "$pkgdir/usr/share/applications/$_pkgname.desktop" << END
[Desktop Entry]
Type=Application
Name=${_pkgname^}
Comment=$pkgdesc
Exec=/usr/bin/env systemctl start --user $_pkgname
Icon=$_pkgname
Categories=RemoteAccess;Network;
Keywords=gamestream;stream;moonlight;remote play;
Actions=RunInTerminal;

[Desktop Action RunInTerminal]
Name=Run in Terminal
Exec=$_pkgname
Terminal=true
Icon=application-x-executable
END
}
