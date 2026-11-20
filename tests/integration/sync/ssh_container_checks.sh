#!/usr/bin/env bash
# Runs INSIDE a throwaway debian container as root (see test_host_container.py). deploy/prod is at /tmp/deploy/prod; openssh-server,
# openssh-client, python3 are installed. Prints one line per check: "PASS <name>" or "FAIL <name> <why>"; always exits 0.
set -u
export MSYS_NO_PATHCONV=1
DEPLOY=/tmp/deploy/prod
export K=/tmp/keys
mkdir -p "$K" /run/sshd
pass() { echo "PASS $1"; }
fail() { echo "FAIL $1 ${2:-}"; }
check() { local n=$1; shift; if "$@" >/tmp/out.$$ 2>&1; then pass "$n"; else fail "$n" "rc=$? $(head -c 300 /tmp/out.$$ | tr '\n' ' ')"; fi; }
S() { ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o IdentitiesOnly=yes -o LogLevel=ERROR -o ConnectTimeout=5 "$@"; }

useradd -m -u 2000 -s /bin/bash admin
mkdir -p /home/admin/.ssh
for n in admin verb tunnel upload obs; do ssh-keygen -q -t ed25519 -N '' -f "$K/$n" -C "test-$n"; done
cp "$K/admin.pub" /home/admin/.ssh/authorized_keys; chown -R admin:admin /home/admin/.ssh; chmod 700 /home/admin/.ssh; chmod 600 /home/admin/.ssh/authorized_keys
ssh-keygen -A >/dev/null

# two fake "postgres" listeners on the loopback: 6436 (it, allowed) and 6434 (general, must stay unreachable with the it tunnel key)
cat > /tmp/listener.py <<'PY'
import socket, sys, threading
def serve(port):
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(("127.0.0.1", port)); s.listen(8)
    while True:
        c, _ = s.accept(); c.sendall(b"PG%d\n" % port); c.close()
for p in (6436, 6434, 9090, 9091):
    threading.Thread(target=serve, args=(p,), daemon=True).start()
threading.Event().wait()
PY
python3 /tmp/listener.py & LISTENER=$!

echo "--- setup-host (first run)"
bash "$DEPLOY/host/setup-host.sh" it --admin-user admin --verb-key "$K/verb.pub" --tunnel-key "$K/tunnel.pub" --upload-key "$K/upload.pub" --obs-key "$K/obs.pub" --skip-systemd \
  --quota images=1g > /tmp/setup1.log 2>&1
check setup_host_first_run test $? -eq 0
cat /tmp/setup1.log | sed 's/^/LOG /'
check sshd_config_valid sshd -t
/usr/sbin/sshd -E /tmp/sshd.log
sleep 1

# ------------------------------------------------------------------------------------------------------------------ accounts, modes
shell_of() { getent passwd "$1" | cut -d: -f7; }
check shell_memesync_is_sh test "$(shell_of memesync-it)" = /bin/sh
check shell_memeup_is_nologin test "$(shell_of memeup-it)" = /usr/sbin/nologin
check memeup_not_in_gate_group bash -c '! id -nG memeup-it | tr " " "\n" | grep -qx memesync-it'
mode_is() { # path owner group mode
  [ "$(stat -c '%U %G %a' "$1")" = "$2 $3 $4" ] || { echo "$1 is $(stat -c '%U %G %a' "$1"), want $2 $3 $4"; return 1; }
}
R=/srv/memes
check dir_srv mode_is $R root root 755
check dir_env mode_is $R/it root root 755
check dir_images mode_is $R/it/images memesync-it memesync-it 2755
check dir_bug_reports test "$(stat -c "%u %G %a" $R/it/bug-reports)" = "10001 memesync-it 2775"
check dir_work mode_is $R/it/work memesync-it memesync-it 700
check dir_retired mode_is $R/it/retired memesync-it memesync-it 700
check dir_staging mode_is $R/it/staging root root 755
check dir_incoming mode_is $R/it/staging/incoming memeup-it memesync-it 2770
check dir_pgdata test "$(stat -c "%u %g %a" $R/it/pgdata)" = "999 999 700"
check dir_backups mode_is $R/it/backups root memesync-it 750
check dir_web mode_is $R/it/web memesync-it memesync-it 755
check dir_facts mode_is /var/lib/memes/it root root 755
check dir_secrets mode_is /etc/memes/it root root 700
check gate_installed mode_is /opt/memes/memesync-gate.py root root 755
check gate_conf_mode mode_is /etc/memes/gate.conf root root 644
check keys_file_verb_tunnel test "$(grep -c . /etc/ssh/memes-keys/memesync-it)" = 2
check keys_file_upload test "$(grep -c . /etc/ssh/memes-keys/memeup-it)" = 1
check keys_file_root_owned mode_is /etc/ssh/memes-keys/memesync-it root root 644
check dir_logs mode_is $R/it/logs root root 755
check dir_logs_backend test "$(stat -c "%u %G %a" $R/it/logs/backend)" = "10001 memesync-it 2750"
check dir_logs_postgres test "$(stat -c "%u %g %a" $R/it/logs/postgres)" = "999 999 700"
check dir_logs_caddy mode_is $R/edge/logs/caddy root root 755
check dir_edge mode_is $R/edge root root 755
check prune_logs_installed mode_is /opt/memes/prune_logs.py root root 755
check shell_obs_is_nologin test "$(shell_of memesync-obs)" = /usr/sbin/nologin
check keys_file_obs_one_line test "$(grep -c . /etc/ssh/memes-keys/memesync-obs)" = 1
check keys_file_obs_root_owned mode_is /etc/ssh/memes-keys/memesync-obs root root 644
check keys_file_obs_options grep -q '^restrict,port-forwarding,permitopen="127.0.0.1:9090",command="/bin/false" ssh-ed25519 ' /etc/ssh/memes-keys/memesync-obs
check obs_password_locked_not_set test "$(getent shadow memesync-obs | cut -d: -f2)" = '*'

# ------------------------------------------------------------------------------------------------------------------ effective sshd config
T() { sshd -T -C "user=$1,host=h,addr=1.2.3.4" 2>/dev/null; }
check sshd_password_auth_off bash -c "$(declare -f T); T memesync-it | grep -qx 'passwordauthentication no'"
check sshd_it_permitopen bash -c "$(declare -f T); T memesync-it | grep -qx 'permitopen 127.0.0.1:6436'"
check sshd_it_forwarding_local bash -c "$(declare -f T); T memesync-it | grep -qx 'allowtcpforwarding local'"
check sshd_obs_permitopen bash -c "$(declare -f T); T memesync-obs | grep -qx 'permitopen 127.0.0.1:9090'"
check sshd_obs_forwarding_local bash -c "$(declare -f T); T memesync-obs | grep -qx 'allowtcpforwarding local'"
check sshd_allowusers_has_obs grep -Eq '^AllowUsers .*memesync-obs' /etc/ssh/sshd_config.d/00-memes-sync.conf
check sshd_up_forced_sftp bash -c "$(declare -f T); T memeup-it | grep -qx 'forcecommand internal-sftp -u 007'"
check sshd_up_no_forwarding bash -c "$(declare -f T); T memeup-it | grep -qx 'allowtcpforwarding no'"
check sshd_up_chroot bash -c "$(declare -f T); T memeup-it | grep -q '^chrootdirectory '"

# ------------------------------------------------------------------------------------------------------------------ verb key
VERB="-i $K/verb memesync-it@127.0.0.1"
out=$(S $VERB handshake 2>/tmp/err); rc=$?
check verb_handshake_runs_gate bash -c "[ $rc -eq 0 ] && echo '$out' | grep -q '\"environment\": \"it\"'"
echo "LOG handshake: rc=$rc out=$out err=$(cat /tmp/err)"
out=$(S $VERB 'sh -c id' 2>&1); rc=$?
check verb_no_shell bash -c "[ $rc -eq 2 ] && echo '$out' | grep -q unknown_verb && ! echo '$out' | grep -q uid="
out=$(S $VERB 'fetch-backup --meta' 2>&1); rc=$?
check verb_fetch_backup_reaches_gate bash -c "[ $rc -eq 3 ] && echo '$out' | grep -q no_backup"
out=$(S $VERB disk 2>&1); rc=$?
check verb_disk_prints_facts bash -c "[ $rc -eq 0 ] && echo '$out' | python3 -c 'import json,sys; d=json.loads(sys.stdin.readline()); assert d[\"mounts\"][\"images\"][\"free_pct\"] > 0'"
out=$(S $VERB containment 2>&1); rc=$?
check verb_containment_prints_facts bash -c "[ $rc -eq 0 ] && echo '$out' | python3 -c 'import json,sys; d=json.loads(sys.stdin.readline()); assert d[\"error\"]'"
check verb_sweep_as_gate_user su -s /bin/sh memesync-it -c 'SSH_ORIGINAL_COMMAND=sweep /usr/bin/python3 /opt/memes/memesync-gate.py it'
check verb_key_has_no_sftp bash -c "! echo 'ls /' | sftp -q -b - -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o IdentitiesOnly=yes -o LogLevel=ERROR -i $K/verb memesync-it@127.0.0.1"

# ------------------------------------------------------------------------------------------------------------------ forwarding
banner_through() { # key local_port remote_target [user] -> prints what a connection receives
  S -f -N -i "$K/$1" -L "$2:$3" "${4:-memesync-it}@127.0.0.1" >/dev/null 2>&1 || { echo "ssh-failed"; return; }
  sleep 1
  python3 - "$2" <<'PY'
import socket, sys
s = socket.socket(); s.settimeout(3)
try:
    s.connect(("127.0.0.1", int(sys.argv[1]))); data = s.recv(64)
except OSError as e:
    data = b""
print(data.decode(errors="replace").strip() or "nothing")
PY
  pkill -f "ssh .*-L $2:" || true
}
banner_obs() { banner_through "$1" "$2" "$3" memesync-obs; }
check tunnel_key_reaches_own_postgres test "$(banner_through tunnel 18436 127.0.0.1:6436)" = PG6436
check tunnel_key_not_other_env_port test "$(banner_through tunnel 18434 127.0.0.1:6434)" = nothing
check obs_key_reaches_prometheus test "$(banner_obs obs 18090 127.0.0.1:9090)" = PG9090
check obs_key_not_other_loopback_port test "$(banner_obs obs 18091 127.0.0.1:9091)" = nothing
check obs_key_not_postgres test "$(banner_obs obs 18436 127.0.0.1:6436)" = nothing
check obs_key_not_sshd test "$(banner_obs obs 18023 127.0.0.1:22)" = nothing
check tunnel_key_not_prometheus test "$(banner_through tunnel 18092 127.0.0.1:9090)" = nothing
out=$(S -i "$K/obs" memesync-obs@127.0.0.1 id 2>&1); rc=$?
check obs_key_runs_nothing bash -c "[ $rc -ne 0 ] && ! echo '$out' | grep -q uid="
out=$(S -tt -i "$K/obs" memesync-obs@127.0.0.1 2>&1 </dev/null); rc=$?
check obs_key_no_shell_session bash -c "[ $rc -ne 0 ] && ! echo '$out' | grep -q uid="
check tunnel_key_not_sshd test "$(banner_through tunnel 18022 127.0.0.1:22)" = nothing
check verb_key_cannot_forward test "$(banner_through verb 18437 127.0.0.1:6436)" = nothing
out=$(S -i "$K/tunnel" memesync-it@127.0.0.1 id 2>&1); rc=$?
check tunnel_key_runs_nothing bash -c "[ $rc -ne 0 ] && ! echo '$out' | grep -q uid="

# ------------------------------------------------------------------------------------------------------------------ upload account
head -c 4096 /dev/urandom > /tmp/x.tar
SF() { sftp -q -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o IdentitiesOnly=yes -o LogLevel=ERROR -i "$K/upload" -b - memeup-it@127.0.0.1; }
check upload_put_and_rename bash -c "$(declare -f SF); printf 'put /tmp/x.tar /incoming/b1.tar.part\nrename /incoming/b1.tar.part /incoming/b1.tar\n' | SF"
# -u 007 masks the mode the client sends: 660 or 640 depending on the source file; either way the gate group can read it
check upload_file_owner_group_readable test "$(stat -c '%U %G' $R/it/staging/incoming/b1.tar)" = "memeup-it memesync-it" -a "$(stat -c %a $R/it/staging/incoming/b1.tar | cut -c2)" -ge 4
check upload_cannot_list_etc bash -c "$(declare -f SF); ! echo 'ls /etc' | SF"
check upload_cannot_see_images bash -c "$(declare -f SF); ! echo 'ls /images' | SF"
check upload_cannot_write_staging_root bash -c "$(declare -f SF); ! echo 'put /tmp/x.tar /evil' | SF"
check upload_cannot_write_outside bash -c "$(declare -f SF); ! echo 'put /tmp/x.tar /../images/evil' | SF"
out=$(S -i "$K/upload" memeup-it@127.0.0.1 id 2>&1); rc=$?
check upload_no_shell bash -c "! echo '$out' | grep -q uid="
out=$(S -i "$K/upload" memesync-it@127.0.0.1 handshake 2>&1); rc=$?
check upload_key_not_valid_for_verb_account bash -c "[ $rc -ne 0 ] && ! echo '$out' | grep -q environment"
out=$(S -i "$K/verb" memeup-it@127.0.0.1 true 2>&1); rc=$?
check verb_key_not_valid_for_upload_account test $rc -ne 0
check gate_account_can_delete_upload su -s /bin/sh memesync-it -c "rm $R/it/staging/incoming/b1.tar"
out=$(S -i "$K/admin" admin@127.0.0.1 true 2>&1); rc=$?
check admin_can_still_log_in test $rc -eq 0
out=$(ssh -o BatchMode=yes -o PubkeyAuthentication=no -o PreferredAuthentications=password -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR admin@127.0.0.1 true 2>&1); rc=$?
check password_login_refused test $rc -ne 0

# ------------------------------------------------------------------------------------------------------------------ idempotency
snap() { find /srv/memes /var/lib/memes /etc/memes /opt/memes /etc/ssh/memes-keys /etc/ssh/sshd_config.d -printf '%p %U %G %m\n' | sort; }
snap > /tmp/snap1
bash "$DEPLOY/host/setup-host.sh" it --admin-user admin --verb-key "$K/verb.pub" --tunnel-key "$K/tunnel.pub" --upload-key "$K/upload.pub" --skip-systemd > /tmp/setup2.log 2>&1
check setup_host_second_run test $? -eq 0
snap > /tmp/snap2
check setup_host_idempotent diff /tmp/snap1 /tmp/snap2
check gate_conf_section_once test "$(grep -c '^\[it\]' /etc/memes/gate.conf)" = 1
check sshd_dropin_unchanged_message grep -q 'sshd drop-in unchanged' /tmp/setup2.log
bash "$DEPLOY/host/setup-host.sh" general --skip-sshd --skip-systemd > /tmp/setup3.log 2>&1
check setup_host_other_env test $? -eq 0
check gate_conf_two_sections test "$(grep -c '^\[' /etc/memes/gate.conf)" = 2
check gate_conf_general_paths grep -q '^base_path *= /srv/memes/general/images' /etc/memes/gate.conf
bash "$DEPLOY/host/setup-host.sh" it --skip-systemd > /tmp/setup4.log 2>&1
check setup_requires_admin_user test $? -ne 0
# AllowUsers is the union of the admin users: a second run with ANOTHER admin keeps the first one (and the memes accounts), a third run
# with the first changes nothing
useradd -m -u 2001 -s /bin/bash admin2
mkdir -p /home/admin2/.ssh; cp "$K/admin.pub" /home/admin2/.ssh/authorized_keys; chown -R admin2:admin2 /home/admin2/.ssh; chmod 700 /home/admin2/.ssh; chmod 600 /home/admin2/.ssh/authorized_keys
bash "$DEPLOY/host/setup-host.sh" it --admin-user admin2 --skip-systemd > /tmp/setup5.log 2>&1
check setup_second_admin_run test $? -eq 0
allow() { grep '^AllowUsers' /etc/ssh/sshd_config.d/00-memes-sync.conf; }
has_all() { local a; a=$(allow); for u in "$@"; do echo "$a" | grep -qw -- "$u" || { echo "missing $u in: $a"; return 1; }; done; }
check sshd_allowusers_union has_all admin admin2 memesync-it memeup-general
before=$(allow)
bash "$DEPLOY/host/setup-host.sh" it --admin-user admin --skip-systemd > /tmp/setup6.log 2>&1
check sshd_allowusers_stable_on_rerun test "$(allow)" = "$before"
out=$(S -i "$K/admin" admin@127.0.0.1 true 2>&1); rc=$?
check admin_still_logs_in_after_union test $rc -eq 0
kill -HUP "$(cat /run/sshd.pid)"; sleep 1   # no systemd here: the reload the script would do on a real host
check admin2_logs_in_after_union S -i "$K/admin" admin2@127.0.0.1 true
check admin_logs_in_after_reload S -i "$K/admin" admin@127.0.0.1 true
echo "LOG quota/notes:"; grep NOTE /tmp/setup1.log | sed 's/^/LOG /'

kill "$LISTENER" 2>/dev/null
echo DONE
exit 0
