"""
vm_stress.tester
================
Orchestrates all stress workers and the metrics-collection thread.

Author  : Panna ABDUL HAKIM <panna@pnax.io>
Org     : PNAX.io LAB  (https://pnax.io)
License : MIT — see LICENSE

:class:`StressTester` is the main engine of the tool.  It:

1. Spawns the selected resource workers (CPU / RAM / Disk / Network).
2. Starts a background :func:`~vm_stress.metrics.collect_metrics` thread.
3. Blocks for the configured duration, displaying a live progress bar.
4. Signals all workers to stop and waits for them to exit cleanly.
5. Returns a fully populated :class:`~vm_stress.config.StressResult`.
"""
from __future__ import annotations

import logging
import queue
import socket
import sys
import threading
import time
from datetime import datetime

from vm_stress.config import StressConfig, StressResult
from vm_stress.metrics import collect_metrics
from vm_stress.workers import cpu_worker, disk_worker, network_worker, ram_worker

logger = logging.getLogger("vm_stress.tester")


class StressTester:
    """
    Orchestrates stress workers for a single local test run.

    Usage::

        cfg = StressConfig(cpu=True, cpu_limit=50, duration=30)
        result = StressTester(cfg).run()

    For remote execution use :func:`vm_stress.remote.run_remote` instead.
    """

    def __init__(self, cfg: StressConfig) -> None:
        self.cfg = cfg
        self._stop   = threading.Event()
        self._errors: queue.Queue[str] = queue.Queue()

    # ── Public entry point ────────────────────────────────────────────────────

    def run(self) -> StressResult:
        """
        Execute the stress test and return a :class:`StressResult`.

        If ``cfg.dry_run`` is ``True`` the test is described on stdout but no
        workers are actually started.
        """
        cfg    = self.cfg
        result = StressResult(hostname=socket.gethostname(), config=cfg)

        if cfg.dry_run:
            logger.info("DRY RUN — no workers will be started")
            self._print_dry_run(cfg)
            result.end_time = datetime.now()
            return result

        logger.info(
            "Starting stress test — duration=%ds, threads=%d",
            cfg.duration, cfg.threads,
        )

        workers = self._launch_workers(cfg)

        if not workers:
            logger.warning("No resources selected — nothing to stress-test.")
            result.end_time = datetime.now()
            return result

        # ── Background metrics sampler ────────────────────────────────────────
        metrics_thread = threading.Thread(
            target=collect_metrics,
            args=(result, self._stop),
            name="metrics",
            daemon=True,
        )
        metrics_thread.start()

        # ── Block for the test duration ───────────────────────────────────────
        result.start_time = datetime.now()
        self._progress_loop(cfg.duration)

        # ── Graceful shutdown ─────────────────────────────────────────────────
        logger.info("Stopping all workers…")
        self._stop.set()

        for w in workers:
            w.join(timeout=10)
        metrics_thread.join(timeout=5)

        result.end_time = datetime.now()

        # Drain error queue into the result
        while not self._errors.empty():
            result.errors.append(self._errors.get_nowait())

        if result.errors:
            for err in result.errors:
                logger.warning("Worker error: %s", err)

        return result

    # ── Worker launch ─────────────────────────────────────────────────────────

    def _launch_workers(self, cfg: StressConfig) -> list[threading.Thread]:
        """Spawn all enabled resource workers and return the thread list."""
        workers: list[threading.Thread] = []

        if cfg.cpu:
            for i in range(cfg.threads):
                t = threading.Thread(
                    target=cpu_worker,
                    args=(cfg.cpu_limit, self._stop),
                    name=f"cpu-{i}",
                    daemon=True,
                )
                workers.append(t)
                t.start()
            logger.info(
                "CPU workers started (%d × %.1f%%)", cfg.threads, cfg.cpu_limit
            )

        if cfg.ram:
            per_thread_mb = cfg.ram_limit_mb / cfg.threads
            for i in range(cfg.threads):
                t = threading.Thread(
                    target=ram_worker,
                    args=(per_thread_mb, self._stop, self._errors),
                    name=f"ram-{i}",
                    daemon=True,
                )
                workers.append(t)
                t.start()
            logger.info(
                "RAM workers started (%d × %.1f MB = %.1f MB total)",
                cfg.threads, per_thread_mb, cfg.ram_limit_mb,
            )

        if cfg.disk:
            for i in range(cfg.threads):
                t = threading.Thread(
                    target=disk_worker,
                    args=(
                        cfg.disk_intensity,
                        cfg.disk_temp_dir,
                        self._stop,
                        self._errors,
                    ),
                    name=f"disk-{i}",
                    daemon=True,
                )
                workers.append(t)
                t.start()
            logger.info(
                "Disk workers started (%d, intensity=%d)",
                cfg.threads, cfg.disk_intensity,
            )

        if cfg.network:
            mbps_per_thread = cfg.network_limit_mbps / cfg.threads
            for i in range(cfg.threads):
                t = threading.Thread(
                    target=network_worker,
                    args=(mbps_per_thread, self._stop, self._errors),
                    name=f"net-{i}",
                    daemon=True,
                )
                workers.append(t)
                t.start()
            logger.info(
                "Network workers started (%d × %.2f Mbps)",
                cfg.threads, mbps_per_thread,
            )

        return workers

    # ── UI helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _progress_loop(duration: int) -> None:
        """Render a live Unicode progress bar to *stderr* for *duration* seconds."""
        bar_width = 40
        for elapsed in range(duration + 1):
            remaining = duration - elapsed
            filled = int(bar_width * elapsed / duration) if duration else bar_width
            bar    = "█" * filled + "░" * (bar_width - filled)
            pct    = int(100 * elapsed / duration) if duration else 100
            sys.stderr.write(
                f"\r  [{bar}] {pct:3d}%  {elapsed:4d}/{duration}s  "
                f"remaining: {remaining:4d}s   "
            )
            sys.stderr.flush()
            if elapsed < duration:
                time.sleep(1)
        sys.stderr.write("\n")
        sys.stderr.flush()

    @staticmethod
    def _print_dry_run(cfg: StressConfig) -> None:
        """Print a human-readable preview of what would be run."""
        print("\n" + "═" * 60)
        print("  DRY RUN — Planned stress-test configuration")
        print("═" * 60)
        if cfg.cpu:
            print(f"  CPU      : {cfg.threads} thread(s) × {cfg.cpu_limit:.1f}%")
        if cfg.ram:
            print(
                f"  RAM      : {cfg.ram_limit_mb:.0f} MB total "
                f"({cfg.threads} thread(s))"
            )
        if cfg.disk:
            print(
                f"  Disk I/O : intensity {cfg.disk_intensity}/10, "
                f"{cfg.threads} thread(s), dir={cfg.disk_temp_dir}"
            )
        if cfg.network:
            print(
                f"  Network  : {cfg.network_limit_mbps:.2f} Mbps total "
                f"({cfg.threads} thread(s))"
            )
        if not any([cfg.cpu, cfg.ram, cfg.disk, cfg.network]):
            print("  (no resources selected)")
        print(f"  Duration : {cfg.duration}s")
        print(f"  Remote   : {cfg.remote_host or 'local'}")
        print("═" * 60 + "\n")
