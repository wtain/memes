"""Shared harness of the gate tests: a temp directory tree, a gate.conf, in-process and subprocess runs, tar builders."""
import hashlib
import importlib.util
import io
import os
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GATE_PATH = ROOT / "deploy" / "prod" / "memesync-gate.py"
ENV_NAME = "general"


def load_gate():
    spec = importlib.util.spec_from_file_location("memesync_gate_under_test", GATE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = load_gate()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Result:
    def __init__(self, code, out, err):
        self.code, self.out, self.err = code, out, err

    def records(self):
        parts = self.out.split(b"\x00")
        if parts and parts[-1] == b"":
            parts.pop()
        return [p.decode("utf-8").split("\t") for p in parts]

    def json(self):
        import json
        return json.loads(self.out.decode("utf-8").splitlines()[0])


class Tree:
    """One environment on disk: images (base), work, staging/incoming, bug-reports, backups, facts."""

    def __init__(self, root: Path, **conf):
        self.root = root
        for name in ("images", "work", "incoming", "bug", "backups", "facts"):
            (root / name).mkdir()
        self.base, self.work, self.incoming = root / "images", root / "work", root / "incoming"
        self.bug, self.backups, self.facts = root / "bug", root / "backups", root / "facts"
        self.conf_path = root / "gate.conf"
        self.write_conf(**conf)

    def write_conf(self, **conf):
        values = {"base_path": self.base, "work_dir": self.work, "incoming_dir": self.incoming, "bug_reports_dir": self.bug,
                  "backups_dir": self.backups, "facts_dir": self.facts, "probe_require_other_read": "false", "lock_timeout_s": "5"}
        values.update(conf)
        lines = ["[%s]  # a comment after the header" % ENV_NAME] + ["%s = %s   # inline comment" % (k, v) for k, v in values.items()]
        self.conf_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.chmod(self.conf_path, 0o644)

    def environ(self, command):
        return {"SSH_ORIGINAL_COMMAND": command, "MEMESYNC_GATE_CONF": str(self.conf_path), "MEMESYNC_GATE_ALLOW_NO_DIRFD": "1"}

    def run(self, command, stdin=b"", env_name=ENV_NAME):
        out, err = io.BytesIO(), io.StringIO()
        code = gate.main(["memesync-gate.py", env_name], self.environ(command), io.BytesIO(stdin), out, err)
        return Result(code, out.getvalue(), err.getvalue())

    def run_subprocess(self, command, stdin=b"", extra_env=None):
        env = dict(os.environ)
        env.update(self.environ(command))
        env.update(extra_env or {})
        proc = subprocess.run([sys.executable, str(GATE_PATH), ENV_NAME], input=stdin, capture_output=True, env=env, timeout=60)
        return Result(proc.returncode, proc.stdout, proc.stderr.decode("utf-8", "replace"))

    # -- helpers
    def upload(self, bid, data: bytes):
        (self.incoming / (bid + ".tar")).write_bytes(data)

    def receive(self, bid, members):
        self.upload(bid, make_tar(members))
        return self.run("receive " + bid)

    def verify_input(self, members):
        return b"".join(sha(d).encode() + b"\x00" + str(len(d)).encode() + b"\x00" + n.encode("utf-8") + b"\x00" for n, d in members.items())

    def promote(self, bid, members):
        """receive + verify + commit; returns the three results."""
        r = self.receive(bid, members)
        assert r.code == 0, r.err
        v = self.run("verify " + bid, self.verify_input(members))
        assert v.code == 0, v.err
        return self.run("commit " + bid)

    def put(self, name, data=b"x"):
        (self.base / name).write_bytes(data)


def make_tar(members, fmt=tarfile.PAX_FORMAT):
    """members: dict name -> bytes, or a list of (name, data) or prebuilt (TarInfo, bytes|None) pairs."""
    buf = io.BytesIO()
    items = members.items() if isinstance(members, dict) else members
    with tarfile.open(fileobj=buf, mode="w", format=fmt) as tar:
        for name, data in items:
            if isinstance(name, tarfile.TarInfo):
                tar.addfile(name, io.BytesIO(data) if data is not None else None)
            else:
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def special(name, kind, linkname="", fmt=tarfile.PAX_FORMAT, **pax):
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = linkname
    info.pax_headers = pax
    return info


def work_names(tree):
    """Names in the work directory except the lock file (every locked verb creates it)."""
    return sorted(p.name for p in tree.work.iterdir() if p.name != "gate.lock")
