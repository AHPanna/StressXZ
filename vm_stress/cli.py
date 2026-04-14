"""
vm_stress.cli
=============
Command-line interface: argument parsing, config assembly, and the

Author  : Panna ABDUL HAKIM <panna@pnax.io>
Org     : PNAX.io LAB  (https://pnax.io)
License : MIT — see LICENSE
``main()`` entry-point.

The :func:`main` function is the single top-level callable that wires
every other module together:

1. Parses CLI arguments → :class:`~vm_stress.config.StressConfig`
2. Validates the config
3. Dispatches to :class:`~vm_stress.tester.StressTester` (local) or
   :func:`~vm_stress.remote.run_remote` (SSH)
4. Calls :func:`~vm_stress.reporting.print_summary` and
   :func:`~vm_stress.reporting.save_report`
"""
from __future__ import annotations

import argparse
import logging
import socket
import sys
from datetime import datetime
from pathlib import Path

from vm_stress.config import StressConfig, StressResult
from vm_stress.logging_setup import configure_logging
from vm_stress.remote import run_remote
from vm_stress.reporting import print_summary, save_report
from vm_stress.tester import StressTester

logger = logging.getLogger("vm_stress.cli")

# All auto-named reports land here; --output can override to any path
RESULTS_DIR = Path(__file__).parent.parent / "results"


# ══════════════════════════════════════════════════════════════════════════════
# Argument parser
# ══════════════════════════════════════════════════════════════════════════════

def build_parser() -> argparse.ArgumentParser:
    """
    Build and return the :class:`argparse.ArgumentParser` for the tool.

    Argument groups:

    - **Resource Selection** — ``--cpu``, ``--ram``, ``--disk``, ``--network``
    - **Resource Limits**    — per-resource intensity / size / bandwidth caps
    - **Run Parameters**     — ``--duration``, ``--threads``, ``--disk-temp-dir``
    - **Remote Execution**   — SSH + bastion flags
    - **Output & Misc**      — ``--output``, ``--dry-run``, ``--log-level``
    """
    parser = argparse.ArgumentParser(
        prog="stressXZ",
        description=(
            "Production-ready Linux VM stress tester.\n"
            "Stress CPU, RAM, Disk I/O, and/or Network individually "
            "with configurable limits and optional remote SSH execution."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Stress CPU at 50%% on 4 threads for 60 seconds
  python3 main.py --cpu --cpu-limit 50 --threads 4 --duration 60

  # Stress RAM (512 MB) + Disk (intensity 7) for 30 seconds
  python3 main.py --ram --ram-limit 512 --disk --disk-intensity 7

  # Full stress test — all resources, save report
  python3 main.py --cpu --ram --disk --network --duration 120 --output report.txt

  # Dry-run — preview config without running anything
  python3 main.py --cpu --ram --dry-run

  # Remote execution via bastion host
  python3 main.py --cpu --duration 60 \\
      --remote-host 10.0.0.5 --remote-user ubuntu --ssh-key ~/.ssh/id_rsa \\
      --bastion-host jump.example.com --bastion-user ec2-user
        """,
    )

    # ── Resource selection ────────────────────────────────────────────────────
    res = parser.add_argument_group("Resource Selection")
    res.add_argument(
        "--cpu", action="store_true",
        help="Enable CPU stress test",
    )
    res.add_argument(
        "--ram", action="store_true",
        help="Enable RAM stress test",
    )
    res.add_argument(
        "--disk", action="store_true",
        help="Enable Disk I/O stress test",
    )
    res.add_argument(
        "--network", action="store_true",
        help="Enable Network stress test (loopback TCP)",
    )

    # ── Resource limits ───────────────────────────────────────────────────────
    lim = parser.add_argument_group("Resource Limits")
    lim.add_argument(
        "--cpu-limit", type=float, default=50.0, metavar="PCT",
        help="CPU usage limit per worker thread in %% (default: 50)",
    )
    lim.add_argument(
        "--ram-limit", type=float, default=256.0, metavar="MB",
        help="Total RAM to allocate across all threads in MB (default: 256)",
    )
    lim.add_argument(
        "--disk-intensity", type=int, default=5, metavar="1-10",
        choices=range(1, 11),
        help=(
            "Disk I/O intensity: 1=very light (1 MB file) … "
            "10=maximum (512 MB file, no sleep).  Default: 5"
        ),
    )
    lim.add_argument(
        "--network-limit", type=float, default=10.0, metavar="MBPS",
        dest="network_limit_mbps",
        help="Total loopback network bandwidth target in Mbps (default: 10)",
    )

    # ── Run parameters ────────────────────────────────────────────────────────
    run = parser.add_argument_group("Run Parameters")
    run.add_argument(
        "--duration", "-d", type=int, default=30, metavar="SEC",
        help="Test duration in seconds (default: 30)",
    )
    run.add_argument(
        "--threads", "-t", type=int, default=2, metavar="N",
        help="Number of worker threads per enabled resource (default: 2)",
    )
    run.add_argument(
        "--disk-temp-dir", default="/tmp", metavar="DIR",
        help="Directory for temporary Disk I/O scratch files (default: /tmp)",
    )

    # ── Remote execution (SSH) ────────────────────────────────────────────────
    rem = parser.add_argument_group("Remote Execution (SSH)")
    rem.add_argument(
        "--remote-host", metavar="HOST",
        help="IP address or hostname of the remote VM to stress-test",
    )
    rem.add_argument(
        "--remote-user", default="root", metavar="USER",
        help="SSH username on the remote host (default: root)",
    )
    rem.add_argument(
        "--remote-port", type=int, default=22, metavar="PORT",
        help="SSH port on the remote host (default: 22)",
    )
    rem.add_argument(
        "--ssh-key", metavar="PATH",
        help="Path to SSH private key.  Falls back to ssh-agent / ~/.ssh/id_*",
    )
    rem.add_argument(
        "--bastion-host", metavar="HOST",
        help="Bastion / jump host through which the SSH connection is tunnelled",
    )
    rem.add_argument(
        "--bastion-user", metavar="USER",
        help="SSH username on the bastion host (default: same as --remote-user)",
    )
    rem.add_argument(
        "--bastion-port", type=int, default=22, metavar="PORT",
        help="SSH port on the bastion host (default: 22)",
    )
    rem.add_argument(
        "--bastion-key", metavar="PATH",
        help=(
            "Path to SSH private key for the bastion host.  "
            "Falls back to --ssh-key if omitted"
        ),
    )

    # ── Privilege escalation ─────────────────────────────────────────────────────
    priv = parser.add_argument_group("Privilege Escalation (Remote Sudo)")
    priv.add_argument(
        "--sudo-password", metavar="PASS",
        help=(
            "Sudo password for the remote VM user.  "
            "Used with 'sudo -S' so it is never logged.  "
            "Prefer --sudo-password-file to avoid secrets in shell history"
        ),
    )
    priv.add_argument(
        "--sudo-password-file", metavar="PATH",
        help=(
            "Path to a local file whose first line is the sudo password.  "
            "Safer than --sudo-password for CI/CD pipelines"
        ),
    )

    # ── Output & misc ─────────────────────────────────────────────────────────
    out = parser.add_argument_group("Output & Misc")
    out.add_argument(
        "--output", "-o", metavar="FILE",
        help=(
            "Path for the .txt report file.  "
            "Auto-saved to results/stress_report_<host>_<timestamp>.txt if omitted"
        ),
    )
    out.add_argument(
        "--dry-run", action="store_true",
        help=(
            "Print the planned configuration and exit without running "
            "any stress workers"
        ),
    )
    out.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity level (default: INFO)",
    )

    return parser


# ══════════════════════════════════════════════════════════════════════════════
# Config assembly
# ══════════════════════════════════════════════════════════════════════════════

def args_to_config(args: argparse.Namespace) -> StressConfig:
    """
    Translate a parsed :class:`argparse.Namespace` into a
    :class:`~vm_stress.config.StressConfig`.
    """
    return StressConfig(
        cpu=args.cpu,
        ram=args.ram,
        disk=args.disk,
        network=args.network,
        cpu_limit=args.cpu_limit,
        ram_limit_mb=args.ram_limit,
        disk_intensity=args.disk_intensity,
        network_limit_mbps=args.network_limit_mbps,
        duration=args.duration,
        threads=args.threads,
        disk_temp_dir=args.disk_temp_dir,
        remote_host=args.remote_host,
        remote_user=args.remote_user,
        remote_port=args.remote_port,
        ssh_key=args.ssh_key,
        bastion_host=args.bastion_host,
        bastion_user=args.bastion_user,
        bastion_port=args.bastion_port,
        bastion_key=args.bastion_key,
        sudo_password=args.sudo_password,
        sudo_password_file=args.sudo_password_file,
        output_file=args.output,
        dry_run=args.dry_run,
        log_level=args.log_level,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def main() -> int:
    """
    Main entry point — parse CLI args, validate, run, report.

    Returns:
        Exit code: ``0`` = success, ``1`` = bad args, ``2`` = runtime error.
    """
    parser = build_parser()
    args   = parser.parse_args()

    configure_logging(args.log_level)

    cfg = args_to_config(args)

    # ── Validation ────────────────────────────────────────────────────────────
    if not any([cfg.cpu, cfg.ram, cfg.disk, cfg.network]) and not cfg.dry_run:
        logger.warning(
            "No resource flags specified. "
            "Use --cpu, --ram, --disk, and/or --network "
            "(or --dry-run to preview a configuration)."
        )
        parser.print_help()
        return 1

    if cfg.threads < 1:
        logger.error("--threads must be >= 1")
        return 1

    if cfg.duration < 1:
        logger.error("--duration must be >= 1 second")
        return 1

    # ── Auto-generate report path → results/ ─────────────────────────────────
    if cfg.output_file is None:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
        host_tag = (cfg.remote_host or socket.gethostname()).replace(".", "_")
        # Collect active test-type tags in a fixed order
        test_tags = "_".join(
            tag for tag, active in [
                ("cpu",     cfg.cpu),
                ("ram",     cfg.ram),
                ("disk",    cfg.disk),
                ("network", cfg.network),
            ] if active
        ) or "none"
        cfg.output_file = str(
            RESULTS_DIR / f"stress_report_{host_tag}_{test_tags}_{ts}.txt"
        )

    # ── Dispatch ──────────────────────────────────────────────────────────────
    result: StressResult
    try:
        if cfg.remote_host and not cfg.dry_run:
            result = run_remote(cfg)
        else:
            result = StressTester(cfg).run()

    except KeyboardInterrupt:
        logger.info("Interrupted by user — collecting partial results…")
        result = StressResult(
            hostname=socket.gethostname(),
            config=cfg,
            end_time=datetime.now(),
        )
        result.errors.append("Interrupted by user (KeyboardInterrupt)")

    except Exception as exc:
        logger.error(
            "Fatal error: %s", exc,
            exc_info=(cfg.log_level == "DEBUG"),
        )
        return 2

    # ── Output ────────────────────────────────────────────────────────────────
    print_summary(result)
    if not cfg.dry_run:
        save_report(result, cfg.output_file)

    return 0
