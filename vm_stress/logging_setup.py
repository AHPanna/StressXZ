"""
vm_stress.logging_setup
=======================
Centralised logging configuration used by every other module.

Author  : Panna ABDUL HAKIM <panna@pnax.io>
Org     : PNAX.io LAB  (https://pnax.io)
License : MIT — see LICENSE

All modules in this package obtain their logger via::

    import logging
    logger = logging.getLogger("vm_stress.<module>")

Calling :func:`configure_logging` once from :func:`vm_stress.cli.main`
propagates the chosen level to the entire ``vm_stress`` tree.
"""
from __future__ import annotations

import logging

# ── Shared format constants ───────────────────────────────────────────────────

LOG_FORMAT  = "%(asctime)s [%(levelname)-8s] %(name)s — %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"


def configure_logging(level: str = "INFO") -> logging.Logger:
    """
    Configure the root logger with *level* and return the top-level
    ``vm_stress`` logger.

    This is idempotent — calling it multiple times with the same level
    is harmless.

    Args:
        level: One of ``"DEBUG"``, ``"INFO"``, ``"WARNING"``, ``"ERROR"``.

    Returns:
        The ``vm_stress`` :class:`logging.Logger` instance.
    """
    numeric = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(format=LOG_FORMAT, datefmt=DATE_FORMAT, level=numeric)
    return logging.getLogger("vm_stress")
