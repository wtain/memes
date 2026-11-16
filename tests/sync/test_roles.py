"""Guard tests of the prod roles SQL (P5d-T1): the generated grants equal the registry exactly, nothing secret, deterministic, current.

No database. The database side (the real H3 check, privileges behaving as stated, convergence) is tests/integration/sync/test_roles_db.py.
The SQL is parsed here independently of the generator's plan (regexes over the text), so a rendering bug cannot hide behind the plan.
"""
import ast
import dataclasses
import re
import sys
from pathlib import Path

import pytest

from Storage.models import Base
from sync import registry, roles
from sync.model import Klass, Mode

ENVS = roles.ENVS
RW = {"SELECT", "INSERT", "UPDATE", "DELETE"}
ROOT = Path(__file__).resolve().parents[2]
RO_EXCLUDED = {"search_history", "search_history_tags", "meme_text_ratings"}


def committed(env):
    return (ROOT / "deploy" / "prod" / "sql" / f"roles.{env}.sql").read_bytes().decode("utf-8")


def _tables(text):
    return set(re.findall(r"[a-z][a-z0-9_]*", text))


def parse(sql):
    """role -> {table: set(privileges)} from the plain GRANT statements, column grants, sequence arrays and the MAINTAIN list."""
    table_privs, column_privs, sequences = {}, {}, {}
    for privs, tables, role in re.findall(r'GRANT\s+([A-Z, ]+?)\s+ON TABLE\s+([^%;]+?)\s+TO\s+"([^"]+)";', sql, re.S):
        for table in _tables(tables):
            table_privs.setdefault(role, {}).setdefault(table, set()).update(p.strip() for p in privs.split(","))
    for columns, table, role in re.findall(r'GRANT\s+SELECT\s+\(([^)]*)\)\s+ON TABLE\s+(\w+)\s+TO\s+"([^"]+)";', sql):
        column_privs.setdefault(role, {})[table] = {c.strip() for c in columns.split(",")}
    maintain = re.search(r"GRANT MAINTAIN ON TABLE %s TO %I',\s*\(SELECT string_agg\(quote_ident\(x\), ', '\) FROM unnest\(ARRAY\[(.*?)\]\) x\),\s*'([^']+)'", sql, re.S)
    for table in _tables(maintain.group(1)) if maintain else ():
        table_privs.setdefault(maintain.group(2), {}).setdefault(table, set()).add("MAINTAIN")
    for tables, privs, role in re.findall(
            r"FOREACH t IN ARRAY ARRAY\[(.*?)\] LOOP\s+s := .*?\s+IF s IS NULL.*?\s+EXECUTE format\('GRANT ([A-Z, ]+) ON SEQUENCE %s TO %I', s, '([^']+)'\)", sql, re.S):
        sequences[role] = (_tables(tables), {p.strip() for p in privs.split(",")})
    return table_privs, column_privs, sequences


def names_of(env):
    return roles.names_for(env)


@pytest.fixture(params=ENVS)
def env(request):
    return request.param


# ------------------------------------------------------------------------------------------ the grants equal the registry

def test_every_table_of_the_models_is_granted_exactly_as_its_class_allows(env):
    n = names_of(env)
    table_privs, column_privs, _ = parse(roles.render(n))
    models = set(Base.metadata.tables)
    assert models == set(registry.names()), "registry and models differ (test_registry owns this, but the grants depend on it)"

    def privs(role, table):
        return table_privs.get(role, {}).get(table, set())

    for table in sorted(models):
        klass = registry.get(table).klass
        want_app = {Klass.D: {"SELECT"}, Klass.P: RW, Klass.X: set()}[klass]
        want_w = {Klass.D: (RW if registry.get(table).delete_ok else RW - {"DELETE"}) | {"MAINTAIN"}, Klass.P: set(), Klass.X: set()}[klass]
        want_r = {"SELECT"} if klass is Klass.P else set()
        want_ro = set() if table in RO_EXCLUDED else {"SELECT"}
        assert privs(n.app, table) == want_app, (table, klass, "app")
        assert privs(n.sync_w, table) == want_w, (table, klass, "sync_w")
        assert privs(n.sync_r, table) == want_r, (table, klass, "sync_r")
        assert privs(n.ro, table) == want_ro, (table, klass, "ro")
        assert privs(n.owner, table) == set()
    # alembic_version: readable by the writer, the reader and ro; the app (and everyone else) gets nothing
    assert privs(n.sync_w, "alembic_version") == {"SELECT"}
    assert privs(n.sync_r, "alembic_version") == {"SELECT"}
    assert privs(n.ro, "alembic_version") == {"SELECT"}
    assert privs(n.app, "alembic_version") == set()
    # no table outside the registry and alembic_version appears in any grant
    granted = {t for per_role in table_privs.values() for t in per_role}
    assert granted <= models | {"alembic_version"}, sorted(granted - models - {"alembic_version"})


def test_the_app_write_set_is_exactly_the_six_class_p_tables_and_the_writer_write_set_the_22_class_d_tables(env):
    n = names_of(env)
    table_privs, _, _ = parse(roles.render(n))
    app_rw = {t for t, p in table_privs[n.app].items() if p & {"INSERT", "UPDATE", "DELETE"}}
    w_rw = {t for t, p in table_privs[n.sync_w].items() if p & {"INSERT", "UPDATE", "DELETE"}}
    assert app_rw == set(registry.prod_write_set("backend_app")) == set(registry.names(Klass.P)) and len(app_rw) == 6
    assert w_rw == set(registry.prod_write_set("sync_writer")) == set(registry.names(Klass.D)) and len(w_rw) == 22
    # DELETE only where the promote replaces rows per image: never images, an upsert table or a global set (review finding H1)
    w_del = {t for t, p in table_privs[n.sync_w].items() if "DELETE" in p}
    assert w_del == set(registry.prod_delete_set("sync_writer"))
    assert w_del == {e.name for e in registry.REGISTRY.values() if e.klass is Klass.D and e.mode is Mode.REPLACE_PER_IMAGE}
    assert not w_del & {"images", "concepts", "concept_images", "concept_image_sets", "concept_texts", "concept_text_sets",
                        "popularity_absorptions"}
    # nobody gets TRUNCATE, REFERENCES, TRIGGER, ALL, CREATE, TEMP, a grant option or a PUBLIC grant
    sql = roles.render(n)
    for needle in ("TRUNCATE", "REFERENCES", "TRIGGER", "GRANT ALL", "TEMPORARY", "TEMP ", "WITH GRANT OPTION", "TO PUBLIC"):
        assert not [line for line in sql.splitlines() if needle in line and not line.lstrip().startswith("--") and "has_" not in line
                    and "privs text[]" not in line and "ARRAY['SELECT', 'INSERT'" not in line], needle


def test_the_writer_has_exactly_the_three_ratings_columns_and_nothing_else_on_class_p(env):
    n = names_of(env)
    table_privs, column_privs, _ = parse(roles.render(n))
    assert column_privs[n.sync_w] == {"meme_text_ratings": {"image_id", "query_hash", "updated_at"}}
    assert column_privs[n.sync_r] == {"images": {"id"}}
    assert set(column_privs) == {n.sync_w, n.sync_r}
    for table in registry.names(Klass.P) + registry.names(Klass.X):
        assert table_privs[n.sync_w].get(table, set()) == set(), table  # the column grant is not a table grant
    assert "query_text" not in roles.render(n)  # no grant (and no mention) of the message text column


def test_sequences_the_writer_on_the_integer_id_class_d_tables_the_app_on_the_impressions_id(env):
    n = names_of(env)
    _, _, sequences = parse(roles.render(n))
    d_setval = {t for t in registry.setval_tables() if registry.get(t).klass is Klass.D}
    assert sequences[n.sync_w] == (d_setval, {"USAGE", "SELECT", "UPDATE"})
    assert d_setval == {"concepts", "concept_image_sets", "concept_images", "concept_text_sets", "concept_texts"}
    assert sequences[n.app] == ({"meme_impressions"}, {"USAGE", "SELECT"})
    assert set(sequences) == {n.sync_w, n.app}


def test_maintain_is_version_guarded_and_only_on_class_d_for_the_writer(env):
    sql = roles.render(names_of(env))
    assert "server_version_num')::int >= 170000" in sql  # PostgreSQL 17+
    table_privs, _, _ = parse(sql)
    maintain = {role: {t for t, p in per.items() if "MAINTAIN" in p} for role, per in table_privs.items()}
    n = names_of(env)
    assert maintain[n.sync_w] == set(registry.names(Klass.D))
    assert not any(tables for role, tables in maintain.items() if role != n.sync_w)


def test_the_verification_block_expects_exactly_what_the_grants_give(env):
    n = names_of(env)
    sql = roles.render(n)
    table_privs, _, _ = parse(sql)
    expected = {tuple(item.split("|")) for item in re.findall(r"'([a-z0-9_]+\|[a-z0-9_]+\|[A-Z]+)'", sql)}
    granted = {(role, table, priv) for role, per in table_privs.items() for table, privs in per.items() for priv in privs}
    assert expected == granted


def test_database_and_schema_contract_and_role_attributes(env):
    n = names_of(env)
    sql = roles.render(n)
    db = f'"{n.database}"'
    assert n.database == f"memes_{env}"
    assert n.owner == f"memes_{env}_owner" and n.app == f"memes_{env}_app"
    assert n.sync_w == f"memes_{env}_sync_w" and n.sync_r == f"memes_{env}_sync_r" and n.ro == f"memes_{env}_ro"
    assert f"IF current_database() <> '{n.database}'" in sql  # the file refuses any other database
    assert f"REVOKE ALL ON DATABASE {db} FROM PUBLIC;" in sql
    assert "REVOKE CREATE ON SCHEMA public FROM PUBLIC;" in sql
    connect = re.search(r"GRANT CONNECT ON DATABASE \S+ TO (.+?);", sql).group(1)
    assert set(re.findall(r'"([^"]+)"', connect)) == {n.app, n.sync_w, n.sync_r, n.ro}
    for role, limit in ((n.owner, 3), (n.app, 24), (n.sync_w, 3), (n.sync_r, 3), (n.ro, 2)):
        line = next(l for l in sql.splitlines() if l.strip().startswith(f'ALTER ROLE "{role}" WITH'))
        assert "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS" in line and f"CONNECTION LIMIT {limit};" in line
    for key, value in (("statement_timeout", "10s"), ("idle_in_transaction_session_timeout", "30s"), ("lock_timeout", "3s")):
        assert f"ALTER ROLE \"{n.app}\" SET {key} = '{value}';" in sql
    # default privileges are removed for the four roles, never granted
    assert "ALTER DEFAULT PRIVILEGES" in sql and "GRANT" not in "".join(
        l for l in sql.splitlines() if l.startswith("ALTER DEFAULT PRIVILEGES"))
    # another environment's names never leak into the file
    for other in ENVS:
        if other != env:
            assert f"memes_{other}" not in sql


# ------------------------------------------------------------------------------------------------- no secret, determinism

def test_no_password_or_secret_in_the_sql(env):
    sql = roles.render(names_of(env))
    code = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    assert not re.search(r"(?i)\bpassword\b", code)
    assert not re.search(r"(?i)password\s+(encrypted\s+)?'", sql)
    assert not re.search(r"(?i)(secret|scram-sha|md5[0-9a-f]{32}|\\password)", sql)
    assert "\\" not in sql  # no psql meta command, no escape


def test_the_output_is_deterministic_and_has_no_timestamp(env):
    first = roles.render(names_of(env))
    assert roles.render(names_of(env)) == first == roles.generate(env)
    assert not re.search(r"\d{4}-\d\d-\d\d[ T]\d\d:\d\d", first)
    assert "\r" not in first and first.endswith("\n") and not first.endswith("\n\n")
    header = first.splitlines()[1]
    assert f"generator version {roles.GENERATOR_VERSION}" in header and f"registry digest {roles.registry_digest()}" in header
    assert re.fullmatch(r"[0-9a-f]{16}", roles.registry_digest())


def test_the_committed_files_are_current_lf_and_byte_identical(env):
    path = ROOT / "deploy" / "prod" / "sql" / f"roles.{env}.sql"
    raw = path.read_bytes()
    assert b"\r" not in raw, "deploy/prod/** is LF only (.gitattributes)"
    assert raw.decode("utf-8") == roles.generate(env), f"{path.name} is stale: run `python -m sync.roles --env {env}`"
    assert roles.main(["--env", env, "--check"]) == 0


def test_check_mode_reports_a_stale_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(roles, "SQL_DIR", tmp_path)
    assert roles.main(["--env", "it", "--check"]) == 1  # missing file counts as stale
    assert roles.main(["--env", "it"]) == 0  # write
    assert roles.main(["--env", "it", "--check"]) == 0
    (tmp_path / "roles.it.sql").write_bytes(roles.generate("it").replace("GRANT CONNECT", "GRANT  CONNECT").encode())
    assert roles.main(["--env", "it", "--check"]) == 1
    assert "stale" in capsys.readouterr().err


def test_an_unknown_environment_and_unsafe_names_are_refused():
    with pytest.raises(ValueError):
        roles.generate("prod")
    with pytest.raises(ValueError):
        roles.render(roles.make_names('x"; drop'))
    with pytest.raises(SystemExit):
        roles.main(["--env", "staging"])


def test_roles_imports_only_the_standard_library_and_sync():
    tree = ast.parse((ROOT / "sync" / "roles.py").read_text(encoding="utf-8"))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    assert roots - (set(sys.stdlib_module_names) | {"sync"}) == set()


# ---------------------------------------------------------------------------------------------- mutation of the guard

def _mutated(monkeypatch, table, **changes):
    monkeypatch.setitem(registry.REGISTRY, table, dataclasses.replace(registry.REGISTRY[table], **changes))


def test_mutation_moving_a_table_across_classes_changes_the_sql_and_trips_the_guards(monkeypatch):
    """A table moved D -> X (and a P table to X) must change the generated SQL; the committed file then fails the currency check."""
    before = roles.generate("general")
    _mutated(monkeypatch, "tags", klass=Klass.X, prod_writer="none")
    after = roles.generate("general")
    assert after != before
    assert after != committed("general"), "the committed file would not notice a class change: --check is blind"
    n = names_of("general")
    table_privs, _, _ = parse(after)
    assert "tags" not in table_privs[n.app] and "tags" not in table_privs[n.sync_w]  # the table follows the registry
    assert table_privs[n.ro]["tags"] == {"SELECT"}  # still readable by ro (everything except the three text tables)
    # the exactness test against the registry as it was would fail on this output: the unmutated expectation is violated
    assert table_privs[n.sync_w].get("tags", set()) != RW | {"MAINTAIN"}


def test_mutation_a_p_table_leaving_the_app_write_set_is_noticed(monkeypatch):
    before = roles.generate("it")
    _mutated(monkeypatch, "search_not_found", prod_writer="none")
    after = roles.generate("it")
    assert after != before and after != committed("it")
    table_privs, _, _ = parse(after)
    assert table_privs[names_of("it").app].get("search_not_found", set()) == {"SELECT"} or "search_not_found" not in table_privs[names_of("it").app]
    assert registry.prod_write_set("backend_app") != sorted(registry.names(Klass.P))


def test_mutation_a_new_sequence_table_changes_the_sequence_grants(monkeypatch):
    before = roles.generate("metal")
    _mutated(monkeypatch, "meme_saves", setval=True)  # pretend a class P table gained a serial id
    after = roles.generate("metal")
    _, _, sequences = parse(after)
    assert sequences[names_of("metal").app][0] == {"meme_impressions", "meme_saves"} and after != before


def test_only_the_owner_gets_set_on_the_three_database_markers_and_the_grant_is_version_guarded(env):
    n = names_of(env)
    sql = roles.render(n)
    assert "server_version_num')::int >= 150000" in sql
    grants = re.findall(r"EXECUTE 'GRANT SET ON PARAMETER ([a-z., ]+) TO \"([^\"]+)\"';", sql)
    assert grants == [("memes.environment, memes.state, memes.revision", n.owner)]
    revokes = re.findall(r"EXECUTE 'REVOKE ALL ON PARAMETER ([a-z., ]+) FROM ([^']+)';", sql)
    assert len(revokes) == 1 and set(re.findall(r'"([^"]+)"', revokes[0][1])) == {n.app, n.sync_w, n.sync_r, n.ro}
