# Changelog

All notable changes to **vm_stress** are documented here.  
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).  
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [1.2.0] — 2026-04-13

### Added
- `results/` folder: all auto-named reports are now saved there instead of the working directory
- `results/.gitkeep` so the folder is tracked by Git
- `.gitignore` covering `results/*.txt` and Python cache files
- Author metadata, MIT licence, and this changelog

### Changed
- `vm_stress/cli.py`: `RESULTS_DIR` constant; `main()` creates the folder on first run
- `--output` help text updated to mention `results/` as the default destination
- README: project tree, Report Output section, and Combined example updated

---

## [1.1.0] — 2026-04-13

### Added
- Multi-module package layout (`vm_stress/` package)
  - `config.py`       — `StressConfig` and `StressResult` dataclasses
  - `logging_setup.py`— centralised logging configuration
  - `executor.py`     — `LocalContext` / `SSHContext` / `build_context()` factory
  - `workers.py`      — `cpu_worker`, `ram_worker`, `disk_worker`, `network_worker`
  - `metrics.py`      — background `collect_metrics()` thread
  - `tester.py`       — `StressTester` orchestrator with live progress bar
  - `remote.py`       — tar.gz upload + remote SSH execution helper
  - `reporting.py`    — `print_summary()` and `save_report()`
  - `cli.py`          — `build_parser()`, `args_to_config()`, `main()`
- `main.py` thin entry-point shim
- `requirements.txt`
- Full `README.md` with CLI reference, examples, architecture diagram

### Changed
- Refactored monolithic `vm_stress_test.py` into the package above
- Remote execution now ships the whole package as a base64 tar.gz (more robust than single-file upload)

### Removed
- `vm_stress_test.py` (superseded by the package)

---

## [1.0.0] — 2026-04-13

### Added
- Initial production-ready release as a single-file script (`vm_stress_test.py`)
- CPU stress: duty-cycle busy-loop with configurable % limit per thread
- RAM stress: committed `bytearray` allocation, random page touches
- Disk I/O stress: sequential write → `fsync` → read cycles, 10-level intensity
- Network stress: loopback TCP echo at a configurable Mbps target
- Remote SSH execution with optional bastion/jump-host (paramiko)
- `argparse` CLI with full option groups
- `psutil` metrics sampler (CPU, RAM, Disk, Network) at 1 s resolution
- Dry-run mode (`--dry-run`)
- Structured `.txt` report output with time-series samples
- INFO / DEBUG / WARNING / ERROR log levels

---

*Maintained by **Panna ABDUL HAKIM** — [PNAX.io LAB](https://pnax.io) — <panna@pnax.io>*
