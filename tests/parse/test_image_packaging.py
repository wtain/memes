from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _lines(name):
    return [line.strip() for line in (REPO_ROOT / name).read_text(encoding="utf-8").splitlines()]


def test_dockerfile_copies_the_parser_the_taxonomy_package_and_its_yaml():
    lines = _lines("Dockerfile.backend")
    assert "COPY parse ./parse" in lines
    assert "COPY taxonomy ./taxonomy" in lines
    assert "COPY batch/data/taxonomy ./batch/data/taxonomy" in lines


def test_dockerignore_lets_the_taxonomy_yaml_through_the_batch_exclusion():
    lines = _lines(".dockerignore")
    assert "batch/*" in lines
    assert lines.index("batch/*") < lines.index("!batch/data") < lines.index("batch/data/*") \
        < lines.index("!batch/data/taxonomy")
    assert "taxonomy" not in lines and "parse" not in lines
