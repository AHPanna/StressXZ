# StressXZ — Linux VM Stress Testing Tool

> **Author:** Panna ABDUL HAKIM — [PNAX.io LAB](https://pnax.io) — <panna@pnax.io>  
> **License:** MIT &nbsp;|&nbsp; **Version:** 1.3.0 &nbsp;|&nbsp; **Python:** 3.10+

A **production-ready Python tool** for stress-testing Linux virtual machines.  
Test CPU, RAM, Disk I/O, and Network individually or together, with precise resource limits, multithreading, remote SSH execution (including bastion/jump host support), and structured report output.

---

## Features

| Feature | Details |
|---|---|
| **CPU stress** | Duty-cycle busy-loop — target any % of a core |
| **RAM stress** | Allocates & holds committed physical pages |
| **Disk I/O stress** | Sequential write → fsync → read cycles, 10-level intensity |
| **Network stress** | Loopback TCP echo at a configurable Mbps target |
| **Resource limits** | Never saturates the system unless you ask it to |
| **Multithreading** | Configurable worker threads per resource |
| **Remote SSH** | Run on any Linux VM — direct or via bastion/jump host |
| **Separate bastion key** | Different SSH private keys for bastion and target VM |
| **Sudo support** | Password piped securely via `sudo -S` (inline or file) |
| **Live progress bar** | Unicode block-character countdown in the terminal |
| **Dry-run mode** | Preview the planned config without running anything |
| **Structured reports** | Auto-saved `.txt` with VM name + test type in filename |
| **Logging levels** | INFO / DEBUG / WARNING / ERROR via `--log-level` |

---

## Project Structure

```
LinuxStressTest/
├── main.py                   ← Entry point (run this)
├── requirements.txt          ← Python dependencies
├── .gitignore                ← Ignores results/*.txt and __pycache__
├── LICENSE                   ← MIT Licence
├── CHANGELOG.md              ← Version history
├── README.md                 ← This file
├── results/                  ← Auto-created; all reports saved here
│   └── .gitkeep              ← Keeps the folder tracked by Git
└── vm_stress/                ← Package
    ├── __init__.py           ← Public API re-exports
    ├── config.py             ← StressConfig & StressResult dataclasses
    ├── logging_setup.py      ← Centralised logging configuration
    ├── executor.py           ← LocalContext / SSHContext abstraction
    ├── workers.py            ← cpu_worker, ram_worker, disk_worker, network_worker
    ├── metrics.py            ← Background psutil metrics sampler
    ├── tester.py             ← StressTester orchestrator
    ├── remote.py             ← SSH upload & remote execution helper
    ├── reporting.py          ← Console summary & .txt file reporter
    └── cli.py                ← argparse CLI + main() entry point
```

### Module Responsibilities

```
cli.py          Parse args → build StressConfig → dispatch → report
  │
  ├── tester.py         Orchestrate local stress run
  │     ├── workers.py  CPU / RAM / Disk / Network worker threads
  │     └── metrics.py  Background psutil sampler thread
  │
  ├── remote.py         Package & run tool on remote VM via SSH
  │     └── executor.py SSHContext (paramiko) / LocalContext abstraction
  │
  ├── reporting.py      Console + file report output
  └── config.py         StressConfig / StressResult data classes
```

---

## Requirements

| Requirement | Version |
|---|---|
| Python | 3.10+ |
| psutil | ≥ 5.9 (metrics collection) |
| paramiko | ≥ 3.0 (remote SSH — optional) |

Install dependencies:

```bash
pip install -r requirements.txt

# Or install only what you need:
pip install psutil          # local testing only
pip install psutil paramiko # local + remote
```

---

## Quick Start

```bash
# Clone / enter the project
cd LinuxStressTest

# Install dependencies
pip install -r requirements.txt

# CPU stress at 50% — 2 threads — 30 seconds
python3 main.py --cpu

# See all options
python3 main.py --help
```

---

## Usage & Examples

### CPU Stress

```bash
# 2 threads × 50% — 30 seconds (defaults)
python3 main.py --cpu

# 4 threads × 30% — 60 seconds
python3 main.py --cpu --cpu-limit 30 --threads 4 --duration 60

# Full core load (100%)
python3 main.py --cpu --cpu-limit 100 --threads $(nproc)
```

### RAM Stress

```bash
# Allocate 256 MB (default)
python3 main.py --ram

# Allocate 2 GB across 4 threads
python3 main.py --ram --ram-limit 2048 --threads 4 --duration 60
```

### Disk I/O Stress

```bash
# Default intensity (5/10), write to /tmp
python3 main.py --disk

# Heavy I/O — intensity 9, custom directory
python3 main.py --disk --disk-intensity 9 --disk-temp-dir /data/scratch --threads 4

# Light background I/O — intensity 2
python3 main.py --disk --disk-intensity 2 --duration 120
```

### Network Stress

```bash
# 10 Mbps loopback traffic (default)
python3 main.py --network

# 100 Mbps across 4 threads
python3 main.py --network --network-limit 100 --threads 4
```

### Combined / Full Stress Test

```bash
# All four resources — 2 minutes
# Report auto-saved to results/stress_report_local_<timestamp>.txt
python3 main.py \
  --cpu  --cpu-limit 80 \
  --ram  --ram-limit 1024 \
  --disk --disk-intensity 7 \
  --network --network-limit 50 \
  --threads 4 --duration 120

# Override the output path
python3 main.py --cpu --ram --duration 60 \
  --output /mnt/nas/results/my_test.txt
```

### Dry-Run (Preview Only)

```bash
python3 main.py --cpu --ram --disk --network --dry-run
```

Output:
```
════════════════════════════════════════════════════════════
  DRY RUN — Planned stress-test configuration
════════════════════════════════════════════════════════════
  CPU      : 2 thread(s) × 50.0%
  RAM      : 256 MB total (2 thread(s))
  Disk I/O : intensity 5/10, 2 thread(s), dir=/tmp
  Network  : 10.00 Mbps total (2 thread(s))
  Duration : 30s
  Remote   : local
════════════════════════════════════════════════════════════
```

---

## Remote Execution (SSH)

The tool uploads itself to the remote VM and runs there — no pre-installation needed on the target.

### Direct SSH

```bash
python3 main.py --cpu --ram --duration 60 \
  --remote-host 10.0.1.20 \
  --remote-user ubuntu \
  --ssh-key ~/.ssh/id_ed25519
```

### Via Bastion / Jump Host

```bash
python3 main.py --cpu --disk --duration 60 \
  --remote-host 192.168.10.5 \
  --remote-user ec2-user \
  --ssh-key ~/.ssh/id_rsa \
  --bastion-host jump.corp.example.com \
  --bastion-user bastion-user \
  --bastion-port 22
```

### Bastion with a Separate SSH Key

Use `--bastion-key` when the bastion host requires a **different private key** from the target VM:

```bash
python3 main.py --cpu --ram --duration 60 \
  --remote-host 10.0.0.5   --remote-user ubuntu  --ssh-key ~/.ssh/id_vm_ed25519 \
  --bastion-host jump.corp.example.com \
  --bastion-user bastion-user --bastion-key ~/.ssh/id_bastion_rsa
```

> If `--bastion-key` is omitted the bastion connection falls back to `--ssh-key`.

### Sudo Password (non-root remote user)

When the remote user is not root and some operations need elevated privileges,
provide a sudo password. It is **piped via stdin** to `sudo -S` — it never
appears in the process argument list or remote shell history.

**Option A — inline** *(quick, but visible in local shell history)*:

```bash
python3 main.py --cpu --disk --duration 60 \
  --remote-host 10.0.0.5 --remote-user ubuntu --ssh-key ~/.ssh/id_rsa \
  --sudo-password 'MyS3cr3t!'
```

**Option B — password file** *(recommended for scripts and CI/CD)*:

```bash
# Create the file (chmod 600 to restrict access)
echo 'MyS3cr3t!' > ~/.ssh/sudo_pass
chmod 600 ~/.ssh/sudo_pass

python3 main.py --cpu --disk --duration 60 \
  --remote-host 10.0.0.5 --remote-user ubuntu --ssh-key ~/.ssh/id_rsa \
  --sudo-password-file ~/.ssh/sudo_pass
```

**Full example — bastion + separate key + sudo:**

```bash
python3 main.py --cpu --ram --disk --network --duration 120 \
  --remote-host 10.0.0.5   --remote-user ubuntu  --ssh-key ~/.ssh/id_vm_rsa \
  --bastion-host jump.corp.example.com \
  --bastion-user bastion-user --bastion-key ~/.ssh/id_bastion_rsa \
  --sudo-password-file ~/.ssh/sudo_pass
```

---

## CLI Reference

```
python3 main.py [OPTIONS]

Resource Selection:
  --cpu                Enable CPU stress test
  --ram                Enable RAM stress test
  --disk               Enable Disk I/O stress test
  --network            Enable Network stress test (loopback TCP)

Resource Limits:
  --cpu-limit PCT      CPU % per worker thread               (default: 50)
  --ram-limit MB       Total RAM to allocate in MB           (default: 256)
  --disk-intensity N   Disk I/O intensity 1–10               (default: 5)
  --network-limit MBPS Loopback bandwidth target in Mbps     (default: 10)

Run Parameters:
  --duration, -d SEC   Test duration in seconds              (default: 30)
  --threads, -t N      Worker threads per resource           (default: 2)
  --disk-temp-dir DIR  Temp dir for disk scratch files        (default: /tmp)

Remote Execution (SSH):
  --remote-host HOST   Target VM IP / hostname
  --remote-user USER   SSH user on target                    (default: root)
  --remote-port PORT   SSH port on target                    (default: 22)
  --ssh-key PATH       Private key file for target VM (auto-detect if omitted)
  --bastion-host HOST  Bastion / jump host
  --bastion-user USER  SSH user on bastion
  --bastion-port PORT  SSH port on bastion                   (default: 22)
  --bastion-key PATH   Private key for bastion (falls back to --ssh-key)

Privilege Escalation (Remote Sudo):
  --sudo-password PASS          Sudo password, piped to 'sudo -S' on remote
  --sudo-password-file PATH     File whose first line is the sudo password

Output & Misc:
  --output, -o FILE    Report file path (auto-named if omitted)
  --dry-run            Preview config without running
  --log-level LEVEL    DEBUG | INFO | WARNING | ERROR        (default: INFO)
```

---

## Report Output

Every run automatically saves a structured `.txt` report inside the **`results/`** folder:

``` 
results/
├── stress_report_myvm_cpu_ram_20260414_120000.txt
├── stress_report_myvm_disk_20260414_130512.txt
└── stress_report_10_0_1_20_cpu_ram_disk_network_20260414_160000.txt   ← remote run
```

File naming: `stress_report_<vm-name>_<test-types>_<YYYYMMDD_HHMMSS>.txt`
- `<vm-name>` is the remote hostname, or the local machine hostname for local runs
- `<test-types>` lists all active tests separated by `_` (e.g. `cpu_ram`, `disk_network`)

Use `--output <path>` to write the report to any custom location instead.

### Report Contents

| Section | Details |
|---|---|
| **Test Parameters** | Resources enabled, limits, threads, remote host |
| **Run Timeline** | ISO-8601 start / end / elapsed seconds |
| **Resource Summary** | Avg & max for CPU %, RAM MB, Disk MB/s, Net MB/s |
| **Time-Series Samples** | Per-second values (up to 60 per metric) |
| **Errors** | Any worker-level errors captured during the run |

### Example report snippet

```
======================================================================
VM STRESS TEST REPORT
Generated  : 2026-04-13T16:06:36
Hostname   : my-vm
======================================================================

── TEST PARAMETERS ──────────────────────────────────────────────────
  Resources  : CPU, RAM
  Duration   : 60s
  Threads    : 4
  CPU limit  : 50.0%
  RAM limit  : 512.0 MB

── RESOURCE SUMMARY ─────────────────────────────────────────────────
  CPU   avg  : 47.30%
  CPU   max  : 52.10%
  RAM   avg  : 528.4 MB
  RAM   max  : 531.2 MB
======================================================================
```

### Git behaviour

The `results/` folder is tracked by Git (via `results/.gitkeep`) but  
the actual `*.txt` report files are listed in `.gitignore` so they  
don't pollute your repository history.

---

## Extending the Tool

The modular design makes it easy to add new stress targets:

1. **New worker** → add a function to `vm_stress/workers.py`
2. **Wire it up** → launch it in `vm_stress/tester.py::_launch_workers()`
3. **CLI flag** → add `--my-resource` in `vm_stress/cli.py::build_parser()`
4. **Config field** → add the field to `StressConfig` in `vm_stress/config.py`
5. **Report field** → add a sample list to `StressResult` and update `vm_stress/reporting.py`

---

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for the full version history.

| Version | Date | Highlights |
|---------|------|------------|
| **1.3.0** | 2026-04-14 | Separate `--bastion-key`, `--sudo-password` / `--sudo-password-file`, VM+test-type in report filenames |
| **1.2.0** | 2026-04-13 | `results/` folder, `.gitignore`, author metadata, MIT licence |
| **1.1.0** | 2026-04-13 | Multi-module package refactor, improved remote SSH upload |
| **1.0.0** | 2026-04-13 | Initial release — single-file production script |

---

## Author

| | |
|---|---|
| **Name** | Panna ABDUL HAKIM |
| **Email** | [panna@pnax.io](mailto:panna@pnax.io) |
| **Organisation** | [PNAX.io LAB](https://pnax.io) |

---

## License

Copyright © 2026 **Panna ABDUL HAKIM** — [PNAX.io LAB](https://pnax.io)

This project is released under the **MIT License** — see [LICENSE](LICENSE) for the full text.

```
MIT License

Copyright (c) 2026 Panna ABDUL HAKIM — PNAX.io LAB <panna@pnax.io>

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
```
