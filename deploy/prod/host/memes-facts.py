#!/usr/bin/env python3
"""memes-facts: writes the two facts files the gate verbs `disk` and `containment` print (P5d-T5).

    python3 /opt/memes/memes-facts.py <env> [--facts-dir DIR] [--root /srv/memes] [--docker docker]

Runs as root from the systemd timer memes-facts@<env> (the gate user never gets the docker socket, interfaces.md d). It writes, atomically,
`<facts-dir>/disk.json` and `<facts-dir>/containment.json` (default /var/lib/memes/<env>). Each file is ONE line of compact JSON because the
dev side (sync.verify, `parse_json_reply`) reads the first line of the verb's output.

disk.json         {"generated_at": ..., "mounts": {"<name>": {"path", "free_bytes", "total_bytes", "free_pct"}},
                   "logs": {"backend_bytes", "postgres_bytes", "caddy_bytes"}}                                   (sync.verify A3)
containment.json  {"generated_at": ..., "container": <short id>, "read_only_root", "cap_drop": [..], "no_new_privileges", "user",
                   "base_path_read_only", "published_ports": ["127.0.0.1:1234"], ...extras}                         (sync.verify S8)

When the backend container cannot be inspected the file still says so (`error`) and holds none of the safe values, so S8 fails loudly
instead of showing a stale green. Standard library only, Python 3.9 or newer. Never writes environment variables of the container.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

ENVS = ("metal", "general", "it")
DEFAULT_ROOT = "/srv/memes"
DEFAULT_FACTS = "/var/lib/memes/%s"
DOCKER_LOGS = "/var/lib/docker/containers"
# mount name -> path below /srv/memes/<env>
MOUNTS = (("images", "images"), ("postgres", "pgdata"), ("bug_reports", "bug-reports"), ("backups", "backups"),
          ("staging", "staging/incoming"), ("work", "work"))
USAGE = "usage: memes-facts.py <metal|general|it> [--facts-dir DIR] [--root DIR] [--docker BIN]\n"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def disk_facts(env: str, root: str = DEFAULT_ROOT, statvfs=None, docker_logs: str = DOCKER_LOGS) -> dict:
    """Free space per mount of one environment. `free_pct` is what a non-root process could still use (`f_bavail`); on an XFS directory
    with a project quota that is the quota's free space, so a quota and a plain filesystem read the same way."""
    statvfs = statvfs or os.statvfs  # looked up late: the module also imports (for its tests) on a platform without it
    mounts = {}
    paths = [(name, os.path.join(root, env, sub)) for name, sub in MOUNTS] + [("docker_logs", docker_logs)]
    for name, path in paths:
        try:
            st = statvfs(path)
        except OSError as exc:
            mounts[name] = {"path": path, "error": exc.__class__.__name__}
            continue
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        mounts[name] = {"path": path, "free_bytes": free, "total_bytes": total, "free_pct": round(100.0 * free / total, 2) if total else 0.0}
    return {"generated_at": _now(), "mounts": mounts, "logs": log_facts(env, root)}


def dir_bytes(path: str) -> int:
    """Bytes of the regular files below `path`. A missing or unreadable directory is 0, never an error; a symlink (to a file or a
    directory) is neither followed nor counted, so a planted link cannot make this read or size anything outside the directory."""
    total = 0
    stack = [path]
    while stack:
        try:
            with os.scandir(stack.pop()) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def log_facts(env: str, root: str = DEFAULT_ROOT) -> dict:
    """Size of the three log directories of one environment (observability spec 6; sync.verify A3 warns at 80% of each bound)."""
    return {"backend_bytes": dir_bytes(os.path.join(root, env, "logs", "backend")),
            "postgres_bytes": dir_bytes(os.path.join(root, env, "logs", "postgres")),
            "caddy_bytes": dir_bytes(os.path.join(root, "edge", "logs", "caddy"))}


def _port_list(inspect: dict) -> list:
    """`HostIp:HostPort` of every published port. An empty HostIp is what Docker means by 0.0.0.0 and is written so, which S8 refuses."""
    out = []
    bindings = (inspect.get("HostConfig") or {}).get("PortBindings") or {}
    for _container_port, binds in sorted(bindings.items()):
        for b in binds or []:
            ip = b.get("HostIp") or "0.0.0.0"
            out.append(f"{ip}:{b.get('HostPort', '')}")
    return out


def containment_facts(inspect: dict, env: str, root: str = DEFAULT_ROOT) -> dict:
    """The configuration of the backend container that matters for the S8 check, from one `docker inspect` document."""
    host = inspect.get("HostConfig") or {}
    config = inspect.get("Config") or {}
    base_env = ""
    for item in config.get("Env") or []:  # read only to find BASE_PATH; no other value is kept or written
        if item.startswith("BASE_PATH="):
            base_env = item.split("=", 1)[1]
    images_host = os.path.normpath(os.path.join(root, env, "images"))
    base_mounts = [m for m in inspect.get("Mounts") or []
                   if (base_env and m.get("Destination") == base_env) or (m.get("Source") and os.path.normpath(m["Source"]) == images_host)]
    secopt = [str(s).replace("=", ":").lower() for s in host.get("SecurityOpt") or []]
    return {
        "generated_at": _now(),
        "container": (inspect.get("Id") or "")[:12],
        "read_only_root": host.get("ReadonlyRootfs") is True,
        "cap_drop": [str(c).upper() for c in host.get("CapDrop") or []],
        "no_new_privileges": any(s in ("no-new-privileges", "no-new-privileges:true") for s in secopt),
        "user": str(config.get("User") or ""),
        "base_path_read_only": bool(base_mounts) and all(m.get("RW") is False for m in base_mounts),
        "published_ports": _port_list(inspect),
        "privileged": host.get("Privileged") is True,
        "pids_limit": host.get("PidsLimit"),
        "memory": host.get("Memory"),
        "mounts": [{"destination": m.get("Destination"), "rw": m.get("RW") is True} for m in inspect.get("Mounts") or []],
    }


def write_json_atomic(path: str, doc: dict, mode: int = 0o644) -> None:
    directory = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".facts-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(doc, sort_keys=True, separators=(",", ":")) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def run_docker(docker: str, args: list) -> str:
    return subprocess.run([docker, *args], check=True, capture_output=True, text=True, timeout=30).stdout


def find_backend(env: str, docker: str, runner=run_docker) -> str:
    """Id of the compose service `backend` of project memes-<env>; exactly one."""
    out = runner(docker, ["ps", "-q", "--filter", f"label=com.docker.compose.project=memes-{env}",
                          "--filter", "label=com.docker.compose.service=backend"]).split()
    if len(out) != 1:
        raise LookupError(f"{len(out)} backend containers for memes-{env}")
    return out[0]


def build_containment(env: str, root: str, docker: str, runner=run_docker) -> dict:
    try:
        cid = find_backend(env, docker, runner)
        inspect = json.loads(runner(docker, ["inspect", cid]))[0]
        return containment_facts(inspect, env, root)
    except (LookupError, OSError, ValueError, IndexError, subprocess.SubprocessError) as exc:
        # no safe key is present, so the dev side's S8 reports every one of them
        return {"generated_at": _now(), "error": "backend_container_not_inspectable", "detail": exc.__class__.__name__}


def main(argv: list, runner=run_docker) -> int:
    args = list(argv[1:])
    opts = {"--facts-dir": None, "--root": DEFAULT_ROOT, "--docker": "docker"}
    env = None
    while args:
        a = args.pop(0)
        if a in opts and args:
            opts[a] = args.pop(0)
        elif env is None and a in ENVS:
            env = a
        else:
            sys.stderr.write(USAGE)
            return 2
    if env is None or not re.fullmatch(r"[a-z]+", env):
        sys.stderr.write(USAGE)
        return 2
    facts_dir = opts["--facts-dir"] or DEFAULT_FACTS % env
    os.makedirs(facts_dir, exist_ok=True)
    write_json_atomic(os.path.join(facts_dir, "disk.json"), disk_facts(env, opts["--root"]))
    write_json_atomic(os.path.join(facts_dir, "containment.json"), build_containment(env, opts["--root"], opts["--docker"], runner))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
