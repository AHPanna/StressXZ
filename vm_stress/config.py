"""
vm_stress.config
================
Data-classes that carry all configuration and collected metrics for a
stress-test run.  No heavy dependencies — just stdlib + dataclasses.

Author  : Panna ABDUL HAKIM <panna@pnax.io>
Org     : PNAX.io LAB  (https://pnax.io)
License : MIT — see LICENSE
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# ── StressConfig ──────────────────────────────────────────────────────────────

@dataclass
class StressConfig:
    """
    All configuration for a single stress-test run.

    Instances are normally created by :func:`vm_stress.cli.args_to_config`
    from parsed CLI arguments, but can also be constructed directly for
    programmatic use.
    """

    # ── Resource toggles ──────────────────────────────────────────────────────
    cpu:     bool = False
    ram:     bool = False
    disk:    bool = False
    network: bool = False

    # ── Resource limits ───────────────────────────────────────────────────────
    cpu_limit:          float = 100.0   # % of one logical core  (0–100)
    ram_limit_mb:       float = 256.0   # MB to physically allocate
    disk_intensity:     int   = 5       # 1–10  (scales file size & frequency)
    network_limit_mbps: float = 10.0   # Mbps target loopback bandwidth

    # ── Run parameters ────────────────────────────────────────────────────────
    duration: int = 30    # seconds
    threads:  int = 2     # worker threads per resource

    # ── Remote execution (SSH) ────────────────────────────────────────────────
    remote_host:  Optional[str] = None
    remote_user:  str           = "root"
    remote_port:  int           = 22
    ssh_key:      Optional[str] = None   # private key for the remote VM
    bastion_host: Optional[str] = None
    bastion_user: Optional[str] = None
    bastion_port: int           = 22
    bastion_key:  Optional[str] = None   # private key for the bastion host (falls back to ssh_key)

    # ── Privilege escalation ──────────────────────────────────────────────────
    sudo_password:      Optional[str] = None   # inline sudo password (remote)
    sudo_password_file: Optional[str] = None   # path to file containing the password

    # ── Output / misc ─────────────────────────────────────────────────────────
    output_file:  Optional[str] = None
    dry_run:      bool          = False
    log_level:    str           = "INFO"
    disk_temp_dir: str          = "/tmp"


# ── StressResult ──────────────────────────────────────────────────────────────

@dataclass
class StressResult:
    """
    Metrics collected during a stress run.

    All ``*_samples`` lists hold one float per sampling interval (default 1 s).
    Call :meth:`summary_dict` to get a flat dictionary suitable for reporting.
    """

    hostname:   str                  = ""
    start_time: datetime             = field(default_factory=datetime.now)
    end_time:   Optional[datetime]   = None
    config:     Optional[StressConfig] = None

    # ── Time-series samples (one entry per second) ────────────────────────────
    cpu_samples:           list[float] = field(default_factory=list)
    ram_used_mb_samples:   list[float] = field(default_factory=list)
    disk_read_mb_samples:  list[float] = field(default_factory=list)
    disk_write_mb_samples: list[float] = field(default_factory=list)
    net_sent_mb_samples:   list[float] = field(default_factory=list)
    net_recv_mb_samples:   list[float] = field(default_factory=list)

    errors: list[str] = field(default_factory=list)

    # ── Utility helpers ───────────────────────────────────────────────────────

    def avg(self, samples: list[float]) -> float:
        """Return the arithmetic mean of *samples*, or 0.0 if empty."""
        return sum(samples) / len(samples) if samples else 0.0

    def max_val(self, samples: list[float]) -> float:
        """Return the maximum of *samples*, or 0.0 if empty."""
        return max(samples) if samples else 0.0

    def summary_dict(self) -> dict:
        """
        Return a flat dictionary of computed summary statistics.
        Suitable for both console printing and structured report writing.
        """
        return {
            "hostname":  self.hostname,
            "start":     self.start_time.isoformat(),
            "end":       self.end_time.isoformat() if self.end_time else "N/A",
            "duration_s": round(
                (self.end_time - self.start_time).total_seconds(), 2
            ) if self.end_time else 0,

            "cpu_avg_%":  round(self.avg(self.cpu_samples),           2),
            "cpu_max_%":  round(self.max_val(self.cpu_samples),       2),
            "ram_avg_MB": round(self.avg(self.ram_used_mb_samples),   2),
            "ram_max_MB": round(self.max_val(self.ram_used_mb_samples), 2),

            "disk_read_avg_MB":  round(self.avg(self.disk_read_mb_samples),  4),
            "disk_write_avg_MB": round(self.avg(self.disk_write_mb_samples), 4),

            "net_sent_avg_MB": round(self.avg(self.net_sent_mb_samples), 4),
            "net_recv_avg_MB": round(self.avg(self.net_recv_mb_samples), 4),

            "errors": self.errors,
        }
