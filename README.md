# Legion Performance Controller

A Linux GUI app for Lenovo Legion laptops that replaces the Windows LenovoController: live hardware monitoring and Extreme Cooling (max fan speed) — all in a native Qt/KDE interface with system tray support.

CPU/GPU performance profiles are intentionally left to the system (`power-profiles-daemon`, `tuned`, KDE's power settings) — they already manage `intel_pstate` EPP correctly.

Tested on: **Lenovo Legion Y720** (Intel Core i7-7700HQ + NVIDIA GTX 1060, Bazzite OS / Fedora).  
Other Legion models with the same EC register layout should work as well.

![Legion Performance Controller](image.png)

---

## Features

- **Live monitoring** — CPU load, frequency, temperature; RAM usage; Intel iGPU frequency; NVIDIA GPU load, frequency, temperature, power draw (updated every 1–2 s)
- **Extreme Cooling** — sets Embedded Controller register `0xBD` (the same register as [extremecooling4linux](https://gitlab.com/OdinTdh/extremecooling4linux)) through the kernel's `ec_sys` driver, forcing fans to maximum speed
- **System tray** — close the window to minimise to tray; toggle Extreme Cooling from the tray context menu without opening the window; `--minimized` starts straight into the tray
- **Polkit auth** — privileged operations request a KDE/GNOME password dialog via `pkexec`; the app itself runs as your normal user

---

## Requirements

- Linux (uses `/sys`, `/proc` — **not cross-platform**)
- Python 3.10 or newer
- **PySide6** (`python3-pyside6` package or `pip install --user PySide6`)
- `pkexec` and a polkit daemon (standard on KDE Plasma, GNOME, and most desktop Linux distros)
- NVIDIA proprietary driver + `nvidia-smi` (optional — only needed for NVIDIA monitoring)
- `ec_sys` kernel module (shipped with most distro kernels; loaded on demand by the helper) and debugfs mounted at `/sys/kernel/debug`

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

### 5. Autostart minimised to tray (optional)

```bash
mkdir -p ~/.config/autostart
sed 's|^Exec=.*|& --minimized|' ~/.local/share/applications/legion-perf.desktop \
    > ~/.config/autostart/legion-perf.desktop
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

The helper accesses the EC through the kernel's `ec_sys` driver (`/sys/kernel/debug/ec/ec0/io`, loaded as `modprobe ec_sys write_support=1`). Unlike raw port I/O via `/dev/port`, this goes through the kernel ACPI EC driver and its locking, so it cannot collide with EC transactions the kernel itself is running (battery, thermal, hotkeys). No out-of-tree modules are required.

### Monitoring

- CPU load / frequency / temperature — `/proc/stat`, `cpufreq`, `coretemp` hwmon
- Intel iGPU — actual frequency from `/sys/class/drm/cardN/gt_act_freq_mhz` (shows *idle (RC6)* when the GPU is power-gated)
- NVIDIA — `nvidia-smi` query every 2 s; skipped while the dGPU is runtime-suspended (D3), since `nvidia-smi` would wake it up

---

## Safety Notes

- Extreme Cooling only switches the fan mode bit the firmware itself exposes; turning it off returns the fans to firmware control.
- All privileged operations go through `pkexec` and require explicit user authentication.
- Kernel lockdown (`integrity`/`confidentiality`, e.g. with Secure Boot on some distros) blocks debugfs writes — Extreme Cooling won't work in that case.

---

## Troubleshooting

**`pkexec` returns code 127** — the helper is not installed at `/usr/local/bin/legion-perf-helper`. Run step 1 of the installation.

**`pkexec` returns code 126** — polkit denied the action. Check that the `.policy` file is in place and readable.

**Extreme Cooling has no effect** — your Legion model may use a different EC register. Check [extremecooling4linux](https://gitlab.com/OdinTdh/extremecooling4linux) for supported models.

**NVIDIA row shows "suspended (D3)"** — the dGPU is powered down by runtime PM (expected under Optimus with no workload). **"unavailable"** — `nvidia-smi` is missing or failed.

**Extreme Cooling fails with `helper error`** — check that `ec_sys` exists (`modinfo ec_sys`) and that `/sys/kernel/security/lockdown` shows `[none]`.

**PySide6 not found** — install it via `rpm-ostree install python3-pyside6` (Bazzite/Fedora) or `pip install --user PySide6`.
