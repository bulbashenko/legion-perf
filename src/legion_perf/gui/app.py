"""Точка входа GUI: трей, окно, опрос метрик и демона."""

from __future__ import annotations

import argparse
import os
import sys

from PySide6.QtCore import QObject, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .. import __version__
from ..api import FanMode, Feature
from . import metrics
from .client import DaemonClient, DaemonState
from .icon import temp_icon
from .window import FAN_MODE_LABELS, MainWindow

METRICS_INTERVAL_MS = 1000
DAEMON_INTERVAL_MS = 5000
NVIDIA_INTERVAL_MS = 2000
INSTANCE_KEY = f"legion-perf-{os.getuid()}"


class NvidiaWorker(QThread):
    """nvidia-smi работает ~100 мс, поэтому в отдельном потоке и только при открытом окне."""

    ready = Signal(object)  # NvidiaStats | None | "suspended"

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.active = False

    def run(self) -> None:
        while not self.isInterruptionRequested():
            if self.active:
                if metrics.nvidia_suspended():
                    self.ready.emit("suspended")
                else:
                    self.ready.emit(metrics.nvidia_query())
            self.msleep(NVIDIA_INTERVAL_MS)


class Controller(QObject):
    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self.app = app
        self.state: DaemonState | None = None
        self.cpu_load = metrics.CpuLoad()
        self.cpu_temp: float | None = None

        self.window = MainWindow()
        self.client = DaemonClient(parent=self)
        self.client.state_changed.connect(self._on_state)
        self.client.unavailable.connect(self._on_unavailable)
        self.client.call_failed.connect(self.window.show_error)
        self.window.extreme_cooling_requested.connect(self.client.set_extreme_cooling)
        self.window.camera_power_requested.connect(self.client.set_camera_power)
        self.window.fan_mode_requested.connect(self.client.set_fan_mode)
        self.window.visibility_changed.connect(self._on_window_visibility)

        self.nvidia = NvidiaWorker(self)
        self.nvidia.ready.connect(self._show_nvidia)
        self.nvidia.start()
        app.aboutToQuit.connect(self._shutdown)

        self._setup_tray()

        self._metrics_timer = QTimer(self)
        self._metrics_timer.timeout.connect(self._tick)
        self._metrics_timer.start(METRICS_INTERVAL_MS)
        self._daemon_timer = QTimer(self)
        self._daemon_timer.timeout.connect(self.client.refresh)
        self._daemon_timer.start(DAEMON_INTERVAL_MS)
        self._tick()
        self.client.refresh()

    # ── Трей ────────────────────────────────────────────────────────────────

    def _setup_tray(self) -> None:
        self.tray = QSystemTrayIcon(temp_icon(None), self)
        menu = QMenu()
        self._act_show = menu.addAction("Show")
        self._act_show.triggered.connect(self.toggle_window)
        menu.addSeparator()

        self._act_cooling = QAction("❄  Extreme Cooling", self, checkable=True)
        self._act_cooling.triggered.connect(self.client.set_extreme_cooling)
        menu.addAction(self._act_cooling)

        self._fan_menu = menu.addMenu("Fan mode")
        self._fan_group = QActionGroup(self)
        self._fan_actions: dict[FanMode, QAction] = {}
        for mode, label in FAN_MODE_LABELS.items():
            action = QAction(label, self._fan_group, checkable=True)
            action.triggered.connect(lambda _=False, m=mode: self.client.set_fan_mode(m))
            self._fan_menu.addAction(action)
            self._fan_actions[mode] = action

        self._act_camera = QAction("📷  Camera power", self, checkable=True)
        self._act_camera.triggered.connect(self.client.set_camera_power)
        menu.addAction(self._act_camera)
        menu.addSeparator()
        menu.addAction("Quit").triggered.connect(self.app.quit)

        def before_menu() -> None:
            self._act_show.setText("Hide" if self.window.isVisible() else "Show")
            self.client.refresh()

        menu.aboutToShow.connect(before_menu)
        self._menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self._update_tray()
        self.tray.show()

    def _update_tray(self) -> None:
        state = self.state
        cooling = bool(state and state.extreme_cooling)
        self.tray.setIcon(temp_icon(self.cpu_temp, cooling))
        features = state.features if state else set()
        self._act_cooling.setVisible(Feature.EXTREME_COOLING in features)
        self._act_cooling.setChecked(cooling)
        self._fan_menu.menuAction().setVisible(Feature.FAN_MODE in features)
        if state and state.fan_mode:
            self._fan_actions[state.fan_mode].setChecked(True)
        self._act_camera.setVisible(Feature.CAMERA_POWER in features)
        self._act_camera.setChecked(bool(state and state.camera_power))

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_window()

    def toggle_window(self) -> None:
        if self.window.isVisible():
            self.window.hide()
        else:
            self.show_window()

    def show_window(self) -> None:
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    # ── Данные ──────────────────────────────────────────────────────────────

    def _on_state(self, state: DaemonState) -> None:
        self.state = state
        self.window.show_state(state)
        self._update_tray()

    def _on_unavailable(self, error: str) -> None:
        self.state = None
        self.window.show_unavailable(error)
        self._update_tray()

    def _on_window_visibility(self, visible: bool) -> None:
        self.nvidia.active = visible
        if visible:
            self.client.refresh()

    def _tick(self) -> None:
        load = self.cpu_load.sample()
        self.cpu_temp = metrics.cpu_temp()
        self.tray.setIcon(temp_icon(self.cpu_temp, bool(self.state and self.state.extreme_cooling)))
        if not self.window.isVisible():
            return

        w = self.window
        freq = metrics.cpu_freq_mhz()
        parts = [
            f"{load:.0f}%" if load is not None else "—",
            f"{freq:.0f} MHz" if freq is not None else "",
            f"{self.cpu_temp:.0f}°C" if self.cpu_temp is not None else "",
        ]
        w.cpu.show_value(load or 0, "   ".join(p for p in parts if p))

        ram = metrics.ram_gb()
        if ram:
            w.ram.show_value(ram[0] / ram[1] * 100, f"{ram[0]:.1f} / {ram[1]:.1f} GB")
        else:
            w.ram.show_value(0, "unavailable")

        igpu = metrics.igpu_mhz()
        if igpu is None:
            w.igpu.show_value(0, "not found")
        elif igpu[0] == 0:
            w.igpu.show_value(0, f"idle (RC6)   max {igpu[1]} MHz")
        else:
            w.igpu.show_value(igpu[0] / igpu[1] * 100 if igpu[1] else 0, f"{igpu[0]} / {igpu[1]} MHz")

    def _show_nvidia(self, stats: metrics.NvidiaStats | str | None) -> None:
        row = self.window.nvidia
        if stats == "suspended":
            row.show_value(0, "suspended (D3)")
        elif isinstance(stats, metrics.NvidiaStats):
            row.show_value(
                stats.load,
                f"{stats.load:.0f}%   {stats.clock_mhz} MHz   {stats.temp:.0f}°C   {stats.power_w:.0f} W",
            )
        else:
            row.show_value(0, "unavailable")

    def _shutdown(self) -> None:
        self.nvidia.requestInterruption()
        self.nvidia.wait(3000)


def _activate_running_instance() -> bool:
    """Если GUI уже запущен — просим его показать окно и выходим."""
    socket = QLocalSocket()
    socket.connectToServer(INSTANCE_KEY)
    if not socket.waitForConnected(300):
        return False
    socket.write(b"show")
    socket.waitForBytesWritten(300)
    socket.disconnectFromServer()
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Legion Performance Controller")
    parser.add_argument("--minimized", action="store_true", help="start in the system tray")
    parser.add_argument("--version", action="version", version=__version__)
    args, qt_args = parser.parse_known_args()

    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName("Legion Performance Controller")
    app.setDesktopFileName("legion-perf")
    app.setQuitOnLastWindowClosed(False)

    if _activate_running_instance():
        return

    controller = Controller(app)

    server = QLocalServer(app)
    QLocalServer.removeServer(INSTANCE_KEY)  # сокет от упавшего процесса
    server.listen(INSTANCE_KEY)

    def on_activate_request() -> None:
        server.nextPendingConnection()
        controller.show_window()

    server.newConnection.connect(on_activate_request)

    if args.minimized:
        # Автозапуск: сразу в трей. Если трей так и не появился — показываем окно,
        # иначе до приложения не добраться.
        def fallback() -> None:
            if not QSystemTrayIcon.isSystemTrayAvailable():
                controller.show_window()

        QTimer.singleShot(5000, fallback)
    else:
        controller.show_window()
    sys.exit(app.exec())
