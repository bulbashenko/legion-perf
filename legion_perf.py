#!/usr/bin/env python3
"""
Legion Performance Controller — Lenovo Legion Y720, Bazzite/Linux.
Единый исполняемый файл: GUI + privileged helper в одном Python-файле.

Установка:  sudo bash install.sh
Запуск GUI: legion-perf
"""
import sys, os, re, time, subprocess
from pathlib import Path

COOL_STATE = Path("/tmp/legion-cool-state")
HELPER     = Path("/usr/local/bin/legion-perf-helper")
_CMDS      = {"perf-max", "perf-reset", "cool-on", "cool-off", "cool-status", "reset"}

# ══════════════════════════════════════════════════════════════════════════════
#  HELPER MODE
#  pkexec вызывает: /usr/local/bin/legion-perf-helper <команда>
#  Раздел завершается до импорта PySide6.
# ══════════════════════════════════════════════════════════════════════════════

if len(sys.argv) > 1 and sys.argv[1] in _CMDS:

    # ── EC I/O через /dev/port (протокол идентичен extremecooling4linux) ────
    _EC_SC = 0x66;  _EC_DATA = 0x62
    _IBF   = 1;     _OBF     = 0
    _RD_EC = 0x80;  _WR_EC   = 0x81
    _COOL_REG = 0xBD;  _COOL_ON = 0x40;  _COOL_OFF = 0x00

    _fd = os.open("/dev/port", os.O_RDWR)

    def _inb(p):
        os.lseek(_fd, p, os.SEEK_SET); return ord(os.read(_fd, 1))

    def _outb(v, p):
        os.lseek(_fd, p, os.SEEK_SET); os.write(_fd, bytes([v]))

    def _ec_wait(p, flag, exp):
        for _ in range(100):
            if ((_inb(p) >> flag) & 1) == exp: return
            time.sleep(0.001)
        raise TimeoutError("EC wait timeout")

    def _ec_read(reg):
        _ec_wait(_EC_SC, _IBF, 0); _outb(_RD_EC, _EC_SC)
        _ec_wait(_EC_SC, _IBF, 0); _outb(reg, _EC_DATA)
        _ec_wait(_EC_SC, _OBF, 1); return _inb(_EC_DATA)

    def _ec_write(reg, val):
        _ec_wait(_EC_SC, _IBF, 0); _outb(_WR_EC, _EC_SC)
        _ec_wait(_EC_SC, _IBF, 0); _outb(reg, _EC_DATA)
        _ec_wait(_EC_SC, _IBF, 0); _outb(val, _EC_DATA)
        _ec_wait(_EC_SC, _IBF, 0)

    # ── sysfs helpers ────────────────────────────────────────────────────────
    def _w(path, val):
        try: Path(path).write_text(str(val) + "\n")
        except: pass

    def _cpu_max():
        for p in Path("/sys/devices/system/cpu").glob("cpu[0-9]*/cpufreq/scaling_governor"):
            _w(p, "performance")
        for p in Path("/sys/devices/system/cpu").glob(
                "cpu[0-9]*/cpufreq/energy_performance_preference"):
            _w(p, "performance")
        base = Path("/sys/devices/system/cpu/intel_pstate")
        _w(base / "no_turbo", 0)
        _w(base / "min_perf_pct", 100)
        _w(base / "max_perf_pct", 100)

    def _cpu_reset():
        for p in Path("/sys/devices/system/cpu").glob("cpu[0-9]*/cpufreq/scaling_governor"):
            _w(p, "powersave")
        _w("/sys/devices/system/cpu/intel_pstate/min_perf_pct", 20)

    def _igpu_max():
        for rp0 in Path("/sys/bus/pci/devices").rglob("gt_RP0_freq_mhz"):
            val = rp0.read_text().strip()
            for key in ("gt_min_freq_mhz", "gt_max_freq_mhz", "gt_boost_freq_mhz"):
                _w(rp0.parent / key, val)

    def _igpu_reset():
        for rp0 in Path("/sys/bus/pci/devices").rglob("gt_RP0_freq_mhz"):
            d = rp0.parent
            hi = rp0.read_text().strip()
            lo = (d / "gt_RPn_freq_mhz").read_text().strip()
            _w(d / "gt_max_freq_mhz", hi); _w(d / "gt_boost_freq_mhz", hi)
            _w(d / "gt_min_freq_mhz", lo)

    def _nvidia_pl(label):
        try:
            out = subprocess.check_output(["nvidia-smi", "-q", "-d", "POWER"],
                                          timeout=5, stderr=subprocess.DEVNULL).decode()
            m = re.search(rf"{re.escape(label)}\s*:\s*([\d.]+)", out)
            if m:
                subprocess.call(["nvidia-smi", "-pl", m.group(1)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except: pass

    def _cool_write(val, state_str):
        _ec_write(_COOL_REG, val)
        COOL_STATE.write_text(state_str)
        COOL_STATE.chmod(0o644)

    # ── Dispatch ─────────────────────────────────────────────────────────────
    try:
        cmd = sys.argv[1]
        if   cmd == "perf-max":    _cpu_max();   _igpu_max();   _nvidia_pl("Max Power Limit")
        elif cmd == "perf-reset":  _cpu_reset(); _igpu_reset(); _nvidia_pl("Default Power Limit")
        elif cmd == "cool-on":     _cool_write(_COOL_ON,  "on")
        elif cmd == "cool-off":    _cool_write(_COOL_OFF, "off")
        elif cmd == "cool-status": print("on" if _ec_read(_COOL_REG) == _COOL_ON else "off")
        elif cmd == "reset":
            _cpu_reset(); _igpu_reset(); _nvidia_pl("Default Power Limit")
            _cool_write(_COOL_OFF, "off")
        os.close(_fd)
        sys.exit(0)
    except Exception as e:
        print(f"helper error: {e}", file=sys.stderr)
        try: os.close(_fd)
        except: pass
        sys.exit(1)


# ══════════════════════════════════════════════════════════════════════════════
#  GUI MODE
# ══════════════════════════════════════════════════════════════════════════════

try:
    from PySide6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QGroupBox, QLabel, QProgressBar, QPushButton, QMessageBox,
        QSystemTrayIcon, QMenu,
    )
    from PySide6.QtCore import Qt, QTimer, QThread, QProcess, Signal
    from PySide6.QtGui import QIcon, QAction
except ImportError:
    print("PySide6 not found.\n"
          "  rpm-ostree install python3-pyside6\n"
          "  or: pip install --user PySide6", file=sys.stderr)
    sys.exit(1)

# ── Метрики (без root) ────────────────────────────────────────────────────────

_prev_stat = None

def _cpu_usage() -> float:
    global _prev_stat
    try:
        v = list(map(int, Path("/proc/stat").read_text().split("\n")[0].split()[1:]))
        idle, total = v[3], sum(v)
        r = 0.0
        if _prev_stat:
            dt = total - _prev_stat[1]; di = idle - _prev_stat[0]
            r = max(0.0, (1 - di / dt) * 100) if dt else 0.0
        _prev_stat = (idle, total)
        return r
    except: return 0.0

def _cpu_freq() -> float:
    try:
        xs = [int(p.read_text()) for p in
              Path("/sys/devices/system/cpu").glob("cpu[0-9]*/cpufreq/scaling_cur_freq")]
        return sum(xs) / len(xs) / 1000 if xs else 0.0
    except: return 0.0

def _cpu_temp() -> float:
    try:
        for hw in Path("/sys/class/hwmon").iterdir():
            if "coretemp" not in (hw / "name").read_text(errors="ignore"): continue
            for lbl in hw.glob("temp*_label"):
                if "Package" in lbl.read_text():
                    return float((hw / lbl.name.replace("_label", "_input")).read_text()) / 1000
    except: pass
    return 0.0

def _ram() -> tuple[float, float]:
    try:
        d = {l.split(":")[0]: int(l.split()[1])
             for l in Path("/proc/meminfo").read_text().splitlines() if ":" in l}
        t = d["MemTotal"] / 1048576; f = d["MemAvailable"] / 1048576
        return t - f, t
    except: return 0.0, 0.0

def _igpu_freq() -> tuple[int, int]:
    try:
        for p in Path("/sys/bus/pci/devices").rglob("gt_cur_freq_mhz"):
            return int(p.read_text()), int((p.parent / "gt_max_freq_mhz").read_text())
    except: pass
    return 0, 0

def _perf_active() -> bool:
    try:
        gov = Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor").read_text().strip()
        pct = int(Path("/sys/devices/system/cpu/intel_pstate/min_perf_pct").read_text().strip())
        return gov == "performance" and pct >= 100
    except: return False

def _cool_init() -> bool | None:
    # Сначала кэш-файл (записывается хелпером), потом sudo -n (без диалога)
    try: return COOL_STATE.read_text().strip() == "on"
    except: pass
    try:
        val = subprocess.check_output(
            ["sudo", "-n", str(HELPER), "cool-status"],
            timeout=2, stderr=subprocess.DEVNULL,
        ).decode().strip()
        return val == "on"
    except: pass
    return None

# ── NVIDIA worker (отдельный поток, обновление каждые 2 с) ───────────────────

class NvWorker(QThread):
    ready = Signal(dict)

    def run(self):
        while not self.isInterruptionRequested():
            try:
                raw = subprocess.check_output(
                    ["nvidia-smi",
                     "--query-gpu=utilization.gpu,clocks.gr,temperature.gpu,power.draw,power.limit",
                     "--format=csv,noheader,nounits"],
                    timeout=3, stderr=subprocess.DEVNULL,
                ).decode().strip()
                p = [x.strip() for x in raw.split(",")]
                def f(s, t=float): return t(float(s)) if s not in ("N/A", "[N/A]", "") else -1
                self.ready.emit({"u": f(p[0]), "clk": f(p[1], int),
                                 "t": f(p[2]), "pw": f(p[3]), "pl": f(p[4])})
            except: self.ready.emit({})
            self.msleep(2000)

# ── Виджеты ───────────────────────────────────────────────────────────────────

class MetricRow(QWidget):
    def __init__(self, name: str, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self); lay.setContentsMargins(0, 3, 0, 3)
        lbl = QLabel(name); lbl.setFixedWidth(58)
        lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        f = lbl.font(); f.setBold(True); lbl.setFont(f)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100); self.bar.setTextVisible(False); self.bar.setFixedHeight(14)
        self.val = QLabel("—"); self.val.setMinimumWidth(240)
        lay.addWidget(lbl); lay.addWidget(self.bar, 1); lay.addWidget(self.val)

    def update(self, pct: float, text: str):
        p = max(0, min(100, int(pct)))
        self.bar.setValue(p)
        c = "#4caf50" if p < 60 else "#ff9800" if p < 85 else "#f44336"
        self.bar.setStyleSheet(
            f"QProgressBar::chunk{{background:{c};border-radius:3px;}}"
            "QProgressBar{border:1px solid palette(mid);border-radius:3px;background:palette(base);}"
        )
        self.val.setText(text)


class ToggleRow(QWidget):
    changed = Signal(bool)
    _HL = "background:palette(highlight);color:palette(highlighted-text);border-radius:4px;"

    def __init__(self, icon: str, label: str, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self); lay.setContentsMargins(0, 4, 0, 4)
        self.dot = QLabel("●"); self.dot.setFixedWidth(20)
        title = QLabel(f"{icon}  {label}"); title.setMinimumWidth(210)
        self.b_off = QPushButton("OFF"); self.b_off.setFixedWidth(75)
        self.b_on  = QPushButton("ON");  self.b_on.setFixedWidth(75)
        self.b_off.clicked.connect(lambda: self._click(False))
        self.b_on.clicked.connect( lambda: self._click(True))
        lay.addWidget(self.dot); lay.addWidget(title, 1)
        lay.addWidget(self.b_off); lay.addWidget(self.b_on)
        self._state: bool | None = None
        self.set_state(None)

    def set_state(self, on: bool | None):
        self._state = on
        self.b_on.setStyleSheet (self._HL if on is True  else "")
        self.b_off.setStyleSheet(self._HL if on is False else "")
        c = "#4caf50" if on is True else "#757575" if on is False else "#ff9800"
        self.dot.setStyleSheet(f"font-size:16px;color:{c};")

    def _click(self, on: bool):
        if self._state is on: return
        self.changed.emit(on)

    def lock(self, locked: bool):
        self.b_off.setEnabled(not locked); self.b_on.setEnabled(not locked)


# ── Главное окно ──────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Legion Performance Controller")
        self.setMinimumWidth(560)
        self._proc: QProcess | None = None
        self._perf = _perf_active()
        self._cool = _cool_init()
        self._nv:   dict = {}

        mon = QGroupBox("Monitoring")
        ml = QVBoxLayout(mon); ml.setSpacing(1)
        self.r_cpu = MetricRow("CPU")
        self.r_ram = MetricRow("RAM")
        self.r_ig  = MetricRow("iGPU")
        self.r_nv  = MetricRow("NVIDIA")
        for r in (self.r_cpu, self.r_ram, self.r_ig, self.r_nv): ml.addWidget(r)

        ctrl = QGroupBox("Controls")
        cl = QVBoxLayout(ctrl); cl.setSpacing(2)
        self.tog_perf = ToggleRow("⚡", "Performance  (CPU + GPU)")
        self.tog_cool = ToggleRow("❄", "Extreme Cooling  (max fan speed)")
        self.btn_reset = QPushButton("↺  Reset all to balanced")
        self.btn_reset.setFixedHeight(34)
        for w in (self.tog_perf, self.tog_cool, self.btn_reset): cl.addWidget(w)

        self.lbl_st = QLabel("Ready")
        self.lbl_st.setAlignment(Qt.AlignCenter)

        c = QWidget(); lay = QVBoxLayout(c)
        lay.setSpacing(10); lay.setContentsMargins(12, 12, 12, 12)
        lay.addWidget(mon); lay.addWidget(ctrl); lay.addWidget(self.lbl_st)
        self.setCentralWidget(c)

        self.tog_perf.set_state(self._perf)
        self.tog_cool.set_state(self._cool)
        self.tog_perf.changed.connect(self._on_perf)
        self.tog_cool.changed.connect(self._on_cool)
        self.btn_reset.clicked.connect(self._on_reset)

        if not HELPER.exists():
            self.lbl_st.setText("⚠ Run first: sudo bash install.sh")
            QMessageBox.warning(self, "Setup Required",
                f"{HELPER} not found.\n\nRun once:\n  sudo bash install.sh")

        self._nv_w = NvWorker(self)
        self._nv_w.ready.connect(lambda d: setattr(self, "_nv", d))
        self._nv_w.start()

        self._setup_tray()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000); self._tick()

    def _setup_tray(self):
        icon = QIcon.fromTheme("preferences-system-performance",
               QIcon.fromTheme("utilities-system-monitor"))
        self._tray = QSystemTrayIcon(icon, self)

        menu = QMenu()
        self._act_show = QAction("Hide", self)
        self._act_show.triggered.connect(self._toggle_window)

        self._act_perf = QAction("", self)
        self._act_perf.triggered.connect(lambda: self._on_perf(not self._perf))
        self._act_cool = QAction("", self)
        self._act_cool.triggered.connect(lambda: self._on_cool(not self._cool))

        act_reset = QAction("↺  Reset to balanced", self)
        act_reset.triggered.connect(self._on_reset)
        act_quit = QAction("Quit", self)
        act_quit.triggered.connect(QApplication.instance().quit)

        menu.addAction(self._act_show)
        menu.addSeparator()
        menu.addAction(self._act_perf)
        menu.addAction(self._act_cool)
        menu.addSeparator()
        menu.addAction(act_reset)
        menu.addSeparator()
        menu.addAction(act_quit)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(
            lambda r: self._toggle_window() if r == QSystemTrayIcon.Trigger else None
        )
        self._tray.show()
        self._update_tray()

    def _toggle_window(self):
        if self.isVisible():
            self.hide()
            self._act_show.setText("Show")
        else:
            self.show(); self.raise_(); self.activateWindow()
            self._act_show.setText("Hide")

    def _update_tray(self):
        p = "ON ✓" if self._perf else "OFF"
        c = "ON ✓" if self._cool is True else "OFF" if self._cool is False else "?"
        self._act_perf.setText(f"⚡  Performance: {p}")
        self._act_cool.setText(f"❄   Extreme Cooling: {c}")
        self._tray.setToolTip(
            f"Legion Performance\n"
            f"Performance: {p}\n"
            f"Extreme Cooling: {c}"
        )

    def _tick(self):
        u, f, t = _cpu_usage(), _cpu_freq(), _cpu_temp()
        self.r_cpu.update(u, f"{u:.0f}%   {f:.0f} MHz   {t:.0f}°C")

        used, total = _ram()
        self.r_ram.update(used / total * 100 if total else 0,
                          f"{used:.1f} / {total:.1f} GB")

        cur, mx = _igpu_freq()
        self.r_ig.update(cur / mx * 100 if mx else 0, f"{cur} / {mx} MHz")

        nv = self._nv
        if nv and nv.get("u", -1) >= 0:
            self.r_nv.update(
                nv["u"],
                f"{nv['u']:.0f}%   {nv['clk']} MHz   {nv['t']:.0f}°C   {nv['pw']:.0f} W",
            )
        else:
            self.r_nv.update(0, "P8 — idle (Optimus)")

        real = _perf_active()
        if real != self._perf and self._proc is None:
            self._perf = real; self.tog_perf.set_state(real)

    # ── Privileged helper via pkexec ─────────────────────────────────────────

    def _run(self, *args, on_ok=None, on_fail=None):
        if self._proc and self._proc.state() != QProcess.NotRunning: return
        if not HELPER.exists():
            self.lbl_st.setText("⚠ Run first: sudo bash install.sh"); return
        self._lock(True); self.lbl_st.setText("⏳ Waiting for authentication…")
        self._proc = QProcess(self)
        self._proc.finished.connect(lambda c, _: self._done(c, on_ok, on_fail))
        self._proc.start("pkexec", [str(HELPER)] + list(args))

    def _done(self, code, on_ok, on_fail):
        self._lock(False); self._proc = None
        if code == 0:
            self.lbl_st.setText("✓ Done"); on_ok and on_ok()
        else:
            self.lbl_st.setText(f"✗ Cancelled (code {code})")
            on_fail and on_fail()

    def _lock(self, on):
        self.tog_perf.lock(on); self.tog_cool.lock(on); self.btn_reset.setEnabled(not on)

    def _on_perf(self, on):
        def ok():
            self._perf = on; self.tog_perf.set_state(on); self._update_tray()
            self.lbl_st.setText("✓ Max performance enabled"
                                if on else "✓ Balanced mode")
        self._run("perf-max" if on else "perf-reset",
                  on_ok=ok, on_fail=lambda: self.tog_perf.set_state(self._perf))

    def _on_cool(self, on):
        def ok():
            self._cool = on; self.tog_cool.set_state(on); self._update_tray()
            self.lbl_st.setText(f"✓ Extreme cooling {'ON' if on else 'OFF'}")
        self._run("cool-on" if on else "cool-off",
                  on_ok=ok, on_fail=lambda: self.tog_cool.set_state(self._cool))

    def _on_reset(self):
        def ok():
            self._perf = False; self._cool = False
            self.tog_perf.set_state(False); self.tog_cool.set_state(False)
            self._update_tray()
            self.lbl_st.setText("✓ Reset to balanced")
        self._run("reset", on_ok=ok)

    def closeEvent(self, e):
        if self._tray.isVisible():
            self.hide()
            self._act_show.setText("Show")
            e.ignore()
        else:
            self._nv_w.requestInterruption(); self._nv_w.wait(1000)
            super().closeEvent(e)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("Legion Performance Controller")
    app.setQuitOnLastWindowClosed(False)  # окно в трее — приложение живёт
    MainWindow().show()
    sys.exit(app.exec())
