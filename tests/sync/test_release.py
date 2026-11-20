"""Release tool (sync/release.py, P5d-T8): the pure planning and diff logic, the refusals, and the guards of the migrate image. No database."""
import re
from pathlib import Path

import pytest

from sync import registry, release, roles

ROOT = Path(__file__).resolve().parents[2]
ENVS = roles.ENVS


def actual_for(env, version=180003, *, tables=None, extra_tables=()):
    """An Actual that matches the registry exactly (what a correctly granted database reports)."""
    names = roles.names_for(env)
    exp = roles.expected(names)
    table = set(exp.table) | (set(exp.maintain) if release.can_maintain(version) else set())
    return release.Actual(
        roles=frozenset(names.roles),
        tables=frozenset(tables if tables is not None else {*registry.names(), "alembic_version", *extra_tables}),
        table=frozenset(table),
        column=frozenset(exp.column),
        sequence=frozenset(exp.sequence),
        server_version_num=version,
    )


def replace(actual, **changes):
    fields = {f: getattr(actual, f) for f in actual.__dataclass_fields__}
    fields.update(changes)
    return release.Actual(**fields)


# ----------------------------------------------------------------------------------------------------------------- diff

@pytest.mark.parametrize("env", ENVS)
@pytest.mark.parametrize("version", [160000, 180003])
def test_a_database_that_matches_the_registry_has_no_mismatch(env, version):
    assert release.diff(env, actual_for(env, version)) == []


def test_the_expected_grants_are_not_empty_and_cover_every_role():
    names = roles.names_for("metal")
    exp = roles.expected(names)
    assert {i[0] for i in exp.table} == set(names.grantees)
    assert exp.maintain and exp.column and exp.sequence


def test_mutation_a_missing_table_grant_is_reported():
    names = roles.names_for("general")
    exp = roles.expected(names)
    victim = next(i for i in exp.table if i[0] == names.app and i[2] == "INSERT")
    act = actual_for("general")
    act = replace(act, table=act.table - {victim})
    found = release.diff("general", act)
    assert found == [release.Mismatch("missing", victim[0], victim[1], "INSERT")]
    assert release.exit_code_for(found) == release.EXIT_MISMATCH


def test_mutation_an_extra_table_grant_is_reported():
    names = roles.names_for("general")
    act = actual_for("general")
    act = replace(act, table=act.table | {(names.sync_r, "images", "SELECT")})  # sync_r reads class P only
    assert release.diff("general", act) == [release.Mismatch("extra", names.sync_r, "images", "SELECT")]


def test_mutation_a_public_style_write_by_the_app_on_a_class_d_table_is_reported():
    names = roles.names_for("metal")
    class_d = registry.names(registry.Klass.D)[0]
    act = actual_for("metal")
    act = replace(act, table=act.table | {(names.app, class_d, "DELETE")})
    found = release.diff("metal", act)
    assert [m.kind for m in found] == ["extra"] and found[0].object == class_d


def test_mutation_a_delete_grant_on_images_for_the_writer_is_reported():
    names = roles.names_for("general")
    act = replace(actual_for("general"), table=actual_for("general").table | {(names.sync_w, "images", "DELETE")})
    assert release.diff("general", act) == [release.Mismatch("extra", names.sync_w, "images", "DELETE")]


def test_mutation_an_extra_table_is_reported_as_unclassified():
    found = release.diff("it", actual_for("it", extra_tables=("shiny_new_table",)))
    assert found == [release.Mismatch("unclassified table", "-", "shiny_new_table", "-")]


def test_a_registry_table_missing_from_the_database_is_reported_once():
    victim = registry.names(registry.Klass.D)[0]
    tables = {*registry.names(), "alembic_version"} - {victim}
    found = release.diff("it", actual_for("it", tables=tables))
    # the table is reported once; its grants are not repeated as one missing line per role and privilege
    assert [m for m in found if m.kind == "missing table"] == [release.Mismatch("missing table", "-", victim, "-")]
    assert not [m for m in found if m.object == victim and m.kind == "missing"]


def test_a_missing_role_is_reported_once():
    names = roles.names_for("it")
    act = actual_for("it")
    act = replace(act, roles=act.roles - {names.ro}, table=frozenset(i for i in act.table if i[0] != names.ro))
    assert release.diff("it", act) == [release.Mismatch("missing role", names.ro, "-", "-")]


def test_a_missing_column_grant_and_a_missing_sequence_grant_are_reported():
    names = roles.names_for("metal")
    act = actual_for("metal")
    column = next(i for i in act.column if i[0] == names.sync_w)
    sequence = next(i for i in act.sequence if i[0] == names.app)
    found = release.diff("metal", replace(act, column=act.column - {column}, sequence=act.sequence - {sequence}))
    assert {(m.kind, m.role, m.object) for m in found} == {
        ("missing", names.sync_w, f"{column[1]}.{column[2]}"),
        ("missing", names.app, f"sequence of {sequence[1]}"),
    }


def test_maintain_is_expected_from_postgresql_17_only():
    names = roles.names_for("metal")
    exp = roles.expected(names)
    on16 = actual_for("metal", 160000)
    assert release.diff("metal", on16) == []
    # a server 17+ that lacks MAINTAIN is a mismatch, a 16 server is not
    without = replace(actual_for("metal", 180003), table=actual_for("metal", 160000).table, server_version_num=180003)
    found = release.diff("metal", without)
    assert {m.privilege for m in found} == {"MAINTAIN"} and len(found) == len(exp.maintain)


def test_the_output_table_names_objects_and_never_a_credential():
    names = roles.names_for("general")
    act = actual_for("general")
    act = replace(act, table=act.table - {(names.app, "images", "SELECT")})
    text = release.render_mismatches(release.diff("general", act))
    assert "missing" in text and "images" in text and "1 mismatch(es)" in text
    assert release.render_mismatches([]) == "grants match the registry"


def test_a_long_diff_is_cut_but_counted():
    many = [release.Mismatch("missing", "r", f"t{i}", "SELECT") for i in range(release.MAX_LINES + 5)]
    text = release.render_mismatches(many)
    assert "... and 5 more" in text and text.endswith(f"{len(many)} mismatch(es)")


# ------------------------------------------------------------------------------------------------------------- refusals

def test_identity_accepts_the_owner_of_the_right_database_and_marker():
    n = roles.names_for("metal")
    assert release.evaluate_identity("metal", "metal", n.database, n.owner, require_owner=True) == []


@pytest.mark.parametrize("marker", ["general", "", None])
def test_identity_refuses_a_wrong_or_absent_environment_marker(marker):
    n = roles.names_for("metal")
    assert release.evaluate_identity("metal", marker, n.database, n.owner, require_owner=True)


def test_identity_refuses_another_role_for_migrate_but_not_for_verify():
    n = roles.names_for("metal")
    assert release.evaluate_identity("metal", "metal", n.database, n.app, require_owner=True)
    assert release.evaluate_identity("metal", "metal", n.database, n.app, require_owner=False) == []


def test_identity_refuses_another_database():
    n = roles.names_for("metal")
    assert release.evaluate_identity("metal", "metal", roles.names_for("it").database, n.owner, require_owner=False)


@pytest.mark.parametrize("env", ENVS)
def test_the_committed_roles_file_is_what_the_generator_writes(env):
    text = release.roles_file(env).read_bytes().decode("utf-8")
    assert release.stale_reason(env, text) is None
    assert release.stale_reason(env, text + "-- edited\n")


def test_normalise_url_keeps_a_socket_url_and_drops_the_driver_suffix():
    assert release.normalise_url("postgresql+asyncpg://u:p@/db?host=/var/run/postgresql") == "postgresql://u:p@/db?host=/var/run/postgresql"
    assert release.normalise_url("postgresql://u@/db?host=/s") == "postgresql://u@/db?host=/s"


def test_main_refuses_without_an_environment(monkeypatch, capsys):
    monkeypatch.delenv("APP_ENV", raising=False)
    assert release.main(["verify-grants"]) == release.EXIT_REFUSED


def test_main_refuses_a_conflicting_env_flag(monkeypatch):
    monkeypatch.setenv("APP_ENV", "metal")
    assert release.main(["verify-grants", "--env", "it"]) == release.EXIT_REFUSED


def test_main_refuses_without_database_url_and_prints_no_url(monkeypatch, capsys):
    monkeypatch.setenv("APP_ENV", "it")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert release.main(["all"]) == release.EXIT_REFUSED
    assert "DATABASE_URL is not set" in capsys.readouterr().err


def test_the_url_is_never_formatted_into_output():
    source = (ROOT / "sync" / "release.py").read_text(encoding="utf-8")
    # every os.environ read of DATABASE_URL goes straight into the driver call
    assert source.count('os.environ.get("DATABASE_URL"') == 1
    assert "print(url" not in source and "{url" not in source


def test_release_imports_no_driver_at_module_level():
    source = (ROOT / "sync" / "release.py").read_text(encoding="utf-8")
    top = [line for line in source.splitlines() if line.startswith(("import ", "from "))]
    assert not [line for line in top if "psycopg2" in line or "alembic" in line]


# --------------------------------------------------------------------------------------------- migrate image and service

def test_the_dockerfile_pins_the_base_by_digest_and_runs_unprivileged():
    text = (ROOT / "Dockerfile.migrate").read_text(encoding="utf-8")
    assert re.search(r"^FROM python:3\.11-slim@sha256:[0-9a-f]{64}", text, re.M)
    assert re.search(r"^USER \d+", text, re.M) and "USER root" not in text
    assert not re.search(r"^ENV .*(PASSWORD|DATABASE_URL|SECRET|KEY)", text, re.M | re.I)
    assert "COPY . " not in text and "COPY ./ " not in text  # never the whole context


def _pins(path):
    return {m.group(1).lower(): m.group(2) for m in re.finditer(r"^([A-Za-z0-9_.-]+)==([^\s#]+)", path.read_text(encoding="utf-8"), re.M)}


def test_every_package_of_the_migrate_image_is_pinned_to_the_repository_pins():
    text = (ROOT / "Dockerfile.migrate").read_text(encoding="utf-8")
    backend, root_pins = _pins(ROOT / "Backend" / "requirements-backend.txt"), _pins(ROOT / "requirements.txt")
    from_backend = re.search(r"MIGRATE_FROM_BACKEND=\"([^\"]+)\"", text).group(1).split()
    from_root = re.search(r"MIGRATE_FROM_ROOT=\"([^\"]+)\"", text).group(1).split()
    assert from_backend and from_root
    for name in from_backend:
        assert name.lower() in backend, f"{name} is not pinned in Backend/requirements-backend.txt"
    for pin in from_root:
        name, version = pin.split("==")
        assert root_pins.get(name.lower()) == version, f"{pin} differs from requirements.txt"
    assert "psycopg2-binary" in from_backend and "SQLAlchemy" in from_backend and any(p.startswith("alembic==") for p in from_root)


def test_the_migrate_dockerignore_admits_only_the_roles_files_of_deploy():
    ignore = (ROOT / "Dockerfile.migrate.dockerignore").read_text(encoding="utf-8")
    lines = [line.strip() for line in ignore.splitlines() if line.strip() and not line.startswith("#")]
    assert "**" == lines[0]  # allow-list: everything is excluded first
    assert "!deploy/prod/sql/roles.*.sql" in lines
    assert not any(line.startswith("!") and (".env" in line or "environments/." in line) for line in lines)
    assert "environments/.env.*" in lines and "**/.env.*" in lines
    backend_ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    # the backend image admits exactly one file of deploy: the gunicorn config
    assert re.search(r"^deploy/\*$", backend_ignore, re.M) and re.search(r"^deploy/prod/\*$", backend_ignore, re.M)
    assert re.findall(r"^!deploy.*$", backend_ignore, re.M) == ["!deploy/prod", "!deploy/prod/gunicorn.conf.py"]


def test_the_migrate_service_has_no_network_no_published_port_and_runs_the_release_tool():
    import yaml

    svc = yaml.safe_load((ROOT / "deploy" / "prod" / "compose.yaml").read_text(encoding="utf-8"))["services"]["migrate"]
    assert svc["network_mode"] == "none"
    assert "ports" not in svc and "networks" not in svc
    assert svc["command"] == ["all"]  # the image entrypoint is `python -m sync.release`
    assert svc["profiles"] == ["ops"] and svc["restart"] == "no"
    assert svc["image"].startswith("memes-migrate:")
    assert [v.split(":")[0] for v in svc["volumes"]] == ["pgsocket"]  # nothing else is mounted
    assert svc["environment"]["DATABASE_URL"].startswith("${DATABASE_URL_OWNER")
