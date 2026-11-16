"""`python -m sync.release` (P5d-T8) against a THROWAWAY pgvector container: the real migration chain, the committed roles file, verify-grants.

Why a container and not the ocrdb_test two-schema fixture: the owner/roles model needs its own database (roles.<env>.sql refuses any
other database name, the alembic chain writes public.alembic_version and the markers are database settings), and ocrdb_test is
shared. This test never touches ocrdb_test or any live database.

  * container `p5d-c-<8 hex>` from the pinned pgvector image (the digest of deploy/prod/compose.yaml), published on a random loopback port
    only, random throwaway superuser password, removed in `finally`; the test is skipped when docker is not usable;
  * the database `memes_it` is provisioned the way provision.sh does it (extensions and markers as the superuser, roles.it.sql as the
    superuser), then the release tool runs as the owner role.
No password is in the repository: the passwords are random per run and live in this process and the container only.
"""
from __future__ import annotations

import re
import secrets
import shutil
import subprocess
import time
from pathlib import Path

import pytest
import yaml

from sync import registry, release, roles

ROOT = Path(__file__).resolve().parents[3]
ENV = "it"
NAMES = roles.names_for(ENV)


def _image() -> str:
    compose = yaml.safe_load((ROOT / "deploy" / "prod" / "compose.yaml").read_text(encoding="utf-8"))
    return compose["services"]["db"]["image"]


def _docker(*args, input_text=None, check=True):
    return subprocess.run(["docker", *args], input=input_text, capture_output=True, text=True, check=check, timeout=240)


@pytest.fixture(scope="module")
def server():
    if shutil.which("docker") is None or _docker("info", check=False).returncode != 0:
        pytest.skip("docker is not usable")
    import psycopg2

    name = f"p5d-c-{secrets.token_hex(4)}"
    superpw, ownerpw = secrets.token_hex(12), secrets.token_hex(12)
    try:
        _docker("run", "-d", "--name", name, "-e", f"POSTGRES_PASSWORD={superpw}", "-p", "127.0.0.1::5432", _image())
        port = re.search(r":(\d+)\s*$", _docker("port", name, "5432/tcp").stdout.splitlines()[0]).group(1)
        for _ in range(60):  # the entrypoint starts a temporary server first; wait for the final one over TCP
            try:
                psycopg2.connect(host="127.0.0.1", port=int(port), user="postgres", password=superpw, dbname="postgres", connect_timeout=2).close()
                break
            except psycopg2.OperationalError:
                time.sleep(1)
        else:
            pytest.skip("the throwaway postgres did not come up")

        def psql(*sql, db="postgres", file_text=None):
            cmd = ["exec", "-i", "-u", "postgres", name, "psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-d", db]
            for s in sql:
                cmd += ["-c", s]
            return _docker(*cmd, input_text=file_text)

        psql(f'create database "{NAMES.database}"')
        psql("create extension vector", "create extension pg_trgm", f"alter database \"{NAMES.database}\" set memes.environment = '{ENV}'",
             f"alter database \"{NAMES.database}\" set memes.state = 'seeding'", db=NAMES.database)
        psql(db=NAMES.database, file_text=release.roles_file(ENV).read_text(encoding="utf-8"))  # creates the five roles (no schema yet)
        psql(f"alter role \"{NAMES.owner}\" password '{ownerpw}'", f"alter role \"{NAMES.sync_w}\" password '{ownerpw}'")
        yield {"name": name, "port": port, "owner_pw": ownerpw, "psql": psql,
               "url": f"postgresql://{NAMES.owner}:{ownerpw}@127.0.0.1:{port}/{NAMES.database}"}
    finally:
        _docker("rm", "-f", "-v", name, check=False)
        assert name not in _docker("ps", "-a", "--format", "{{.Names}}", check=False).stdout


@pytest.fixture()
def env(server, monkeypatch):
    monkeypatch.setenv("APP_ENV", ENV)
    monkeypatch.setenv("DATABASE_URL", server["url"])
    monkeypatch.delenv("APP_STAGE", raising=False)
    return server


def as_owner(server, sql):
    """DDL as the owner role (a table made by the superuser would not be the owner's to revoke on)."""
    import psycopg2

    conn = psycopg2.connect(server["url"])
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
    finally:
        conn.close()


def test_the_chain_runs_from_empty_and_the_registry_classifies_every_table(env, capsys):
    assert release.main(["migrate"]) == release.EXIT_OK
    import psycopg2

    conn = psycopg2.connect(env["url"])
    try:
        with conn.cursor() as cur:
            cur.execute("select version_num from public.alembic_version")
            assert [r[0] for r in cur.fetchall()] == [release.code_head()]
            cur.execute("select relname from pg_class where relnamespace = 'public'::regnamespace and relkind in ('r', 'p')")
            tables = {r[0] for r in cur.fetchall()}
    finally:
        conn.close()
    # the registry covers every table the chain creates, and nothing in it is absent from the chain
    assert tables == set(registry.names()) | {"alembic_version"}
    assert env["owner_pw"] not in capsys.readouterr().out


def test_grants_then_verify_is_clean_and_idempotent(env):
    assert release.main(["grants"]) == release.EXIT_OK
    assert release.main(["verify-grants"]) == release.EXIT_OK
    assert release.main(["all"]) == release.EXIT_OK  # a second full run changes nothing


def test_mutation_a_revoked_grant_fails_verify_and_grants_repairs_it(env, capsys):
    assert release.main(["verify-grants"]) == release.EXIT_OK
    as_owner(env, f'revoke insert on meme_impressions from "{NAMES.app}"')
    capsys.readouterr()
    assert release.main(["verify-grants"]) == release.EXIT_MISMATCH
    err = capsys.readouterr().err
    assert "missing" in err and NAMES.app in err and "meme_impressions" in err and "INSERT" in err
    assert env["owner_pw"] not in err and "postgresql://" not in err
    assert release.main(["all"]) == release.EXIT_OK  # the grants step re-applies, verification passes


def test_mutation_an_extra_grant_and_an_extra_table_fail_verify(env, capsys):
    as_owner(env, f'grant delete on images to "{NAMES.sync_r}"')
    assert release.main(["verify-grants"]) == release.EXIT_MISMATCH
    assert "extra" in capsys.readouterr().err
    assert release.main(["grants"]) == release.EXIT_OK
    as_owner(env, "create table shiny_new_table(a int)")
    try:
        assert release.main(["verify-grants"]) == release.EXIT_MISMATCH
        err = capsys.readouterr().err
        assert "unclassified table" in err and "shiny_new_table" in err
    finally:
        as_owner(env, "drop table shiny_new_table")
    assert release.main(["verify-grants"]) == release.EXIT_OK


def test_a_wrong_environment_marker_and_a_non_owner_are_refused(env, monkeypatch, capsys):
    # APP_ENV that does not equal the marker (and whose names do not fit the database): refused before anything runs
    monkeypatch.setenv("APP_ENV", "metal")
    assert release.main(["migrate"]) == release.EXIT_REFUSED
    monkeypatch.setenv("APP_ENV", ENV)
    monkeypatch.setenv("DATABASE_URL", env["url"].replace(NAMES.owner, NAMES.sync_w, 1))
    assert release.main(["migrate"]) == release.EXIT_REFUSED
    assert "owner role" in capsys.readouterr().err
