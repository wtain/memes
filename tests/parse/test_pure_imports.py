import ast
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "parse"
ALLOWED = set(sys.stdlib_module_names) | {"taxonomy", "parse"}


def _roots(path: Path) -> set[str]:
    roots = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_every_parse_module_imports_only_stdlib_taxonomy_and_parse():
    modules = sorted(PACKAGE.glob("*.py"))
    assert modules, "parse/ has no modules"
    for path in modules:
        offenders = _roots(path) - ALLOWED
        assert not offenders, f"parse/{path.name} imports {sorted(offenders)}; the backend image cannot rely on them"


def test_the_package_init_is_empty():
    assert (PACKAGE / "__init__.py").read_text(encoding="utf-8").strip() == ""
