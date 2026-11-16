"""host/setup-host.sh and the sshd drop-in + key templates against a REAL sshd in a throwaway debian container (P5d-T5).

The container is `debian:trixie-slim` with openssh-server, openssh-client and python3 installed through apt (needs network; the test skips if
that fails), no published port (everything is checked from inside over the container's own loopback), name prefix `p5d-b-`, removed afterwards.
`ssh_container_checks.sh` runs setup-host.sh as root, starts sshd, and prints PASS/FAIL per check: accounts and modes, the effective sshd
config, the verb key reaching the real gate (and nothing else), the tunnel key reaching only that environment's Postgres port, the chrooted
upload account, password login refused, a second run changing nothing, a second environment, the refusal without --admin-user.

What it cannot show: systemd units (no systemd in the container), XFS quotas (needs an XFS mount with prjquota), `docker network create`
(no docker in the container), the Windows OpenSSH client the owner will use, the uid 10001 backend container reading the images through "other".
"""
import re
import secrets
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.docker

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
IMAGE = "debian:trixie-slim"


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=kw.pop("timeout", 300), **kw)


@pytest.fixture(scope="module")
def container():
    if shutil.which("docker") is None or _run(["docker", "version", "--format", "{{.Server.Version}}"]).returncode != 0:
        pytest.skip("no Docker daemon")
    if _run(["docker", "image", "inspect", IMAGE]).returncode != 0:
        pytest.skip(f"image {IMAGE} not present locally")
    name = "p5d-b-ssh-" + secrets.token_hex(4)
    started = _run(["docker", "run", "-d", "--name", name, IMAGE, "sleep", "3600"])
    assert started.returncode == 0, started.stderr
    try:
        install = _run(["docker", "exec", name, "sh", "-c",
                        "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends "
                        "openssh-server openssh-client python3 findutils procps >/dev/null"], timeout=600)
        if install.returncode != 0:
            pytest.skip("apt could not install openssh in the container (no network?): " + install.stderr[-200:])
        _run(["docker", "exec", name, "mkdir", "-p", "/tmp/deploy"])
        cp = _run(["docker", "cp", str(ROOT / "deploy" / "prod"), f"{name}:/tmp/deploy/prod"])
        assert cp.returncode == 0, cp.stderr
        cp = _run(["docker", "cp", str(HERE / "ssh_container_checks.sh"), f"{name}:/tmp/checks.sh"])
        assert cp.returncode == 0, cp.stderr
        yield name
    finally:
        _run(["docker", "rm", "-f", "-v", name])


@pytest.fixture(scope="module")
def results(container):
    run = _run(["docker", "exec", container, "bash", "/tmp/checks.sh"], timeout=600)
    out = run.stdout
    print(out)
    print(run.stderr[-2000:])
    parsed = {}
    for line in out.splitlines():
        m = re.match(r"(PASS|FAIL) (\S+)(?: (.*))?$", line)
        if m:
            parsed[m.group(2)] = (m.group(1), m.group(3) or "")
    assert "DONE" in out, "the check script did not finish"
    return parsed


REQUIRED = """setup_host_first_run sshd_config_valid shell_memesync_is_sh shell_memeup_is_nologin memeup_not_in_gate_group
dir_images dir_bug_reports dir_work dir_retired dir_staging dir_incoming dir_pgdata dir_backups dir_web dir_facts dir_secrets gate_installed
gate_conf_mode keys_file_verb_tunnel keys_file_upload keys_file_root_owned
sshd_password_auth_off sshd_it_permitopen sshd_it_forwarding_local sshd_up_forced_sftp sshd_up_no_forwarding sshd_up_chroot
verb_handshake_runs_gate verb_no_shell verb_fetch_backup_reaches_gate verb_disk_prints_facts verb_containment_prints_facts verb_sweep_as_gate_user
verb_key_has_no_sftp tunnel_key_reaches_own_postgres tunnel_key_not_other_env_port tunnel_key_not_sshd verb_key_cannot_forward tunnel_key_runs_nothing
upload_put_and_rename upload_file_owner_group_readable upload_cannot_list_etc upload_cannot_see_images upload_cannot_write_staging_root
upload_cannot_write_outside upload_no_shell upload_key_not_valid_for_verb_account verb_key_not_valid_for_upload_account
gate_account_can_delete_upload admin_can_still_log_in password_login_refused setup_host_second_run setup_host_idempotent gate_conf_section_once
sshd_dropin_unchanged_message setup_host_other_env gate_conf_two_sections gate_conf_general_paths setup_requires_admin_user
setup_second_admin_run sshd_allowusers_union sshd_allowusers_stable_on_rerun admin_still_logs_in_after_union admin2_logs_in_after_union admin_logs_in_after_reload""".split()


@pytest.mark.parametrize("name", REQUIRED)
def test_check(results, name):
    assert name in results, f"check {name} did not run"
    status, why = results[name]
    assert status == "PASS", why
