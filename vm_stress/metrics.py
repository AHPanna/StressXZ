"""
vm_stress.metrics
=================
Background metrics-sampling thread.

Author  : Panna ABDUL HAKIM <panna@pnax.io>
Org     : PNAX.io LAB  (https://pnax.io)
License : MIT — see LICENSE

:func:`collect_metrics` runs in a daemon thread and samples resource
counters once per second, appending deltas into the
:class:`~vm_stress.config.StressResult` passed to it.

Priority order:
1. ``psutil`` — full cross-platform counters (preferred).
2. Linux ``/proc`` filesystem — pure-stdlib fallback for CPU, RAM,
   and network when psutil is absent (covers all remote Linux VMs).

If neither is available the function logs a warning and returns.
"""
from __future__ import annotations

import logging
import os
import threading
import time

from vm_stress.config import StressResult

logger = logging.getLogger("vm_stress.metrics")

# ── Optional dependency ───────────────────────────────────────────────────────
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

# Check if Linux /proc fallback is usable
_PROC_AVAILABLE = os.path.exists("/proc/stat")


def collect_metrics(
    result: StressResult,
    stop_event: threading.Event,
    interval: float = 1.0,
) -> None:
    """
    Sample system-wide resource counters every *interval* seconds.

    Appends one float per sample to the following :class:`StressResult` lists:

    - ``cpu_samples``           — overall CPU utilisation in %
    - ``ram_used_mb_samples``   — physical RAM in use (MB)
    - ``disk_read_mb_samples``  — disk bytes read since last sample (MB/s)
    - ``disk_write_mb_samples`` — disk bytes written since last sample (MB/s)
    - ``net_sent_mb_samples``   — network bytes sent since last sample (MB/s)
    - ``net_recv_mb_samples``   — network bytes received since last sample (MB/s)

    Args:
        result:     The :class:`StressResult` to populate in-place.
        stop_event: Set this event to stop the collection loop.
        interval:   Sampling interval in seconds (default 1.0).
    """
    if PSUTIL_AVAILABLE:
        _collect_psutil(result, stop_event, interval)
    elif _PROC_AVAILABLE:
        logger.warning("psutil not available — using /proc fallback for metrics")
        _collect_proc(result, stop_event, interval)
    else:
        logger.warning("psutil is not installed and /proc is unavailable — metric collection disabled")
        return


# ══════════════════════════════════════════════════════════════════════════════
# psutil backend
# ══════════════════════════════════════════════════════════════════════════════

def _collect_psutil(
    result: StressResult,
    stop_event: threading.Event,
    interval: float = 1.0,
) -> None:
    """Collect metrics using the psutil library."""
    # Warm up the cpu_percent baseline (first call always returns 0.0)
    psutil.cpu_percent(interval=None)

    # Grab baseline counters before the first delta
    prev_disk = psutil.disk_io_counters()
    prev_net  = psutil.net_io_counters()

    while not stop_event.is_set():
        time.sleep(interval)

        # ── CPU (%) ───────────────────────────────────────────────────────────
        result.cpu_samples.append(psutil.cpu_percent(interval=None))

        # ── RAM (MB used) ─────────────────────────────────────────────────────
        vm = psutil.virtual_memory()
        result.ram_used_mb_samples.append(vm.used / 1024 / 1024)

        # ── Disk I/O (MB delta since last sample) ─────────────────────────────
        curr_disk = psutil.disk_io_counters()
        if curr_disk and prev_disk:
            read_mb  = (curr_disk.read_bytes  - prev_disk.read_bytes)  / 1024 / 1024
            write_mb = (curr_disk.write_bytes - prev_disk.write_bytes) / 1024 / 1024
            result.disk_read_mb_samples.append(max(0.0, read_mb))
            result.disk_write_mb_samples.append(max(0.0, write_mb))
            prev_disk = curr_disk

        # ── Network I/O (MB delta since last sample) ──────────────────────────
        curr_net = psutil.net_io_counters()
        if curr_net and prev_net:
            sent_mb = (curr_net.bytes_sent - prev_net.bytes_sent) / 1024 / 1024
            recv_mb = (curr_net.bytes_recv - prev_net.bytes_recv) / 1024 / 1024
            result.net_sent_mb_samples.append(max(0.0, sent_mb))
            result.net_recv_mb_samples.append(max(0.0, recv_mb))
            prev_net = curr_net


# ══════════════════════════════════════════════════════════════════════════════
# /proc fallback backend (Linux-only, zero dependencies)
# ══════════════════════════════════════════════════════════════════════════════

def _read_cpu_times() -> tuple[float, float]:
    """Return (idle_jiffies, total_jiffies) from /proc/stat line 0."""
    with open("/proc/stat", "r") as fh:
        parts = fh.readline().split()   # cpu  user  nice  system  idle  ...
    values = [int(x) for x in parts[1:]]
    # Fields: user, nice, system, idle, iowait, irq, softirq, steal, ...
    idle  = values[3] + (values[4] if len(values) > 4 else 0)  # idle + iowait
    total = sum(values)
    return float(idle), float(total)


def _read_ram_used_mb() -> float:
    """Return used RAM in MB from /proc/meminfo."""
    info: dict[str, int] = {}
    with open("/proc/meminfo", "r") as fh:
        for line in fh:
            key, _, value = line.partition(":")
            info[key.strip()] = int(value.split()[0])  # value is in kB
    total     = info.get("MemTotal", 0)
    available = info.get("MemAvailable", info.get("MemFree", 0))
    used_kb   = max(0, total - available)
    return used_kb / 1024.0


def _read_net_counters() -> tuple[int, int]:
    """Return (bytes_sent_total, bytes_recv_total) across all non-loopback interfaces."""
    sent_total = 0
    recv_total = 0
    with open("/proc/net/dev", "r") as fh:
        for line in fh:
            line = line.strip()
            if ":" not in line:
                continue
            iface, _, data = line.partition(":")
            iface = iface.strip()
            if iface == "lo":
                continue   # skip loopback — our network worker uses loopback
            fields = data.split()
            if len(fields) >= 9:
                recv_total += int(fields[0])   # bytes received
                sent_total += int(fields[8])   # bytes sent
    return sent_total, recv_total


def _collect_proc(
    result: StressResult,
    stop_event: threading.Event,
    interval: float = 1.0,
) -> None:
    """Collect metrics using the Linux /proc filesystem (zero dependencies)."""
    prev_idle, prev_total = _read_cpu_times()
    prev_sent, prev_recv  = _read_net_counters()

    while not stop_event.is_set():
        time.sleep(interval)

        # ── CPU (%) ───────────────────────────────────────────────────────────
        curr_idle, curr_total = _read_cpu_times()
        delta_total = curr_total - prev_total
        delta_idle  = curr_idle  - prev_idle
        if delta_total > 0:
            cpu_pct = 100.0 * (1.0 - delta_idle / delta_total)
            result.cpu_samples.append(max(0.0, cpu_pct))
        prev_idle, prev_total = curr_idle, curr_total

        # ── RAM (MB used) ─────────────────────────────────────────────────────
        result.ram_used_mb_samples.append(_read_ram_used_mb())

        # ── Network I/O (MB delta since last sample) ──────────────────────────
        curr_sent, curr_recv = _read_net_counters()
        sent_mb = max(0.0, (curr_sent - prev_sent) / 1024 / 1024)
        recv_mb = max(0.0, (curr_recv - prev_recv) / 1024 / 1024)
        result.net_sent_mb_samples.append(sent_mb)
        result.net_recv_mb_samples.append(recv_mb)
        prev_sent, prev_recv = curr_sent, curr_recv
