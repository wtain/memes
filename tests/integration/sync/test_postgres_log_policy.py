"""The shipped Postgres configuration on a THROWAWAY container (task O6c, review focus 4): the log policy keeps values out of the log files and
the exporter's peer line admits only the OS user nobody as the stats-only role.

The container `p5d-o6c-<8 hex>` runs the pinned pgvector image with the REAL deploy/prod/postgres/postgresql.conf and the pg_hba.conf and
pg_ident.conf rendered from the real templates (only the TCP subnet is widened to 0.0.0.0/0 so the test client can reach it through the
docker port forward). The log directory is a tmpfs owned by the postgres uid, like the host directory of the compose mount. Published on a
random loopback port, random throwaway password, removed in `finally`; skipped when docker is not usable. Never touches ocrdb_test or a
live database.
"""
from __future__ import annotations

import asyncio
import re
import secrets
import shutil
import subprocess
import time
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
PG = ROOT / "deploy" / "prod" / "postgres"
ENV = "it"
DB, APP, MON = f"memes_{ENV}", f"memes_{ENV}_app", f"memes_{ENV}_mon"


def _image() -> str:
    return yaml.safe_load((ROOT / "deploy" / "prod" / "compose.yaml").read_text(encoding="utf-8"))["services"]["db"]["image"]


def _docker(*args, input_text=None, check=True):
    return subprocess.run(["docker", *args], input=input_text, capture_output=True, text=True, check=check, timeout=240)


def _logs(name: str) -> str:
    return _docker("exec", name, "sh", "-c", "cat /var/log/postgresql/*.log 2>/dev/null; true", check=False).stdout


@pytest.fixture(scope="module")
def pg(tmp_path_factory):
    if shutil.which("docker") is None or _docker("info", check=False).returncode != 0:
        pytest.skip("docker is not usable")
    name = f"p5d-o6c-{secrets.token_hex(4)}"
    apppw = secrets.token_hex(12)
    conf = tmp_path_factory.mktemp("pgconf") / "memes"
    conf.mkdir()
    (conf / "postgresql.conf").write_bytes((PG / "postgresql.conf").read_bytes())
    hba = (PG / "pg_hba.conf.template").read_text(encoding="utf-8").replace("@ENV@", ENV).replace("@SUBNET@", "0.0.0.0/0")
    (conf / "pg_hba.conf").write_text(hba, encoding="utf-8", newline="\n")
    ident = (PG / "pg_ident.conf.template").read_text(encoding="utf-8").replace("@ENV@", ENV)
    (conf / "pg_ident.conf").write_text(ident, encoding="utf-8", newline="\n")
    try:
        _docker("create", "--name", name, "-e", f"POSTGRES_PASSWORD={secrets.token_hex(12)}", "--shm-size", "256m",
                "--tmpfs", "/var/log/postgresql:rw,uid=999,gid=999,mode=0755", "-p", "127.0.0.1::5432", _image(),
                "postgres", "-c", "config_file=/etc/memes/postgresql.conf")
        _docker("cp", str(conf), f"{name}:/etc/memes")
        _docker("start", name)
        port = None
        for _ in range(90):  # the final server (the one with logging_collector on) is up once its first log file exists
            if "postgresql-" in _docker("exec", name, "sh", "-c", "ls /var/log/postgresql", check=False).stdout and _docker(
                    "exec", "-u", "postgres", name, "pg_isready", "-q", "-h", "/var/run/postgresql", check=False).returncode == 0:
                port = re.search(r":(\d+)\s*$", _docker("port", name, "5432/tcp").stdout.splitlines()[0]).group(1)
                break
            time.sleep(1)
        if port is None:
            pytest.skip("the throwaway postgres did not come up: " + _docker("logs", name, check=False).stderr[-300:])

        def psql(*sql, db="postgres", user="postgres", host=None, file_text=None):
            cmd = ["exec", "-i", "-u", user, name, "psql", "-X", "-q", "-At", "-v", "ON_ERROR_STOP=1", "-d", db]
            if host:
                cmd += ["-h", host, "-U", MON if user == "nobody" else user]
            for s in sql:
                cmd += ["-c", s]
            return _docker(*cmd, input_text=file_text)

        psql(f'create database "{DB}"')
        # the five roles of the contract (the roles file also revokes PUBLIC's TEMP on the database), then the monitor role, as provision.sh does
        psql(db=DB, file_text=(ROOT / "deploy" / "prod" / "sql" / f"roles.{ENV}.sql").read_text(encoding="utf-8"))
        psql(f"alter role \"{APP}\" password '{apppw}'")
        psql("create table t (v text primary key, n int)", f'grant select, insert, update on t to "{APP}"', db=DB)
        psql(db=DB, file_text=(PG / "monitor_role.sql.template").read_text(encoding="utf-8").replace("@ENV@", ENV))
        yield {"name": name, "port": int(port), "app_pw": apppw, "psql": psql}
    finally:
        _docker("rm", "-f", "-v", name, check=False)
        assert name not in _docker("ps", "-a", "--format", "{{.Names}}", check=False).stdout


def test_the_exporter_os_user_connects_as_the_monitor_role_over_the_socket_and_nobody_else_does(pg):
    out = pg["psql"]("select current_user", "select count(*) from pg_stat_database", db=DB, user="nobody", host="/var/run/postgresql").stdout
    assert out.splitlines()[0] == MON
    # the stats views work, user rows do not
    with pytest.raises(subprocess.CalledProcessError) as denied:
        pg["psql"]("select count(*) from t", db=DB, user="nobody", host="/var/run/postgresql")
    assert "permission denied" in denied.value.stderr
    # another OS user cannot act as the monitor role, and nobody cannot act as the app role (no local line for it)
    with pytest.raises(subprocess.CalledProcessError):
        _docker("exec", "-u", "postgres", pg["name"], "psql", "-X", "-h", "/var/run/postgresql", "-U", MON, "-d", DB, "-c", "select 1")
    with pytest.raises(subprocess.CalledProcessError):
        _docker("exec", "-u", "nobody", pg["name"], "psql", "-X", "-h", "/var/run/postgresql", "-U", APP, "-d", DB, "-c", "select 1")


def _run_app_statements(pg, held: str, blocked: str, dup: str):
    import asyncpg

    async def go():
        conn = await asyncpg.connect(host="127.0.0.1", port=pg["port"], user=APP, password=pg["app_pw"], database=DB)
        other = await asyncpg.connect(host="127.0.0.1", port=pg["port"], user=APP, password=pg["app_pw"], database=DB)
        try:
            # 1. a failing parameterised query: unique violation, the key goes over the wire as a bound parameter ($1)
            await conn.execute("insert into t (v, n) values ($1, 1)", dup)
            with pytest.raises(asyncpg.UniqueViolationError):
                await conn.execute("insert into t (v, n) values ($1, 1)", dup)
            # 2. a lock wait (log_lock_waits is on, deadlock_timeout 1s): its DETAIL lists the blocking process's statement
            await conn.execute("insert into t (v, n) values ($1, 1)", held)
            tx = conn.transaction()
            await tx.start()
            await conn.execute(f"update t set n = 2 where v = '{held}'")
            task = asyncio.ensure_future(other.execute("update t set n = 3 where v = $1", held))
            await asyncio.sleep(2.5)
            await tx.commit()
            await task
            # 3. a failing statement with the value as a literal: the STATEMENT line must stay out
            with pytest.raises(asyncpg.PostgresError):
                await conn.execute(f"insert into no_such_table values ('{blocked}')")
        finally:
            await conn.close()
            await other.close()

    asyncio.run(go())


def test_values_of_failed_and_blocked_statements_stay_out_of_the_postgres_log_files(pg):
    tag = secrets.token_hex(6)
    dup, held, blocked = f"DUPKEY-{tag}", f"HELDKEY-{tag}", f"LITERAL-{tag}"
    _run_app_statements(pg, held, blocked, dup)
    # control: the same failure under default verbosity and an error statement threshold DOES log the value, so the assertion below can fail
    ctl = f"CONTROL-{tag}"
    pg["psql"]("insert into t (v, n) values ('%s', 1)" % ctl, db=DB)
    with pytest.raises(subprocess.CalledProcessError):
        pg["psql"]("set log_error_verbosity = default", "set log_min_error_statement = error",
                   "insert into t (v, n) values ('%s', 1)" % ctl, db=DB)
    text = ""
    for _ in range(30):  # the logging collector writes asynchronously
        text = _logs(pg["name"])
        if "still waiting" in text and ctl in text and "no_such_table" in text:
            break
        time.sleep(0.5)
    assert ctl in text, "the control (default verbosity) did not log the value: the test cannot detect a leak\n" + text[-800:]
    scrubbed = "\n".join(line for line in text.splitlines() if ctl not in line)
    assert "duplicate key value violates unique constraint" in scrubbed, "the unique violation was not logged at all"
    assert "still waiting" in scrubbed, "the lock wait was not logged (log_lock_waits)"
    for value in (dup, held, blocked):
        assert value not in scrubbed, f"{value} leaked into the Postgres log"
    for marker in ("STATEMENT:", "DETAIL:", "CONTEXT:", "connection authorized", "connection received", "execute <unnamed>"):
        assert marker not in scrubbed, marker
    files = _docker("exec", pg["name"], "sh", "-c", "ls /var/log/postgresql").stdout.split()
    assert files and all(re.fullmatch(r"postgresql-\d{8}-\d{6}\.log", f) for f in files), files
