"""Доступ к железу. Только этот модуль знает пути в sysfs/debugfs.

fan_mode и camera_power из ideapad_laptop на Y720 не работают: драйвер создаёт
атрибуты, если прошивка отвечает на чтение, но запись прошивка игнорирует и всегда
читает 0 (проверено на BIOS 4GCN38WW). Поэтому они здесь не поддерживаются.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Protocol

from .api import Feature

log = logging.getLogger(__name__)


class HardwareError(Exception):
    pass


class Control(Protocol):
    def get(self) -> bool: ...
    def set(self, value: bool) -> None: ...


class EcSysExtremeCooling:
    """Extreme Cooling через EC-регистр 0xBD, доступный через ec_sys (write_support=1).

    Тот же регистр и значения использует LenovoLegionLinux (EXT_MAXIMUMFANSPEED) и
    extremecooling4linux. ec_sys пишет через ACPI EC-драйвер ядра с его блокировками.
    Ядро не умеет включать этот режим на Y720 штатно: lenovo-wmi-gamezone не
    поддерживает SetFanCooling. Если поддержка появится (hwmon pwm1_enable),
    достаточно добавить ещё одну реализацию Control.
    """

    REGISTER = 0xBD
    ON = 0x40
    OFF = 0x00

    def __init__(self, io_path: Path) -> None:
        self._io = io_path

    def get(self) -> bool:
        try:
            fd = os.open(self._io, os.O_RDONLY)
            try:
                return os.pread(fd, 1, self.REGISTER)[0] == self.ON
            finally:
                os.close(fd)
        except (OSError, IndexError) as e:
            raise HardwareError(f"EC read failed: {e}") from e

    def set(self, value: bool) -> None:
        try:
            fd = os.open(self._io, os.O_WRONLY)
            try:
                os.pwrite(fd, bytes([self.ON if value else self.OFF]), self.REGISTER)
            finally:
                os.close(fd)
        except OSError as e:
            raise HardwareError(f"EC write failed: {e}") from e


class Hardware:
    def __init__(self, controls: dict[Feature, Control]) -> None:
        self.controls = controls

    @classmethod
    def detect(cls, root: Path = Path("/")) -> Hardware:
        """Находит доступные функции. root подменяется в тестах фейковым деревом."""
        controls: dict[Feature, Control] = {}

        ec_io = root / "sys/kernel/debug/ec/ec0/io"
        if ec_io.exists():
            controls[Feature.EXTREME_COOLING] = EcSysExtremeCooling(ec_io)
        else:
            log.warning("%s not found: is ec_sys loaded with write_support=1?", ec_io)

        log.info("available features: %s", ", ".join(f.value for f in controls) or "none")
        return cls(controls)
