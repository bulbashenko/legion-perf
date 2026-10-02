import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

# GUI-тесты без дисплея (CI); должно быть выставлено до импорта Qt
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

EC_IO = "sys/kernel/debug/ec/ec0/io"
IDEAPAD = "sys/bus/platform/drivers/ideapad_acpi/VPC2004:00"


@pytest.fixture
def fake_root(tmp_path: Path) -> Path:
    """Фейковое дерево sysfs/debugfs: 256-байтный EC и атрибуты ideapad_laptop."""
    ec = tmp_path / EC_IO
    ec.parent.mkdir(parents=True)
    ec.write_bytes(bytes(256))
    ideapad = tmp_path / IDEAPAD
    ideapad.mkdir(parents=True)
    (ideapad / "fan_mode").write_text("1\n")
    (ideapad / "camera_power").write_text("1\n")
    return tmp_path


def ec_byte(root: Path, reg: int) -> int:
    return (root / EC_IO).read_bytes()[reg]


@pytest.fixture
def dbus_address() -> Iterator[str]:
    """Отдельный dbus-daemon на время теста."""
    if shutil.which("dbus-daemon") is None:
        pytest.skip("dbus-daemon not installed")
    proc = subprocess.Popen(
        ["dbus-daemon", "--session", "--nofork", "--print-address"],
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ, "DBUS_VERBOSE": "0"},
    )
    assert proc.stdout is not None
    address = proc.stdout.readline().strip()
    try:
        yield address
    finally:
        proc.terminate()
        proc.wait(5)
