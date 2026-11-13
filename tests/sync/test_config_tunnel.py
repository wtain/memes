"""sync/config.py and sync/tunnel.py: settings, secrets, state dir, lock, tunnel with a fake ssh (loopback only, no database)."""
import ast
import datetime as dt
import multiprocessing
import os
import re
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from sync import config, tunnel

FAKE = str(Path(__file__).with_name("fake_ssh.py"))
PKG = Path(config.__file__).parent


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ------------------------------------------------------------------------------------------------ imports

def test_config_and_tunnel_import_only_the_standard_library_at_module_level():
    for name in ("config.py", "tunnel.py"):
        tree = ast.parse((PKG / name).read_text(encoding="utf-8"))
        for node in tree.body:  # module level only; dynaconf and dotenv are imported inside functions on purpose
            roots = []
            if isinstance(node, ast.Import):
                roots = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots = [node.module.split(".")[0]]
            for root in roots:
                assert root in sys.stdlib_module_names or root in {"sync", "__future__"}, (name, root)


# ------------------------------------------------------------------------------------------------ settings

@pytest.mark.parametrize("env,lp,pp", [("metal", 16432, 6432), ("general", 16434, 6434), ("it", 16436, 6436)])
def test_tracked_settings_per_environment(env, lp, pp):
    s = config.load_settings(env)
    assert s.get("prod.database") == f"memes_{env}"
    assert s.get("prod.writer_role") == f"memes_{env}_sync_w"
    assert s.get("prod.reader_role") == f"memes_{env}_sync_r"
    assert s.get("prod.ssh_alias") == f"memes-prod-{env}"
    assert s.get("prod.gate_alias") == f"memes-prod-{env}-sync"
    assert s.get("prod.upload_alias") == f"memes-prod-{env}-up"
    assert (s.get("prod.local_port"), s.get("prod.pg_port"), s.get("prod.pg_host")) == (lp, pp, "127.0.0.1")
    assert s.get("ssh_bin").endswith("ssh.exe")
    assert s.state_dir == config.REPO_ROOT / "Storage" / "sync-state" / env


def test_dev_database_placeholder_fails_loudly():
    s = config.load_settings("general")
    with pytest.raises(config.ConfigError, match=r"sync\.dev\.database.*not set"):
        s.require("dev.database")


def test_environment_variables_do_not_override_tracked_values(monkeypatch):
    monkeypatch.setenv("SYNC", "x")
    monkeypatch.setenv("sync", "x")
    assert config.load_settings("it").get("prod.database") == "memes_it"


def test_unknown_environment():
    with pytest.raises(config.ConfigError):
        config.load_settings("prod")


# ------------------------------------------------------------------------------------------------ secrets

def make_repo(tmp_path, content="SYNC_WRITER_PASSWORD=w\nSYNC_READER_PASSWORD=r\n"):
    (tmp_path / "environments").mkdir()
    if content is not None:
        (tmp_path / "environments" / ".env.general.sync").write_text(content)
    return tmp_path


def test_secrets_read_into_dict_not_environ(tmp_path, monkeypatch):
    monkeypatch.delenv("SYNC_WRITER_PASSWORD", raising=False)
    monkeypatch.setattr(config, "_git_tracks", lambda p, c: False)
    got = config.load_secrets("general", make_repo(tmp_path))
    assert got == {"SYNC_WRITER_PASSWORD": "w", "SYNC_READER_PASSWORD": "r"}
    assert "SYNC_WRITER_PASSWORD" not in os.environ


def test_secrets_missing_file(tmp_path):
    with pytest.raises(config.ConfigError, match="not found"):
        config.load_secrets("general", make_repo(tmp_path, None))


def test_secrets_refuse_git_tracked_file(tmp_path):
    root = make_repo(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-f", "environments/.env.general.sync"], cwd=root, check=True)
    with pytest.raises(config.ConfigError, match="tracked by git"):
        config.load_secrets("general", root)


def test_untracked_file_in_git_repo_is_accepted(tmp_path):
    root = make_repo(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    assert config.load_secrets("general", root)["SYNC_READER_PASSWORD"] == "r"


def test_ambient_prod_variables_are_ignored_with_a_warning_naming_them(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_git_tracks", lambda p, c: False)
    monkeypatch.setenv("PROD_DB_PASSWORD", "topsecret")
    with pytest.warns(UserWarning, match="PROD_DB_PASSWORD") as rec:
        got = config.load_secrets("general", make_repo(tmp_path))
    assert "topsecret" not in "".join(str(w.message) for w in rec)
    assert "PROD_DB_PASSWORD" not in got


def test_warns_when_other_accounts_can_read(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_git_tracks", lambda p, c: False)
    monkeypatch.setattr(config, "other_accounts_can_read", lambda p: ["BUILTIN\\Users"])
    with pytest.warns(UserWarning, match="readable by other accounts"):
        config.load_secrets("general", make_repo(tmp_path))


def test_acl_parser_flags_other_principals(monkeypatch):
    out = ("X:\\a\\.env.general.sync NT AUTHORITY\\SYSTEM:(I)(F)\n"
           "                        BUILTIN\\Administrators:(I)(F)\n"
           "                        BUILTIN\\Users:(I)(RX)\n")
    monkeypatch.setattr(config.os, "name", "nt")
    monkeypatch.setattr(config.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=out))
    assert config.other_accounts_can_read(Path("X:/a/.env.general.sync")) == ["BUILTIN\\Users"]


# ------------------------------------------------------------------------------------- state, run id, writes

def test_run_id_format_and_uniqueness():
    now = dt.datetime(2026, 11, 13, 1, 2, 3, tzinfo=dt.timezone.utc)
    rid = config.new_run_id(now)
    assert re.fullmatch(r"20261113T010203Z-[0-9a-f]{8}", rid)
    assert config.new_run_id(now) != rid


def test_state_dir_and_run_dir_under_root(tmp_path):
    s = config.SyncSettings("it", {"state_dir": "Storage/sync-state/{env}"}, tmp_path)
    assert config.state_dir(s, create=True) == tmp_path / "Storage" / "sync-state" / "it"
    folder = config.run_dir(s, "20261113T010203Z-abcd1234")
    assert folder.is_dir() and folder.parent.name == "runs"


def test_atomic_write_replaces_and_leaves_no_temp(tmp_path):
    target = tmp_path / "sub" / "f.json"
    config.atomic_write(target, '{"a": 1}')
    config.atomic_write(target, b'{"a": 2}')
    assert target.read_bytes() == b'{"a": 2}'
    assert [p.name for p in target.parent.iterdir()] == ["f.json"]


def test_atomic_write_failure_keeps_old_file(tmp_path, monkeypatch):
    target = tmp_path / "f"
    config.atomic_write(target, "old")

    def boom(*a):
        raise OSError("boom")

    monkeypatch.setattr(config.os, "replace", boom)
    with pytest.raises(OSError):
        config.atomic_write(target, "new")
    assert target.read_text() == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["f"]


def test_dry_marker(tmp_path):
    folder = tmp_path / "runs" / "x"
    assert not config.is_dry(folder)
    config.mark_dry(folder)
    assert config.is_dry(folder) and (folder / "dry").read_bytes() == b""


# ------------------------------------------------------------------------------------------------- lock

def _hold(path, ready, release):
    with config.exclusive_lock(Path(path)):
        ready.set()
        release.wait(20)


def test_lock_second_holder_exits_2_and_release_frees_it(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    ready, release = ctx.Event(), ctx.Event()
    proc = ctx.Process(target=_hold, args=(str(tmp_path), ready, release))
    proc.start()
    try:
        assert ready.wait(60)
        with pytest.raises(config.LockHeldError, match="another sync command holds the lock") as info:
            with config.exclusive_lock(tmp_path):
                pass
        assert info.value.exit_code == config.EXIT_PRECONDITION == 2
    finally:
        release.set()
        proc.join(20)
    with config.exclusive_lock(tmp_path):
        pass


def test_exit_codes_and_hierarchy():
    assert (config.EXIT_OK, config.EXIT_FAILURE, config.EXIT_PRECONDITION, config.EXIT_GUARD, config.EXIT_VERIFY) == (0, 1, 2, 3, 4)
    assert config.exit_code_for(config.GuardError("x")) == 3
    assert config.exit_code_for(config.VerificationError("x")) == 4
    assert config.exit_code_for(config.ConfigError("x")) == 2
    assert config.exit_code_for(config.LockHeldError("x")) == 2
    assert config.exit_code_for(RuntimeError("x")) == 1
    with pytest.raises(SystemExit) as e:
        config.die(config.GuardError("x"))
    assert e.value.code == 3


# ------------------------------------------------------------------------------------------------ tunnel

def test_command_shape():
    cmd = tunnel.build_command("C:/Windows/System32/OpenSSH/ssh.exe", 16434, "127.0.0.1", 6434, "memes-prod-general")
    assert cmd[0].endswith("ssh.exe") and cmd[1:3] == ["-N", "-T"]
    for opt in ("BatchMode=yes", "ExitOnForwardFailure=yes", "StrictHostKeyChecking=yes", "ServerAliveInterval=15", "ServerAliveCountMax=3"):
        assert opt in cmd
    assert cmd[cmd.index("-L") + 1] == "127.0.0.1:16434:127.0.0.1:6434"
    assert cmd[-1] == "memes-prod-general"
    assert "localhost" not in " ".join(cmd)
    with pytest.raises(tunnel.TunnelError):
        tunnel.build_command("ssh", 1, "localhost", 2, "a")


def test_from_settings_uses_tracked_values():
    t = tunnel.Tunnel.from_settings(config.load_settings("metal"))
    assert t.local_port == 16432
    assert t.command[t.command.index("-L") + 1] == "127.0.0.1:16432:127.0.0.1:6432"
    assert t.command[-1] == "memes-prod-metal"


def fake_command(mode, port):
    return [sys.executable, FAKE, mode] + tunnel.build_command("ssh", port, "127.0.0.1", 5432, "alias")[1:]


def test_tunnel_up_and_torn_down():
    port = free_port()
    with tunnel.Tunnel(fake_command("ok", port), port, timeout_s=30) as t:
        assert t.alive()
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            pass
        proc = t._proc
    assert proc.poll() is not None
    assert t._stderr_path is None
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=1)


def test_torn_down_when_body_raises():
    port = free_port()
    holder = {}
    with pytest.raises(RuntimeError):
        with tunnel.Tunnel(fake_command("ok", port), port, timeout_s=30) as t:
            holder["p"] = t._proc
            raise RuntimeError("body")
    assert holder["p"].poll() is not None


def test_ssh_failure_surfaces_stderr():
    port = free_port()
    with pytest.raises(tunnel.TunnelError, match=r"code 255.*Permission denied") as info:
        tunnel.Tunnel(fake_command("fail", port), port, timeout_s=30).open()
    assert info.value.exit_code == 2


def test_timeout_kills_a_hanging_ssh():
    port = free_port()
    t = tunnel.Tunnel(fake_command("hang", port), port, timeout_s=1)
    with pytest.raises(tunnel.TunnelError, match="not accepting connections"):
        t.open()
    assert t._proc is None


def test_refuses_a_port_already_listening():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        with pytest.raises(tunnel.TunnelError, match="already in use"):
            tunnel.Tunnel(fake_command("ok", port), port, timeout_s=1).open()


def test_missing_binary():
    port = free_port()
    with pytest.raises(tunnel.TunnelError, match="cannot start ssh"):
        tunnel.Tunnel(["X:/nope/ssh.exe", "-N"], port, timeout_s=1).open()
