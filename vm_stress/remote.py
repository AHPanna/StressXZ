"""
vm_stress.remote
================
Helpers for running a stress test on a **remote** Linux VM over SSH.

Author  : Panna ABDUL HAKIM <panna@pnax.io>
Org     : PNAX.io LAB  (https://pnax.io)
License : MIT — see LICENSE

Strategy
--------
1. The local copy of ``main.py`` + the entire ``vm_stress/`` package are
   packaged into a single-file archive (base64-encoded tar.gz) and pushed
   to ``/tmp/vm_stress_pkg/`` on the remote host via a single SSH ``exec``
   command.
2. The remote ``python3`` then runs ``main.py`` with the same flags used
   locally (minus the ``--remote-host`` / bastion flags to avoid recursion).
3. The remote stdout is captured, parsed, and surfaced in the local
   :class:`~vm_stress.config.StressResult`.

Public API
----------
- :func:`run_remote`  — top-level entry point called by :func:`vm_stress.cli.main`
"""
from __future__ import annotations

import base64
import io
import logging
import tarfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from vm_stress.config import StressConfig, StressResult
from vm_stress.executor import SSHContext, PARAMIKO_AVAILABLE

logger = logging.getLogger("vm_stress.remote")

# Remote staging directory
_REMOTE_DIR = "/tmp/_vm_stress_remote"


# ══════════════════════════════════════════════════════════════════════════════
# Public entry point
# ══════════════════════════════════════════════════════════════════════════════

def run_remote(cfg: StressConfig) -> StressResult:
    """
    Upload the tool to *cfg.remote_host* and execute it there.

    The function:

    1. Builds an in-memory tar.gz of the local ``vm_stress/`` package +
       ``main.py``.
    2. Ships it to the remote as a base64 blob via a single SSH exec.
    3. Runs the stress test on the remote with the same parameters.
    4. Returns a :class:`StressResult` populated from the remote output.

    Args:
        cfg: Full :class:`StressConfig`; ``remote_host`` must be set.

    Returns:
        A :class:`StressResult` describing the remote run.

    Raises:
        RuntimeError: If the upload or remote execution fails.
    """
    if not PARAMIKO_AVAILABLE:
        raise RuntimeError(
            "paramiko is required for remote execution.\n"
            "Install it with:  pip install paramiko"
        )

    logger.info("Preparing remote execution on %s", cfg.remote_host)

    # Build the archive once
    archive_b64 = _build_archive_b64()

    t_start = datetime.now()
    with SSHContext(cfg) as ctx:
        _upload_package(ctx, archive_b64)
        _ensure_deps(ctx)
        _execute_remote(ctx, cfg)
        t_end = datetime.now()
        report_txt = _fetch_remote_report(ctx)

    return _parse_remote_report(report_txt, cfg, t_start, t_end)


# ══════════════════════════════════════════════════════════════════════════════
# Internal helpers
# ══════════════════════════════════════════════════════════════════════════════

def _build_archive_b64() -> str:
    """
    Create a base64-encoded tar.gz of the local package (``vm_stress/`` +
    ``main.py``).  Everything is read from the filesystem relative to this
    file's parent directory.
    """
    pkg_root = Path(__file__).parent.parent.resolve()   # project root
    buf = io.BytesIO()

    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        # Add the vm_stress package
        vm_stress_dir = pkg_root / "vm_stress"
        tar.add(str(vm_stress_dir), arcname="vm_stress")

        # Add main.py
        main_py = pkg_root / "main.py"
        if main_py.exists():
            tar.add(str(main_py), arcname="main.py")
        else:
            # Fallback: add a minimal main.py that calls the module
            minimal = (
                "#!/usr/bin/env python3\n"
                "from vm_stress.cli import main\n"
                "import sys\n"
                "sys.exit(main())\n"
            )
            info = tarfile.TarInfo(name="main.py")
            data = minimal.encode()
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

    return base64.b64encode(buf.getvalue()).decode()


def _ensure_deps(ctx: SSHContext) -> None:
    """
    Silently install ``psutil`` into the remote staging directory so that
    no ``sudo``, no system-wide pip, and no user-level pip are required.

    Strategy
    --------
    1. Try ``python3 -c 'import psutil'`` — already available system-wide.
    2. Try ``pip3 install --target <staging_dir> psutil`` — installs into the
       project directory that is already on ``sys.path`` for the remote run.
    3. Try ``python3 -m pip install --target <staging_dir> psutil`` — same but
       via the module interface (works in some venv setups where ``pip3`` is absent).
    4. Fall back to ``apt-get`` / ``yum`` with ``sudo`` only when all else fails.

    Failures are logged as warnings — the test still runs without psutil
    but metric readings will be zero.
    """
    logger.info("Checking remote Python dependencies (psutil)…")
    # --target installs the package files directly into the staging dir.
    # We set PYTHONPATH so the verification step finds it the same way the
    # remote main.py will (via _execute_remote's PYTHONPATH= prefix).
    target = _REMOTE_DIR
    check_cmd = " || ".join([
        "python3 -c 'import psutil' 2>/dev/null",
        f"pip3 install -q --target {target} psutil 2>/dev/null",
        f"python3 -m pip install -q --target {target} psutil 2>/dev/null",
        "sudo apt-get install -y -q python3-psutil 2>/dev/null",
        "sudo yum install -y -q python3-psutil 2>/dev/null",
    ])
    # Run install attempt, then verify with PYTHONPATH so we know it'll work at runtime
    verify_cmd = (
        f"({check_cmd}) && "
        f"PYTHONPATH={target}:$PYTHONPATH python3 -c 'import psutil' 2>/dev/null"
    )
    rc, _, _ = ctx.run(verify_cmd)
    if rc != 0:
        logger.warning(
            "Could not install psutil on remote (tried pip3 --target / apt-get / yum) — "
            "will use /proc fallback for metrics",
        )
    else:
        logger.info("Remote psutil OK")


def _upload_package(ctx: SSHContext, archive_b64: str) -> None:
    """Decode and extract the archive on the remote host."""
    # One-liner: decode stdin (base64) → parse as tar.gz → extract to _REMOTE_DIR
    cmd = (
        f"mkdir -p {_REMOTE_DIR} && "
        f"python3 -c \""
        f"import base64, io, sys, tarfile; "
        f"data = base64.b64decode(sys.stdin.read()); "
        f"tarfile.open(fileobj=io.BytesIO(data)).extractall('{_REMOTE_DIR}')"
        f"\" <<'__ARCHIVE__'\n"
        f"{archive_b64}\n"
        f"__ARCHIVE__"
    )
    logger.info("Uploading package to %s:%s …", ctx._cfg.remote_host, _REMOTE_DIR)
    rc, _out, err = ctx.run(cmd)
    if rc != 0:
        raise RuntimeError(
            f"Package upload to {ctx._cfg.remote_host} failed:\n{err.strip()}"
        )
    logger.info("Upload complete")


def _execute_remote(ctx: SSHContext, cfg: StressConfig) -> None:
    """Run the stress test on the remote (stdout is informational only)."""
    args = _build_remote_args(cfg)
    # PYTHONPATH ensures that packages installed via `pip --target <_REMOTE_DIR>`
    # (e.g. psutil) are importable even without a system-wide or user-level install.
    cmd  = (
        f"cd {_REMOTE_DIR} && "
        f"PYTHONPATH={_REMOTE_DIR}:$PYTHONPATH "
        f"python3 main.py {args}"
    )
    logger.info("Remote command: %s", cmd)

    rc, out, err = ctx.run(cmd)
    if rc not in (0, 1):   # rc=1 is "no resources" warning — still valid
        raise RuntimeError(
            f"Remote execution failed (rc={rc}):\n{err.strip()}"
        )
    if err.strip():
        logger.debug("Remote stderr:\n%s", err.strip())
    if out.strip():
        logger.debug("Remote stdout:\n%s", out.strip())


def _fetch_remote_report(ctx: SSHContext) -> str:
    """Download the structured report file written by the remote run."""
    remote_path = "/tmp/vm_stress_remote_report.txt"
    rc, out, err = ctx.run(f"cat {remote_path}")
    if rc != 0:
        logger.warning(
            "Could not read remote report %s (rc=%d): %s",
            remote_path, rc, err.strip(),
        )
        return ""
    logger.debug("Fetched remote report (%d bytes)", len(out))
    return out


def _build_remote_args(cfg: StressConfig) -> str:
    """
    Translate *cfg* into CLI flags suitable for passing to the remote
    ``main.py``, intentionally omitting all SSH / bastion flags to avoid
    infinite recursion.
    """
    parts: list[str] = []
    if cfg.cpu:
        parts += ["--cpu", f"--cpu-limit {cfg.cpu_limit}"]
    if cfg.ram:
        parts += ["--ram", f"--ram-limit {cfg.ram_limit_mb}"]
    if cfg.disk:
        parts += ["--disk", f"--disk-intensity {cfg.disk_intensity}"]
    if cfg.network:
        parts += ["--network", f"--network-limit {cfg.network_limit_mbps}"]

    parts += [
        f"--duration {cfg.duration}",
        f"--threads {cfg.threads}",
        f"--log-level {cfg.log_level}",
        "--output /tmp/vm_stress_remote_report.txt",
    ]
    return " ".join(parts)


def _parse_remote_report(
    report_txt: str,
    cfg: StressConfig,
    t_start: Optional[datetime] = None,
    t_end: Optional[datetime] = None,
) -> StressResult:
    """
    Parse the structured ``.txt`` report written by the remote run and
    populate a :class:`StressResult` with the actual metric samples.

    Lines of interest (from :func:`vm_stress.reporting.save_report`)::

        CPU   avg  : 6.26%
        RAM   avg  : 5513.0 MB
        Net   TX    avg : 23.9810 MB/s  (191.848 Mbps)
        Net   RX    avg : 23.9742 MB/s  (191.794 Mbps)
        5302  6056  6050  ...            (RAM SAMPLES section)
        3.219  44.097  ...              (NET TX SAMPLES section)

    Missing sections simply leave the corresponding sample list empty.
    """
    import re

    now = datetime.now()
    result = StressResult(
        hostname=cfg.remote_host or "remote",
        config=cfg,
        start_time=t_start or now,
        end_time=t_end or now,
    )

    if not report_txt.strip():
        logger.warning("Remote report was empty — metrics will show as zero.")
        return result

    # ── Section tracking ──────────────────────────────────────────────────────
    # After we hit a "── XYZ SAMPLES" heading the *next* non-empty, non-heading
    # line contains the space-separated floats for that metric.
    _SECTION_CPU      = "cpu_samples"
    _SECTION_RAM      = "ram_samples"
    _SECTION_DISK_W   = "disk_write_samples"
    _SECTION_NET_TX   = "net_tx_samples"
    _SECTION_NET_RX   = "net_rx_samples"

    current_section: Optional[str] = None

    for raw_line in report_txt.splitlines():
        line = raw_line.strip()
        if not line:
            current_section = None
            continue

        # ── Section headings ──────────────────────────────────────────────────
        low = line.lower()
        if "cpu samples" in low:
            current_section = _SECTION_CPU
            continue
        if "ram samples" in low:
            current_section = _SECTION_RAM
            continue
        if "disk write samples" in low:
            current_section = _SECTION_DISK_W
            continue
        if "net tx samples" in low:
            current_section = _SECTION_NET_TX
            continue
        if "net rx samples" in low:
            current_section = _SECTION_NET_RX
            continue
        # Any other heading-like lines (start with ──) reset the section
        if line.startswith("──") or line.startswith("=="):
            current_section = None
            continue

        # ── Summary scalar lines ──────────────────────────────────────────────
        # These are used as a fallback when sample lines are absent.
        m_cpu_avg = re.match(r"CPU\s+avg\s*:\s+([\d.]+)%", line, re.I)
        if m_cpu_avg and not result.cpu_samples:
            result.cpu_samples = [float(m_cpu_avg.group(1))]

        m_ram_avg = re.match(r"RAM\s+avg\s*:\s+([\d.]+)\s+MB", line, re.I)
        if m_ram_avg and not result.ram_used_mb_samples:
            result.ram_used_mb_samples = [float(m_ram_avg.group(1))]

        m_net_tx = re.match(
            r"Net\s+TX\s+avg\s*:\s+([\d.]+)\s+MB/s", line, re.I
        )
        if m_net_tx and not result.net_sent_mb_samples:
            result.net_sent_mb_samples = [float(m_net_tx.group(1))]

        m_net_rx = re.match(
            r"Net\s+RX\s+avg\s*:\s+([\d.]+)\s+MB/s", line, re.I
        )
        if m_net_rx and not result.net_recv_mb_samples:
            result.net_recv_mb_samples = [float(m_net_rx.group(1))]

        m_disk_w = re.match(
            r"Disk\s+write\s+avg\s*:\s+([\d.]+)\s+MB/s", line, re.I
        )
        if m_disk_w and not result.disk_write_mb_samples:
            result.disk_write_mb_samples = [float(m_disk_w.group(1))]

        # ── Sample data lines ─────────────────────────────────────────────────
        if current_section:
            floats = []
            for tok in line.split():
                try:
                    floats.append(float(tok))
                except ValueError:
                    pass
            if floats:
                if current_section == _SECTION_CPU:
                    result.cpu_samples = floats
                elif current_section == _SECTION_RAM:
                    result.ram_used_mb_samples = floats
                elif current_section == _SECTION_DISK_W:
                    result.disk_write_mb_samples = floats
                elif current_section == _SECTION_NET_TX:
                    result.net_sent_mb_samples = floats
                elif current_section == _SECTION_NET_RX:
                    result.net_recv_mb_samples = floats
                current_section = None   # consumed; wait for next heading

    logger.debug(
        "Parsed remote report — RAM samples=%d  NetTX samples=%d",
        len(result.ram_used_mb_samples),
        len(result.net_sent_mb_samples),
    )
    return result
