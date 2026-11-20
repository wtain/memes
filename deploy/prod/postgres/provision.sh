#!/usr/bin/env bash
# Provisioning of the production Postgres of one environment (design: P5d, "Postgres per environment" and the P5d-T1/T2
# findings). Run on the VPS, from any directory, as a user who may run docker. Order (each step is idempotent):
#
#   1. provision.sh render <env> <outdir>   write postgresql.conf, the rendered pg_hba.conf and pg_ident.conf to <outdir> (the
#                                           PGCONF_DIR of the env file). Needs no docker; run BEFORE the first `compose up db`.
#   2. docker compose -p memes-<env> --env-file env/<env>.prod.env up -d db        (initdb runs with these files)
#   3. provision.sh apply <env>             database -> extensions (bootstrap superuser) -> roles.<env>.sql -> the stats-only
#                                           monitor role memes_<env>_mon (monitor_role.sql.template; no password, peer only) ->
#                                           passwords -> markers. memes.state stays 'seeding'.
#   4. the first migration: NOT here, it is the `migrate` service (release runbook, P5d-T8).
#   5. provision.sh grants <env>            roles.<env>.sql and the monitor role again (the table grants need the schema), then
#                                           verifies both.
#   6. by hand, after the first seed and the HNSW rebuild: ALTER DATABASE memes_<env> SET memes.state = 'serving'.
#
# Passwords are never written to a file and never appear in an argument list: `apply` takes them from the environment
# variables MEMES_PW_OWNER, MEMES_PW_APP, MEMES_PW_SYNC_W, MEMES_PW_SYNC_R, MEMES_PW_RO (or asks on the terminal) and feeds
# them to psql on standard input, as `\set pw '<value>'` followed by `ALTER ROLE ... PASSWORD :'pw'`. A password must match
# [A-Za-z0-9+/=_.~-]{24,} (for example `openssl rand -base64 33`). The bootstrap superuser has no password use at all
# (peer over the socket via `docker exec -u postgres`; over TCP it is rejected).
#
# Test hook: MEMES_DB_CONTAINER names the container to use instead of the compose project's `db` service.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SQL_DIR="$HERE/../sql"

usage() {
    echo "usage: provision.sh render <env> <outdir> | apply <env> | grants <env> | prune-logs <env>   (env: metal general it)" >&2
    exit 2
}

subnet_of() {
    case "$1" in
        metal) echo "172.31.1.0/24" ;;
        general) echo "172.31.2.0/24" ;;
        it) echo "172.31.3.0/24" ;;
        *) echo "unknown environment: $1" >&2; exit 2 ;;
    esac
}

container_of() {
    local env="$1" id
    if [ -n "${MEMES_DB_CONTAINER:-}" ]; then
        echo "$MEMES_DB_CONTAINER"
        return
    fi
    id="$(docker ps -q --filter "label=com.docker.compose.project=memes-$env" --filter "label=com.docker.compose.service=db")"
    if [ -z "$id" ] || [ "$(printf '%s\n' "$id" | wc -l)" -ne 1 ]; then
        echo "expected exactly one running db container of project memes-$env" >&2
        exit 1
    fi
    echo "$id"
}

# psql as the bootstrap superuser over the socket of the container; SQL on standard input.
psql_su() {
    local container="$1" db="$2"
    docker exec -i -u postgres "$container" psql -X -q -v ON_ERROR_STOP=1 -d "$db"
}

# The stats-only role of the metrics exporter: pg_monitor + CONNECT, no password; the SQL verifies itself and rolls back on a mismatch.
apply_monitor_role() {
    local env="$1" container="$2"
    sed -e "s|@ENV@|$env|g" "$HERE/monitor_role.sql.template" | psql_su "$container" "memes_$env"
}

cmd_render() {
    local env="$1" outdir="$2" subnet
    subnet="$(subnet_of "$env")"
    mkdir -p "$outdir"
    sed -e "s|@ENV@|$env|g" -e "s|@SUBNET@|$subnet|g" "$HERE/pg_hba.conf.template" >"$outdir/pg_hba.conf"
    sed -e "s|@ENV@|$env|g" "$HERE/pg_ident.conf.template" >"$outdir/pg_ident.conf"
    cp "$HERE/postgresql.conf" "$outdir/postgresql.conf"
    chmod 644 "$outdir/pg_hba.conf" "$outdir/pg_ident.conf" "$outdir/postgresql.conf"
    if grep -q '@' "$outdir/pg_hba.conf" "$outdir/pg_ident.conf"; then
        echo "unrendered placeholder left in $outdir/pg_hba.conf or pg_ident.conf" >&2
        exit 1
    fi
    echo "rendered $outdir/pg_hba.conf, $outdir/pg_ident.conf and $outdir/postgresql.conf for $env"
}

read_password() {
    local var="$1" label="$2" value
    value="${!var:-}"
    if [ -z "$value" ]; then
        if [ -t 0 ]; then
            read -rsp "password for $label: " value
            echo >&2
        else
            echo "set $var (no terminal to ask on)" >&2
            exit 1
        fi
    fi
    # grep matches line by line: a value with an embedded newline would pass a grep check on its first line and then write the rest
    # into the psql input. Refuse any newline or carriage return first, then match the WHOLE value.
    case "$value" in *$'\n'*|*$'\r'*) echo "password of $label must not contain a newline" >&2; exit 1 ;; esac
    if ! [[ "$value" =~ ^[A-Za-z0-9+/=_.~-]{24,}$ ]]; then
        echo "password of $label must match [A-Za-z0-9+/=_.~-]{24,}" >&2
        exit 1
    fi
    printf '%s' "$value"
}

set_password() {
    local container="$1" role="$2" password="$3"
    # \set pw '<value>' then the statement, all on stdin: no argument list, no file.
    { printf "\\\\set pw '%s'\n" "$password"; printf 'ALTER ROLE "%s" PASSWORD :'"'"'pw'"'"';\n' "$role"; } | psql_su "$container" postgres
}

cmd_apply() {
    local env="$1" container db prefix file kind role state
    local -A pw=()
    db="memes_$env"
    prefix="memes_${env}"
    file="$SQL_DIR/roles.$env.sql"
    [ -f "$file" ] || { echo "missing $file" >&2; exit 1; }
    container="$(container_of "$env")"

    # Collect and validate all passwords first: nothing is changed when one is missing or malformed.
    for kind in owner app sync_w sync_r ro; do
        local upper
        upper="$(printf '%s' "$kind" | tr '[:lower:]' '[:upper:]')"
        pw[$kind]="$(read_password "MEMES_PW_$upper" "${prefix}_$kind")"
    done

    echo "1/5 database $db"
    if [ "$(printf "SELECT 1 FROM pg_database WHERE datname = '%s'\n" "$db" | docker exec -i -u postgres "$container" psql -X -q -At -d postgres)" != "1" ]; then
        printf 'CREATE DATABASE "%s" TEMPLATE template0 ENCODING '"'"'UTF8'"'"';\n' "$db" | psql_su "$container" postgres
    fi

    echo "2/5 extensions (bootstrap superuser: vector is not a trusted extension)"
    printf 'CREATE EXTENSION IF NOT EXISTS vector;\nCREATE EXTENSION IF NOT EXISTS pg_trgm;\n' | psql_su "$container" "$db"

    echo "3/5 roles and grants ($file)"
    psql_su "$container" "$db" <"$file"
    apply_monitor_role "$env" "$container"

    echo "4/5 passwords"
    for kind in owner app sync_w sync_r ro; do
        role="${prefix}_$kind"
        set_password "$container" "$role" "${pw[$kind]}"
    done

    echo "5/5 markers (memes.state is only set when absent: a database already serving is not sent back to seeding)"
    printf 'ALTER DATABASE "%s" SET memes.environment = '"'"'%s'"'"';
' "$db" "$env" | psql_su "$container" postgres
    state="$(printf "SELECT c FROM pg_db_role_setting s JOIN pg_database d ON d.oid = s.setdatabase, unnest(s.setconfig) c WHERE d.datname = '%s' AND s.setrole = 0 AND c LIKE 'memes.state=%%'
" "$db" | docker exec -i -u postgres "$container" psql -X -q -At -d postgres)"
    if [ -z "$state" ]; then
        printf 'ALTER DATABASE "%s" SET memes.state = '"'"'seeding'"'"';
' "$db" | psql_su "$container" postgres
    fi
    echo "done. Next: the migrate service, then: provision.sh grants $env"
}

cmd_grants() {
    local env="$1" container db file
    db="memes_$env"
    file="$SQL_DIR/roles.$env.sql"
    container="$(container_of "$env")"
    echo "re-applying $file (grants and verification block)"
    psql_su "$container" "$db" <"$file"
    apply_monitor_role "$env" "$container"
    echo "grants applied and verified"
}

[ "$#" -ge 2 ] || usage
sub="$1"
env="$2"
subnet_of "$env" >/dev/null
case "$sub" in
    render) [ "$#" -eq 3 ] || usage; cmd_render "$env" "$3" ;;
    apply) [ "$#" -eq 2 ] || usage; cmd_apply "$env" ;;
    grants) [ "$#" -eq 2 ] || usage; cmd_grants "$env" ;;
    # keep the Postgres log directory within its bound (newest files, total bytes; see prune_logs.py); needs no docker
    prune-logs) [ "$#" -eq 2 ] || usage; python3 "$HERE/prune_logs.py" "/srv/memes/$env/logs/postgres" ;;
    *) usage ;;
esac
