"""P5d-T5 and P5d-T7 guards that need neither a VPS nor a database: sshd snippet and authorized_keys templates (exact option strings), the
facts writer against what the gate verbs print and sync.verify reads, systemd units, the generated backup table list, script hygiene.

What needs a real sshd or Postgres is in tests/integration/sync/test_host_container.py and test_backup_restore_container.py.
"""
import configparser
import datetime as dt
import importlib.util
import io
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from sync import backup as backup_tables
from sync import registry
from sync.model import Klass
from sync.verify import runtime
from tests.sync.gate_helpers import Tree

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy" / "prod"
ENVS = {"metal": 6432, "general": 6434, "it": 6436}
SYNC_ONLY = (DEPLOY / "host", DEPLOY / "sshd", DEPLOY / "systemd")


def load_facts():
    spec = importlib.util.spec_from_file_location("memes_facts_under_test", DEPLOY / "host" / "memes-facts.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


facts = load_facts()


@pytest.fixture(autouse=True)
def _statvfs_exists(monkeypatch):
    """Windows has no os.statvfs; the writer only ever runs on the VPS. Tests that call main() use a fake that reports a healthy disk."""
    import os

    class St:
        f_frsize, f_blocks, f_bavail = 4096, 1000, 900

    monkeypatch.setattr(os, "statvfs", lambda path: St(), raising=False)


KEY_TYPE, KEY_DATA = "ssh-ed25519", "AAAAC3NzaC1lZDI1NTE5AAAAIPLACEHOLDERPUBLICKEYxxxxxxxxxxxxxxxxxxxxxxxxx"


def template_line(name, env):
    lines = [ln for ln in (DEPLOY / "sshd" / f"authorized_keys.{name}.template").read_text(encoding="utf-8").splitlines()
             if ln.strip() and not ln.startswith("#")]
    assert len(lines) == 1, "one key line per template"
    return (lines[0].replace("@ENV@", env).replace("@PGPORT@", str(ENVS[env]))
            .replace("@KEY_TYPE@", KEY_TYPE).replace("@KEY_DATA@", KEY_DATA))


def split_options(line):
    """(options list, rest) of an authorized_keys line; quotes honoured."""
    opts, cur, quoted, i = [], "", False, 0
    while i < len(line):
        ch = line[i]
        if ch == '"':
            quoted = not quoted
        elif ch == " " and not quoted:
            break
        elif ch == "," and not quoted:
            opts.append(cur)
            cur = ""
            i += 1
            continue
        cur += ch
        i += 1
    opts.append(cur)
    return opts, line[i + 1:]


# ------------------------------------------------------------------------------------------------------------- authorized_keys templates

@pytest.mark.parametrize("env", ENVS)
def test_verb_key_line_is_exactly_restrict_plus_the_gate_command(env):
    opts, rest = split_options(template_line("verb", env))
    assert opts == ["restrict", f'command="/usr/bin/python3 /opt/memes/memesync-gate.py {env}"']
    assert rest.startswith(f"{KEY_TYPE} {KEY_DATA} memesync-{env}-verb")
    assert opts[0] == "restrict"
    # nothing re-enables what restrict closed
    assert not {"pty", "port-forwarding", "agent-forwarding", "X11-forwarding", "user-rc", "permitopen"} & {o.split("=")[0] for o in opts}


@pytest.mark.parametrize("env", ENVS)
def test_tunnel_key_line_forwards_only_to_its_postgres(env):
    opts, rest = split_options(template_line("tunnel", env))
    assert opts == ["restrict", "port-forwarding", f'permitopen="127.0.0.1:{ENVS[env]}"', 'command="/bin/false"']
    assert opts[0] == "restrict"
    assert not any("*" in o or "0.0.0.0" in o for o in opts)
    assert len([o for o in opts if o.startswith("permitopen")]) == 1  # a single target, no list, no wildcard
    assert "pty" not in " ".join(opts)  # restrict implies no-pty and nothing turns it back on
    assert rest.startswith(f"{KEY_TYPE} {KEY_DATA} memesync-{env}-tunnel")


@pytest.mark.parametrize("env", ENVS)
def test_upload_key_line_is_restrict_only(env):
    opts, rest = split_options(template_line("upload", env))
    assert opts == ["restrict"]  # chroot and internal-sftp come from the Match block, not from the key
    assert rest.startswith(f"{KEY_TYPE} {KEY_DATA} memeup-{env}-upload")


def test_templates_hold_only_placeholders():
    for tpl in (DEPLOY / "sshd").glob("authorized_keys.*.template"):
        text = tpl.read_text(encoding="utf-8")
        assert "@KEY_DATA@" in text and "@KEY_TYPE@" in text
        assert not re.search(r"AAAA[A-Za-z0-9+/=]{20,}", text), "a real key body in a template"
        assert "PRIVATE KEY" not in text


# ------------------------------------------------------------------------------------------------------------- sshd drop-in

def parse_sshd(text):
    """(global directives list, {match criteria: [directives]}) from the drop-in."""
    glob, matches, cur = [], {}, None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("Match "):
            cur = matches.setdefault(line[6:].strip(), [])
        else:
            (cur if cur is not None else glob).append(line)
    return glob, matches


def test_sshd_dropin_global_and_match_blocks():
    glob, matches = parse_sshd((DEPLOY / "sshd" / "memes-sync.conf").read_text(encoding="utf-8"))
    assert "PasswordAuthentication no" in glob and "KbdInteractiveAuthentication no" in glob
    assert "PermitRootLogin prohibit-password" in glob
    assert not any(d.split()[0] == "AcceptEnv" for d in glob), "an empty AcceptEnv is a syntax error; the default stays"
    allow = [d for d in glob if d.startswith("AllowUsers")][0].split()[1:]
    assert allow[0] == "@ADMIN_USER@"
    assert sorted(allow[1:]) == sorted([f"{p}-{e}" for e in ENVS for p in ("memesync", "memeup")])
    for env, port in ENVS.items():
        block = matches[f"User memesync-{env}"]
        assert f"PermitOpen 127.0.0.1:{port}" in block
        assert "AllowTcpForwarding local" in block
        for line in ("GatewayPorts no", "PermitTTY no", "AllowAgentForwarding no", "X11Forwarding no", "PermitTunnel no", "PermitUserRC no"):
            assert line in block
        assert sum(1 for d in block if d.startswith("PermitOpen")) == 1
        assert "AuthorizedKeysFile /etc/ssh/memes-keys/%u" in block
    up = matches["User memeup-metal,memeup-general,memeup-it"]
    assert "ChrootDirectory %h" in up and "ForceCommand internal-sftp -u 007" in up and "AllowTcpForwarding no" in up
    assert "PermitTTY no" in up and "X11Forwarding no" in up and "AllowAgentForwarding no" in up
    everything = " ".join(sum(matches.values(), glob))
    assert "PermitOpen *" not in everything and "AllowTcpForwarding yes" not in everything and "AllowTcpForwarding all" not in everything
    assert "PasswordAuthentication yes" not in everything


# ------------------------------------------------------------------------------------------------------------- setup-host.sh table

def have_bash():
    path = shutil.which("bash")
    if not path:
        return None
    probe = subprocess.run([path, "-c", "echo $BASH_VERSION"], capture_output=True, text=True)
    return path if probe.returncode == 0 and probe.stdout.strip() else None


BASH = have_bash()
SUBNETS = {"metal": "172.31.1.0/24", "general": "172.31.2.0/24", "it": "172.31.3.0/24"}


@pytest.mark.skipif(BASH is None, reason="bash not available")
@pytest.mark.parametrize("env", ENVS)
def test_setup_host_environment_table(env):
    out = subprocess.run([BASH, str(DEPLOY / "host" / "setup-host.sh"), env, "--print-config"], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == (f"env={env} pgport={ENVS[env]} subnet={SUBNETS[env]} backend_alias=api-{env} "
                                  f"sync_user=memesync-{env} up_user=memeup-{env} group=memesync-{env}")


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_setup_host_refuses_unknown_environment_and_non_root():
    assert subprocess.run([BASH, str(DEPLOY / "host" / "setup-host.sh"), "prod"], capture_output=True).returncode != 0


def test_setup_host_sets_the_documented_modes_and_network():
    text = (DEPLOY / "host" / "setup-host.sh").read_text(encoding="utf-8")
    for expected in ('mk 2755 "$SYNC_USER" "$GROUP" "$ENV_ROOT/images"', 'mk 2775 "$BACKEND_UID" "$GROUP" "$ENV_ROOT/bug-reports"',
                     'mk 0700 "$SYNC_USER" "$GROUP" "$ENV_ROOT/work"', 'mk 0700 "$SYNC_USER" "$GROUP" "$ENV_ROOT/retired"',
                     'mk 0755 root root "$ENV_ROOT/staging"', 'mk 2770 "$UP_USER" "$GROUP" "$ENV_ROOT/staging/incoming"',
                     'mk 0700 "$PG_UID" "$PG_UID" "$ENV_ROOT/pgdata"', 'mk 0750 "$SYNC_USER" "$GROUP" "$ENV_ROOT/backups"',
                     'mk 0755 "$SYNC_USER" "$GROUP" "$ENV_ROOT/web"',
                     "docker network create --subnet 172.30.0.0/24 memes-edge"):
        assert expected in text, expected
    assert "docker network inspect memes-edge" in text  # created only if absent


# ------------------------------------------------------------------------------------------------------------- facts writer

def inspect_doc(**over):
    doc = {
        "Id": "0123456789abcdef" * 4,
        "Config": {"User": "1000:1001", "Env": ["BASE_PATH=/data/images", "SECRET_TOKEN=must-never-be-copied"]},
        "HostConfig": {"ReadonlyRootfs": True, "CapDrop": ["ALL"], "SecurityOpt": ["no-new-privileges:true"], "PortBindings": {},
                       "Privileged": False, "PidsLimit": 200, "Memory": 512 * 1024 * 1024},
        "Mounts": [{"Type": "bind", "Source": "/srv/memes/it/images", "Destination": "/data/images", "RW": False},
                   {"Type": "bind", "Source": "/srv/memes/it/bug-reports", "Destination": "/data/bug-reports", "RW": True}],
    }
    for key, value in over.items():
        section, _, name = key.partition("__")
        doc[section][name] = value
    return doc


def make_runner(doc, count=1):
    calls = []

    def runner(docker, args):
        calls.append(args)
        if args[0] == "ps":
            return "\n".join(["abc123def456"] * count) + ("\n" if count else "")
        if args[0] == "inspect":
            return json.dumps([doc])
        raise AssertionError(args)

    runner.calls = calls
    return runner


def judge_s8(doc):
    return runtime.check_s8(facts.containment_facts(doc, "it", "/srv/memes"))


def test_good_container_passes_s8_and_secrets_are_not_copied():
    out = facts.containment_facts(inspect_doc(), "it", "/srv/memes")
    assert out["read_only_root"] is True and out["no_new_privileges"] is True and out["base_path_read_only"] is True
    assert out["cap_drop"] == ["ALL"] and out["user"] == "1000:1001" and out["published_ports"] == []
    assert "must-never-be-copied" not in json.dumps(out)
    assert runtime.check_s8(out).status == "ok"


@pytest.mark.parametrize("over,problem", [
    ({"HostConfig__ReadonlyRootfs": False}, "read_only_root_off"),
    ({"HostConfig__CapDrop": []}, "cap_drop_all_missing"),
    ({"HostConfig__CapDrop": ["NET_RAW"]}, "cap_drop_all_missing"),
    ({"HostConfig__SecurityOpt": []}, "no_new_privileges_off"),
    ({"Config__User": ""}, "runs_as_root"),
    ({"Config__User": "root"}, "runs_as_root"),
    ({"HostConfig__PortBindings": {"8000/tcp": [{"HostIp": "", "HostPort": "8000"}]}}, "port_not_loopback"),
    ({"HostConfig__PortBindings": {"8000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8000"}]}}, "port_not_loopback"),
    ({"Mounts": [{"Type": "bind", "Source": "/srv/memes/it/images", "Destination": "/data/images", "RW": True}]}, "base_path_read_only_off"),
    ({"Mounts": []}, "base_path_read_only_off"),
])
def test_each_weakness_is_reported_by_s8(over, problem):
    doc = inspect_doc()
    for key, value in over.items():
        if "__" in key:
            section, _, name = key.partition("__")
            doc[section][name] = value
        else:
            doc[key] = value
    check = judge_s8(doc)
    assert check.status == "error" and problem in check.detail


def test_loopback_published_port_is_accepted():
    doc = inspect_doc(HostConfig__PortBindings={"8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18000"}]})
    assert facts.containment_facts(doc, "it")["published_ports"] == ["127.0.0.1:18000"]
    assert judge_s8(doc).status == "ok"


def test_base_path_mount_found_by_host_path_when_the_env_var_is_missing():
    doc = inspect_doc()
    doc["Config"]["Env"] = []
    assert facts.containment_facts(doc, "it", "/srv/memes")["base_path_read_only"] is True


def test_disk_facts_shape_and_a3():
    class St:
        f_frsize, f_blocks, f_bavail = 4096, 1000, 250

    out = facts.disk_facts("it", "/srv/memes", statvfs=lambda p: St(), docker_logs="/x")
    assert set(out["mounts"]) == {"images", "postgres", "bug_reports", "backups", "staging", "work", "docker_logs"}
    assert out["mounts"]["images"]["free_pct"] == 25.0 and out["mounts"]["images"]["total_bytes"] == 4096000
    assert runtime.check_a3(out).status == "ok"
    St.f_bavail = 50  # 5 percent free
    low = facts.disk_facts("it", "/srv/memes", statvfs=lambda p: St(), docker_logs="/x")
    assert runtime.check_a3(low).status == "error"


def test_unreadable_mount_is_marked_and_makes_a3_skip_instead_of_going_green():
    def boom(path):
        raise FileNotFoundError(path)

    out = facts.disk_facts("it", "/srv/memes", statvfs=boom, docker_logs="/x")
    assert all("error" in m for m in out["mounts"].values())
    assert runtime.check_a3(out).status == "skipped"


def test_main_writes_single_line_json_that_the_gate_prints_and_verify_judges(tmp_path):
    facts_dir = tmp_path / "facts"
    rc = facts.main(["memes-facts.py", "it", "--facts-dir", str(facts_dir), "--root", str(tmp_path / "srv")], runner=make_runner(inspect_doc()))
    assert rc == 0
    for name in ("disk.json", "containment.json"):
        raw = (facts_dir / name).read_bytes()
        assert raw.endswith(b"\n") and raw.count(b"\n") == 1 and b"\r" not in raw
    # the gate verbs `disk` and `containment` print these files; verify reads the first line
    gate_dir = tmp_path / "gate"
    gate_dir.mkdir()
    tree = Tree(gate_dir, facts_dir=str(facts_dir))
    containment = tree.run("containment")
    assert containment.code == 0, containment.err
    assert runtime.check_s8(runtime.parse_json_reply(containment.out)).status == "ok"
    disk = tree.run("disk")
    assert disk.code == 0
    parsed = runtime.parse_json_reply(disk.out)
    assert len(parsed["mounts"]) == 7 and runtime.check_a3(parsed).status == "ok"  # 90 percent free from the fake statvfs


def test_missing_backend_container_writes_a_loud_failure_not_a_stale_green(tmp_path):
    runner = make_runner(inspect_doc(), count=0)
    assert facts.main(["memes-facts.py", "it", "--facts-dir", str(tmp_path), "--root", str(tmp_path)], runner=runner) == 0
    doc = json.loads((tmp_path / "containment.json").read_text(encoding="utf-8"))
    assert doc["error"] == "backend_container_not_inspectable"
    assert runtime.check_s8(doc).status == "error"


def test_two_backend_containers_are_refused(tmp_path):
    facts.main(["memes-facts.py", "it", "--facts-dir", str(tmp_path), "--root", str(tmp_path)], runner=make_runner(inspect_doc(), count=2))
    assert "error" in json.loads((tmp_path / "containment.json").read_text(encoding="utf-8"))


def test_facts_cli_rejects_other_environments(tmp_path):
    assert facts.main(["memes-facts.py", "prod", "--facts-dir", str(tmp_path)], runner=make_runner(inspect_doc())) == 2
    assert facts.main(["memes-facts.py"], runner=make_runner(inspect_doc())) == 2


def test_facts_uses_compose_labels_of_the_environment(tmp_path):
    runner = make_runner(inspect_doc())
    facts.main(["memes-facts.py", "general", "--facts-dir", str(tmp_path), "--root", str(tmp_path)], runner=runner)
    ps = runner.calls[0]
    assert "label=com.docker.compose.project=memes-general" in ps and "label=com.docker.compose.service=backend" in ps


# ------------------------------------------------------------------------------------------------------------- systemd

def read_unit(name):
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    cp.optionxform = str
    cp.read_string((DEPLOY / "systemd" / name).read_text(encoding="utf-8"))
    return cp


@pytest.mark.parametrize("stem", ["memes-facts", "memes-backup", "memes-sweep"])
def test_every_unit_has_a_service_and_a_timer_template(stem):
    service, timer = read_unit(f"{stem}@.service"), read_unit(f"{stem}@.timer")
    assert service["Service"]["Type"] == "oneshot"
    assert "%i" in service["Service"]["ExecStart"]
    assert "timers.target" in timer["Install"]["WantedBy"]
    assert any(k in timer["Timer"] for k in ("OnCalendar", "OnUnitActiveSec"))


def test_unit_commands_point_at_what_setup_host_installs():
    setup = (DEPLOY / "host" / "setup-host.sh").read_text(encoding="utf-8")
    execs = {"memes-facts": "/opt/memes/memes-facts.py", "memes-backup": "/opt/memes/backup.sh", "memes-sweep": "/opt/memes/memesync-gate.py"}
    for stem, program in execs.items():
        assert program in read_unit(f"{stem}@.service")["Service"]["ExecStart"]
        assert f'"$OPT/{Path(program).name}"' in setup, f"setup-host does not install {program}"
    sweep = read_unit("memes-sweep@.service")["Service"]
    assert sweep["User"] == "memesync-%i" and sweep["Environment"] == "SSH_ORIGINAL_COMMAND=sweep"  # the gate's own verb, as the gate account
    assert read_unit("memes-backup@.service")["Service"].get("User", "root") == "root"  # docker exec needs it
    assert "memes-edge" not in "".join(p.read_text() for p in (DEPLOY / "systemd").iterdir())


def test_sweep_unit_runs_a_verb_the_gate_has():
    assert "sweep" in re.search(r"^VERBS = \{(.*?)^\}", (DEPLOY / "memesync-gate.py").read_text(encoding="utf-8"), re.S | re.M).group(1)


# ------------------------------------------------------------------------------------------------------------- backup: table list and scripts

def test_backup_tables_file_is_current_and_is_class_p():
    assert backup_tables.main(["sync.backup", "--check"]) == 0
    listed = [ln for ln in (DEPLOY / "backup.tables").read_text(encoding="utf-8").splitlines() if ln and not ln.startswith("#")]
    assert listed == registry.names(Klass.P) == registry.prod_write_set("backend_app")
    assert len(listed) == 6


def test_backup_tables_check_detects_drift(tmp_path, monkeypatch):
    stale = tmp_path / "backup.tables"
    stale.write_text("# generated\nmeme_saves\n", encoding="utf-8")
    monkeypatch.setattr(backup_tables, "TABLES_FILE", stale)
    assert backup_tables.main(["sync.backup", "--check"]) == 1
    assert backup_tables.main(["sync.backup", "--write"]) == 0
    assert backup_tables.main(["sync.backup", "--check"]) == 0


def script_text(name):
    return (DEPLOY / name).read_text(encoding="utf-8")


def test_scripts_do_not_hardcode_table_names_or_put_passwords_on_a_command_line():
    for name in ("backup.sh", "restore.sh"):
        text = script_text(name)
        code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
        for table in registry.names(Klass.P):
            assert table not in code, f"{name} hardcodes {table}: the list comes from backup.tables"
        assert "--env-file" in code and "backup.tables" in text
        assert not re.search(r"PGPASSWORD=[^\s<]", code), "a password literal in a script"
        assert " -W " not in code and "--password" not in code and "-e PGPASSWORD" not in code
        assert "set -x" not in code


def test_backup_dump_is_data_only_of_the_listed_tables_and_skips_sequences():
    code = script_text("backup.sh")
    assert "pg_dump" in code and "-Fc" in code and " -a " in code
    assert "--exclude-table-data='public.*_seq'" in code  # sync_r has no sequence privilege; restore.sh repairs the positions
    assert "-t \"public.$t\"" in code
    assert 'BASE="memes-$ENV_NAME-classp-$STAMP"' in code and ".meta.json" in code
    assert '"pg_restore_list": "ok"' in code and "retention" in code.lower()
    assert 'mv -f "$BK/$DUMP.part" "$BK/$DUMP"' in code and code.index('mv -f "$BK/$META.part"') < code.index('mv -f "$BK/$DUMP.part"')  # dump last


def test_restore_refuses_by_design():
    code = script_text("restore.sh")
    for needle in ("database marker mismatch", "tables are not empty", "confirmation did not match", "is not a dump of", "sha256 of the dump differs",
                   "ON_ERROR_STOP=1", "BEGIN;", "COMMIT;", "--list", "--dry-run"):
        assert needle in code, needle


def test_backup_names_match_what_the_gate_fetch_backup_serves():
    gate_src = (DEPLOY / "memesync-gate.py").read_text(encoding="utf-8")
    pattern = re.search(r"BACKUP_NAME_RE = re\.compile\(r\"(.*)\"\)", gate_src).group(1)
    assert re.fullmatch(pattern, "memes-it-classp-20261116T031500Z.dump")
    assert "name[:-5] + \".meta.json\"" in gate_src  # the sidecar the manifest is written to
    backup = script_text("backup.sh")
    assert 'BASE="memes-$ENV_NAME-classp-$STAMP"' in backup and 'DUMP="$BASE.dump"; META="$BASE.meta.json"' in backup


# ------------------------------------------------------------------------------------------------------------- hygiene

def deliverables():
    files = [DEPLOY / "backup.sh", DEPLOY / "restore.sh", DEPLOY / "backup.tables", ROOT / "sync" / "backup.py"]
    for d in SYNC_ONLY:
        files += [p for p in d.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    return files


def test_no_crlf_in_any_deliverable():
    for p in deliverables():
        assert b"\r" not in p.read_bytes(), f"{p} has CR characters (these run on Linux)"


def test_no_secret_material_in_deliverables():
    for p in deliverables():
        text = p.read_text(encoding="utf-8")
        assert "PRIVATE KEY" not in text, p
        assert not re.search(r"AAAAC3Nz[A-Za-z0-9+/]{30,}", text), p
        assert not re.search(r"PGPASSWORD=[A-Za-z0-9]{6,}", text), p


@pytest.mark.skipif(BASH is None, reason="bash not available")
@pytest.mark.parametrize("script", ["backup.sh", "restore.sh", "host/setup-host.sh"])
def test_bash_syntax(script):
    out = subprocess.run([BASH, "-n", str(DEPLOY / script)], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


def test_gate_dirfd_probe_follows_os_rename_not_os_replace():
    """Regression (P5d-T5, found against a real Debian Python): os.replace is NOT listed in os.supports_dir_fd on Linux although it takes
    dir_fd arguments, so a probe on os.replace made the gate refuse every verb ("unsafe_platform") on the VPS. Tests hid it by setting
    MEMESYNC_GATE_ALLOW_NO_DIRFD."""
    import os
    src = (DEPLOY / "memesync-gate.py").read_text(encoding="utf-8")
    probe = src[src.index("def _dirfd_supported"):src.index("HAVE_DIRFD = ")]
    assert "os.replace in os.supports_dir_fd" not in probe
    if hasattr(os, "supports_dir_fd") and os.rename in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY"):
        from tests.sync.gate_helpers import gate
        assert gate.HAVE_DIRFD is True
