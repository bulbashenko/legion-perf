from pathlib import Path

import pytest

from legion_perf.gui import metrics


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_cpu_load(tmp_path: Path) -> None:
    write(tmp_path, "proc/stat", "cpu  100 0 100 700 100 0 0 0 0 0\ncpu0 ...\n")
    load = metrics.CpuLoad(tmp_path)
    assert load.sample() == 0.0  # первый замер — без базы
    write(tmp_path, "proc/stat", "cpu  200 0 200 750 150 0 0 0 0 0\ncpu0 ...\n")
    # прошло 300 тиков, из них 100 idle+iowait → 2/3 занято
    assert load.sample() == pytest.approx(66.67, abs=0.01)


def test_cpu_temp_package_only(tmp_path: Path) -> None:
    write(tmp_path, "sys/class/hwmon/hwmon0/name", "acpitz\n")
    write(tmp_path, "sys/class/hwmon/hwmon0/temp1_input", "99000\n")
    write(tmp_path, "sys/class/hwmon/hwmon3/name", "coretemp\n")
    write(tmp_path, "sys/class/hwmon/hwmon3/temp2_label", "Core 0\n")
    write(tmp_path, "sys/class/hwmon/hwmon3/temp2_input", "50000\n")
    write(tmp_path, "sys/class/hwmon/hwmon3/temp1_label", "Package id 0\n")
    write(tmp_path, "sys/class/hwmon/hwmon3/temp1_input", "61500\n")
    assert metrics.cpu_temp(tmp_path) == 61.5


def test_cpu_temp_missing(tmp_path: Path) -> None:
    assert metrics.cpu_temp(tmp_path) is None


def test_ram(tmp_path: Path) -> None:
    write(tmp_path, "proc/meminfo", "MemTotal:  16777216 kB\nMemFree: 1 kB\nMemAvailable: 4194304 kB\n")
    assert metrics.ram_gb(tmp_path) == (12.0, 16.0)


def test_igpu(tmp_path: Path) -> None:
    write(tmp_path, "sys/class/drm/card1/gt_act_freq_mhz", "350\n")
    write(tmp_path, "sys/class/drm/card1/gt_max_freq_mhz", "1100\n")
    assert metrics.igpu_mhz(tmp_path) == (350, 1100)


def test_igpu_missing(tmp_path: Path) -> None:
    (tmp_path / "sys/class/drm/card0").mkdir(parents=True)  # dGPU без gt_*
    assert metrics.igpu_mhz(tmp_path) is None


@pytest.mark.parametrize(("status", "expected"), [("suspended", True), ("active", False)])
def test_nvidia_suspended(tmp_path: Path, status: str, expected: bool) -> None:
    write(tmp_path, "sys/bus/pci/drivers/nvidia/0000:01:00.0/power/runtime_status", f"{status}\n")
    assert metrics.nvidia_suspended(tmp_path) is expected
