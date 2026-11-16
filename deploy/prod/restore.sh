#!/usr/bin/env bash
# restore.sh -- restores a backup.sh dump into the EXISTING schema of one prod environment (epic environment-data-sync, P5d-T7).
#
#   restore.sh <env> --list                       the dumps in /srv/memes/<env>/backups, newest first
#   restore.sh <env> <dump> [--truncate] [--with-bug-reports] [--dry-run]
#
# Order of a disaster recovery (the schema and the images rows must exist first, the ratings table has a foreign key to images):
#   1. provision.sh / migrate: the schema is there (alembic head), 2. promote files and data from dev (the images rows), 3. this script.
# Class D tables are rebuildable by a full promote and are NOT in the dump; the dump holds the class P tables of backup.tables only.
#
# What it does: checks the database marker (database memes_<env> and memes.environment = <env>, as the owner role over the socket), that the
# dump belongs to <env> (file name, manifest, sha256), that the six tables exist and are empty (--truncate empties them in the same
# transaction), prints the plan, and only after you TYPE `restore memes_<env>` runs ONE transaction: [TRUNCATE], the dump's data, the
# sequences set past the highest id, a row count check against the dump (any difference rolls everything back). With --with-bug-reports it
# then unpacks the matching bug-reports archive (refused when the directory already holds files).
# The password comes from /etc/memes/<env>/restore.env (PGPASSWORD of memes_<env>_owner, 0600, root) through `docker exec --env-file`,
# never a command line. Overrides for tests: DOCKER, DB_CONTAINER, MEMES_ROOT, RESTORE_ENV_FILE, BACKUP_TABLES, PYTHON, PG_SOCKET_DIR, MEMES_VARLIB.
# The dump, its manifest and the bug-reports archive are first COPIED into a root-only work directory (mktemp -d under /var/lib/memes/<env>)
# and hashed, read and unpacked from the copy only: what is checked is what is used, whatever happens to backups/ in between.
set -euo pipefail
umask 077
export MSYS_NO_PATHCONV=1

die() { echo "restore: $*" >&2; exit 1; }
say() { echo "restore: $*"; }

[ $# -ge 2 ] || die "usage: restore.sh <metal|general|it> (--list | <dump> [--truncate] [--with-bug-reports] [--dry-run])"
ENV_NAME=$1; shift
case "$ENV_NAME" in metal|general|it) ;; *) die "unknown environment '$ENV_NAME'" ;; esac
DB="memes_$ENV_NAME"
ROLE="memes_${ENV_NAME}_owner"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT=${MEMES_ROOT:-/srv/memes}
DOCKER=${DOCKER:-docker}
PYTHON=${PYTHON:-python3}
ENV_FILE=${RESTORE_ENV_FILE:-/etc/memes/$ENV_NAME/restore.env}
TABLES_FILE=${BACKUP_TABLES:-$HERE/backup.tables}
SOCK=${PG_SOCKET_DIR:-/var/run/postgresql}
BK="$ROOT/$ENV_NAME/backups"
WORKBASE="${MEMES_VARLIB:-/var/lib/memes}/$ENV_NAME"
BUGS="$ROOT/$ENV_NAME/bug-reports"

jget() { "$PYTHON" -c 'import json,sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
for k in sys.argv[2].split("."):
    d = d.get(k) if isinstance(d, dict) else None
print("" if d is None else d)' "$1" "$2"; }

if [ "$1" = "--list" ]; then
  [ -d "$BK" ] || die "$BK does not exist"
  found=0
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    found=1
    meta="$BK/${f%.dump}.meta.json"
    created=""; rows=""
    if [ -f "$meta" ]; then created=$(jget "$meta" created_utc); rows=$("$PYTHON" -c 'import json,sys
print(",".join("%s=%s" % kv for kv in sorted(json.load(open(sys.argv[1], encoding="utf-8")).get("rows", {}).items())))' "$meta"); fi
    printf '%s  %s bytes  created=%s  rows=%s\n' "$f" "$(wc -c < "$BK/$f" | tr -d ' ')" "${created:-unknown(no manifest)}" "${rows:--}"
  done < <(cd "$BK" && ls -1t -- memes-"$ENV_NAME"-classp-*.dump 2>/dev/null || true)
  [ "$found" = 1 ] || say "no dumps in $BK"
  exit 0
fi

DUMP_ARG=$1; shift
TRUNCATE=0; WITH_BUGS=0; DRY=0
for a in "$@"; do
  case "$a" in --truncate) TRUNCATE=1 ;; --with-bug-reports) WITH_BUGS=1 ;; --dry-run) DRY=1 ;; *) die "unknown option '$a'" ;; esac
done

case "$DUMP_ARG" in */*) DUMP=$DUMP_ARG ;; *) DUMP="$BK/$DUMP_ARG" ;; esac
[ -f "$DUMP" ] || die "dump '$DUMP' not found"
DNAME=$(basename "$DUMP")
case "$DNAME" in memes-"$ENV_NAME"-classp-*.dump) ;; *) die "'$DNAME' is not a dump of $ENV_NAME (expected memes-$ENV_NAME-classp-*.dump); refusing" ;; esac
SRCDIR=$(dirname "$DUMP")
SRCMETA="$SRCDIR/${DNAME%.dump}.meta.json"
[ ! -L "$DUMP" ] || die "'$DNAME' is a symlink; refusing"
mkdir -p "$WORKBASE"
R=$(mktemp -d "$WORKBASE/restore.XXXXXX")   # mode 0700, root only
trap 'rm -rf "$R"' EXIT
copy_in() { # source name -> copies a regular, non-symlink file into $R
  [ -f "$1" ] && [ ! -L "$1" ] || return 1
  install -m 0600 "$1" "$R/$2"
}
copy_in "$DUMP" "$DNAME" || die "cannot copy '$DNAME' (not a regular file?)"
DUMP="$R/$DNAME"
META="$R/${DNAME%.dump}.meta.json"
if copy_in "$SRCMETA" "${DNAME%.dump}.meta.json"; then
  [ "$(jget "$META" environment)" = "$ENV_NAME" ] || die "manifest says environment '$(jget "$META" environment)', not $ENV_NAME; refusing"
  want=$(jget "$META" dump.sha256)
  have=$(sha256sum "$DUMP" | cut -d' ' -f1)
  [ "$want" = "$have" ] || die "sha256 of the dump differs from its manifest; refusing"
  say "manifest ok (created $(jget "$META" created_utc), sha256 ${have:0:12}...)"
else
  say "WARNING no manifest next to the dump; sha256 and environment cannot be checked beyond the file name"
fi

[ -f "$ENV_FILE" ] || die "$ENV_FILE not found (a file with the single line PGPASSWORD=<password of $ROLE>)"
if [ "$(uname -s)" = Linux ]; then
  mode=$(stat -c %a "$ENV_FILE"); [ "$mode" = 600 ] || [ "$mode" = 400 ] || die "$ENV_FILE must be mode 0600 (is $mode)"
fi
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

dexec() { "$DOCKER" exec -i --env-file "$ENV_FILE" "$C" "$@"; }
psqlq() { dexec psql -X -w -A -t -h "$SOCK" -U "$ROLE" -d "$DB" -c "$1" < /dev/null | tr -d '\r'; }

marker=$(psqlq "select current_database() || ' ' || coalesce(current_setting('memes.environment', true), '')")
[ "$marker" = "$DB $ENV_NAME" ] || die "database marker mismatch: got '$marker', want '$DB $ENV_NAME'; refusing to restore"
state=$(psqlq "select coalesce(current_setting('memes.state', true), '')")

# expected rows per table, counted from the dump's own COPY blocks
declare -A EXPECT=()
counted=$(dexec pg_restore -a -f - < "$DUMP" | tr -d '\r' | awk '
  /^COPY / { split($2, a, "."); cur = a[2]; rows[cur] = 0; next }
  /^\\\.$/ { cur = ""; next }
  cur != "" { rows[cur]++ }
  END { for (t in rows) print t, rows[t] }') || die "cannot read the dump (pg_restore failed); nothing changed"
[ -n "$counted" ] || die "the dump holds no table data; refusing"
while read -r t n; do EXPECT[$t]=$n; done <<< "$counted"
for t in "${EXPECT[@]+"${!EXPECT[@]}"}"; do
  found=0; for x in "${TABLES[@]}"; do [ "$x" = "$t" ] && found=1; done
  [ "$found" = 1 ] || die "the dump holds data of '$t', which is not in $TABLES_FILE; refusing"
done

plan_rows=""; nonempty=0
for t in "${TABLES[@]}"; do
  exists=$(psqlq "select to_regclass('public.$t') is not null")
  [ "$exists" = t ] || die "table $t does not exist in $DB: run the migrations first"
  cur=$(psqlq "select count(*) from public.$t")
  [ "$cur" = 0 ] || nonempty=1
  plan_rows+=$(printf '\n    %-22s now %8s  <-  dump %8s' "$t" "$cur" "${EXPECT[$t]:-0}")
done

echo "restore plan"
echo "  environment : $ENV_NAME (database $DB, memes.state '$state', container ${C:0:12})"
echo "  dump        : $DNAME"
echo "  tables      :$plan_rows"
echo "  mode        : $([ "$TRUNCATE" = 1 ] && echo 'TRUNCATE the tables above first, same transaction' || echo 'insert into empty tables only')"
echo "  bug reports : $([ "$WITH_BUGS" = 1 ] && echo "unpack the matching archive into $BUGS" || echo 'not touched')"
if [ "$nonempty" = 1 ] && [ "$TRUNCATE" = 0 ]; then die "tables are not empty; use --truncate to replace their content (this deletes rows)"; fi

BUGTAR=""
if [ "$WITH_BUGS" = 1 ]; then
  [ -f "$META" ] || die "--with-bug-reports needs the manifest"
  bn=$(jget "$META" bug_reports.name); [ -n "$bn" ] || die "this backup has no bug-reports archive"
  case "$bn" in */*|.*|'') die "manifest names a bad archive '$bn'" ;; esac
  copy_in "$SRCDIR/$bn" "$bn" || die "archive $bn not found next to the dump (or not a regular file)"
  BUGTAR="$R/$bn"
  [ "$(sha256sum "$BUGTAR" | cut -d' ' -f1)" = "$(jget "$META" bug_reports.sha256)" ] || die "sha256 of $bn differs from the manifest"
  [ -d "$BUGS" ] || die "$BUGS does not exist (run host/setup-host.sh $ENV_NAME)"
  [ -z "$(ls -A "$BUGS")" ] || die "$BUGS is not empty; refusing to unpack over it"
fi
[ "$DRY" = 0 ] || { say "dry run: nothing changed"; exit 0; }

phrase="restore $DB"
printf 'Type "%s" to continue: ' "$phrase"
IFS= read -r answer || die "no confirmation given"
answer=${answer%$'\r'}   # a Windows terminal sends CRLF
[ "$answer" = "$phrase" ] || die "confirmation did not match; nothing changed"

tbl_list=$(printf 'public.%s, ' "${TABLES[@]}"); tbl_list=${tbl_list%, }
arr=$(printf "'%s'," "${TABLES[@]}"); arr=${arr%,}
checks=""
for t in "${TABLES[@]}"; do
  checks+="IF (SELECT count(*) FROM public.$t) <> ${EXPECT[$t]:-0} THEN RAISE EXCEPTION 'row count of $t differs from the dump'; END IF;"$'\n'
done
post="DO \$restore\$ DECLARE t text; s text; BEGIN
  FOREACH t IN ARRAY ARRAY[$arr] LOOP
    s := pg_get_serial_sequence('public.' || t, 'id');
    IF s IS NOT NULL THEN
      EXECUTE format('SELECT setval(%L, GREATEST(COALESCE((SELECT max(id) FROM public.%I), 0), 1), (SELECT max(id) IS NOT NULL FROM public.%I))', s, t, t);
    END IF;
  END LOOP;
$checks END \$restore\$;"

say "restoring (one transaction)"
{
  echo "BEGIN;"
  [ "$TRUNCATE" = 0 ] || echo "TRUNCATE TABLE $tbl_list RESTART IDENTITY;"
  dexec pg_restore -a -f - < "$DUMP"
  echo "$post"
  echo "COMMIT;"
} | dexec psql -X -w -q -v ON_ERROR_STOP=1 -h "$SOCK" -U "$ROLE" -d "$DB" -f - > /dev/null
say "database restored and checked against the dump"

if [ -n "$BUGTAR" ]; then
  tar --no-same-owner --no-overwrite-dir -xzf - -C "$ROOT/$ENV_NAME" < "$BUGTAR"
  say "bug reports unpacked into $BUGS"
fi
say "done"
