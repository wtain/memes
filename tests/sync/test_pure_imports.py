import ast
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "sync"
ALLOWED = set(sys.stdlib_module_names) | {"sync"}


def _roots(path: Path) -> set[str]:
    roots = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_the_registry_core_imports_only_the_standard_library_and_sync():
    # The registry core. Later modules of the package (the promote and pull tools) may need a database driver.
    modules = [PACKAGE / name for name in ("model.py", "registry.py", "render.py")]
    for path in modules:
        assert path.exists(), path
        offenders = _roots(path) - ALLOWED
        assert not offenders, f"sync/{path.name} imports {sorted(offenders)}; the sync tools and the migration image cannot rely on them"


def test_the_package_init_is_empty():
    assert (PACKAGE / "__init__.py").read_text(encoding="utf-8").strip() == ""
