"""The ssh tunnel to the prod Postgres (interfaces.md c): `ssh -N -T -L 127.0.0.1:<local_port>:<pg_host>:<pg_port> <ssh_alias>`.

Standard library only. ssh stderr goes to a temp file that is shown only when the tunnel fails (ssh prints no secrets;
it is still capped and never logged on success).
"""
from __future__ import annotations

import atexit
import contextlib
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Iterator

from sync.config import PreconditionError, SyncSettings

LOOPBACK = "127.0.0.1"
_STDERR_CAP = 2000


class TunnelError(PreconditionError):
    """The tunnel could not be opened (exit 2)."""


def build_command(ssh_bin: str, local_port: int, pg_host: str, pg_port: int, ssh_alias: str,
                  keepalive_interval: int = 15, keepalive_count_max: int = 3) -> list[str]:
    if pg_host.lower() == "localhost" or ssh_alias.strip() == "":
        raise TunnelError("the tunnel needs an explicit address (never 'localhost') and an ssh alias")
    return [
        ssh_bin, "-N", "-T",
        "-o", "BatchMode=yes",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "StrictHostKeyChecking=yes",
        "-o", f"ServerAliveInterval={keepalive_interval}",
        "-o", f"ServerAliveCountMax={keepalive_count_max}",
        "-L", f"{LOOPBACK}:{local_port}:{pg_host}:{pg_port}",
        ssh_alias,
    ]


def _port_accepts(port: int) -> bool:
    try:
        with socket.create_connection((LOOPBACK, port), timeout=0.5):
            return True
    except OSError:
        return False


class Tunnel:
    def __init__(self, command: list[str], local_port: int, timeout_s: float = 20.0):
        self.command = command
        self.local_port = local_port
        self.timeout_s = timeout_s
        self._proc: subprocess.Popen | None = None
        self._stderr_path: str | None = None
        self._stderr_file = None
        self._registered = False

    @classmethod
    def from_settings(cls, settings: SyncSettings, timeout_s: float | None = None) -> "Tunnel":
        ssh_bin = str(settings.require("ssh_bin"))
        local_port = int(settings.require("prod.local_port"))
        command = build_command(
            ssh_bin, local_port,
            str(settings.require("prod.pg_host")), int(settings.require("prod.pg_port")),
            str(settings.require("prod.ssh_alias")),
            int(settings.get("tunnel.keepalive_interval_s", 15)), int(settings.get("tunnel.keepalive_count_max", 3)),
        )
        return cls(command, local_port, float(timeout_s if timeout_s is not None else settings.get("tunnel.connect_timeout_s", 20)))

    def _stderr_text(self) -> str:
        if not self._stderr_path:
            return ""
        with contextlib.suppress(OSError):
            return Path(self._stderr_path).read_text(encoding="utf-8", errors="replace").strip()[:_STDERR_CAP]
        return ""

    def open(self) -> "Tunnel":
        if self._proc is not None:
            return self
        if _port_accepts(self.local_port):
            raise TunnelError(f"local port {self.local_port} is already in use; refusing to reuse an unknown listener")
        fd, self._stderr_path = tempfile.mkstemp(prefix="sync-ssh-", suffix=".err")
        self._stderr_file = os.fdopen(fd, "wb")
        try:
            self._proc = subprocess.Popen(
                self.command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=self._stderr_file,
                **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
            )
        except OSError as exc:
            self._cleanup_files()
            raise TunnelError(f"cannot start ssh: {type(exc).__name__}") from None
        if not self._registered:
            atexit.register(self.close)
            self._registered = True
        deadline = time.monotonic() + self.timeout_s
        while time.monotonic() < deadline:
            code = self._proc.poll()
            if code is not None:
                detail = self._stderr_text()
                self.close()
                raise TunnelError(f"ssh exited with code {code} before the tunnel was up" + (f": {detail}" if detail else ""))
            if _port_accepts(self.local_port):
                return self
            time.sleep(0.05)
        detail = self._stderr_text()
        self.close()
        raise TunnelError(f"tunnel not accepting connections on {LOOPBACK}:{self.local_port} after {self.timeout_s:g}s"
                          + (f": {detail}" if detail else ""))

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _cleanup_files(self) -> None:
        if self._stderr_file is not None:
            with contextlib.suppress(OSError):
                self._stderr_file.close()
            self._stderr_file = None
        if self._stderr_path:
            with contextlib.suppress(OSError):
                os.unlink(self._stderr_path)
            self._stderr_path = None

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    proc.wait(timeout=5)
        self._cleanup_files()
        if self._registered:
            with contextlib.suppress(Exception):
                atexit.unregister(self.close)
            self._registered = False

    def __enter__(self) -> "Tunnel":
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()


@contextlib.contextmanager
def open_tunnel(settings: SyncSettings, timeout_s: float | None = None) -> Iterator[Tunnel]:
    tunnel = Tunnel.from_settings(settings, timeout_s)
    try:
        yield tunnel.open()
    finally:
        tunnel.close()
