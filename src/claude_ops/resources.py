"""Resource monitoring via /proc for Claude processes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class ResourceStats:
    """CPU and memory stats for a process."""
    cpu_pct: float
    rss_mb: float


# Cache previous CPU ticks for delta calculation
_prev_ticks: dict[int, tuple[int, int]] = {}  # pid -> (total_ticks, proc_ticks)


def _read_proc_stat(pid: int) -> tuple[int, int] | None:
    """Read utime + stime from /proc/<pid>/stat. Returns (proc_ticks, total_ticks)."""
    try:
        stat_line = Path(f"/proc/{pid}/stat").read_text()
        idx = stat_line.rfind(")")
        if idx < 0:
            return None
        fields = stat_line[idx + 2:].split()
        utime = int(fields[11])
        stime = int(fields[12])
        proc_ticks = utime + stime
    except (FileNotFoundError, OSError, IndexError, ValueError):
        return None

    try:
        cpu_line = Path("/proc/stat").read_text().split("\n")[0]
        total_ticks = sum(int(x) for x in cpu_line.split()[1:])
    except (FileNotFoundError, OSError, IndexError, ValueError):
        return None

    return proc_ticks, total_ticks


def _read_rss_mb(pid: int) -> float | None:
    """Read VmRSS from /proc/<pid>/status in MB."""
    try:
        for line in Path(f"/proc/{pid}/status").read_text().split("\n"):
            if line.startswith("VmRSS:"):
                parts = line.split()
                return int(parts[1]) / 1024.0
    except (FileNotFoundError, OSError, IndexError, ValueError):
        pass
    return None


def get_process_resources(pids: list[int]) -> dict[int, ResourceStats]:
    """Read CPU% and RSS from /proc for given PIDs.

    CPU% is calculated as delta between calls. First call returns 0% CPU.
    Returns empty dict if /proc is unavailable.
    """
    result: dict[int, ResourceStats] = {}

    for pid in pids:
        ticks = _read_proc_stat(pid)
        rss = _read_rss_mb(pid)
        if ticks is None or rss is None:
            continue

        proc_ticks, total_ticks = ticks
        prev = _prev_ticks.get(pid)

        if prev is not None:
            prev_total, prev_proc = prev
            total_delta = total_ticks - prev_total
            proc_delta = proc_ticks - prev_proc
            cpu_pct = (proc_delta / total_delta * 100.0) if total_delta > 0 else 0.0
        else:
            cpu_pct = 0.0

        _prev_ticks[pid] = (total_ticks, proc_ticks)
        result[pid] = ResourceStats(cpu_pct=cpu_pct, rss_mb=rss)

    return result
