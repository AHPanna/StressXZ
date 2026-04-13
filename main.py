#!/usr/bin/env python3
"""
main.py — Entry point for the vm_stress Linux VM Stress Testing Tool.

Author  : Panna ABDUL HAKIM <panna@pnax.io>
Org     : PNAX.io LAB  (https://pnax.io)
License : MIT — see LICENSE
Version : 1.2.0
Date    : 2026-04-13

Run this file directly or install the package and call `vm-stress-test`.

Usage:
    python3 main.py --help
    python3 main.py --cpu --ram --duration 60
"""
from __future__ import annotations

import sys

from vm_stress.cli import main

if __name__ == "__main__":
    sys.exit(main())
