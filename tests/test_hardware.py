from pathlib import Path

import pytest

from legion_perf.api import Feature
from legion_perf.hardware import EcSysExtremeCooling, Hardware, HardwareError

from .conftest import EC_IO, ec_byte


def test_detect_finds_all_features(fake_root: Path) -> None:
    hw = Hardware.detect(fake_root)
    assert set(hw.controls) == {Feature.EXTREME_COOLING}


def test_detect_without_ec_sys(tmp_path: Path) -> None:
    assert Hardware.detect(tmp_path).controls == {}


def test_extreme_cooling_writes_only_register_0xbd(fake_root: Path) -> None:
    ec = Hardware.detect(fake_root).controls[Feature.EXTREME_COOLING]
    assert ec.get() is False

    ec.set(True)
    assert ec_byte(fake_root, 0xBD) == 0x40
    assert ec.get() is True
    data = (fake_root / EC_IO).read_bytes()
    assert data[:0xBD] == bytes(0xBD) and data[0xBE:] == bytes(256 - 0xBE)

    ec.set(False)
    assert ec_byte(fake_root, 0xBD) == 0x00


def test_extreme_cooling_io_error(tmp_path: Path) -> None:
    with pytest.raises(HardwareError):
        EcSysExtremeCooling(tmp_path / "missing").get()
