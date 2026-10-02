"""Метрики системы. Root не нужен; root-параметр подменяется в тестах."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

ROOT = Path("/")


class CpuLoad:
    """Загрузка CPU между двумя вызовами sample()."""

    def __init__(self, root: Path = ROOT) -> None:
        self._stat = root / "proc/stat"
        self._prev: tuple[int, int] | None = None

    def sample(self) -> float | None:
        try:
            values = [int(v) for v in self._stat.read_text().split("\n", 1)[0].split()[1:]]
        except (OSError, ValueError):
            return None
        idle, total = values[3] + values[4], sum(values)  # idle + iowait
        prev, self._prev = self._prev, (idle, total)
        if prev is None or total == prev[1]:
            return 0.0
        return max(0.0, (1 - (idle - prev[0]) / (total - prev[1])) * 100)


def cpu_freq_mhz(root: Path = ROOT) -> float | None:
    try:
        freqs = [
            int(p.read_text())
            for p in (root / "sys/devices/system/cpu").glob("cpu[0-9]*/cpufreq/scaling_cur_freq")
        ]
    except (OSError, ValueError):
        return None
    return sum(freqs) / len(freqs) / 1000 if freqs else None


def cpu_temp(root: Path = ROOT) -> float | None:
    """Температура пакета CPU (coretemp, Package id 0)."""
    for hwmon in (root / "sys/class/hwmon").glob("hwmon*"):
        try:
            if (hwmon / "name").read_text().strip() != "coretemp":
                continue
            for label in hwmon.glob("temp*_label"):
                if label.read_text().startswith("Package"):
                    return int((hwmon / label.name.replace("_label", "_input")).read_text()) / 1000
        except (OSError, ValueError):
            continue
    return None


def ram_gb(root: Path = ROOT) -> tuple[float, float] | None:
    """(занято, всего) в GiB."""
    try:
        info = {
            line.split(":")[0]: int(line.split()[1])
            for line in (root / "proc/meminfo").read_text().splitlines()
            if ":" in line
        }
        total, available = info["MemTotal"] / 1048576, info["MemAvailable"] / 1048576
    except (OSError, ValueError, KeyError, IndexError):
        return None
    return total - available, total


def igpu_mhz(root: Path = ROOT) -> tuple[int, int] | None:
    """(реальная, максимальная) частота iGPU i915. Реальная = 0, когда GPU в RC6."""
    for act in sorted((root / "sys/class/drm").glob("card[0-9]*/gt_act_freq_mhz")):
        try:
            return int(act.read_text()), int((act.parent / "gt_max_freq_mhz").read_text())
        except (OSError, ValueError):
            continue
    return None


def nvidia_suspended(root: Path = ROOT) -> bool:
    """nvidia-smi будит dGPU из runtime D3, поэтому спящую карту не опрашиваем."""
    for status in (root / "sys/bus/pci/drivers/nvidia").glob("0000:*/power/runtime_status"):
        try:
            if status.read_text().strip() == "suspended":
                return True
        except OSError:
            pass
    return False


@dataclass(frozen=True)
class NvidiaStats:
    load: float
    clock_mhz: int
    temp: float
    power_w: float


def nvidia_query() -> NvidiaStats | None:
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,clocks.gr,temperature.gpu,power.draw",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        ).stdout
        load, clock, temp, power = (v.strip() for v in out.splitlines()[0].split(","))
        return NvidiaStats(
            float(load),
            int(float(clock)),
            float(temp),
            float(power) if power not in ("[N/A]", "N/A") else 0.0,
        )
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None
