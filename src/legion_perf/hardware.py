"""Доступ к железу. Только этот модуль знает пути в sysfs/debugfs."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, ClassVar, Protocol

from .api import FanMode, Feature

log = logging.getLogger(__name__)


class HardwareError(Exception):
    pass


class Control(Protocol):
    def get(self) -> Any: ...
    def set(self, value: Any) -> None: ...


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


class SysfsAttribute:
    def __init__(self, path: Path) -> None:
        self._path = path

    def read_int(self) -> int:
        try:
            return int(self._path.read_text().strip())
        except (OSError, ValueError) as e:
            raise HardwareError(f"read {self._path} failed: {e}") from e

    def write_int(self, value: int) -> None:
        try:
            self._path.write_text(f"{value}\n")
        except OSError as e:
            raise HardwareError(f"write {self._path} failed: {e}") from e


class IdeapadSwitch:
    """Булев атрибут ideapad_laptop: camera_power и т.п."""

    def __init__(self, attr: SysfsAttribute) -> None:
        self._attr = attr

    def get(self) -> bool:
        return self._attr.read_int() != 0

    def set(self, value: bool) -> None:
        self._attr.write_int(1 if value else 0)


class IdeapadFanMode:
    _VALUES: ClassVar[dict[FanMode, int]] = {
        FanMode.SUPER_SILENT: 0,
        FanMode.STANDARD: 1,
        FanMode.DUST_CLEANING: 2,
        FanMode.EFFICIENT_THERMAL_DISSIPATION: 4,
    }
    _MODES: ClassVar[dict[int, FanMode]] = {v: k for k, v in _VALUES.items()}

    def __init__(self, attr: SysfsAttribute) -> None:
        self._attr = attr

    def get(self) -> FanMode:
        raw = self._attr.read_int()
        try:
            return self._MODES[raw]
        except KeyError:
            raise HardwareError(f"unknown fan_mode value {raw}") from None

    def set(self, value: FanMode) -> None:
        self._attr.write_int(self._VALUES[value])


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

        ideapad = next(iter(sorted((root / "sys/bus/platform/drivers/ideapad_acpi").glob("VPC2004:*"))), None)
        if ideapad is None:
            log.warning("ideapad_laptop device not found")
        else:
            if (ideapad / "fan_mode").exists():
                controls[Feature.FAN_MODE] = IdeapadFanMode(SysfsAttribute(ideapad / "fan_mode"))
            if (ideapad / "camera_power").exists():
                controls[Feature.CAMERA_POWER] = IdeapadSwitch(SysfsAttribute(ideapad / "camera_power"))

        log.info("available features: %s", ", ".join(f.value for f in controls) or "none")
        return cls(controls)
