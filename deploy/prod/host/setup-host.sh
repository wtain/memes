#!/usr/bin/env bash
# setup-host.sh -- prepares the VPS host for ONE prod environment (epic environment-data-sync, P5d-T5). Run as root, safe to re-run.
#
#   setup-host.sh <metal|general|it> --admin-user NAME [--verb-key FILE --tunnel-key FILE] [--upload-key FILE] [--obs-key FILE]
#                 [--quota images=40g] [--quota backups=10g] ... [--skip-sshd] [--skip-systemd]
#   setup-host.sh <env> --print-config        # the fixed per-environment values, no root needed, changes nothing
#
# It creates (all idempotent): the Grafana tunnel account memesync-obs (--obs-key; one for all environments), the log directories under
# /srv/memes/<env>/logs and /srv/memes/edge/logs/caddy; group memesync-<env>; users memesync-<env> (verb and tunnel keys) and memeup-<env> (upload only, chrooted);
# /srv/memes/<env>/{images,bug-reports,work,retired,staging/incoming,pgdata,backups,web} with the owners and modes of interfaces.md d;
# /opt/memes (gate, facts writer, backup, restore); /etc/memes/gate.conf (section of this environment, secrets: none) and /etc/memes/<env>/
# (0700, where the owner puts backup.env and restore.env by hand); /var/lib/memes/<env> (the facts files); the sshd drop-in and the key files;
# the systemd units and timers; XFS project quotas when asked and possible; the docker network memes-edge when absent.
# Nothing here holds a password or a private key. Public keys come from files given on the command line.
#
# Deviation from the design text, on purpose: memesync-<env> gets the shell /bin/sh, not nologin. sshd runs a forced command through the
# account's shell (`$SHELL -c <command>`), and nologin would refuse the gate. The authorized_keys options (restrict, command=) and the
# sshd Match block (no tty, no forwarding but the one Postgres port) are what confine it. memeup-<env> never reaches a shell
# (internal-sftp runs inside sshd) and keeps /usr/sbin/nologin.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY="$(dirname "$HERE")"           # deploy/prod
OPT=/opt/memes
ETC=/etc/memes
SRV=${MEMES_SRV:-/srv/memes}          # overridable for tests only
VARLIB=/var/lib/memes
KEYS_DIR=/etc/ssh/memes-keys
SSHD_DROPIN=/etc/ssh/sshd_config.d/00-memes-sync.conf
PG_UID=${PG_UID:-999}                 # uid of `postgres` in the pgvector/pgvector image (debian based)
BACKEND_UID=${BACKEND_UID:-10001}     # the backend container user (compose SVC_UID), owner of bug-reports; not 1000, that is the first human account

die() { echo "setup-host: $*" >&2; exit 1; }
say() { echo "setup-host: $*"; }

usage() { sed -n '2,8p' "${BASH_SOURCE[0]}" >&2; exit 2; }

[ $# -ge 1 ] || usage
ENV_NAME=$1; shift
case "$ENV_NAME" in
  metal)   PGPORT=6432; ENV_INDEX=1; SUBNET=172.31.1.0/24; ALIAS=api-metal ;;
  general) PGPORT=6434; ENV_INDEX=2; SUBNET=172.31.2.0/24; ALIAS=api-general ;;
  it)      PGPORT=6436; ENV_INDEX=3; SUBNET=172.31.3.0/24; ALIAS=api-it ;;
  *) die "unknown environment '$ENV_NAME' (metal, general, it)" ;;
esac
SYNC_USER="memesync-$ENV_NAME"
UP_USER="memeup-$ENV_NAME"
GROUP="memesync-$ENV_NAME"
OBS_USER=memesync-obs                 # the Grafana tunnel account, shared by the three environments (forwards to Prometheus 127.0.0.1:9090 only)
UP_GROUP="memeup-$ENV_NAME"
ENV_ROOT="$SRV/$ENV_NAME"

ADMIN_USER=""; VERB_KEY=""; TUNNEL_KEY=""; UPLOAD_KEY=""; OBS_KEY=""; SKIP_SSHD=0; SKIP_SYSTEMD=0; PRINT=0
declare -A QUOTA=()
while [ $# -gt 0 ]; do
  case "$1" in
    --admin-user)   ADMIN_USER=${2:?}; shift 2 ;;
    --verb-key)     VERB_KEY=${2:?}; shift 2 ;;
    --tunnel-key)   TUNNEL_KEY=${2:?}; shift 2 ;;
    --upload-key)   UPLOAD_KEY=${2:?}; shift 2 ;;
    --obs-key)      OBS_KEY=${2:?}; shift 2 ;;
    --quota)        kv=${2:?}; QUOTA[${kv%%=*}]=${kv#*=}; shift 2 ;;
    --skip-sshd)    SKIP_SSHD=1; shift ;;
    --skip-systemd) SKIP_SYSTEMD=1; shift ;;
    --print-config) PRINT=1; shift ;;
    *) die "unknown option '$1'" ;;
  esac
done

if [ "$PRINT" = 1 ]; then
  echo "env=$ENV_NAME pgport=$PGPORT subnet=$SUBNET backend_alias=$ALIAS sync_user=$SYNC_USER up_user=$UP_USER group=$GROUP"
  exit 0
fi

[ "$(id -u)" = 0 ] || die "run as root"
command -v python3 >/dev/null || die "python3 is required (the gate needs 3.9 or newer)"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || die "python3 older than 3.9"
[ -f "$DEPLOY/memesync-gate.py" ] || die "run from a checkout of deploy/prod (memesync-gate.py not found next to host/)"
if [ "$SKIP_SSHD" = 0 ]; then
  [ -n "$ADMIN_USER" ] || die "--admin-user is required (it stays in AllowUsers, otherwise this script could lock everybody out); or --skip-sshd"
  [[ "$ADMIN_USER" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || die "bad --admin-user"
  id "$ADMIN_USER" >/dev/null 2>&1 || die "admin user '$ADMIN_USER' does not exist"
  admin_home=$(getent passwd "$ADMIN_USER" | cut -d: -f6)
  [ -s "$admin_home/.ssh/authorized_keys" ] || die "$ADMIN_USER has no $admin_home/.ssh/authorized_keys: password login is switched off, install your key first"
fi
if { [ -n "$VERB_KEY" ] && [ -z "$TUNNEL_KEY" ]; } || { [ -z "$VERB_KEY" ] && [ -n "$TUNNEL_KEY" ]; }; then
  die "--verb-key and --tunnel-key go together (one file holds both lines)"
fi

# ---------------------------------------------------------------------------------------------------------------- groups and users
ensure_group() { getent group "$1" >/dev/null || groupadd --system "$1"; }
ensure_user() { # name group home shell
  if ! getent passwd "$1" >/dev/null; then
    useradd --system --gid "$2" --home-dir "$3" --no-create-home --shell "$4" --comment "memes sync $1" "$1"
  else
    usermod --gid "$2" --home "$3" --shell "$4" "$1"
  fi
  usermod -p '*' "$1"   # not locked (sshd refuses '!' accounts), no password login possible
}
ensure_group "$GROUP"
ensure_group "$UP_GROUP"
install -d -m 0755 -o root -g root "$SRV" "$ENV_ROOT" "$VARLIB" "$VARLIB/$ENV_NAME" "$ETC"
ensure_user "$SYNC_USER" "$GROUP" "$VARLIB/$ENV_NAME" /bin/sh
ensure_user "$UP_USER" "$UP_GROUP" "$ENV_ROOT/staging" /usr/sbin/nologin
ensure_group "$OBS_USER"
ensure_user "$OBS_USER" "$OBS_USER" /nonexistent /usr/sbin/nologin   # port forwarding needs no shell; the key line runs /bin/false anyway
# memeup-<env> is NOT in group memesync-<env>: it owns incoming/ (mode 2770), and the setgid bit gives what it uploads the group of the
# gate account, which reads and deletes there through that group.
say "accounts $SYNC_USER, $UP_USER and $OBS_USER ready"

# ---------------------------------------------------------------------------------------------------------------- directories
# interfaces.md d. install -d only creates; chown/chmod after it makes a re-run correct. The staging chroot root must be root-owned and
# not writable, and so must every parent of it (/srv, /srv/memes, /srv/memes/<env>): sshd refuses the chroot otherwise.
mk() { # mode owner group path
  install -d -m "$1" -o "$2" -g "$3" "$4"
  chown "$2:$3" "$4"; chmod "$1" "$4"
}
mk 2755 "$SYNC_USER" "$GROUP" "$ENV_ROOT/images"
mk 2775 "$BACKEND_UID" "$GROUP" "$ENV_ROOT/bug-reports"
mk 0700 "$SYNC_USER" "$GROUP" "$ENV_ROOT/work"
mk 0700 "$SYNC_USER" "$GROUP" "$ENV_ROOT/retired"
mk 0755 root root "$ENV_ROOT/staging"
mk 2770 "$UP_USER" "$GROUP" "$ENV_ROOT/staging/incoming"
mk 0700 "$PG_UID" "$PG_UID" "$ENV_ROOT/pgdata"
# backups/ is root-owned: backup.sh and restore.sh run as root and must not trust anything a less privileged account could plant
# there (symlinks, swapped files). The gate account only READS it (group r-x, fetch-backup); it cannot create, rename or delete in it.
mk 0750 root "$GROUP" "$ENV_ROOT/backups"
mk 0755 "$SYNC_USER" "$GROUP" "$ENV_ROOT/web"
# Log directories (observability spec 6 and 9a-7). The parents are root-owned and not writable; each service writes only its own
# subdirectory and none of them holds a symlink. backend: owned by the backend container's uid (BACKEND_UID, compose SVC_UID), which
# writes its own files as owner; the group $GROUP only reads (2750: with the container's umask 002 a group-writable directory would let
# the gate account alter or delete the files); postgres: the postgres uid of the image, like pgdata; caddy: Caddy runs as root.
mk 0755 root root "$ENV_ROOT/logs"
mk 2750 "$BACKEND_UID" "$GROUP" "$ENV_ROOT/logs/backend"
mk 0700 "$PG_UID" "$PG_UID" "$ENV_ROOT/logs/postgres"
mk 0755 root root "$SRV/edge"
mk 0755 root root "$SRV/edge/logs"
mk 0755 root root "$SRV/edge/logs/caddy"
say "directories under $ENV_ROOT ready"

# images, work and retired must share one filesystem (rename(2); the gate handshake exits 3 otherwise)
dev_of() { stat -c %d "$1"; }
[ "$(dev_of "$ENV_ROOT/images")" = "$(dev_of "$ENV_ROOT/work")" ] || die "images and work are on different filesystems"
[ "$(dev_of "$ENV_ROOT/images")" = "$(dev_of "$ENV_ROOT/retired")" ] || die "images and retired are on different filesystems"

# ---------------------------------------------------------------------------------------------------------------- programs and config
install -d -m 0755 -o root -g root "$OPT"
install -m 0755 -o root -g root "$DEPLOY/memesync-gate.py" "$OPT/memesync-gate.py"
install -m 0755 -o root -g root "$HERE/memes-facts.py" "$OPT/memes-facts.py"
install -m 0755 -o root -g root "$DEPLOY/postgres/prune_logs.py" "$OPT/prune_logs.py"
install -m 0755 -o root -g root "$DEPLOY/backup.sh" "$OPT/backup.sh"
install -m 0755 -o root -g root "$DEPLOY/restore.sh" "$OPT/restore.sh"
install -m 0644 -o root -g root "$DEPLOY/backup.tables" "$OPT/backup.tables"

install -d -m 0700 -o root -g root "$ETC/$ENV_NAME"   # backup.env / restore.env (passwords) are put here by hand
if [ ! -f "$ETC/gate.conf" ]; then
  printf '# /etc/memes/gate.conf -- one section per environment; see deploy/prod/gate.conf.example. Owned by root, never group/world writable.\n' > "$ETC/gate.conf"
  chmod 0644 "$ETC/gate.conf"; chown root:root "$ETC/gate.conf"
fi
if grep -q "^\[$ENV_NAME\]" "$ETC/gate.conf"; then
  say "gate.conf already has [$ENV_NAME]; left as it is (edit it by hand to change a cap)"
else
  python3 - "$DEPLOY/gate.conf.example" "$ENV_NAME" >> "$ETC/gate.conf" <<'PY'
import re, sys
text, env = open(sys.argv[1], encoding="utf-8").read(), sys.argv[2]
start = text.index("[general]")
end = text.index("# [metal]")
section = text[start:end].rstrip() + "\n"
section = section.replace("[general]", "[%s]" % env, 1).replace("/general", "/" + env)
sys.stdout.write("\n" + section)
PY
  say "gate.conf section [$ENV_NAME] written"
fi
chmod 0644 "$ETC/gate.conf"

# ---------------------------------------------------------------------------------------------------------------- quotas
# XFS project quotas where the filesystem supports them; otherwise the facts timer's disk.json plus sync.verify A3 (warn 20 %, error 10 %)
# are the free-space alarm, and this script says so instead of pretending.
apply_quota() { # name dir size
  local name=$1 dir=$2 size=$3 projid=$(( 3000 + ENV_INDEX * 100 + $4 )) pname="memes-$ENV_NAME-$1"
  local fstype; fstype=$(findmnt -no FSTYPE -T "$dir" 2>/dev/null || echo unknown)
  if [ "$fstype" != xfs ] || ! command -v xfs_quota >/dev/null || ! findmnt -no OPTIONS -T "$dir" | grep -Eq '(^|,)(prjquota|pquota)(,|$)'; then
    say "NOTE quota $name=$size NOT applied: $dir is on '$fstype' without project quota; the free-space alarm (disk.json, verify A3) stands in"
    return 0
  fi
  touch /etc/projects /etc/projid
  sed -i "/^$projid:/d" /etc/projects; echo "$projid:$dir" >> /etc/projects
  sed -i "/^$pname:/d" /etc/projid;   echo "$pname:$projid" >> /etc/projid
  local mnt; mnt=$(findmnt -no TARGET -T "$dir")
  xfs_quota -x -c "project -s $pname" "$mnt"
  xfs_quota -x -c "limit -p bsoft=$size bhard=$size $pname" "$mnt"
  say "quota $name=$size applied (project $pname, id $projid)"
}
qi=0
for name in images bug-reports backups incoming pgdata; do
  qi=$((qi + 1))
  if [ -n "${QUOTA[$name]:-}" ]; then
    case "$name" in
      incoming) d="$ENV_ROOT/staging/incoming" ;;
      *)        d="$ENV_ROOT/$name" ;;
    esac
    apply_quota "$name" "$d" "${QUOTA[$name]}" "$qi"
  fi
done
for name in "${!QUOTA[@]}"; do
  case "$name" in images|bug-reports|backups|incoming|pgdata) ;; *) die "unknown quota target '$name'" ;; esac
done
if [ "${#QUOTA[@]}" = 0 ]; then
  say "NOTE no --quota given: no XFS project quota set; free-space alarms (disk.json, verify A3) are the only guard against a full disk"
fi

# ---------------------------------------------------------------------------------------------------------------- sshd and keys
valid_pubkey() { # file -> prints "type data" or dies. Options, comments and extra lines in the file are dropped on purpose.
  local f=$1 t d
  [ -f "$f" ] || die "key file $f not found"
  [ "$(grep -cv '^[[:space:]]*\(#\|$\)' "$f")" = 1 ] || die "$f must hold exactly one public key line"
  read -r t d _ < <(grep -v '^[[:space:]]*\(#\|$\)' "$f")
  case "$t" in ssh-ed25519|ecdsa-sha2-nistp256|ecdsa-sha2-nistp384|ecdsa-sha2-nistp521|sk-ssh-ed25519@openssh.com|sk-ecdsa-sha2-nistp256@openssh.com|ssh-rsa) ;;
    *) die "$f: first field '$t' is not a public key type (options such as command= are not accepted from a key file)" ;; esac
  [[ "$d" =~ ^[A-Za-z0-9+/=]+$ ]] || die "$f: key data is not base64"
  echo "$t $d"
}
render() { # template env pgport "type data"
  local tpl=$1 kt kd; kt=${4%% *}; kd=${4#* }
  sed -e "s|@ENV@|$2|g" -e "s|@PGPORT@|$3|g" -e "s|@KEY_TYPE@|$kt|g" -e "s|@KEY_DATA@|$kd|g" -e '/^#/d' "$tpl"
}
if [ "$SKIP_SSHD" = 0 ]; then
  install -d -m 0755 -o root -g root "$KEYS_DIR"
  if [ -n "$VERB_KEY" ]; then
    vk=$(valid_pubkey "$VERB_KEY"); tk=$(valid_pubkey "$TUNNEL_KEY")
    [ "$vk" != "$tk" ] || die "the verb key and the tunnel key must be two different keys"
    { render "$DEPLOY/sshd/authorized_keys.verb.template" "$ENV_NAME" "$PGPORT" "$vk"
      render "$DEPLOY/sshd/authorized_keys.tunnel.template" "$ENV_NAME" "$PGPORT" "$tk"; } > "$KEYS_DIR/$SYNC_USER.new"
    chmod 0644 "$KEYS_DIR/$SYNC_USER.new"; chown root:root "$KEYS_DIR/$SYNC_USER.new"; mv -f "$KEYS_DIR/$SYNC_USER.new" "$KEYS_DIR/$SYNC_USER"
    say "keys of $SYNC_USER written"
  else
    say "NOTE no --verb-key/--tunnel-key: $KEYS_DIR/$SYNC_USER untouched"
  fi
  if [ -n "$UPLOAD_KEY" ]; then
    uk=$(valid_pubkey "$UPLOAD_KEY")
    render "$DEPLOY/sshd/authorized_keys.upload.template" "$ENV_NAME" "$PGPORT" "$uk" > "$KEYS_DIR/$UP_USER.new"
    chmod 0644 "$KEYS_DIR/$UP_USER.new"; chown root:root "$KEYS_DIR/$UP_USER.new"; mv -f "$KEYS_DIR/$UP_USER.new" "$KEYS_DIR/$UP_USER"
    say "key of $UP_USER written"
  else
    say "NOTE no --upload-key: $KEYS_DIR/$UP_USER untouched"
  fi
  if [ -n "$OBS_KEY" ]; then
    ok=$(valid_pubkey "$OBS_KEY")
    if [ -n "$VERB_KEY" ]; then
      [ "$ok" != "$vk" ] && [ "$ok" != "$tk" ] || die "the observability key must differ from the verb and tunnel keys"
    fi
    render "$DEPLOY/sshd/authorized_keys.obs.template" "$ENV_NAME" "$PGPORT" "$ok" > "$KEYS_DIR/$OBS_USER.new"
    chmod 0644 "$KEYS_DIR/$OBS_USER.new"; chown root:root "$KEYS_DIR/$OBS_USER.new"; mv -f "$KEYS_DIR/$OBS_USER.new" "$KEYS_DIR/$OBS_USER"
    say "key of $OBS_USER written"
  else
    say "NOTE no --obs-key: $KEYS_DIR/$OBS_USER untouched"
  fi
  install -d -m 0755 /etc/ssh/sshd_config.d
  # AllowUsers is the UNION of the admin users already allowed by a previous run (any name that is not one of this stack's own accounts)
  # and the current --admin-user: running this for the second environment with another --admin-user must not lock the first one out.
  admins=""
  if [ -f "$SSHD_DROPIN" ]; then
    for u in $(sed -n 's/^AllowUsers[[:space:]]\+//p' "$SSHD_DROPIN" | head -1); do
      case "$u" in memesync-*|memeup-*) continue ;; esac
      [[ "$u" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || continue
      case " $admins " in *" $u "*) ;; *) admins="${admins:+$admins }$u" ;; esac
    done
  fi
  case " $admins " in *" $ADMIN_USER "*) ;; *) admins="${admins:+$admins }$ADMIN_USER" ;; esac
  new=$(mktemp); sed "s|@ADMIN_USER@|$admins|g" "$DEPLOY/sshd/memes-sync.conf" > "$new"
  if ! cmp -s "$new" "$SSHD_DROPIN" 2>/dev/null; then
    [ -f "$SSHD_DROPIN" ] && cp -p "$SSHD_DROPIN" "$SSHD_DROPIN.bak"
    install -m 0644 -o root -g root "$new" "$SSHD_DROPIN"
    if command -v sshd >/dev/null && ! sshd -t; then
      if [ -f "$SSHD_DROPIN.bak" ]; then mv -f "$SSHD_DROPIN.bak" "$SSHD_DROPIN"; else rm -f "$SSHD_DROPIN"; fi
      rm -f "$new"; die "sshd -t rejected the drop-in; it was rolled back"
    fi
    if command -v systemctl >/dev/null && [ -d /run/systemd/system ]; then systemctl reload ssh 2>/dev/null || systemctl reload sshd; fi
    say "sshd drop-in installed ($SSHD_DROPIN); verify a NEW session before closing this one"
  else
    say "sshd drop-in unchanged"
  fi
  rm -f "$new"
fi

# ---------------------------------------------------------------------------------------------------------------- docker network
if command -v docker >/dev/null; then
  docker network inspect memes-edge >/dev/null 2>&1 || docker network create --subnet 172.30.0.0/24 memes-edge >/dev/null
  say "docker network memes-edge present"
else
  say "NOTE docker is not installed: memes-edge not created (docker network create --subnet 172.30.0.0/24 memes-edge)"
fi

# ---------------------------------------------------------------------------------------------------------------- systemd
if [ "$SKIP_SYSTEMD" = 0 ] && [ -d /run/systemd/system ]; then
  for u in "$DEPLOY"/systemd/*.service "$DEPLOY"/systemd/*.timer; do install -m 0644 -o root -g root "$u" /etc/systemd/system/; done
  systemctl daemon-reload
  for t in memes-facts memes-backup memes-sweep memes-prune-logs; do systemctl enable --now "$t@$ENV_NAME.timer"; done
  systemctl start "memes-facts@$ENV_NAME.service" || say "NOTE first facts run failed (containers not up yet is fine)"
  say "systemd timers enabled for $ENV_NAME"
else
  say "NOTE systemd not running here (or --skip-systemd): units not installed; first facts run is done by hand"
  python3 "$OPT/memes-facts.py" "$ENV_NAME" --root "$SRV" --facts-dir "$VARLIB/$ENV_NAME" || say "NOTE facts run failed"
fi

say "done for $ENV_NAME. By hand: $ETC/$ENV_NAME/backup.env (PGPASSWORD of memes_${ENV_NAME}_sync_r) and restore.env (the owner role), both 0600 root."
