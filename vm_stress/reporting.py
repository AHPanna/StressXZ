"""
vm_stress.reporting
===================
Console summary and file-based report generation.

Author  : Panna ABDUL HAKIM <panna@pnax.io>
Org     : PNAX.io LAB  (https://pnax.io)
License : MIT — see LICENSE

Two public functions:

- :func:`print_summary`  — pretty-print a test result to *stdout*
- :func:`save_report`    — write a structured ``.txt`` report to disk

Both accept a :class:`~vm_stress.config.StressResult` and read the
attached :class:`~vm_stress.config.StressConfig` to know which resources
were tested.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from vm_stress.config import StressResult

logger = logging.getLogger("vm_stress.reporting")

# Visual separator used in both console and file output
_SEP70  = "═" * 70
_SEP70_ = "=" * 70   # ASCII variant for file (better cross-platform compat)


# ══════════════════════════════════════════════════════════════════════════════
# Console summary
# ══════════════════════════════════════════════════════════════════════════════

def print_summary(result: StressResult) -> None:
    """
    Print a formatted stress-test summary to *stdout*.

    Only the resources that were enabled in ``result.config`` are shown.

    Args:
        result: A completed :class:`~vm_stress.config.StressResult`.
    """
    s   = result.summary_dict()
    cfg = result.config

    print(f"\n{_SEP70}")
    print(f"  STRESS TEST REPORT  —  {s['hostname']}")
    print(_SEP70)
    print(f"  Start    : {s['start']}")
    print(f"  End      : {s['end']}")
    print(f"  Duration : {s['duration_s']}s")
    print()

    if cfg and cfg.cpu:
        print(f"  CPU      avg={s['cpu_avg_%']:.2f}%   max={s['cpu_max_%']:.2f}%")
    if cfg and cfg.ram:
        print(
            f"  RAM      avg={s['ram_avg_MB']:.1f} MB   "
            f"max={s['ram_max_MB']:.1f} MB"
        )
    if cfg and cfg.disk:
        print(f"  Disk R   avg={s['disk_read_avg_MB']:.3f} MB/s")
        print(f"  Disk W   avg={s['disk_write_avg_MB']:.3f} MB/s")
    if cfg and cfg.network:
        print(
            f"  Net TX   avg={s['net_sent_avg_MB']:.3f} MB/s  "
            f"≈ {s['net_sent_avg_MB'] * 8:.2f} Mbps"
        )
        print(
            f"  Net RX   avg={s['net_recv_avg_MB']:.3f} MB/s  "
            f"≈ {s['net_recv_avg_MB'] * 8:.2f} Mbps"
        )

    if s["errors"]:
        print(f"\n  ⚠ Errors ({len(s['errors'])}):")
        for e in s["errors"]:
            print(f"    • {e}")

    print(_SEP70 + "\n")


# ══════════════════════════════════════════════════════════════════════════════
# File report
# ══════════════════════════════════════════════════════════════════════════════

def save_report(result: StressResult, path: str) -> None:
    """
    Write a human-readable structured report to *path*.

    The report includes:
    - Generation timestamp and hostname
    - All configured test parameters
    - Run timeline (start / end / elapsed)
    - Avg & max summary for every metric
    - Raw CPU and RAM time-series samples (up to 60 per section)
    - Any errors encountered during the run

    The parent directory is created automatically if it does not exist.

    Args:
        result: A completed :class:`~vm_stress.config.StressResult`.
        path:   Destination file path (typically ``*.txt``).
    """
    s   = result.summary_dict()
    cfg = result.config

    lines: list[str] = [
        _SEP70_,
        "VM STRESS TEST REPORT",
        f"Generated  : {datetime.now().isoformat()}",
        f"Hostname   : {s['hostname']}",
        _SEP70_,
        "",
        "── TEST PARAMETERS ──────────────────────────────────────────────────",
    ]

    if cfg:
        active = ", ".join(
            r for r, on in [
                ("CPU", cfg.cpu), ("RAM", cfg.ram),
                ("Disk", cfg.disk), ("Network", cfg.network),
            ] if on
        ) or "(none)"
        lines += [
            f"  Resources  : {active}",
            f"  Duration   : {cfg.duration}s",
            f"  Threads    : {cfg.threads}",
        ]
        if cfg.cpu:
            lines.append(f"  CPU limit  : {cfg.cpu_limit}%")
        if cfg.ram:
            lines.append(f"  RAM limit  : {cfg.ram_limit_mb} MB")
        if cfg.disk:
            lines.append(f"  Disk inten.: {cfg.disk_intensity}/10")
        if cfg.network:
            lines.append(f"  Net limit  : {cfg.network_limit_mbps} Mbps")
        if cfg.remote_host:
            lines.append(f"  Remote     : {cfg.remote_host}")
        if cfg.bastion_host:
            lines.append(f"  Bastion    : {cfg.bastion_host}")

    lines += [
        "",
        "── RUN TIMELINE ─────────────────────────────────────────────────────",
        f"  Start      : {s['start']}",
        f"  End        : {s['end']}",
        f"  Duration   : {s['duration_s']}s",
        "",
        "── RESOURCE SUMMARY ─────────────────────────────────────────────────",
        f"  CPU   avg  : {s['cpu_avg_%']:.2f}%",
        f"  CPU   max  : {s['cpu_max_%']:.2f}%",
        f"  RAM   avg  : {s['ram_avg_MB']:.1f} MB",
        f"  RAM   max  : {s['ram_max_MB']:.1f} MB",
        f"  Disk  read  avg : {s['disk_read_avg_MB']:.4f} MB/s",
        f"  Disk  write avg : {s['disk_write_avg_MB']:.4f} MB/s",
        f"  Net   TX    avg : {s['net_sent_avg_MB']:.4f} MB/s"
          f"  ({s['net_sent_avg_MB'] * 8:.3f} Mbps)",
        f"  Net   RX    avg : {s['net_recv_avg_MB']:.4f} MB/s"
          f"  ({s['net_recv_avg_MB'] * 8:.3f} Mbps)",
        "",
    ]

    # ── Time-series appendices ────────────────────────────────────────────────
    if result.cpu_samples:
        lines.append("── CPU SAMPLES (% per sec, up to 60) ────────────────────────────────")
        lines.append("  " + "  ".join(f"{v:.1f}" for v in result.cpu_samples[:60]))
        lines.append("")

    if result.ram_used_mb_samples:
        lines.append("── RAM SAMPLES (MB used per sec, up to 60) ──────────────────────────")
        lines.append("  " + "  ".join(f"{v:.0f}" for v in result.ram_used_mb_samples[:60]))
        lines.append("")

    if result.disk_write_mb_samples:
        lines.append("── DISK WRITE SAMPLES (MB/s per sec, up to 60) ──────────────────────")
        lines.append("  " + "  ".join(f"{v:.2f}" for v in result.disk_write_mb_samples[:60]))
        lines.append("")

    if result.net_sent_mb_samples:
        lines.append("── NET TX SAMPLES (MB/s per sec, up to 60) ──────────────────────────")
        lines.append("  " + "  ".join(f"{v:.3f}" for v in result.net_sent_mb_samples[:60]))
        lines.append("")

    # ── Errors ────────────────────────────────────────────────────────────────
    if s["errors"]:
        lines.append("── ERRORS ───────────────────────────────────────────────────────────")
        for err in s["errors"]:
            lines.append(f"  • {err}")
        lines.append("")

    lines.append(_SEP70_)

    # ── Write to disk ─────────────────────────────────────────────────────────
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Report saved to: %s", path)
    print(f"\n  📄  Report saved → {path}")
