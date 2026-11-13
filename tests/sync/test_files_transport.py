"""P3-T4: SshTransport command construction (injected runner, no process), LocalTransport against the real gate, H6 wiring."""
import json
import os
from types import SimpleNamespace

import pytest

from sync import connect
from sync.config import PreconditionError, SyncSettings
from sync.files.transport import (GateFailure, GateReply, LocalTransport, SshTransport, clean_code, parse_ls_size)
from tests.sync.gate_helpers import ENV_NAME, Tree, make_tar, sha

SSH = r"C:\Windows\System32\OpenSSH\ssh.exe"
SFTP = r"C:\Windows\System32\OpenSSH\sftp.exe"


class Runner:
    """Records every command and answers from a queue of (code, stdout, stderr)."""

    def __init__(self, *answers):
        self.calls, self.answers = [], list(answers)

    def __call__(self, command, stdin, timeout):
        self.calls.append((list(command), stdin, timeout))
        return self.answers.pop(0) if self.answers else (0, b"", b"")


def ssh(runner):
    return SshTransport(SSH, SFTP, "memes-prod-general-sync", "memes-prod-general-up", runner=runner)


def test_verb_command_uses_full_ssh_path_batchmode_strict_host_keys_and_the_gate_alias():
    r = Runner((0, b'{"environment": "general"}\n', b""))
    ssh(r).handshake()
    command, stdin, timeout = r.calls[0]
    assert command[0] == SSH and command[1] == "-T"
    assert command[-2:] == ["memes-prod-general-sync", "handshake"]  # alias, then the verb as the remote command
    opts = " ".join(command)
    for needed in ("BatchMode=yes", "StrictHostKeyChecking=yes", "ConnectTimeout=", "ServerAliveInterval=", "ClearAllForwardings=yes"):
        assert needed in opts
    assert stdin == b"" and timeout > 0


def test_verbs_pass_stdin_and_validated_batch_ids_only():
    r = Runner((0, b"", b""), (0, b"", b""))
    t = ssh(r)
    t.verify("abc123", b"sums")
    assert r.calls[0][0][-1] == "verify abc123" and r.calls[0][1] == b"sums"
    t.receive("deadbeef")
    assert r.calls[1][0][-1] == "receive deadbeef"
    for bad in ("a b", "../x", "", "x;rm", "-x", "a\nb"):
        with pytest.raises(GateFailure):
            t.commit(bad)
    assert len(r.calls) == 2  # nothing was run for the bad ids


def test_a_verb_outside_the_files_set_is_refused_before_a_process_starts():
    r = Runner()
    with pytest.raises(GateFailure):
        ssh(r).verb_command("retire x")
    assert not r.calls


def test_ssh_exit_255_is_a_gate_failure_but_gate_exit_codes_are_replies():
    with pytest.raises(GateFailure) as err:
        ssh(Runner((255, b"", b"Permission denied (publickey)."))).list()
    assert err.value.code == "ssh_failed" and isinstance(err.value, PreconditionError)
    reply = ssh(Runner((1, b"", b"gate: batch_exists: abc\n"))).receive("abc")
    assert (reply.code, reply.reason, reply.detail, reply.ok) == (1, "batch_exists", "abc", False)


def test_handshake_parses_the_json_line_and_reports_gate_refusals_as_codes():
    info = {"environment": "general", "version": "1", "devices": {}, "free": {}, "caps": {}, "probe": {}}
    assert ssh(Runner((0, json.dumps(info).encode() + b"\n", b""))).handshake() == info
    with pytest.raises(GateFailure) as err:
        ssh(Runner((3, b"", b"gate: device_mismatch: work dir and images are on different filesystems\n"))).handshake()
    assert err.value.code == "device_mismatch"
    with pytest.raises(GateFailure) as err:
        ssh(Runner((0, b"not json\n", b""))).handshake()
    assert err.value.code == "handshake_not_json"


def test_upload_is_sftp_put_to_part_then_size_check_then_rename(tmp_path):
    local = tmp_path / "a dir" / "b1.tar"
    local.parent.mkdir()
    local.write_bytes(b"x" * 1234)
    ls = b"sftp> put ...\nsftp> ls -l /incoming/b1.tar.part\n-rw-------    1 1001     1001         1234 Nov 13 12:00 /incoming/b1.tar.part\n"
    r = Runner((0, ls, b""), (0, b"", b""))
    ssh(r).upload("b1", local)
    (put_cmd, put_in, _), (ren_cmd, ren_in, _) = r.calls
    assert put_cmd[0] == SFTP and put_cmd[-3:] == ["-b", "-", "memes-prod-general-up"] and "BatchMode=yes" in put_cmd
    script = put_in.decode()
    assert script.splitlines()[0].startswith('put "') and "a dir/b1.tar" in script and script.splitlines()[0].endswith(" /incoming/b1.tar.part")
    assert script.splitlines()[1] == "ls -l /incoming/b1.tar.part"
    assert ren_in.decode().splitlines() == ["-rm /incoming/b1.tar", "rename /incoming/b1.tar.part /incoming/b1.tar"]


def test_upload_size_mismatch_stops_before_the_rename(tmp_path):
    local = tmp_path / "b2.tar"
    local.write_bytes(b"x" * 100)
    ls = b"-rw-------    1 1001     1001           50 Nov 13 12:00 /incoming/b2.tar.part\n"
    r = Runner((0, ls, b""))
    with pytest.raises(GateFailure) as err:
        ssh(r).upload("b2", local)
    assert err.value.code == "upload_size_mismatch" and len(r.calls) == 1
    with pytest.raises(GateFailure) as err:
        ssh(Runner((1, b"", b"Connection closed"))).upload("b2", local)
    assert err.value.code == "upload_failed"


def test_parse_ls_size_and_clean_code():
    assert parse_ls_size("sftp> ls\n-rw-r--r-- 1 u g 777 Jan  1 00:00 x.tar.part\n", "x.tar.part") == 777
    assert parse_ls_size("nothing here", "x.tar.part") is None
    assert clean_code("Device Mismatch!") == "device_mismatch" and clean_code("") == "failed"


def test_from_settings_reads_the_tracked_keys():
    s = SyncSettings("general", {"ssh_bin": SSH, "sftp_bin": SFTP, "prod": {"gate_alias": "g-sync", "upload_alias": "g-up"}})
    t = SshTransport.from_settings(s, runner=Runner())
    assert (t.ssh_bin, t.sftp_bin, t.gate_alias, t.upload_alias) == (SSH, SFTP, "g-sync", "g-up")
    with pytest.raises(PreconditionError):
        SshTransport.from_settings(SyncSettings("general", {"ssh_bin": SSH, "sftp_bin": SFTP, "prod": {}}))


# ---------------------------------------------------------------------------------------------------------------------------------
# LocalTransport against the real gate

@pytest.fixture
def tree(tmp_path):
    return Tree(tmp_path, free_floor_pct="0")


def local(tree, **kw):
    return LocalTransport(ENV_NAME, tree.conf_path, tree.incoming, **kw)


def test_local_handshake_has_the_fields_the_gate_prints(tree):
    info = local(tree).handshake()
    assert info["environment"] == ENV_NAME and info["version"]
    for key in ("devices", "free", "caps", "probe"):
        assert key in info


def test_local_full_verb_cycle_through_upload(tree, tmp_path):
    members = {"one.jpg": b"1" * 10, "two.jpg": b"2" * 20}
    tar = tmp_path / "t.tar"
    tar.write_bytes(make_tar(members))
    t = local(tree)
    t.upload("b" * 32, tar)
    assert (tree.incoming / ("b" * 32 + ".tar")).exists() and not list(tree.incoming.glob("*.part"))
    assert t.receive("b" * 32).ok
    assert t.verify("b" * 32, tree.verify_input(members)).ok
    reply = t.commit("b" * 32)
    assert reply.ok and reply.json()["committed"] == 2
    assert (tree.base / "one.jpg").read_bytes() == members["one.jpg"]
    assert t.receive("b" * 32).reason == "no_such_upload"
    assert b"one.jpg" in t.list()


def test_local_subprocess_mode_runs_the_same_gate(tree):
    assert local(tree, subprocess_mode=True).handshake()["environment"] == ENV_NAME


def test_local_gate_refusal_is_a_gate_failure_with_a_code(tree):
    with pytest.raises(GateFailure) as err:
        LocalTransport("it", tree.conf_path, tree.incoming).handshake()  # no [it] section in this gate.conf
    assert err.value.code == "unknown_environment"


def test_before_verb_hook_simulates_a_kill(tree):
    class Killed(BaseException):
        pass

    def hook(line):
        if line == "list":
            raise Killed

    with pytest.raises(Killed):
        local(tree, before_verb=hook).list()


# ---------------------------------------------------------------------------------------------------------------------------------
# H6 wiring (sync.connect.handshake step for tool F)

class FakeDev(connect.SyncConnection):
    def __init__(self):
        super().__init__(None, connect.DEV_RO, None, None, "general")

    def query(self, sql, args=None):
        if sql.startswith("select current_database()"):
            return [("devdb",)]
        if "current_setting" in sql:
            return [(None,)]
        raise AssertionError(sql)


def handshake_f(transport):
    settings = SyncSettings("general", {"dev": {"database": "devdb"}})
    return connect.handshake("general", "F", settings=settings, dev=FakeDev(), transport=transport, fail_fast=False)


def test_h6_with_the_local_transport(tree):
    results = handshake_f(local(tree))
    assert [r["step"] for r in results] == ["H1", "H6"]
    assert all(r["ok"] for r in results), results


def test_h6_reports_a_gate_failure_as_a_code_not_an_exception(tree):
    results = handshake_f(LocalTransport("it", tree.conf_path, tree.incoming))
    assert results[-1] == {"step": "H6", "ok": False, "detail": "gate:unknown_environment"}


def test_h6_fails_on_a_gate_that_says_another_environment():
    results = handshake_f(SimpleNamespace(handshake=lambda: {"environment": "metal"}))
    assert results[-1]["detail"] == "gate_environment_mismatch"
