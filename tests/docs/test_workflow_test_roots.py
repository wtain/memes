"""Every test root must be run by a CI workflow (or be on the explicit allowlist below).

A root that no workflow runs is silently unenforced: its tests (including the AST purity tests of parse/,
retrieval/ and taxonomy/) can go red unnoticed. Roots run in separate pytest steps because their asyncio modes
differ (see CLAUDE.md), so a workflow runs a root when it has a `pytest <root>` command naming it.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO / ".github" / "workflows"

# root -> why no workflow runs it
ALLOWLIST = {
    "tests/load": "synthetic_load_check.py is a script against the TEST database, not collected tests",
}


def find_roots():
    roots = set()
    candidates = [p for p in (REPO / "tests").iterdir() if p.is_dir()]
    candidates += [REPO / "batch" / "tests", REPO / "Backend" / "tests"]
    for directory in candidates:
        if any(directory.glob("test_*.py")) or any(directory.glob("*/test_*.py")):
            roots.add(directory.relative_to(REPO).as_posix())
    return roots


def pytest_commands():
    text = "\n".join(p.read_text(encoding="utf-8") for p in sorted(WORKFLOWS.glob("*.yml")))
    return [line for line in text.splitlines() if re.search(r"\bpytest\b", line)]


def test_every_test_root_is_run_by_a_workflow():
    roots = find_roots()
    assert {"tests/rules", "tests/parse", "tests/integration", "batch/tests"} <= roots, "root discovery broke"
    commands = pytest_commands()
    missing = []
    for root in sorted(roots):
        if root in ALLOWLIST:
            continue
        # Backend's workflow runs `cd Backend` then `pytest tests/`.
        name = root[len("Backend/"):] if root.startswith("Backend/") else root
        if not any(re.search(rf"\bpytest\s+{re.escape(name)}/?(\s|$)", c) for c in commands):
            missing.append(root)
    assert not missing, f"test roots no workflow runs (add a pytest step or an allowlist entry): {missing}"


def test_allowlist_entries_exist():
    for root in ALLOWLIST:
        assert (REPO / root).is_dir(), root
