"""
vm_stress.executor
==================
Execution-context abstraction that cleanly separates *local* command

Author  : Panna ABDUL HAKIM <panna@pnax.io>
Org     : PNAX.io LAB  (https://pnax.io)
License : MIT — see LICENSE
execution from *remote SSH* command execution (with optional bastion /
jump-host support).

Public API
----------
- :class:`ExecutionContext`  — abstract base class
- :class:`LocalContext`      — subprocess-based local execution
- :class:`SSHContext`        — paramiko-based remote execution
- :func:`build_context`      — factory that returns the right implementation
"""
from __future__ import annotations

import logging
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from vm_stress.config import StressConfig

logger = logging.getLogger("vm_stress.executor")

# ── Optional dependency: paramiko ─────────────────────────────────────────

SUDO_TIMEOUT = 30   # seconds granted for sudo authentication

def _resolve_sudo_password(cfg: "StressConfig") -> Optional[str]:
    """
    Return the plaintext sudo password from *cfg*, or ``None`` if not set.

    Resolution order:
    1. ``cfg.sudo_password``       — inline value
    2. ``cfg.sudo_password_file``  — first line of a local file (trimmed)
    """
    if cfg.sudo_password:
        return cfg.sudo_password
    if cfg.sudo_password_file:
        p = Path(cfg.sudo_password_file).expanduser()
        return p.read_text(encoding="utf-8").splitlines()[0].strip()
    return None
try:
    import paramiko
    PARAMIKO_AVAILABLE = True
except ImportError:
    PARAMIKO_AVAILABLE = False
    paramiko = None  # type: ignore[assignment]


# ══════════════════════════════════════════════════════════════════════════════
# Abstract base
# ══════════════════════════════════════════════════════════════════════════════

class ExecutionContext(ABC):
    """
    Abstract execution context.

    Provides a uniform :meth:`run` interface so that the rest of the tool
    does not care whether it is targeting the local machine or a remote VM.

    Supports the context-manager protocol for automatic cleanup::

        with build_context(cfg) as ctx:
            rc, out, err = ctx.run("uname -a")
    """

    @abstractmethod
    def run(self, command: str) -> tuple[int, str, str]:
        """
        Execute *command* in a shell.

        Returns:
            A ``(returncode, stdout, stderr)`` tuple.
        """
        ...

    @abstractmethod
    def close(self) -> None:
        """Release any underlying connections or resources."""
        ...

    def __enter__(self) -> ExecutionContext:
        return self

    def __exit__(self, *_) -> None:
        self.close()


# ══════════════════════════════════════════════════════════════════════════════
# Local execution
# ══════════════════════════════════════════════════════════════════════════════

class LocalContext(ExecutionContext):
    """Runs shell commands on the **local** machine via :mod:`subprocess`."""

    def run(self, command: str) -> tuple[int, str, str]:
        logger.debug("LOCAL exec: %s", command)
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True
        )
        return result.returncode, result.stdout, result.stderr

    def close(self) -> None:
        pass   # Nothing to release for local execution


# ══════════════════════════════════════════════════════════════════════════════
# SSH / Remote execution
# ══════════════════════════════════════════════════════════════════════════════

class SSHContext(ExecutionContext):
    """
    Runs commands on a **remote** Linux VM via paramiko.

    Supports an optional bastion (jump host): when ``cfg.bastion_host`` is
    set the first SSH connection goes to the bastion, and a ``direct-tcpip``
    channel is then forwarded to the actual target host.

    Authentication order:
    1. Explicit ``cfg.ssh_key`` file (auto-detects RSA / Ed25519 / ECDSA / DSS)
    2. SSH agent (when no key file is given)
    3. Default key-file search (``~/.ssh/id_*``)
    """

    def __init__(self, cfg: StressConfig) -> None:
        if not PARAMIKO_AVAILABLE:
            raise RuntimeError(
                "paramiko is required for remote execution.\n"
                "Install it with:  pip install paramiko"
            )
        self._cfg = cfg
        self._sudo_pass = _resolve_sudo_password(cfg)
        if self._sudo_pass:
            logger.info(
                "sudo password provided — privileged commands will use 'sudo -S'"
            )
        self._client = paramiko.SSHClient()  # type: ignore[union-attr]
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self._bastion_client = None   # type: Optional[paramiko.SSHClient]
        self._connect()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _load_key(self, path: Optional[str]):
        """
        Try each paramiko key type in turn and return the first that
        successfully loads *path*.  Returns ``None`` if *path* is ``None``.
        """
        if not path:
            return None
        key_path = Path(path).expanduser()
        for cls in (
            paramiko.RSAKey,
            paramiko.Ed25519Key,
            paramiko.ECDSAKey,
            paramiko.DSSKey,
        ):
            try:
                return cls.from_private_key_file(str(key_path))
            except paramiko.ssh_exception.SSHException:
                continue
        raise ValueError(f"Could not load SSH key: {key_path}")

    def _connect(self) -> None:  # noqa: C901
        """Open SSH connection, optionally tunnelled through a bastion host."""
        cfg = self._cfg
        # Key for the target remote VM
        key = self._load_key(cfg.ssh_key)
        # Key for the bastion host — falls back to the remote key if not set
        bastion_key = self._load_key(cfg.bastion_key) if cfg.bastion_key else key

        connect_kwargs: dict = dict(
            username=cfg.remote_user,
            pkey=key,
            look_for_keys=(key is None),
            allow_agent=(key is None),
            timeout=15,
        )

        if cfg.bastion_host:
            logger.info(
                "Connecting via bastion %s → %s",
                cfg.bastion_host, cfg.remote_host,
            )
            self._bastion_client = paramiko.SSHClient()  # type: ignore[union-attr]
            self._bastion_client.set_missing_host_key_policy(
                paramiko.AutoAddPolicy()
            )
            self._bastion_client.connect(
                cfg.bastion_host,
                port=cfg.bastion_port,
                username=cfg.bastion_user or cfg.remote_user,
                pkey=bastion_key,
                look_for_keys=(bastion_key is None),
                allow_agent=(bastion_key is None),
                timeout=15,
            )
            bastion_transport = self._bastion_client.get_transport()
            dest_addr = (cfg.remote_host, cfg.remote_port)
            src_addr  = (cfg.bastion_host, cfg.bastion_port)
            channel = bastion_transport.open_channel(
                "direct-tcpip", dest_addr, src_addr
            )
            self._client.connect(
                cfg.remote_host,
                port=cfg.remote_port,
                sock=channel,
                **connect_kwargs,
            )
        else:
            logger.info("Connecting directly to %s", cfg.remote_host)
            self._client.connect(
                cfg.remote_host,
                port=cfg.remote_port,
                **connect_kwargs,
            )

        logger.info("SSH connection established to %s", cfg.remote_host)

    # ── Public interface ──────────────────────────────────────────────────────

    def run(self, command: str) -> tuple[int, str, str]:
        logger.debug("SSH exec on %s: %s", self._cfg.remote_host, command)
        _stdin, stdout, stderr = self._client.exec_command(command, timeout=300)
        exit_code = stdout.channel.recv_exit_status()
        return exit_code, stdout.read().decode(), stderr.read().decode()

    def sudo_run(self, command: str) -> tuple[int, str, str]:
        """
        Run *command* with ``sudo -S`` on the remote host.

        The sudo password is piped via stdin so it never appears in the
        process argument list or shell history.

        If no sudo password was configured the command is run as:
        ``sudo <command>`` (i.e. the remote user must already have
        passwordless sudo or be root).

        Args:
            command: Shell command to execute with elevated privileges.

        Returns:
            ``(returncode, stdout, stderr)`` tuple.
        """
        if not self._sudo_pass:
            # No password — assume passwordless sudo / root
            return self.run(f"sudo {command}")

        sudo_cmd = f"sudo -S -p '' {command}"
        logger.debug("SSH sudo exec on %s: %s", self._cfg.remote_host, command)
        _stdin, stdout, stderr = self._client.exec_command(
            sudo_cmd, timeout=300
        )
        _stdin.write(self._sudo_pass + "\n")
        _stdin.flush()
        _stdin.channel.shutdown_write()
        exit_code = stdout.channel.recv_exit_status()
        return exit_code, stdout.read().decode(), stderr.read().decode()

    def close(self) -> None:
        for client in (self._client, self._bastion_client):
            if client:
                try:
                    client.close()
                except Exception:
                    pass


# ══════════════════════════════════════════════════════════════════════════════
# Factory
# ══════════════════════════════════════════════════════════════════════════════

def build_context(cfg: StressConfig) -> ExecutionContext:
    """
    Return the appropriate :class:`ExecutionContext` for *cfg*.

    - If ``cfg.remote_host`` is set → :class:`SSHContext`
    - Otherwise                     → :class:`LocalContext`
    """
    if cfg.remote_host:
        return SSHContext(cfg)
    return LocalContext()
