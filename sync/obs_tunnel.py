"""The ssh tunnel to the prod Prometheus, for the workstation's Grafana (observability design, section 5.4).

    python -m sync.obs_tunnel [--env ENV]    start:  ssh -N -L 19090:127.0.0.1:9090 memes-prod-obs   (left running in the background)
    python -m sync.obs_tunnel --stop         stop it again

The alias `memes-prod-obs` lives in the workstation's ssh config (restricted user, a key allowed to forward to 127.0.0.1:9090 only);
this module only names the alias, so no credential, key path or host name is ever in a command line or in the repository.
`--env` only picks which settings overlay supplies `sync.ssh_bin` (the full path of the ssh binary); it is not a data environment.
The pid of the background ssh is kept in Storage/sync-state/obs/obs-tunnel.pid (git-ignored). The sync tool does not manage this tunnel.
Standard library plus sync.config only.
"""
from __future__ import annotations

import argparse
import contextlib
import os
import signal
import socket
import subprocess
import sys
from pathlib import Path
from typing import Callable

from sync import config

LOOPBACK = "127.0.0.1"
LOCAL_PORT = 19090
REMOTE_TARGET = "127.0.0.1:9090"
SSH_ALIAS = "memes-prod-obs"
PID_FILE_NAME = "obs-tunnel.pid"


class ObsTunnelError(config.PreconditionError):
    """The tunnel could not be started or stopped (exit 2)."""


def build_command(ssh_bin: str, local_port: int = LOCAL_PORT, remote: str = REMOTE_TARGET, alias: str = SSH_ALIAS,
                  keepalive_interval: int = 15, keepalive_count_max: int = 3) -> list[str]:
    if not ssh_bin or not alias.strip():
        raise ObsTunnelError("the obs tunnel needs the ssh binary path and an ssh alias")
    return [
        ssh_bin, "-N", "-T",
        "-o", "BatchMode=yes",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "StrictHostKeyChecking=yes",
        "-o", f"ServerAliveInterval={keepalive_interval}",
        "-o", f"ServerAliveCountMax={keepalive_count_max}",
        "-L", f"{LOOPBACK}:{local_port}:{remote}",
        alias,
    ]


def pid_file(root: Path | None = None) -> Path:
    return (Path(root) if root is not None else config.REPO_ROOT) / "Storage" / "sync-state" / "obs" / PID_FILE_NAME


def read_pid(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return int(text) if text.isdigit() and int(text) > 0 else None


def port_accepts(port: int) -> bool:
    try:
        with socket.create_connection((LOOPBACK, port), timeout=0.5):
            return True
    except OSError:
        return False


def process_alive(pid: int) -> bool:
    """Whether a process with this pid exists (os.kill(pid, 0) would terminate it on Windows, so ask the OS instead)."""
    if os.name == "nt":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def spawn(command: list[str]) -> int:
    """Starts ssh detached from this process and returns its pid."""
    flags = {}
    if os.name == "nt":
        flags["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        flags["start_new_session"] = True
    proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **flags)
    return proc.pid


def terminate(pid: int) -> None:
    os.kill(pid, signal.SIGTERM)


def start(command: list[str], pids: Path, *, launcher: Callable[[list[str]], int] = spawn,
          alive: Callable[[int], bool] = process_alive, accepts: Callable[[int], bool] = port_accepts,
          local_port: int = LOCAL_PORT) -> str:
    """Starts the tunnel unless one is running. Returns a one-line status."""
    old = read_pid(pids)
    if old is not None and alive(old):
        return f"the obs tunnel is already running (pid {old}) on {LOOPBACK}:{local_port}"
    if old is not None or pids.exists():
        with contextlib.suppress(OSError):
            pids.unlink()  # stale
    if accepts(local_port):
        raise ObsTunnelError(f"local port {local_port} is already in use; refusing to reuse an unknown listener")
    try:
        pid = launcher(command)
    except OSError as exc:
        raise ObsTunnelError(f"cannot start ssh: {type(exc).__name__}") from None
    pids.parent.mkdir(parents=True, exist_ok=True)
    config.atomic_write(pids, f"{pid}\n")
    return f"started the obs tunnel (pid {pid}) on {LOOPBACK}:{local_port}; stop it with --stop"


def stop(pids: Path, *, alive: Callable[[int], bool] = process_alive, killer: Callable[[int], None] = terminate,
         accepts: Callable[[int], bool] = port_accepts, local_port: int = LOCAL_PORT) -> str:
    """Stops the tunnel recorded in the pid file. A pid whose tunnel port no longer accepts connections is treated as stale and never
    killed (the pid may have been reused by an unrelated process)."""
    pid = read_pid(pids)
    if pid is None:
        with contextlib.suppress(OSError):
            pids.unlink()
        return "no obs tunnel is recorded"
    message = f"the recorded pid {pid} is not running; removed the pid file"
    if alive(pid) and accepts(local_port):
        try:
            killer(pid)
        except OSError as exc:
            raise ObsTunnelError(f"cannot stop pid {pid}: {type(exc).__name__}") from None
        message = f"stopped the obs tunnel (pid {pid})"
    with contextlib.suppress(OSError):
        pids.unlink()
    return message


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sync.obs_tunnel", description=__doc__.split("\n\n")[0])
    parser.add_argument("--env", default="general", choices=config.ENVIRONMENTS,
                        help="settings overlay that supplies sync.ssh_bin (default general)")
    parser.add_argument("--stop", action="store_true", help="stop the running tunnel")
    args = parser.parse_args(argv)
    try:
        pids = pid_file()
        if args.stop:
            print(stop(pids))
            return 0
        settings = config.load_settings(args.env)
        print(start(build_command(str(settings.require("ssh_bin"))), pids))
        return 0
    except config.SyncError as exc:
        print(f"obs tunnel: {exc}", file=sys.stderr)
        return config.exit_code_for(exc)


if __name__ == "__main__":
    sys.exit(main())
