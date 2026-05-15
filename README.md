# Apollo-CachyOS

A fork of [MrOz59/Apollo-Linux](https://github.com/MrOz59/Apollo-Linux) that finally streams a **real virtual display** to a Moonlight client on **CachyOS / Arch + KDE Plasma 6 (Wayland)**, with **first-class [Gamescope](https://github.com/ValveSoftware/gamescope) integration** for the SteamOS Gaming Mode experience — without booting into SteamOS.

## Headline features

- 🖥️ **Real headless virtual display via EVDI** — the host's physical monitor stays free (or asleep) while your client streams its own dedicated desktop at the client's exact resolution, refresh rate, and HDR state. Fixes the upstream kwin / Wayland integration that was silently mirroring the physical monitor instead.
- 🎮 **Gamescope Steam Session integration** — a built-in app entry launches **Steam Big Picture inside [Gamescope](https://github.com/ValveSoftware/gamescope)**, the same micro-compositor SteamOS uses for Gaming Mode. You get FSR upscaling, tear-free presentation, and gamescope's keybinds (Super+F to toggle fullscreen, Super+I/O for FSR sharpness, etc.) — all wrapped in a Moonlight app entry that picks up the client's exact `WIDTH × HEIGHT @ FPS` at launch via a small `/usr/bin/apollo-gamescope-launch` helper script.
- 🔍 **Resolution Scale Factor — alive on Linux** — Apollo's per-app "Resolution Scale Factor" slider (well-known on Windows) was hidden from the Linux web UI even though the host-side rendering code already supported it. This fork unhides it, AND adds a `default_scale_factor` global setting in `sunshine.conf` so you don't have to edit every app entry. Result: stream a `1280×800` client like a Steam Deck at `1536×960` (120%) for sharper text and UI without changing the client's request.
- 🛡️ **Crash-safe monitor recovery** — apollo can be SIGKILL'd, OOM-killed, or the box can lose power; the physical monitor still comes back automatically via a state file + two independent recovery paths (`systemd ExecStopPost` + `recover_on_startup()`).
- 🎬 **VAAPI VBR + low-latency tuning** for AMD radeonsi, with a configurable `vaapi_quality` knob to trade compression for encode speed.
- 📊 **Per-stage fps instrumentation** (`[FPS-EVDI]` / `[FPS-CAP]` / `[FPS-ENC]` log lines every 5 seconds) so you can pinpoint where any framerate cap actually lives.
- 🔧 **Standalone EVDI pageflip-rate probe** for diagnosing kwin behaviour without apollo or Moonlight in the loop.

The upstream code did not work on KDE Plasma 6 / Wayland with the EVDI virtual display: it created the EVDI card but kwin left the output disabled, so Apollo silently captured the physical display. This fork is 19 patches that fix that end-to-end, plus the Gamescope integration and quality-of-life improvements on top.

---

## ⚠️ Status

| | |
|---|---|
| Confirmed working | **AMD Radeon (radeonsi / VAAPI)** on Linux + KDE Plasma 6 / Wayland |
| Code paths exist for | Nvidia (nvenc), Intel (VAAPI/QSV), software encoders |
| **NOT tested on Nvidia yet** | incoming and will validate after this AMD scenario is fully stable. PRs and issue reports from Nvidia users very welcome. |
| **Not a Sunshine / Apollo replacement on Windows** | This is a Linux-only delta. Windows builds of Apollo are upstream-only. |
| Made with the help of an AI coding assistant (Claude / Claude Code) | See [§ AI-assisted disclosure](#ai-assisted-disclosure) below. |

---

## Tested hardware (the rig this was built and validated on)

| | |
|---|---|
| Distro | CachyOS (rolling) |
| Kernel | `linux-cachyos 7.0.5-2` |
| CPU | (any modern x86_64) |
| GPU | **AMD Radeon RX 9070 XT** (RDNA4, gfx1201) |
| Mesa | `2:26.1.0` (radeonsi + libva-mesa-driver) |
| Compositor | KDE Plasma 6 / `kwin_wayland` |
| EVDI | `evdi-dkms 1.14.16` |
| Apollo base | `0.4.8.r4.evdi` (MrOz59 fork) |
| Client | Moonlight on **Steam Deck LCD** (1280×800, 60Hz, HEVC) |
| Network | Local LAN |

The host runs apollo as a `systemd --user` service. The Steam Deck connects via Moonlight over LAN.

---

## What this fork actually does (the 22 patches)

The build is staged as a numbered patch series on top of upstream `MrOz59/Apollo-Linux`. Three patches were superseded by later ones and are archived under [`patches/archive/`](patches/archive/); the rest are applied in order by the PKGBUILD. Group summary:

### EVDI + kwin plumbing (what makes a virtual display actually work)

- **01 fix-evdi-device-index** — upstream hardcoded `/dev/dri/card0` for EVDI; fixes lookup so it works regardless of which DRM minor your real GPU got.
- **02 promote-evdi-to-kscreen-primary** — after `evdi_connect` we poll `kscreen-doctor -j` for the new output and promote it to priority 1. Without this kwin keeps it disabled.
- **03 drop-spurious-drm-open** — upstream opened the EVDI card with `O_RDWR` for dead code; that competes with kwin for DRM master and kwin loses, so the connector is never enumerated. Removed.
- **04 kmsgrab-fallback-for-hotplugged-evdi** — kmsgrab's `card_descriptors` is built once at startup; a freshly hot-plugged EVDI isn't in it. Fall back to CRTC geometry instead of returning fatal `-1`.
- **07 evdi-cpu-buffer-capture-backend** — adds `display_evdi_t`, a capture path that reads pixels from a userspace CPU buffer instead of calling `gbm_create_device()` on the EVDI card (which SIGSEGVs mesa). Also adds the libevdi event-pump thread that drains `evdi_grab_pixels` and acks kwin's vblanks.
- **08 disable-physical-primary-during-stream** — during the stream, the user's real monitor is `kscreen-doctor … .disable`'d so games can't spawn on it and become invisible to the Deck.
- **09 edid-exact-60hz-1280x800** — hand-tuned CVT-RB timing so the synthetic EDID advertises a true 60.000 Hz (was drifting to 59.94 and breaking vsync).
- **11 edid-advertise-only-requested-resolution** — strip ghost modes from the generated EDID so games don't see resolutions the EVDI device can't actually deliver.
- **18 event-driven-evdi-capture** — `display_evdi_t::capture` now waits on a `condition_variable` notified by the libevdi event pump (instant wakeup on a fresh frame) but with a hard rate cap so the capture loop never outruns the encoder.
- **22 evdi-search-range-to-64** — `find_available_evdi_device()` was looping `0..15`; after several restart cycles new EVDI cards land at minor 16+ and apollo would silently fall back to passthrough. Bumped to `0..63`.

### Encoder + video pipeline

- **13 vaapi-vbr-default-on-amd** — radeonsi's hevc/h264 VAAPI encoder needs VBR + single-frame VBV size to actually honour the host's max-bitrate ceiling; CQP is the default upstream and ignores it.
- **14 vaapi-quality-and-forced-idr** — set ffmpeg vaapi `quality=4` (balanced) + `forced_idr=1` so client IDR requests actually generate keyframes.
- **19 vaapi-quality-configurable** — expose `vaapi_quality = 0..8` in `sunshine.conf` so you can trade compression for encode speed without rebuilding.
- **20 fps-instrumentation-and-tighter-evdi-pump** — three new `[FPS-EVDI]` / `[FPS-CAP]` / `[FPS-ENC]` log lines every 5s telling you `pageflips_per_sec`, `pushed_per_sec`, `encoded_per_sec`, `skipped_per_sec`, `avg_encode_us`. Lets you pinpoint where the cap lives instead of guessing. Also drops the event-pump `usleep` from 2000us to 500us for a tighter kwin → libevdi → capture pipeline.

### UX / safety

- **10 unhide-resolution-scale-factor-on-linux** — web UI's "Resolution Scale Factor" slider was hidden on Linux; it's actually wired up, just show it.
- **12 dxvk-hdr-off-when-client-sdr** — inject `DXVK_HDR=0` / `PROTON_ENABLE_HDR=0` when the moonlight client negotiated SDR so games don't render in HDR and look washed out.
- **15 add-gamescope-steam-session-app** — adds a default "Gamescope Steam Session" entry to `apps.json` that wraps Steam Big Picture in `gamescope` with FSR upscaling. The companion `apollo-gamescope-launch` helper script (installed to `/usr/bin/`) reads the client's actual `APOLLO_CLIENT_WIDTH/HEIGHT/FPS` env vars at runtime — apollo's `$(VAR)` substitution in apps.json doesn't compose with bash's `$(…)` syntax, so a standalone helper is the only reliable way to do this.
- **16 host-side-default-scale-factor** — `default_scale_factor = 120` in `sunshine.conf` applies to every app, instead of editing per-app entries.
- **21 monitor-recovery-safety-net** — **never leave the user's physical monitor disabled.** Writes the saved-primary name to `$XDG_STATE_HOME/apollo/saved-primary` the moment we disable an output. Two independent recovery paths consult it:
  1. `kscreen::recover_on_startup()` runs the moment apollo starts.
  2. `ExecStopPost=-/usr/bin/apollo-monitor-recovery` on the systemd unit runs even after `SIGKILL`/OOM.

  Apollo can crash, get OOM-killed, or the box can lose power — your monitor still comes back.

### Archived (superseded)

- **05, 06** — early event-loop / cleanup attempts, folded into 0007.
- **17** — PBO async CPU→GPU upload; added one frame of latency on AMD UMA hardware, reverted in favour of the synchronous `glTexSubImage2D` path.

---

## Repo layout

```
Apollo-CachyOS/
├── README.md                       # this file
├── LICENSE                         # GPL-3.0-only (inherited from upstream)
├── PKGBUILD                        # Arch / CachyOS build recipe
├── apollo.install                  # pacman pre/post install hooks (CAP_SYS_ADMIN reminder)
├── evdi-perms.service              # system unit: lets `video` group write /sys/devices/evdi/{add,remove_all}
├── apollo-monitor-recovery.sh      # installed as /usr/bin/apollo-monitor-recovery (ExecStopPost)
├── apollo-gamescope-launch.sh      # installed as /usr/bin/apollo-gamescope-launch (the Gamescope app's cmd)
├── patches/                        # numbered patch series applied by PKGBUILD's prepare()
│   ├── 0001-fix-evdi-device-index.patch
│   ├── 0002-promote-evdi-to-kscreen-primary.patch
│   ├── …
│   ├── 0022-evdi-search-range-to-64.patch
│   └── archive/                    # archived/superseded patches (not applied)
└── tests/
    ├── evdi_pageflip_probe.py      # standalone libevdi-only kwin pageflip-rate probe
    └── test_evdi_kscreen.py        # exercises evdi_add_device + kscreen-doctor promotion
```

---

## Build & install

```bash
git clone https://github.com/<your-fork-here>/Apollo-CachyOS.git
cd Apollo-CachyOS
makepkg -f                                           # builds apollo-0.4.8.r4.evdi-99-x86_64.pkg.tar.zst
sudo pacman -U apollo-*.pkg.tar.zst
sudo systemctl enable --now evdi-perms.service        # so the video group can write /sys/devices/evdi/add
systemctl --user enable --now apollo                  # starts the streaming server
```

The PKGBUILD is the only one you need; it git-clones MrOz59/Apollo-Linux into `src/`, applies all 19 active patches, builds, and packages.

### Runtime requirements

| | |
|---|---|
| Kernel | `evdi` module loaded (`modprobe evdi`). With `evdi-dkms` installed and `evdi-perms.service` enabled this is automatic. |
| Compositor | KDE Plasma 6 / `kwin_wayland`. The kscreen integration depends on `kscreen-doctor` being installed (optdep). |
| User session | systemd `--user` (so the systemd unit can pick up `WAYLAND_DISPLAY` and friends). |
| Group | Your user must be in the `video` group (`gpasswd -a $USER video`). |
| For the Gamescope app entry | `gamescope` 3.16+. |

### `optdepends` already declared in the PKGBUILD

- `intel-media-driver` — Intel VAAPI encode
- `libva-mesa-driver` — **AMD VAAPI encode (this is what's tested)**
- `kscreen` — required on KDE Plasma to auto-promote the EVDI output to primary
- `gamescope` — needed if you want the "Gamescope Steam Session" app entry

---

## Using the Gamescope Steam Session app

Once installed, Moonlight on your client will show a new app entry alongside Desktop and Steam Big Picture:

| App entry | What it does |
|---|---|
| **Desktop** | streams your KDE Plasma desktop on the virtual display |
| **Steam Big Picture** | launches Steam in big-picture mode on the virtual display (uses KDE for compositing) |
| **Gamescope Steam Session** | launches Steam in big-picture mode **inside a Gamescope micro-compositor** running on the virtual display |

What the Gamescope path gives you that the plain Steam Big Picture path doesn't:

- **AMD FidelityFX Super Resolution (FSR) upscaling.** Configured to filter `fsr` with sharpness 4 (gamescope's 0-20 scale, 0 = sharpest). For games rendering below the client's native resolution, this is the same upscaler SteamOS uses in Gaming Mode.
- **Tear-free presentation** independent of KDE's compositor pageflip semantics.
- **No KDE Plasma window decorations / panels** — gamescope owns the whole virtual output for as long as the session runs.
- **Gamescope keybindings** (Super+F fullscreen toggle, Super+N nearest-neighbour, Super+U FSR toggle, Super+I/O sharpness up/down).

### How it's wired up

```
Moonlight on client → apollo's "Gamescope Steam Session" app entry
   → /usr/bin/apollo-gamescope-launch  (helper script, reads env)
   → gamescope -W $APOLLO_CLIENT_WIDTH -H $APOLLO_CLIENT_HEIGHT -r $FPS \
                -f -e -F fsr --fsr-sharpness 4 -- steam -bigpicture
```

The helper script is the small piece that makes the integration robust: apollo's `$(VAR)` substitution in apps.json greedily intercepts every `$(…)` pattern at parse time, so inline bash with `$(date)` etc. silently breaks. Putting the launch logic in a standalone script bypasses that entirely.

### Tuning

Edit `/usr/bin/apollo-gamescope-launch` (or copy it into your `~/.local/bin/` to override system-wide). Most useful tweaks:

| Flag | Default | What it does |
|---|---|---|
| `--fsr-sharpness N` | `4` | 0 = maximum sharpening, 20 = minimum. Drop to `8`-`10` if text looks over-sharpened. |
| `-F nearest` | `fsr` | Nearest-neighbour upscaling instead of FSR (sharper but pixelated). |
| `-S integer` | not set | Integer-scale upscaling — useful for pixel-art games. |
| `--immediate-flips` | not set | DRM-backend only; do NOT enable on the wayland backend (causes EVDI fd corruption). |

Restart apollo (`systemctl --user restart apollo`) only if you edit `apps.json` — script changes are picked up on the next stream launch.

---

## Configuration knobs (`~/.config/sunshine/sunshine.conf`)

This fork exposes a few config keys upstream doesn't:

```ini
# AMD radeonsi only. ffmpeg vaapi `quality` option (0-8). Lower = faster encode at
# the cost of compression efficiency. -1 = use the codec default (4).
vaapi_quality = 1

# Force HEVC over AV1. Steam Deck LCD's VCN3 decoder is faster on HEVC than AV1.
hevc_mode = 2
av1_mode  = 1

# Host-side global scale factor (percentage). Applies to every app instead of
# editing each apps.json entry. Per-app `scale-factor` still wins.
default_scale_factor = 120

# When kwin's pageflip rate on the virtual display is jittery, request 2x the
# client's target fps from the EVDI EDID so the capture pipeline has a steadier
# stream. The encoder still emits at the client's requested rate.
double_refreshrate = true
```

---

## Diagnostics (when something's off)

### 1. fps stats baked into the log

After patch 0020, while a stream is running you get one line per stage every 5 seconds:

```
[FPS-EVDI] events=58/s grabs=58/s polls=2014/s avg_grab=423us
[FPS-CAP]  pushed=58/s avg_copy=872us avg_push=18us wake_event=58/s wake_timeout=0/s
[FPS-ENC]  popped=58/s encoded=58/s skipped=0/s pop_timeout=0/s avg_convert=509us avg_encode=2403us
```

Read it as:
- `events ~ encoded` → 60 fps everywhere → healthy.
- `events ≈ 30` → kwin half-rate pageflipping (a kwin-side bug, try `KWIN_DRM_USE_MODIFIERS=0`).
- `skipped > 0` → encoder's variation-threshold drop is firing (often: `double_refreshrate=true` and the capture rate is double the encode rate, which is fine).
- `avg_encode` close to or above the frame budget (`1_000_000 / fps` µs) → encoder is the cap; lower `vaapi_quality`.

### 2. EVDI-only standalone probe

If you suspect kwin isn't pageflipping into the EVDI device at all, `tests/evdi_pageflip_probe.py` creates a fresh EVDI card, registers a buffer, and counts `evdi_grab_pixels` events per second for N seconds — entirely independent of apollo and Moonlight:

```bash
python3 tests/evdi_pageflip_probe.py 10
```

### 3. Monitor recovery

If apollo dies mid-stream, two independent paths bring your monitor back:

- The systemd unit's `ExecStopPost` runs `/usr/bin/apollo-monitor-recovery` even after `SIGKILL`.
- The next apollo start runs `kscreen::recover_on_startup()` before doing anything else.

If neither fires, the state file lives at `~/.local/state/apollo/saved-primary` and `kscreen-doctor output.<that-name>.enable output.<that-name>.priority.1` will restore manually.

---

## Why CachyOS specifically?

Mostly: that's what was on the test rig. The fork itself is regular Arch-style PKGBUILD + patches and there is nothing CachyOS-specific in the patches. You can build and install on plain Arch the same way. Distros that ship Apollo via a different package manager will need to adapt the `PKGBUILD` install steps but the patch series applies cleanly to upstream MrOz59/Apollo-Linux's `main`.

The name "Apollo-CachyOS" is just the rig of origin.

---

## Limitations / known issues

- **Nvidia path is unvalidated.** The nvenc encoder code is upstream Apollo's and untouched here, but no patch has been written against the Nvidia GPU's interaction with EVDI + kwin specifically. The author has an RTX 5060 Ti incoming; updates will follow.
- **Static-content fps drop.** kwin only pageflips when something visually changes. On a totally still desktop you may see `events_per_sec` drop below 60 in the FPS stats; the capture loop fills the gap with timeout-driven captures and the encoder is happy. This is by design upstream.
- **Mesa 26.0 had a VAAPI regression (MR 37884) on RX 9070 XT.** Mesa 26.1 has partial VCN5 fixes that mask the issue but it has not been re-verified end-to-end. If you see encoder failures specific to RDNA4, file an issue with `glxinfo | grep Mesa`.
- **Gamescope inside KDE Plasma still has minor stutter** in some configurations. Workaround: use the plain "Steam Big Picture" app entry, which gives nearly-identical end-to-end framerate without gamescope's nested compositor layer.

---

## Credits

- [LizardByte / Sunshine](https://github.com/LizardByte/Sunshine) — the original streaming server.
- [ClassicOldSong / Apollo](https://github.com/ClassicOldSong/Apollo) — the Apollo fork that added per-client virtual displays.
- [MrOz59 / Apollo-Linux](https://github.com/MrOz59/Apollo-Linux) — the Linux-side EVDI scaffolding this fork patches.
- [DisplayLink / evdi](https://github.com/DisplayLink/evdi) — the kernel module + libevdi userspace API.
- [KDE / kscreen-doctor](https://invent.kde.org/plasma/libkscreen) — the kwin output configuration tool.

The PKGBUILD is descended from the AUR `apollo` package by xiota, pointed at MrOz59's upstream instead of LizardByte's.

---

## Disclaimer
So i wanted to use the Apollo's capability to stream using virtual desktops, the Apollo-Linux did not worked correctly for me so I use Claude Code to make it work for my Rig, I added a Gamescope Session App and Resolution Scale Factor like Apollo in Windows, I hope it works for you

---

---

## AI-assisted disclosure

This fork was developed with substantial assistance from an AI coding assistant (Anthropic's Claude / Claude Code). Specifically:

- Patches were drafted, iterated, and audited interactively with Claude.
- README / commit messages / docstrings are AI-assisted.
- The end-to-end debugging flow that surfaced the kwin / EVDI / kscreen interaction bugs was done conversationally.

Every patch was reviewed by the author, tested on the rig listed above, and only landed once a real end-to-end behaviour change was observable in Apollo's logs or in Moonlight's stream. The code is human-owned and human-merged; AI was the keyboard.

If you're a reviewer who cares about provenance: each patch in `patches/` has a `Subject:` header summarising what changed and why, in the same English the author and AI iterated on. The commit history (when this is pushed to a remote) reflects the actual human-authored squashes.

---

## License

GPL-3.0-only, inherited from upstream Sunshine / Apollo. See [`LICENSE`](LICENSE).
