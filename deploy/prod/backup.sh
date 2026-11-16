#!/usr/bin/env bash
# backup.sh <metal|general|it> -- backup of the only data that exists on prod and nowhere else (epic environment-data-sync, P5d-T7).
#
# What it writes into /srv/memes/<env>/backups (names below; the gate verb fetch-backup serves the newest *.dump and its sidecar):
#   memes-<env>-classp-<UTC stamp>.dump        pg_dump -Fc --data-only of exactly the tables listed in backup.tables (class P, generated
#                                              from sync/registry.py by `python -m sync.backup`), taken through `docker exec` over the
#                                              Unix socket of the database container as the role memes_<env>_sync_r (SELECT only)
#   memes-<env>-bugreports-<UTC stamp>.tar.gz  the bug-reports directory
#   memes-<env>-classp-<UTC stamp>.meta.json   the manifest: sizes, sha256, rows per table, `pg_restore_list: "ok"` (what verify S6 reads)
# Files are written as *.part and renamed (the .dump last, so fetch-backup never sees half a set); sets older than 14 days are removed.
#
# The password is NOT on a command line: /etc/memes/<env>/backup.env (0600, root, put there by hand) holds `PGPASSWORD=...` and is handed to
# `docker exec --env-file`. Run as root (docker); the timer memes-backup@<env> does.
#
# Overrides, all for tests or an unusual stack: DOCKER (binary or a wrapper), DB_CONTAINER (default: the compose service `db` of project
# memes-<env>), MEMES_ROOT (default /srv/memes), BACKUP_ENV_FILE, BACKUP_TABLES, PYTHON (python3), RETENTION_DAYS (14), PG_SOCKET_DIR.
set -euo pipefail
umask 077
export MSYS_NO_PATHCONV=1   # Git Bash on Windows rewrites /paths in arguments; harmless elsewhere

die() { echo "backup: $*" >&2; exit 1; }
say() { echo "backup: $*"; }

[ $# -eq 1 ] || die "usage: backup.sh <metal|general|it>"
ENV_NAME=$1
case "$ENV_NAME" in metal|general|it) ;; *) die "unknown environment '$ENV_NAME'" ;; esac
DB="memes_$ENV_NAME"
ROLE="memes_${ENV_NAME}_sync_r"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT=${MEMES_ROOT:-/srv/memes}
DOCKER=${DOCKER:-docker}
PYTHON=${PYTHON:-python3}
ENV_FILE=${BACKUP_ENV_FILE:-/etc/memes/$ENV_NAME/backup.env}
TABLES_FILE=${BACKUP_TABLES:-$HERE/backup.tables}
RETENTION_DAYS=${RETENTION_DAYS:-14}
SOCK=${PG_SOCKET_DIR:-/var/run/postgresql}
BK="$ROOT/$ENV_NAME/backups"
BUGS="$ROOT/$ENV_NAME/bug-reports"

[ -d "$BK" ] || die "$BK does not exist (run host/setup-host.sh $ENV_NAME)"
[ -f "$ENV_FILE" ] || die "$ENV_FILE not found (a file with the single line PGPASSWORD=<password of $ROLE>)"
if [ "$(uname -s)" = Linux ]; then
  mode=$(stat -c %a "$ENV_FILE"); [ "$mode" = 600 ] || [ "$mode" = 400 ] || die "$ENV_FILE must be mode 0600 (is $mode)"
fi
[ -f "$TABLES_FILE" ] || die "$TABLES_FILE not found"

TABLES=()
while IFS= read -r line; do
  case "$line" in ''|'#'*) continue ;; esac
  [[ "$line" =~ ^[a-z][a-z0-9_]*$ ]] || die "bad table name in $TABLES_FILE"
  TABLES+=("$line")
done < "$TABLES_FILE"
[ "${#TABLES[@]}" -gt 0 ] || die "no tables in $TABLES_FILE"

C=${DB_CONTAINER:-}
if [ -z "$C" ]; then
  C=$("$DOCKER" ps -q --filter "label=com.docker.compose.project=memes-$ENV_NAME" --filter "label=com.docker.compose.service=db")
fi
[ -n "$C" ] && [ "$(echo "$C" | wc -w)" = 1 ] || die "need exactly one running db container for memes-$ENV_NAME (set DB_CONTAINER to override)"

dexec() { "$DOCKER" exec -i --env-file "$ENV_FILE" "$C" "$@" < "${STDIN:-/dev/null}"; }
psqlq() { dexec psql -X -w -A -t -h "$SOCK" -U "$ROLE" -d "$DB" -c "$1"; }

# the database must be the one this environment name says (marker set by provision.sh)
marker=$(psqlq "select current_database() || ' ' || coalesce(current_setting('memes.environment', true), '')" | tr -d '\r')
[ "$marker" = "$DB $ENV_NAME" ] || die "database marker mismatch: got '$marker', want '$DB $ENV_NAME'; refusing to back up"
server_version=$(psqlq "show server_version" | tr -d '\r')

LOCK="$BK/.backup.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  pid=$(cat "$LOCK/pid" 2>/dev/null || true)
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then die "another backup is running (pid $pid)"; fi
  rm -rf "$LOCK"; mkdir "$LOCK"
fi
echo $$ > "$LOCK/pid"

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BASE="memes-$ENV_NAME-classp-$STAMP"
DUMP="$BASE.dump"; META="$BASE.meta.json"; BUG="memes-$ENV_NAME-bugreports-$STAMP.tar.gz"
cleanup() { rm -f "$BK/$DUMP.part" "$BK/$META.part" "$BK/$BUG.part" "$BK/$BASE.rows.tmp"; rm -rf "$LOCK"; }
trap cleanup EXIT

targs=()
for t in "${TABLES[@]}"; do targs+=(-t "public.$t"); done

say "dumping ${#TABLES[@]} tables of $DB as $ROLE"
# --exclude-table-data='public.*_seq': PostgreSQL 18 pulls the owned sequence (meme_impressions_id_seq) in with its table, and the read role has no
# privilege on sequences (and should not get one). Sequence positions are not needed: restore.sh sets them past the highest restored id.
dexec pg_dump -w -h "$SOCK" -U "$ROLE" -d "$DB" -Fc -a --lock-wait-timeout=60s "${targs[@]}" --exclude-table-data='public.*_seq' > "$BK/$DUMP.part"
[ -s "$BK/$DUMP.part" ] || die "empty dump"

# the dump must be readable and hold exactly the listed tables (this is the `pg_restore --list` result the manifest records)
listing=$(STDIN="$BK/$DUMP.part" dexec pg_restore --list | tr -d '\r')
for t in "${TABLES[@]}"; do
  grep -Eq "TABLE DATA public $t( |$)" <<< "$listing" || die "dump does not hold table data of $t"
done
extra=$(echo "$listing" | grep -E "TABLE DATA" | grep -Evc "TABLE DATA public ($(IFS='|'; echo "${TABLES[*]}"))( |$)" || true)
[ "$extra" = 0 ] || die "dump holds table data of $extra table(s) that are not in $TABLES_FILE"

# exact rows per table, counted from the dump itself (COPY blocks), so a restore can be checked against it
STDIN="$BK/$DUMP.part" dexec pg_restore -a -f - | tr -d '\r' | awk '
  /^COPY / { split($2, a, "."); cur = a[2]; rows[cur] = 0; next }
  /^\\\.$/ { cur = ""; next }
  cur != "" { rows[cur]++ }
  END { for (t in rows) print t, rows[t] }' > "$BK/$BASE.rows.tmp"

if [ -d "$BUGS" ]; then
  say "archiving bug reports"
  rc=0; tar -C "$ROOT/$ENV_NAME" -czf - bug-reports > "$BK/$BUG.part" || rc=$?
  [ "$rc" -le 1 ] || die "tar failed with $rc"   # 1 = a file changed while it was read: the archive is still usable
else
  BUG=""
fi

sha() { sha256sum "$1" | cut -d' ' -f1; }
size() { wc -c < "$1" | tr -d ' '; }
dump_sha=$(sha "$BK/$DUMP.part"); dump_size=$(size "$BK/$DUMP.part")
bug_sha=""; bug_size=0
if [ -n "$BUG" ]; then bug_sha=$(sha "$BK/$BUG.part"); bug_size=$(size "$BK/$BUG.part"); fi

"$PYTHON" - "$BK/$META.part" "$ENV_NAME" "$DUMP" "$dump_size" "$dump_sha" "$BUG" "$bug_size" "$bug_sha" "$server_version" "$BK/$BASE.rows.tmp" <<'PY'
import json, sys, datetime
out, env, dump, dsize, dsha, bug, bsize, bsha, ver, rows_file = sys.argv[1:]
rows = {}
for line in open(rows_file, encoding="utf-8"):
    name, n = line.split()
    rows[name] = int(n)
doc = {
    "format": 1, "environment": env, "database": "memes_" + env,
    "created_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "server_version": ver, "pg_restore_list": "ok",
    "dump": {"name": dump, "size": int(dsize), "sha256": dsha},
    "bug_reports": ({"name": bug, "size": int(bsize), "sha256": bsha} if bug else None),
    "rows": rows,
}
with open(out, "w", encoding="utf-8", newline="\n") as fh:
    json.dump(doc, fh, sort_keys=True, indent=1)
    fh.write("\n")
PY

# group access for the gate account (fetch-backup); the files stay unreadable to everybody else
final_perms() {
  chmod 0640 "$1"
  if [ "$(id -u)" = 0 ] && id "memesync-$ENV_NAME" >/dev/null 2>&1; then chown "memesync-$ENV_NAME:memesync-$ENV_NAME" "$1"; fi
}
[ -z "$BUG" ] || { final_perms "$BK/$BUG.part"; mv -f "$BK/$BUG.part" "$BK/$BUG"; }
final_perms "$BK/$META.part"; mv -f "$BK/$META.part" "$BK/$META"
final_perms "$BK/$DUMP.part"; mv -f "$BK/$DUMP.part" "$BK/$DUMP"      # last: the dump is what fetch-backup looks for
say "wrote $DUMP ($dump_size bytes, sha256 ${dump_sha:0:12}...)"

# rotation: sets older than RETENTION_DAYS go, and any .part left by a crash; the set just written is newer than both
find "$BK" -maxdepth 1 -type f \( -name "memes-$ENV_NAME-classp-*" -o -name "memes-$ENV_NAME-bugreports-*" \) -mtime "+$RETENTION_DAYS" -delete
find "$BK" -maxdepth 1 -type f -name "memes-$ENV_NAME-*.part" -mtime +1 -delete
say "done"
