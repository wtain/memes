import ast
import sys
from pathlib import Path

CORE_MODULES = ["__init__", "model", "loader", "validate", "prompt", "lint", "lock"]
ALLOWED_THIRD_PARTY = {"yaml"}
PACKAGE = Path(__file__).resolve().parents[2] / "taxonomy"


def _imported_roots(path: Path) -> set[str]:
    roots = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_core_modules_import_only_stdlib_yaml_and_taxonomy():
    allowed = set(sys.stdlib_module_names) | ALLOWED_THIRD_PARTY | {"taxonomy"}
    for module in CORE_MODULES:
        offenders = _imported_roots(PACKAGE / f"{module}.py") - allowed
        assert not offenders, f"taxonomy/{module}.py imports {sorted(offenders)}; the backend cannot install them"
