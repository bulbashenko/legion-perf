"""D-Bus API демона: имена и значения, общие для демона и клиентов."""

from enum import StrEnum

BUS_NAME = "io.github.bulbashenko.LegionPerf"
OBJECT_PATH = "/io/github/bulbashenko/LegionPerf"
INTERFACE = "io.github.bulbashenko.LegionPerf"
POLKIT_ACTION = "io.github.bulbashenko.LegionPerf.configure"

ERROR_NOT_AUTHORIZED = f"{INTERFACE}.Error.NotAuthorized"
ERROR_NOT_SUPPORTED = f"{INTERFACE}.Error.NotSupported"
ERROR_HARDWARE = f"{INTERFACE}.Error.Hardware"


class Feature(StrEnum):
    EXTREME_COOLING = "extreme-cooling"
