"""
vm_stress.workers
=================
Stress-worker functions, each designed to run inside a :class:`threading.Thread`.

Author  : Panna ABDUL HAKIM <panna@pnax.io>
Org     : PNAX.io LAB  (https://pnax.io)
License : MIT — see LICENSE

Each worker accepts a :class:`threading.Event` (``stop_event``) and exits
cleanly when it is set.  Workers that can encounter non-fatal errors push
descriptive strings into a shared ``error_queue``.

Workers
-------
- :func:`cpu_worker`     — duty-cycle busy-loop targeting a % of one core
- :func:`ram_worker`     — allocates and holds committed physical RAM
- :func:`disk_worker`    — sequential write → fsync → read cycles
- :func:`network_worker` — loopback TCP echo throughput at a target Mbps
"""
from __future__ import annotations

import logging
import math
import os
import queue
import random
import socket
import threading
import time

logger = logging.getLogger("vm_stress.workers")


# ══════════════════════════════════════════════════════════════════════════════
# CPU worker
# ══════════════════════════════════════════════════════════════════════════════

def cpu_worker(cpu_limit_pct: float, stop_event: threading.Event) -> None:
    """
    Busy-loop consuming approximately *cpu_limit_pct* % of **one** CPU core.

    Uses a duty-cycle strategy: within each 100 ms window the thread performs
    arithmetic work for ``limit%`` of that window, then sleeps for the
    remainder.  Run one instance of this function per physical thread you
    want to stress.

    Args:
        cpu_limit_pct: Target CPU percentage (0–100) for a single core.
        stop_event:    Set this event to stop the worker gracefully.
    """
    if cpu_limit_pct <= 0:
        return

    fraction = min(cpu_limit_pct / 100.0, 1.0)
    cycle    = 0.1   # duty-cycle window in seconds

    logger.debug("CPU worker started — target %.1f%%", cpu_limit_pct)
    while not stop_event.is_set():
        t_start = time.perf_counter()

        # ── Active (compute) phase ────────────────────────────────────────────
        while (time.perf_counter() - t_start) < (cycle * fraction):
            _ = math.sqrt(random.random()) * math.pi   # FP work to heat the core

        # ── Sleep (idle) phase ────────────────────────────────────────────────
        sleep_time = cycle * (1.0 - fraction)
        if sleep_time > 0:
            time.sleep(sleep_time)

    logger.debug("CPU worker stopped")


# ══════════════════════════════════════════════════════════════════════════════
# RAM worker
# ══════════════════════════════════════════════════════════════════════════════

def ram_worker(
    ram_limit_mb: float,
    stop_event: threading.Event,
    error_queue: queue.Queue,
) -> None:
    """
    Allocate up to *ram_limit_mb* MB of physical RAM and hold it.

    Uses ``bytearray`` (rather than ``b"\\x00" * N``) and writes random
    bytes at every 4 KB page boundary so the OS actually backs the pages
    with physical memory (no copy-on-write tricks).

    Args:
        ram_limit_mb: Total MB to allocate in this worker.
        stop_event:   Set to stop the worker and release memory.
        error_queue:  Receives :class:`str` error messages on failure.
    """
    target_bytes = int(ram_limit_mb * 1024 * 1024)
    chunk_size   = 64 * 1024 * 1024   # allocate in 64 MB chunks
    buffers: list[bytearray] = []
    allocated = 0

    logger.debug("RAM worker started — target %.1f MB", ram_limit_mb)
    try:
        while allocated < target_bytes and not stop_event.is_set():
            size = min(chunk_size, target_bytes - allocated)
            try:
                buf = bytearray(size)
                # Touch every page so the kernel actually maps them
                for i in range(0, size, 4096):
                    buf[i] = random.randint(0, 255)
                buffers.append(buf)
                allocated += size
            except MemoryError as exc:
                error_queue.put(f"RAM worker MemoryError: {exc}")
                break

        logger.debug("RAM worker holding %.1f MB", allocated / 1024 / 1024)

        # Hold allocation until the test ends
        while not stop_event.is_set():
            time.sleep(0.5)
    finally:
        buffers.clear()
        logger.debug("RAM worker freed memory")


# ══════════════════════════════════════════════════════════════════════════════
# Disk worker
# ══════════════════════════════════════════════════════════════════════════════

def disk_worker(
    intensity: int,
    temp_dir: str,
    stop_event: threading.Event,
    error_queue: queue.Queue,
) -> None:
    """
    Perform sequential write → ``fsync`` → read cycles on a temporary file.

    *intensity* (1–10) controls both the file size and the sleep between
    cycles:

    ============  ===========  ===========
    Intensity      File size    Inter-cycle sleep
    ============  ===========  ===========
    1 (lightest)   1 MB          0.45 s
    5 (default)    16 MB         0.25 s
    10 (heaviest)  512 MB        0 s
    ============  ===========  ===========

    The temporary file is removed on exit even if an error occurs.

    Args:
        intensity:  I/O intensity level (1–10).
        temp_dir:   Directory where the temp file is created.
        stop_event: Set to stop the worker.
        error_queue: Receives error messages.
    """
    intensity = max(1, min(10, intensity))
    file_mb   = int(2 ** (intensity - 1))           # 1 … 512 MB
    sleep_s   = max(0.0, (10 - intensity) * 0.05)   # 0.45 … 0 s

    # Keep chunk ≤ 4 MB to avoid huge single allocations
    chunk   = os.urandom(min(file_mb * 1024 * 1024, 4 * 1024 * 1024))
    repeats = max(1, (file_mb * 1024 * 1024) // len(chunk))

    tmp_path = os.path.join(
        temp_dir,
        f"stress_disk_{os.getpid()}_{threading.get_ident()}.tmp",
    )

    logger.debug(
        "Disk worker started — intensity=%d, ~%d MB file, sleep=%.2fs",
        intensity, file_mb, sleep_s,
    )
    try:
        while not stop_event.is_set():
            # ── Write phase ───────────────────────────────────────────────────
            try:
                with open(tmp_path, "wb", buffering=0) as fh:
                    for _ in range(repeats):
                        if stop_event.is_set():
                            break
                        fh.write(chunk)
                    fh.flush()
                    os.fsync(fh.fileno())
            except OSError as exc:
                error_queue.put(f"Disk write error: {exc}")
                break

            if stop_event.is_set():
                break

            # ── Read phase ────────────────────────────────────────────────────
            try:
                with open(tmp_path, "rb", buffering=0) as fh:
                    while fh.read(4 * 1024 * 1024):
                        if stop_event.is_set():
                            break
            except OSError as exc:
                error_queue.put(f"Disk read error: {exc}")
                break

            if sleep_s > 0:
                time.sleep(sleep_s)
    finally:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        logger.debug("Disk worker cleaned up %s", tmp_path)


# ══════════════════════════════════════════════════════════════════════════════
# Network worker
# ══════════════════════════════════════════════════════════════════════════════

def network_worker(
    limit_mbps: float,
    stop_event: threading.Event,
    error_queue: queue.Queue,
) -> None:
    """
    Generate loopback TCP traffic at approximately *limit_mbps* Mbps.

    Internally the function:

    1. Binds a TCP echo server on a random loopback port.
    2. Connects a client to that server.
    3. Sends 64 KB payloads in a tight loop, throttled to the target rate.

    This approach exercises the kernel network stack (and therefore shows up
    in :func:`psutil.net_io_counters`) without requiring any external server
    or internet access.

    Args:
        limit_mbps:  Target bandwidth in Megabits per second per worker.
        stop_event:  Set to stop the worker.
        error_queue: Receives error messages.
    """
    target_bps   = (limit_mbps * 1_000_000) / 8   # bytes per second
    payload_size = 65536                            # 64 KB per send/recv
    payload      = bytes(random.getrandbits(8) for _ in range(payload_size))

    # ── Echo server setup ─────────────────────────────────────────────────────
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind(("127.0.0.1", 0))
    port = server_sock.getsockname()[1]
    server_sock.listen(5)
    server_sock.settimeout(1.0)

    echo_stop = threading.Event()

    def _echo_server() -> None:
        """Accept connections and spawn a handler thread for each."""
        while not echo_stop.is_set():
            try:
                conn, _ = server_sock.accept()
                conn.settimeout(1.0)
                threading.Thread(
                    target=_handle_echo, args=(conn,), daemon=True
                ).start()
            except socket.timeout:
                continue
            except OSError:
                break

    def _handle_echo(conn: socket.socket) -> None:
        """Reflect all received bytes back to the sender."""
        try:
            while not echo_stop.is_set():
                data = conn.recv(payload_size)
                if not data:
                    break
                conn.sendall(data)
        except (OSError, socket.timeout):
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    threading.Thread(target=_echo_server, daemon=True).start()

    # ── Client loop ───────────────────────────────────────────────────────────
    logger.debug(
        "Network worker started — target %.2f Mbps on loopback port %d",
        limit_mbps, port,
    )
    client: Optional[socket.socket] = None  # type: ignore[name-defined]
    try:
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.connect(("127.0.0.1", port))
        client.settimeout(2.0)

        while not stop_event.is_set():
            t0   = time.perf_counter()
            sent = 0

            while sent < target_bps and not stop_event.is_set():
                try:
                    client.sendall(payload)
                    _ = client.recv(payload_size)
                    sent += payload_size
                except (socket.timeout, OSError) as exc:
                    error_queue.put(f"Network socket error: {exc}")
                    stop_event.set()
                    break

            # Rate-limit: wait out the remainder of each 1-second window
            elapsed = time.perf_counter() - t0
            if elapsed < 1.0 and not stop_event.is_set():
                time.sleep(1.0 - elapsed)

    except OSError as exc:
        error_queue.put(f"Network connection error: {exc}")
    finally:
        echo_stop.set()
        if client:
            try:
                client.close()
            except OSError:
                pass
        server_sock.close()
        logger.debug("Network worker stopped")
