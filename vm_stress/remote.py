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

    with SSHContext(cfg) as ctx:
        _upload_package(ctx, archive_b64)
        stdout = _execute_remote(ctx, cfg)

    return _parse_remote_output(stdout, cfg)


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


def _execute_remote(ctx: SSHContext, cfg: StressConfig) -> str:
    """Run the stress test on the remote and return its stdout."""
    args = _build_remote_args(cfg)
    cmd  = f"cd {_REMOTE_DIR} && python3 main.py {args}"
    logger.info("Remote command: %s", cmd)

    rc, out, err = ctx.run(cmd)
    if rc not in (0, 1):   # rc=1 is "no resources" warning — still valid
        raise RuntimeError(
            f"Remote execution failed (rc={rc}):\n{err.strip()}"
        )
    if err.strip():
        logger.debug("Remote stderr:\n%s", err.strip())
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


def _parse_remote_output(raw: str, cfg: StressConfig) -> StressResult:
    """
    Build a minimal :class:`StressResult` from the remote stdout.

    The remote process writes its full report to a file; we surface the
    raw stdout here so the local operator can see what happened.  In a
    future iteration this could be replaced with a structured JSON export.
    """
    result = StressResult(
        hostname=cfg.remote_host or "remote",
        config=cfg,
        start_time=datetime.now(),
        end_time=datetime.now(),
    )
    # Surface raw output so it appears in the local report
    preview = raw.strip()[:3000] or "(no output)"
    result.errors.append(f"[remote stdout preview]\n{preview}")
    return result
