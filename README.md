# Legion Performance Controller

A Linux GUI app for Lenovo Legion laptops that replaces the Windows LenovoController: live hardware monitoring, one-click maximum performance mode, and Extreme Cooling (max fan speed) — all in a native Qt/KDE interface with system tray support.

Tested on: **Lenovo Legion Y720** (Intel Core i7-7700HQ + NVIDIA GTX 1060, Bazzite OS / Fedora).  
Other Legion models with the same EC register layout should work as well.

![screenshot placeholder](screenshot.png)

---

## Features

- **Live monitoring** — CPU load, frequency, temperature; RAM usage; Intel iGPU frequency; NVIDIA GPU load, frequency, temperature, power draw (updated every 1–2 s)
- **Performance mode** — sets CPU governor to `performance`, enables turbo via `intel_pstate`, pins Intel iGPU to its maximum RP0 frequency, raises NVIDIA power limit to the firmware maximum
- **Extreme Cooling** — writes directly to Embedded Controller register `0xBD` (the same method as [extremecooling4linux](https://gitlab.com/OdinTdh/extremecooling4linux)), forcing fans to maximum speed
- **Independent toggles** — Performance and Extreme Cooling are separate; enabling Performance does not automatically enable the fans
- **System tray** — close the window to minimise to tray; toggle both modes from the tray context menu without opening the window
- **Polkit auth** — privileged operations request a KDE/GNOME password dialog via `pkexec`; the app itself runs as your normal user

---

## Requirements

- Linux (uses `/dev/port`, `/sys`, `/proc` — **not cross-platform**)
- Python 3.10 or newer
- **PySide6** (`python3-pyside6` package or `pip install --user PySide6`)
- `pkexec` and a polkit daemon (standard on KDE Plasma, GNOME, and most desktop Linux distros)
- NVIDIA proprietary driver + `nvidia-smi` (optional — only needed for NVIDIA monitoring and power limit)
- Read/write access to `/dev/port` (requires root, handled via `pkexec`)

### Installing PySide6

**Fedora / Bazzite (rpm-ostree):**
```bash
rpm-ostree install python3-pyside6
# reboot or enter a new session after layering
```

**Universal (pip):**
```bash
pip install --user PySide6
```

---

## Manual Installation

The app consists of a single Python file (`legion_perf.py`) that acts as both the privileged helper (when called via `pkexec`) and the GUI (when launched normally).

### 1. Install the privileged helper

```bash
sudo install -m 755 legion_perf.py /usr/local/bin/legion-perf-helper
```

### 2. Create the polkit action

```bash
sudo mkdir -p /etc/polkit-1/actions
sudo tee /etc/polkit-1/actions/io.github.legion.perf.policy > /dev/null << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE policyconfig PUBLIC
 "-//freedesktop//DTD PolicyKit Policy Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/PolicyKit/1.0/policyconfig.dtd">
<policyconfig>
  <action id="io.github.legion.perf.helper">
    <description>Legion Performance Controller</description>
    <message>Enter your password to change performance settings</message>
    <defaults>
      <allow_any>auth_admin</allow_any>
      <allow_inactive>auth_admin</allow_inactive>
      <allow_active>auth_admin</allow_active>
    </defaults>
    <annotate key="org.freedesktop.policykit.exec.path">/usr/local/bin/legion-perf-helper</annotate>
    <annotate key="org.freedesktop.policykit.exec.allow_gui">true</annotate>
  </action>
</policyconfig>
EOF
```

### 3. Install the user GUI launcher

```bash
mkdir -p ~/.local/bin
install -m 755 legion_perf.py ~/.local/bin/legion-perf
```

Make sure `~/.local/bin` is in your `PATH` (it usually is on modern distros). If not, add to `~/.bashrc`:
```bash
export PATH="$HOME/.local/bin:$PATH"
```

### 4. Create a KDE/desktop menu entry (optional)

```bash
mkdir -p ~/.local/share/applications
cat > ~/.local/share/applications/legion-perf.desktop << EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Legion Performance Controller
Comment=CPU, GPU and fan control for Lenovo Legion
Exec=$HOME/.local/bin/legion-perf
Icon=cpu-x
Terminal=false
Categories=System;Settings;HardwareSettings;
Keywords=cpu;gpu;performance;fan;cooling;legion;
EOF
```

### Running

```bash
legion-perf
# or directly:
python3 legion_perf.py
```

---

## How It Works

### Extreme Cooling

Lenovo Legion laptops expose a fan control register in the **Embedded Controller** at address `0xBD`:

| Value | Effect |
|-------|--------|
| `0x40` | Extreme Cooling ON — fans run at maximum speed |
| `0x00` | Extreme Cooling OFF — fans under firmware control |

The app reads and writes this register directly via `/dev/port` (I/O ports `0x62`/`0x66`), using the standard EC command protocol — the same approach used by [extremecooling4linux](https://gitlab.com/OdinTdh/extremecooling4linux). No kernel modules are required.

### CPU Performance

- Sets `scaling_governor` to `performance` on all cores
- Sets `intel_pstate/no_turbo` to `0` (turbo enabled)
- Sets `intel_pstate/min_perf_pct` and `max_perf_pct` to `100`

### Intel iGPU Performance

- Reads the hardware maximum frequency from `gt_RP0_freq_mhz`
- Writes it to `gt_min_freq_mhz`, `gt_max_freq_mhz`, and `gt_boost_freq_mhz`

### NVIDIA GPU Performance

- Reads the firmware maximum power limit from `nvidia-smi -q -d POWER`
- Applies it with `nvidia-smi -pl <max>`

> **Note on Optimus:** persistence mode (`-pm 1`) is intentionally not used, as it conflicts with runtime power management on Optimus hybrid graphics setups (the dGPU cannot suspend when unused).

---

## Safety Notes

- `min_perf_pct=100` keeps the CPU at maximum frequency at all times. This is intentional for AC-powered use, but generates more heat. Consider enabling Extreme Cooling alongside Performance mode.
- `nvidia-smi -pl` can only raise the power limit up to the firmware-enforced maximum — it cannot exceed hardware limits.
- The **Reset** button returns everything to balanced defaults: `powersave` governor, restored GPU limits, fans back under firmware control.
- All privileged operations go through `pkexec` and require explicit user authentication.

---

## Troubleshooting

**`pkexec` returns code 127** — the helper is not installed at `/usr/local/bin/legion-perf-helper`. Run step 1 of the installation.

**`pkexec` returns code 126** — polkit denied the action. Check that the `.policy` file is in place and readable.

**Extreme Cooling has no effect** — your Legion model may use a different EC register. Check [extremecooling4linux](https://gitlab.com/OdinTdh/extremecooling4linux) for supported models.

**NVIDIA row shows "P8 — idle (Optimus)"** — the dGPU is sleeping (expected when no 3D workload is running under Optimus).

**PySide6 not found** — install it via `rpm-ostree install python3-pyside6` (Bazzite/Fedora) or `pip install --user PySide6`.
