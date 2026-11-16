"""backup.sh and restore.sh against a throwaway PostgreSQL 18 container (P5d-T7).

The scripts run unchanged: `DOCKER` points at a wrapper that records every argument (so the test can prove no password is ever on a command
line) and `DB_CONTAINER` names the throwaway container instead of the compose service; `MEMES_ROOT` is a temp tree. The database is built the
way the other sync fixtures are (`Base.metadata.create_all` + the real committed `roles.it.sql`), so the six class P tables, the grants of
`memes_it_sync_r` and the owner are the real ones; the container's `pg_hba` is changed to scram on the socket, like prod's `local` lines.

Needs Docker and the image `pgvector/pgvector:pg18-trixie` (already used by dev); skips otherwise. Container name prefix `p5d-b-`, random
loopback port, removed afterwards. Never touches the three live environments or ocrdb_test.
"""
import contextlib
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.docker

ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "deploy" / "prod"
IMAGE = "pgvector/pgvector:pg18-trixie"
ENV = "it"
DB = "memes_it"
OWNER, SYNC_R = "memes_it_owner", "memes_it_sync_r"


@contextlib.contextmanager
def cursor(connect, dbname="postgres"):
    conn = connect(dbname)  # autocommit; psycopg2's own `with conn` would open a transaction (CREATE DATABASE refuses that)
    try:
        yield conn.cursor()
    finally:
        conn.close()


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=kw.pop("timeout", 120), **kw)


@pytest.fixture(scope="module")
def bash():
    path = shutil.which("bash")
    if not path:
        pytest.skip("bash not available")
    probe = _run([path, "-c", "echo $BASH_VERSION; command -v awk sha256sum tar"])
    if probe.returncode != 0 or not probe.stdout.strip():
        pytest.skip("bash without awk/sha256sum/tar")
    return path


@pytest.fixture(scope="module")
def pg(tmp_path_factory):
    psycopg2 = pytest.importorskip("psycopg2")
    if shutil.which("docker") is None or _run(["docker", "version", "--format", "{{.Server.Version}}"]).returncode != 0:
        pytest.skip("no Docker daemon")
    if _run(["docker", "image", "inspect", IMAGE]).returncode != 0:
        pytest.skip(f"image {IMAGE} not present locally")
    name = "p5d-b-pg-" + secrets.token_hex(4)
    admin_pw = secrets.token_hex(12)
    owner_pw, reader_pw = secrets.token_hex(12), secrets.token_hex(12)
    started = _run(["docker", "run", "-d", "--name", name, "-e", f"POSTGRES_PASSWORD={admin_pw}", "-p", "127.0.0.1::5432", IMAGE])
    assert started.returncode == 0, started.stderr
    try:
        port = int(_run(["docker", "port", name, "5432/tcp"]).stdout.strip().splitlines()[0].rsplit(":", 1)[1])

        def connect(dbname="postgres", user="postgres", password=admin_pw):
            conn = psycopg2.connect(host="127.0.0.1", port=port, user=user, password=password, dbname=dbname, connect_timeout=3)
            conn.autocommit = True
            return conn

        deadline = time.time() + 90
        while True:
            try:
                connect().close()
                logs = _run(["docker", "logs", name]).stderr + _run(["docker", "logs", name]).stdout
                if "init process complete" in logs and logs.count("ready to accept connections") >= 2:
                    break
            except psycopg2.OperationalError:
                pass
            assert time.time() < deadline, "postgres did not start"
            time.sleep(1)

        with cursor(connect) as cur:
            cur.execute(f"CREATE DATABASE {DB}")
        with cursor(connect, DB) as cur:
            cur.execute("CREATE EXTENSION vector")
            cur.execute("CREATE EXTENSION pg_trgm")
            roles_sql = (DEPLOY / "sql" / f"roles.{ENV}.sql").read_text(encoding="utf-8")
            cur.execute(roles_sql)  # creates the roles; no schema yet, table grants are skipped with a notice

        import sqlalchemy as sa
        from Storage.models import Base
        engine = sa.create_engine(f"postgresql+psycopg2://postgres:{admin_pw}@127.0.0.1:{port}/{DB}", connect_args={"options": f"-c role={OWNER}"})
        Base.metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(sa.text("create table alembic_version (version_num varchar(32) not null primary key)"))
            conn.execute(sa.text("insert into alembic_version values ('p5d_b_test')"))
        engine.dispose()

        with cursor(connect, DB) as cur:
            cur.execute(roles_sql)  # the grants step, now with a schema; it verifies itself and raises on any difference
            cur.execute(f"ALTER ROLE {OWNER} PASSWORD '{owner_pw}'")
            cur.execute(f"ALTER ROLE {SYNC_R} PASSWORD '{reader_pw}'")
            cur.execute(f"ALTER DATABASE {DB} SET memes.environment = '{ENV}'")
            cur.execute(f"ALTER DATABASE {DB} SET memes.state = 'serving'")
            cur.execute("select current_setting('hba_file')")
            hba = cur.fetchone()[0]
        sed = r"s/^local[[:space:]]+all[[:space:]]+all[[:space:]]+trust/local all all scram-sha-256/"
        done = _run(["docker", "exec", name, "sed", "-i", "-E", sed, hba])
        assert done.returncode == 0, done.stderr
        with cursor(connect) as cur:
            cur.execute("select pg_reload_conf()")

        work = tmp_path_factory.mktemp("p5d_b")
        yield {"name": name, "connect": connect, "owner_pw": owner_pw, "reader_pw": reader_pw, "admin_pw": admin_pw, "tmp": work}
    finally:
        _run(["docker", "rm", "-f", "-v", name])


SEED = """
insert into images (id, filename) select gen_random_uuid(), 'img' || g || '.jpg' from generate_series(1, 3) g;
insert into meme_impressions (image_id, shown_at, rank, surface)
  select (select id from images order by filename limit 1), now(), g, 'search' from generate_series(1, 5) g;
insert into meme_saves (id, image_id, created_at) select gen_random_uuid(), (select id from images order by filename limit 1), now() from generate_series(1, 3) g;
insert into meme_text_ratings (id, query_hash, image_id, grade, rank, reading, parser_version, taxonomy_version, degraded, language, rated_at, updated_at)
  select gen_random_uuid(), repeat('a', 63) || g, i.id, 2, g, 'parsed', 'v1', 1, false, 'en', now(), now()
  from generate_series(1, 2) g, (select id from images order by filename limit 1) i;
insert into search_history (id, searched_at, client, result_count, kind) select gen_random_uuid(), now(), 'web', g, 'search' from generate_series(1, 4) g;
insert into search_history_tags (id, search_id, category, value)
  select gen_random_uuid(), s.id, 'emotion', 'tag ' || g from generate_series(1, 6) g, (select id from search_history order by result_count limit 1) s;
insert into search_not_found (id, search_history_id, created_at) select gen_random_uuid(), s.id, now() from (select id from search_history order by result_count limit 2) s;
"""
def _tables():
    return [line for line in (DEPLOY / "backup.tables").read_text().splitlines() if line and not line.startswith("#")]


def counts_and_digests(pg):
    out = {}
    with cursor(pg["connect"], DB) as cur:
        for t in _tables():
            cur.execute(f"select count(*), coalesce(md5(string_agg(x::text, ',' order by x::text)), '') from {t} x")
            out[t] = cur.fetchone()
    return out


@pytest.fixture(scope="module")
def env(pg, bash):
    tmp = pg["tmp"]
    root = tmp / "srv"
    for sub in ("backups", "bug-reports"):
        (root / ENV / sub).mkdir(parents=True)
    (root / ENV / "bug-reports" / "r1.json").write_text('{"a": 1}', encoding="utf-8")
    (root / ENV / "bug-reports" / "r2.txt").write_text("second report", encoding="utf-8")
    log = tmp / "docker-argv.log"
    wrapper = tmp / "docker-wrap.sh"
    wrapper.write_text(f'#!/bin/sh\nprintf \'%s\\n\' "$*" >> "{log.as_posix()}"\nexec docker "$@"\n', encoding="utf-8", newline="\n")
    wrapper.chmod(0o755)
    (tmp / "backup.env").write_text(f"PGPASSWORD={pg['reader_pw']}\n", encoding="utf-8", newline="\n")
    (tmp / "restore.env").write_text(f"PGPASSWORD={pg['owner_pw']}\n", encoding="utf-8", newline="\n")
    for f in ("backup.env", "restore.env"):
        (tmp / f).chmod(0o600)
    with cursor(pg["connect"], DB) as cur:
        cur.execute(SEED)
    base = dict(os.environ, DOCKER=wrapper.as_posix(), DB_CONTAINER=pg["name"], MEMES_ROOT=root.as_posix(), PYTHON=Path(sys.executable).as_posix(),
                BACKUP_ENV_FILE=(tmp / "backup.env").as_posix(), RESTORE_ENV_FILE=(tmp / "restore.env").as_posix(), MSYS_NO_PATHCONV="1")
    return {"root": root, "env": base, "log": log, "backups": root / ENV / "backups", "bugs": root / ENV / "bug-reports"}


def sh(bash, script, args, env, stdin=""):
    return subprocess.run([bash, str(DEPLOY / script), *args], input=stdin, capture_output=True, text=True, env=env, timeout=300)


def test_backup_restore_roundtrip(pg, env, bash):
    before = counts_and_digests(pg)
    assert [before[t][0] for t in _tables()] == [5, 3, 2, 4, 6, 2]  # meme_impressions, meme_saves, ratings, history, tags, not_found

    # --- backup as the SELECT-only role, password only through --env-file
    b = sh(bash, "backup.sh", [ENV], env["env"])
    assert b.returncode == 0, b.stdout + b.stderr
    dumps = sorted(env["backups"].glob("memes-it-classp-*.dump"))
    assert len(dumps) == 1
    meta = json.loads((env["backups"] / (dumps[0].stem + ".meta.json")).read_text(encoding="utf-8"))
    assert meta["pg_restore_list"] == "ok" and meta["environment"] == ENV
    assert meta["dump"]["sha256"] == hashlib.sha256(dumps[0].read_bytes()).hexdigest() and meta["dump"]["size"] == dumps[0].stat().st_size
    assert meta["rows"] == {t: before[t][0] for t in _tables()}
    bug = env["backups"] / meta["bug_reports"]["name"]
    assert hashlib.sha256(bug.read_bytes()).hexdigest() == meta["bug_reports"]["sha256"]
    assert not list(env["backups"].glob("*.part")) and not (env["backups"] / ".backup.lock").exists()
    argv_log = env["log"].read_text(encoding="utf-8")
    assert argv_log.strip(), "the docker wrapper recorded nothing"
    for secret in (pg["reader_pw"], pg["owner_pw"], pg["admin_pw"]):
        assert secret not in argv_log, "a password reached a command line"
    assert "--env-file" in argv_log

    # --- the gate serves it, and the dev side judges the manifest as a healthy backup (S6)
    sys.path.insert(0, str(ROOT / "tests" / "sync"))
    from gate_helpers import Tree
    import datetime as dt
    from sync.verify import runtime
    tree_dir = pg["tmp"] / "gate"
    tree_dir.mkdir()
    tree = Tree(tree_dir, backups_dir=str(env["backups"]))
    reply = tree.run("fetch-backup --meta")
    assert reply.code == 0, reply.err
    info = reply.json()
    assert info["name"] == dumps[0].name and info["meta"]["pg_restore_list"] == "ok"
    check = runtime.check_s6(info, dt.datetime.now(dt.timezone.utc))
    assert check.status == "ok", check

    # --- restore refuses: a non-empty target, a wrong confirmation, a dump of another environment, a marker mismatch
    r = sh(bash, "restore.sh", [ENV, dumps[0].name], env["env"], stdin="restore memes_it\n")
    assert r.returncode != 0 and "not empty" in r.stderr
    with cursor(pg["connect"], DB) as cur:
        cur.execute("TRUNCATE " + ", ".join(_tables()) + " RESTART IDENTITY")
    r = sh(bash, "restore.sh", [ENV, dumps[0].name], env["env"], stdin="yes\n")
    assert r.returncode != 0 and "confirmation did not match" in r.stderr
    assert counts_and_digests(pg)["meme_saves"][0] == 0
    r = sh(bash, "restore.sh", ["general", dumps[0].as_posix()], env["env"], stdin="restore memes_general\n")
    assert r.returncode != 0 and "not a dump of general" in r.stderr
    with cursor(pg["connect"], DB) as cur:
        cur.execute(f"ALTER DATABASE {DB} SET memes.environment = 'general'")
    r = sh(bash, "restore.sh", [ENV, dumps[0].name], env["env"], stdin="restore memes_it\n")
    assert r.returncode != 0 and "marker mismatch" in r.stderr
    b2 = sh(bash, "backup.sh", [ENV], env["env"])
    assert b2.returncode != 0 and "marker mismatch" in b2.stderr
    with cursor(pg["connect"], DB) as cur:
        cur.execute(f"ALTER DATABASE {DB} SET memes.environment = '{ENV}'")

    # --- list and dry run change nothing
    ls = sh(bash, "restore.sh", [ENV, "--list"], env["env"])
    assert ls.returncode == 0 and dumps[0].name in ls.stdout
    dry = sh(bash, "restore.sh", [ENV, dumps[0].name, "--dry-run"], env["env"])
    assert dry.returncode == 0 and "restore plan" in dry.stdout and "dry run" in dry.stdout
    assert counts_and_digests(pg)["meme_saves"][0] == 0

    # --- the real restore, data and bug reports
    for f in env["bugs"].iterdir():
        f.unlink()
    r = sh(bash, "restore.sh", [ENV, dumps[0].name, "--with-bug-reports"], env["env"], stdin="restore memes_it\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert counts_and_digests(pg) == before
    assert (env["bugs"] / "r1.json").read_text(encoding="utf-8") == '{"a": 1}'
    assert (env["bugs"] / "r2.txt").read_text(encoding="utf-8") == "second report"
    with cursor(pg["connect"], DB) as cur:  # the sequence continues after the restored ids
        cur.execute("insert into meme_impressions (image_id, shown_at, rank, surface) select id, now(), 9, 'search' from images limit 1 returning id")
        assert cur.fetchone()[0] > 5
        cur.execute("delete from meme_impressions where rank = 9")

    # --- --truncate replaces the content in one transaction; a corrupted dump changes nothing
    with cursor(pg["connect"], DB) as cur:
        cur.execute("delete from meme_saves")
        cur.execute("insert into meme_saves (id, image_id, created_at) select gen_random_uuid(), id, now() from images limit 1")
    r = sh(bash, "restore.sh", [ENV, dumps[0].name, "--truncate"], env["env"], stdin="restore memes_it\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert counts_and_digests(pg) == before
    bad = env["backups"] / "memes-it-classp-20200101T000000Z.dump"
    bad.write_bytes(dumps[0].read_bytes()[:-40] + b"corrupted-tail-bytes-xxxxxxxxxxxxxxxxxx")
    r = sh(bash, "restore.sh", [ENV, bad.name, "--truncate"], env["env"], stdin="restore memes_it\n")
    assert r.returncode != 0
    assert counts_and_digests(pg) == before


def test_backup_with_wrong_password_fails_and_leaves_nothing(pg, env, bash):
    wrong = pg["tmp"] / "wrong.env"
    wrong.write_text("PGPASSWORD=not-the-password\n", encoding="utf-8", newline="\n")
    wrong.chmod(0o600)
    before = sorted(p.name for p in env["backups"].iterdir())
    r = sh(bash, "backup.sh", [ENV], dict(env["env"], BACKUP_ENV_FILE=wrong.as_posix()))
    assert r.returncode != 0
    assert sorted(p.name for p in env["backups"].iterdir()) == before


def test_rotation_removes_old_sets_only(pg, env, bash):
    old = env["backups"] / "memes-it-classp-19990101T000000Z.dump"
    old_meta = env["backups"] / "memes-it-classp-19990101T000000Z.meta.json"
    other = env["backups"] / "keep-me.txt"
    for f in (old, old_meta, other):
        f.write_bytes(b"x")
    t = time.time() - 20 * 86400
    for f in (old, old_meta, other):
        os.utime(f, (t, t))
    r = sh(bash, "backup.sh", [ENV], env["env"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert not old.exists() and not old_meta.exists() and other.exists()
    assert len(list(env["backups"].glob("memes-it-classp-2*.dump"))) >= 1
