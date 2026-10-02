"""D-Bus API демона: имена и значения, общие для демона и клиентов."""

from enum import StrEnum

BUS_NAME = "io.github.bulbashenko.LegionPerf"
OBJECT_PATH = "/io/github/bulbashenko/LegionPerf"
INTERFACE = "io.github.bulbashenko.LegionPerf"
POLKIT_ACTION = "io.github.bulbashenko.LegionPerf.configure"

ERROR_NOT_AUTHORIZED = f"{INTERFACE}.Error.NotAuthorized"
ERROR_NOT_SUPPORTED = f"{INTERFACE}.Error.NotSupported"
ERROR_INVALID_ARGS = f"{INTERFACE}.Error.InvalidArgs"
ERROR_HARDWARE = f"{INTERFACE}.Error.Hardware"


class Feature(StrEnum):
    EXTREME_COOLING = "extreme-cooling"
    FAN_MODE = "fan-mode"
    CAMERA_POWER = "camera-power"


class FanMode(StrEnum):
    """Режимы ideapad_laptop fan_mode (Documentation/ABI/testing/sysfs-platform-ideapad-laptop)."""

    SUPER_SILENT = "super-silent"
    STANDARD = "standard"
    DUST_CLEANING = "dust-cleaning"
    EFFICIENT_THERMAL_DISSIPATION = "efficient-thermal-dissipation"
