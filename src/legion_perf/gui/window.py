"""Главное окно: метрики и переключатели."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..api import FanMode, Feature
from .client import DaemonState

FAN_MODE_LABELS = {
    FanMode.SUPER_SILENT: "Super silent",
    FanMode.STANDARD: "Standard",
    FanMode.DUST_CLEANING: "Dust cleaning",
    FanMode.EFFICIENT_THERMAL_DISSIPATION: "Efficient thermal dissipation",
}


class MetricRow(QWidget):
    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 3, 0, 3)
        label = QLabel(name)
        label.setFixedWidth(58)
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        font = label.font()
        font.setBold(True)
        label.setFont(font)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(14)
        self.value = QLabel("—")
        self.value.setMinimumWidth(240)
        lay.addWidget(label)
        lay.addWidget(self.bar, 1)
        lay.addWidget(self.value)

    def show_value(self, percent: float, text: str) -> None:
        p = max(0, min(100, int(percent)))
        self.bar.setValue(p)
        color = "#4caf50" if p < 60 else "#ff9800" if p < 85 else "#f44336"
        self.bar.setStyleSheet(
            f"QProgressBar::chunk{{background:{color};border-radius:3px;}}"
            "QProgressBar{border:1px solid palette(mid);border-radius:3px;background:palette(base);}"
        )
        self.value.setText(text)


class ToggleRow(QWidget):
    toggled = Signal(bool)
    _HIGHLIGHT = "background:palette(highlight);color:palette(highlighted-text);border-radius:4px;"

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 4)
        self.dot = QLabel("●")
        self.dot.setFixedWidth(20)
        self.off = QPushButton("OFF")
        self.on = QPushButton("ON")
        for button in (self.off, self.on):
            button.setFixedWidth(75)
        self.off.clicked.connect(lambda: self._click(False))
        self.on.clicked.connect(lambda: self._click(True))
        lay.addWidget(self.dot)
        lay.addWidget(QLabel(title), 1)
        lay.addWidget(self.off)
        lay.addWidget(self.on)
        self._state: bool | None = None
        self.set_state(None)

    def set_state(self, on: bool | None) -> None:
        self._state = on
        self.on.setStyleSheet(self._HIGHLIGHT if on is True else "")
        self.off.setStyleSheet(self._HIGHLIGHT if on is False else "")
        color = "#4caf50" if on is True else "#757575" if on is False else "#ff9800"
        self.dot.setStyleSheet(f"font-size:16px;color:{color};")

    def _click(self, on: bool) -> None:
        if self._state is not on:
            self.toggled.emit(on)


class MainWindow(QMainWindow):
    extreme_cooling_requested = Signal(bool)
    camera_power_requested = Signal(bool)
    fan_mode_requested = Signal(object)  # FanMode
    visibility_changed = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Legion Performance Controller")
        self.setMinimumWidth(560)
        self.hide_on_close = True

        monitoring = QGroupBox("Monitoring")
        ml = QVBoxLayout(monitoring)
        ml.setSpacing(1)
        self.cpu = MetricRow("CPU")
        self.ram = MetricRow("RAM")
        self.igpu = MetricRow("iGPU")
        self.nvidia = MetricRow("NVIDIA")
        for row in (self.cpu, self.ram, self.igpu, self.nvidia):
            ml.addWidget(row)

        self.controls = QGroupBox("Controls")
        cl = QVBoxLayout(self.controls)
        cl.setSpacing(2)
        self.extreme_cooling = ToggleRow("❄  Extreme Cooling  (max fan speed)")
        self.camera_power = ToggleRow("📷  Camera power")
        self.fan_mode_row = QWidget()
        fl = QHBoxLayout(self.fan_mode_row)
        fl.setContentsMargins(20, 4, 0, 4)
        fl.addWidget(QLabel("Fan mode"), 1)
        self.fan_mode = QComboBox()
        for mode, label in FAN_MODE_LABELS.items():
            self.fan_mode.addItem(label, mode)
        self.fan_mode.setFixedWidth(240)
        fl.addWidget(self.fan_mode)
        for w in (self.extreme_cooling, self.fan_mode_row, self.camera_power):
            cl.addWidget(w)

        self.status = QLabel("Connecting to daemon…")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setWordWrap(True)

        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setSpacing(10)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.addWidget(monitoring)
        lay.addWidget(self.controls)
        lay.addWidget(self.status)
        self.setCentralWidget(central)

        self.extreme_cooling.toggled.connect(self.extreme_cooling_requested)
        self.camera_power.toggled.connect(self.camera_power_requested)
        self.fan_mode.activated.connect(lambda i: self.fan_mode_requested.emit(self.fan_mode.itemData(i)))

    def show_state(self, state: DaemonState) -> None:
        self.controls.setEnabled(True)
        self.extreme_cooling.setVisible(Feature.EXTREME_COOLING in state.features)
        self.fan_mode_row.setVisible(Feature.FAN_MODE in state.features)
        self.camera_power.setVisible(Feature.CAMERA_POWER in state.features)
        self.extreme_cooling.set_state(state.extreme_cooling)
        self.camera_power.set_state(state.camera_power)
        if state.fan_mode is not None:
            self.fan_mode.setCurrentIndex(self.fan_mode.findData(state.fan_mode))
        if not state.features:
            self.status.setText("No supported controls found (is ec_sys loaded?)")
        elif self.status.text().startswith(("Connecting", "⚠ Daemon")):
            self.status.setText("Ready")

    def show_unavailable(self, error: str) -> None:
        self.controls.setEnabled(False)
        self.status.setText(f"⚠ Daemon is not available: {error}\nCheck: systemctl status legion-perfd")

    def show_error(self, error: str) -> None:
        self.status.setText(f"✗ {error}")

    def showEvent(self, event):  # type: ignore[no-untyped-def]
        super().showEvent(event)
        self.visibility_changed.emit(True)

    def hideEvent(self, event):  # type: ignore[no-untyped-def]
        super().hideEvent(event)
        self.visibility_changed.emit(False)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.hide_on_close:
            self.hide()
            event.ignore()
        else:
            super().closeEvent(event)
