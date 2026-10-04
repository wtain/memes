import ast
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "evaluation"
PURE = ["gold.py", "metrics.py", "pooling.py", "runner.py", "report.py", "retrievers/base.py"]
ALLOWED = set(sys.stdlib_module_names) | {"taxonomy", "evaluation"}


def _roots(path: Path) -> set[str]:
    roots = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_pure_modules_import_only_stdlib_taxonomy_and_evaluation():
    for name in PURE:
        offenders = _roots(PACKAGE / name) - ALLOWED
        assert not offenders, f"evaluation/{name} imports {sorted(offenders)}"


def test_retrievers_package_init_is_empty_so_importing_base_pulls_in_no_database_code():
    init = PACKAGE / "retrievers" / "__init__.py"
    assert init.read_text(encoding="utf-8").strip() == ""
