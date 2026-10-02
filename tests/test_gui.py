"""GUI-клиент против настоящего демона (отдельный процесс) на отдельной шине."""

import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
import shiboken6
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtDBus import QDBusConnection
from PySide6.QtWidgets import QApplication

from legion_perf.api import Feature
from legion_perf.gui.client import DaemonClient, DaemonState
from legion_perf.gui.icon import GREEN, GREY, RED, YELLOW, temp_color, temp_icon

from .conftest import EC_IO, ec_byte


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def daemon_bus(dbus_address: str, fake_root: Path) -> Iterator[str]:
    proc = subprocess.Popen(
        [sys.executable, "-m", "legion_perf.daemon", "--session", "--root", str(fake_root)],
        env={**os.environ, "DBUS_SESSION_BUS_ADDRESS": dbus_address},
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stderr is not None
    for line in proc.stderr:  # ждём, пока демон займёт имя на шине
        if "ready" in line:
            break
    yield dbus_address
    proc.terminate()
    proc.wait(5)


def wait_for(signal: object, action: Callable[[], None], timeout_ms: int = 3000) -> list:
    got: list = []
    loop = QEventLoop()
    signal.connect(lambda *args: (got.extend(args), loop.quit()))  # type: ignore[attr-defined]
    QTimer.singleShot(timeout_ms, loop.quit)
    action()
    loop.exec()
    return got


@pytest.fixture
def make_client(qapp: QApplication, dbus_address: str) -> Iterator[Callable[[str], DaemonClient]]:
    """Клиент на отдельном соединении. Соединение рвём до остановки dbus-daemon
    (фикстура зависит от dbus_address, поэтому снимается раньше неё), иначе QtDBus
    падает, обращаясь к уже удалённым объектам."""
    created: list[tuple[DaemonClient, str]] = []

    def make(address: str) -> DaemonClient:
        name = f"test-{os.urandom(4).hex()}"
        client = DaemonClient(QDBusConnection.connectToBus(address, name))
        created.append((client, name))
        return client

    yield make
    for client, name in created:
        shiboken6.delete(client)
        QDBusConnection.disconnectFromBus(name)


def test_refresh(make_client: Callable[[str], DaemonClient], daemon_bus: str) -> None:
    client = make_client(daemon_bus)
    (state,) = wait_for(client.state_changed, client.refresh)
    assert state == DaemonState(
        features={Feature.EXTREME_COOLING},
        extreme_cooling=False,
    )


def test_set_calls(make_client: Callable[[str], DaemonClient], daemon_bus: str, fake_root: Path) -> None:
    client = make_client(daemon_bus)
    (state,) = wait_for(client.state_changed, lambda: client.set_extreme_cooling(True))
    assert state.extreme_cooling is True
    assert ec_byte(fake_root, 0xBD) == 0x40

    (state,) = wait_for(client.state_changed, lambda: client.set_extreme_cooling(False))
    assert state.extreme_cooling is False
    assert ec_byte(fake_root, 0xBD) == 0x00


def test_call_error_is_reported(
    make_client: Callable[[str], DaemonClient], daemon_bus: str, fake_root: Path
) -> None:
    (fake_root / EC_IO).unlink()  # железо пропало после старта демона
    client = make_client(daemon_bus)
    (error,) = wait_for(client.call_failed, lambda: client.set_extreme_cooling(True))
    assert "EC write failed" in error


def test_daemon_not_running(make_client: Callable[[str], DaemonClient], dbus_address: str) -> None:
    client = make_client(dbus_address)
    (error,) = wait_for(client.unavailable, client.refresh)
    assert error


@pytest.mark.parametrize(
    ("temp", "color"), [(None, GREY), (45.0, GREEN), (69.9, GREEN), (70.0, YELLOW), (85.0, RED)]
)
def test_temp_color(temp: float | None, color: object) -> None:
    assert temp_color(temp) == color


def test_temp_icon_renders(qapp: QApplication) -> None:
    assert not temp_icon(72.0, extreme_cooling=True).isNull()
    assert not temp_icon(None).isNull()
