"""Settings, secrets, state directory, run id, atomic writes and the lock of the sync tools (interfaces.md a, b, i).

Standard library only at module level. The two third-party readers (Dynaconf for the tracked `sync.*` settings,
python-dotenv for `environments/.env.<env>.sync`) are imported inside the functions that need them, so importing
this module for the constants, exceptions, run ids or the lock needs nothing outside the standard library.
Secrets are returned as a dict and never put into os.environ.
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import os
import secrets
import subprocess
import sys
import time
import warnings
from pathlib import Path
from typing import Any, Iterator, Mapping

ENVIRONMENTS = ("metal", "general", "it")
REPO_ROOT = Path(__file__).resolve().parents[1]

# Exit codes of every sync tool (interfaces.md i).
EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_PRECONDITION = 2  # handshake or precondition failed, including a held lock
EXIT_GUARD = 3  # a guard tripped before any write
EXIT_VERIFY = 4  # verification found differences

LOCK_MESSAGE = "another sync command holds the lock"


class SyncError(Exception):
    """Base of the sync exceptions; `exit_code` is what the command line should exit with. Messages carry no row content or secrets."""

    exit_code = EXIT_FAILURE


class PreconditionError(SyncError):
    exit_code = EXIT_PRECONDITION


class ConfigError(PreconditionError):
    """A missing or unusable tracked setting or secret file."""


class LockHeldError(PreconditionError):
    pass


class GuardError(SyncError):
    exit_code = EXIT_GUARD


class VerificationError(SyncError):
    exit_code = EXIT_VERIFY


# --------------------------------------------------------------------------------------------- tracked settings

def _check_env(env: str) -> str:
    if env not in ENVIRONMENTS:
        raise ConfigError(f"unknown environment {env!r}; expected one of {', '.join(ENVIRONMENTS)}")
    return env


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k).lower(): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def load_sync_settings(env: str, root: Path | None = None) -> dict:
    """The `sync` group of settings.yaml merged with settings.<env>.yaml, as a plain dict with lower-case keys.

    Built from the two files only: the process environment cannot override a value ("no env-var fallback"), and the
    global `config.settings` object is not touched, so this works whatever APP_ENV the shell has.
    """
    _check_env(env)
    base = Path(root) if root is not None else REPO_ROOT
    files = [base / "environments" / "settings.yaml", base / "environments" / f"settings.{env}.yaml"]
    missing = [str(f) for f in files if not f.exists()]
    if missing:
        raise ConfigError(f"settings file not found: {', '.join(missing)}")
    from dynaconf import Dynaconf

    instance = Dynaconf(
        envvar_prefix="SYNC_SETTINGS_NEVER_SET_IN_ENV",  # a prefix nobody uses: no environment variable overrides a tracked value
        merge_enabled=True,
        load_dotenv=False,
        settings_files=[str(f) for f in files],
    )
    group = instance.get("sync")
    if group is None:
        raise ConfigError("no `sync:` block in the tracked settings")
    return _plain(group)


class SyncSettings:
    """Dotted-path access to the `sync.*` settings of one environment."""

    def __init__(self, env: str, data: dict, root: Path | None = None):
        self.env = _check_env(env)
        self.data = data
        self.root = Path(root) if root is not None else REPO_ROOT

    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def require(self, path: str) -> Any:
        """The value at `path`; a missing, empty or zero placeholder fails loudly (exit 2) and names the key and file."""
        value = self.get(path)
        if value is None or value == "" or value == 0 or value == []:
            raise ConfigError(
                f"tracked setting sync.{path} is not set for environment {self.env!r}; "
                f"fill it in environments/settings.{self.env}.yaml (it is an empty placeholder on purpose)"
            )
        return value

    @property
    def state_dir(self) -> Path:
        return state_dir(self)


def load_settings(env: str, root: Path | None = None) -> SyncSettings:
    return SyncSettings(env, load_sync_settings(env, root), root)


# ------------------------------------------------------------------------------------------------- secrets file

def _git_tracks(path: Path, cwd: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", str(path)],
            cwd=str(cwd), capture_output=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False  # no git available: the .gitignore rule is the remaining guard
    return result.returncode == 0


def other_accounts_can_read(path: Path) -> list[str]:
    """Names of accounts other than the current user, SYSTEM and Administrators that can read `path` (Windows ACL), or
    ['group/other'] for POSIX mode bits. Empty when nothing was found or the check is unavailable."""
    if os.name != "nt":
        mode = path.stat().st_mode
        return ["group/other"] if mode & 0o077 else []
    try:
        out = subprocess.run(["icacls", str(path)], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    me = (os.environ.get("USERNAME") or "").lower()
    allowed = ("nt authority\\system", "builtin\\administrators", "nt authority\\system", "creator owner")
    found = []
    for line in out.splitlines():
        line = line.strip()
        if path.name in line:
            line = line.split(path.name, 1)[1].strip()
        if "(" not in line or ":" not in line:
            continue
        principal, rights = line.rsplit(":", 1)[0], line.rsplit(":", 1)[1]
        # "principal:(R)" or "principal:(I)(F)"; deny entries start with (DENY)
        principal = principal.strip()
        low = principal.lower()
        if "(deny)" in rights.lower():
            continue
        if low in allowed or (me and low.endswith("\\" + me)):
            continue
        if any(token in rights for token in ("(F)", "(M)", "(RX)", "(R)", "(GR)", "(GA)")):
            found.append(principal)
    return sorted(set(found))


def ambient_prod_variables() -> list[str]:
    return sorted(k for k in os.environ if k.upper().startswith("PROD_"))


def load_secrets(env: str, root: Path | None = None) -> dict[str, str]:
    """`environments/.env.<env>.sync` as a dict (never os.environ). Refuses a git-tracked file, warns about readability by
    other Windows accounts, and warns (naming the variables, never their values) about ambient PROD_* variables, which are ignored."""
    _check_env(env)
    base = Path(root) if root is not None else REPO_ROOT
    path = base / "environments" / f".env.{env}.sync"
    ambient = ambient_prod_variables()
    if ambient:
        warnings.warn(f"ignoring ambient environment variables {', '.join(ambient)}: sync reads only {path.name}", stacklevel=2)
    if not path.exists():
        raise ConfigError(f"secrets file not found: environments/.env.{env}.sync")
    if _git_tracks(path, base):
        raise ConfigError(f"environments/.env.{env}.sync is tracked by git; refusing to read it")
    readers = other_accounts_can_read(path)
    if readers:
        warnings.warn(f"environments/.env.{env}.sync is readable by other accounts: {', '.join(readers)}", stacklevel=2)
    from dotenv import dotenv_values

    return {k: v for k, v in dotenv_values(path).items() if v is not None}


# ------------------------------------------------------------------------------------------------ state, run id

def state_dir(settings: SyncSettings, create: bool = False) -> Path:
    raw = settings.get("state_dir") or "Storage/sync-state/{env}"
    path = Path(str(raw).replace("{env}", settings.env))
    if not path.is_absolute():
        path = settings.root / path
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def new_run_id(now: _dt.datetime | None = None) -> str:
    """`YYYYMMDDTHHMMSSZ-<8 hex>`."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    if now.tzinfo is not None:
        now = now.astimezone(_dt.timezone.utc)
    return f"{now:%Y%m%dT%H%M%SZ}-{secrets.token_hex(4)}"


def run_dir(settings: SyncSettings, run_id: str, create: bool = True) -> Path:
    path = state_dir(settings) / "runs" / run_id
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


DRY_MARKER = "dry"


def mark_dry(folder: Path) -> Path:
    """Put the empty `dry` marker in a run folder: no consumer accepts a file from it."""
    folder.mkdir(parents=True, exist_ok=True)
    marker = folder / DRY_MARKER
    atomic_write(marker, b"")
    return marker


def is_dry(folder: Path) -> bool:
    return (Path(folder) / DRY_MARKER).exists()


def atomic_write(path: Path, data: bytes | str) -> None:
    """Write temp file in the same directory, fsync, os.replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = data.encode("utf-8") if isinstance(data, str) else data
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    try:
        with open(tmp, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(8):  # Windows: a virus scanner or indexer can hold the target for a moment (PermissionError, WinError 32)
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if os.name != "nt" or attempt == 7:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp)


# --------------------------------------------------------------------------------------------------------- lock

def _try_lock(fd: int) -> bool:
    if os.name == "nt":
        import msvcrt

        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        with contextlib.suppress(OSError):
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)


@contextlib.contextmanager
def exclusive_lock(settings_or_dir: SyncSettings | Path) -> Iterator[Path]:
    """Hold the OS exclusive lock `<state dir>/lock` for the whole command. A second holder gets LockHeldError (exit 2)."""
    directory = state_dir(settings_or_dir, create=True) if isinstance(settings_or_dir, SyncSettings) else Path(settings_or_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if not _try_lock(fd):
            raise LockHeldError(LOCK_MESSAGE)
        try:
            yield path
        finally:
            _unlock(fd)
    finally:
        os.close(fd)


def exit_code_for(exc: BaseException) -> int:
    return exc.exit_code if isinstance(exc, SyncError) else EXIT_FAILURE


def die(exc: SyncError) -> "None":
    """Print the one-line reason to stderr and exit with the code of the exception."""
    print(f"sync: {exc}", file=sys.stderr)
    raise SystemExit(exc.exit_code)
