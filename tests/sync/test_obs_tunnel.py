"""sync/obs_tunnel.py: command construction and pid-file logic with an injected launcher (ssh is never spawned)."""
import ast
import sys
from pathlib import Path

import pytest

from sync import obs_tunnel as ot

SSH = r"C:\Windows\System32\OpenSSH\ssh.exe"


def test_obs_tunnel_command_has_no_secret_and_uses_alias():
    cmd = ot.build_command(SSH)
    assert cmd[0] == SSH
    assert cmd[-1] == "memes-prod-obs"
    assert cmd[cmd.index("-L") + 1] == "127.0.0.1:19090:127.0.0.1:9090"
    assert "-N" in cmd and "BatchMode=yes" in cmd and "ExitOnForwardFailure=yes" in cmd
    assert any(a.startswith("ServerAliveInterval=") for a in cmd) and any(a.startswith("ServerAliveCountMax=") for a in cmd)
    # no key, user, host name or password option: only the alias identifies the target
    assert "-i" not in cmd and "-l" not in cmd
    assert not any("@" in a or "IdentityFile" in a or "pass" in a.lower() for a in cmd)


def test_command_needs_binary_and_alias():
    with pytest.raises(ot.ObsTunnelError):
        ot.build_command("")
    with pytest.raises(ot.ObsTunnelError):
        ot.build_command(SSH, alias=" ")


def test_pid_file_lives_under_the_ignored_sync_state_dir(tmp_path):
    assert ot.pid_file(tmp_path) == tmp_path / "Storage" / "sync-state" / "obs" / "obs-tunnel.pid"


def test_start_records_pid_and_does_not_start_twice(tmp_path):
    pids = ot.pid_file(tmp_path)
    launched = []
    msg = ot.start(["ssh"], pids, launcher=lambda c: launched.append(c) or 4242, alive=lambda p: True, accepts=lambda p: False)
    assert launched == [["ssh"]] and pids.read_text().strip() == "4242" and "4242" in msg
    msg2 = ot.start(["ssh"], pids, launcher=lambda c: launched.append(c) or 1, alive=lambda p: True, accepts=lambda p: False)
    assert len(launched) == 1 and "already running" in msg2


def test_start_replaces_a_stale_pid_file(tmp_path):
    pids = ot.pid_file(tmp_path)
    pids.parent.mkdir(parents=True)
    pids.write_text("111\n")
    ot.start(["ssh"], pids, launcher=lambda c: 222, alive=lambda p: False, accepts=lambda p: False)
    assert pids.read_text().strip() == "222"


def test_start_refuses_an_occupied_port_and_writes_no_pid(tmp_path):
    pids = ot.pid_file(tmp_path)
    with pytest.raises(ot.ObsTunnelError, match="already in use"):
        ot.start(["ssh"], pids, launcher=lambda c: 1, alive=lambda p: False, accepts=lambda p: True)
    assert not pids.exists()


def test_start_launch_failure_is_a_clean_error(tmp_path):
    def boom(_):
        raise FileNotFoundError("secret path")

    with pytest.raises(ot.ObsTunnelError) as err:
        ot.start(["ssh"], ot.pid_file(tmp_path), launcher=boom, alive=lambda p: False, accepts=lambda p: False)
    assert "secret path" not in str(err.value)


def test_stop_kills_a_live_tunnel_and_removes_the_file(tmp_path):
    pids = ot.pid_file(tmp_path)
    pids.parent.mkdir(parents=True)
    pids.write_text("77\n")
    killed = []
    msg = ot.stop(pids, alive=lambda p: True, killer=killed.append, accepts=lambda p: True)
    assert killed == [77] and not pids.exists() and "stopped" in msg


def test_stop_never_kills_a_stale_or_reused_pid(tmp_path):
    pids = ot.pid_file(tmp_path)
    pids.parent.mkdir(parents=True)
    killed = []
    pids.write_text("77\n")
    ot.stop(pids, alive=lambda p: False, killer=killed.append, accepts=lambda p: True)
    pids.write_text("77\n")
    ot.stop(pids, alive=lambda p: True, killer=killed.append, accepts=lambda p: False)  # alive but the tunnel port is dead: reused pid
    assert killed == [] and not pids.exists()


def test_stop_without_a_pid_file_and_garbage_pid(tmp_path):
    pids = ot.pid_file(tmp_path)
    assert "no obs tunnel" in ot.stop(pids)
    pids.parent.mkdir(parents=True)
    pids.write_text("not a pid")
    assert ot.read_pid(pids) is None
    assert "no obs tunnel" in ot.stop(pids) and not pids.exists()


def test_module_imports_only_the_standard_library_and_sync():
    tree = ast.parse(Path(ot.__file__).read_text(encoding="utf-8"))
    for node in tree.body:
        roots = []
        if isinstance(node, ast.Import):
            roots = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots = [node.module.split(".")[0]]
        for root in roots:
            assert root in sys.stdlib_module_names or root in {"sync", "__future__"}, root
