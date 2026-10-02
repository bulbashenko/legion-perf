"""Клиент демона на QtDBus: асинхронные вызовы, GUI не блокируется."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QObject, Signal
from PySide6.QtDBus import QDBusConnection, QDBusInterface, QDBusPendingCallWatcher

from ..api import BUS_NAME, INTERFACE, OBJECT_PATH, Feature

_PROPERTIES = ("Features", "ExtremeCooling")


@dataclass
class DaemonState:
    features: set[Feature] = field(default_factory=set)
    extreme_cooling: bool = False

    @classmethod
    def from_properties(cls, props: dict[str, Any]) -> DaemonState:
        known = {f.value: f for f in Feature}
        return cls(
            features={known[f] for f in props["Features"] if f in known},
            extreme_cooling=bool(props["ExtremeCooling"]),
        )


class DaemonClient(QObject):
    state_changed = Signal(object)  # DaemonState
    unavailable = Signal(str)  # демон не отвечает
    call_failed = Signal(str)  # отказ polkit, ошибка железа

    def __init__(self, connection: QDBusConnection | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        conn = connection or QDBusConnection.systemBus()
        self._props = QDBusInterface(BUS_NAME, OBJECT_PATH, "org.freedesktop.DBus.Properties", conn, self)
        self._iface = QDBusInterface(BUS_NAME, OBJECT_PATH, INTERFACE, conn, self)
        self._refreshing = False

    def refresh(self) -> None:
        if self._refreshing:
            return
        self._refreshing = True
        values: dict[str, Any] = {}
        errors: list[str] = []

        def done(watcher: QDBusPendingCallWatcher, name: str) -> None:
            if watcher.isError():
                errors.append(watcher.error().message())
            else:
                values[name] = watcher.reply().arguments()[0].variant()
            watcher.deleteLater()
            if len(values) + len(errors) < len(_PROPERTIES):
                return
            self._refreshing = False
            if errors:
                self.unavailable.emit(errors[0])
            else:
                self.state_changed.emit(DaemonState.from_properties(values))

        for name in _PROPERTIES:
            call = self._props.asyncCallWithArgumentList("Get", [INTERFACE, name])
            watcher = QDBusPendingCallWatcher(call, self)
            watcher.finished.connect(lambda w, n=name: done(w, n))

    def set_extreme_cooling(self, on: bool) -> None:
        self._call("SetExtremeCooling", on)

    def _call(self, method: str, value: Any) -> None:
        def done(watcher: QDBusPendingCallWatcher) -> None:
            if watcher.isError():
                self.call_failed.emit(watcher.error().message())
            watcher.deleteLater()
            self.refresh()

        watcher = QDBusPendingCallWatcher(self._iface.asyncCallWithArgumentList(method, [value]), self)
        watcher.finished.connect(done)
