"""
vm_stress.metrics
=================
Background metrics-sampling thread.

Author  : Panna ABDUL HAKIM <panna@pnax.io>
Org     : PNAX.io LAB  (https://pnax.io)
License : MIT — see LICENSE

:func:`collect_metrics` runs in a daemon thread and samples
:mod:`psutil` counters once per second, appending deltas into the
:class:`~vm_stress.config.StressResult` passed to it.

If :mod:`psutil` is not installed the function logs a warning and
returns immediately, leaving the sample lists empty — the rest of the
tool still works, just without live monitoring data.
"""
from __future__ import annotations

import logging
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
    if not PSUTIL_AVAILABLE:
        logger.warning("psutil is not installed — metric collection disabled")
        return

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
