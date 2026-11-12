"""Renders docs/sync-registry.md from the registry.

    python -m sync.render --write   # regenerate the page
    python -m sync.render --check   # exit 1 when the page is stale (tests/sync does the same)
"""
import sys
from pathlib import Path

from sync import registry
from sync.model import Klass, ScopeKind

DOC = Path(__file__).resolve().parents[1] / "docs" / "sync-registry.md"

_HEADER = """# Sync registry

Generated from `sync/registry.py` by `python -m sync.render --write`; do not edit by hand (`tests/sync/` fails when this page is
stale). It lists every table of `Storage/models.py` with its dev/prod sync class: **D** dev-origin, promoted dev to prod; **P**
prod-origin reader activity, pulled prod to dev; **X** dev-only, never synced. A new table needs an entry in the registry. The
rationale of each class is in `docs/superpowers/specs/2026-11-12-environment-data-sync/` (S1 classification, decisions log in the
README).

Scope: `images` is the promotable set (status `active`, not flagged); `image_fk(c)` rows whose `c` is in that set; `via(parent.c)`
rows reached through a scoped parent; `global_set` no image relation. Level is the load order (parents first).
"""


def _scope(entry) -> str:
    scope = entry.scope
    if scope.kind is ScopeKind.IMAGE_FK:
        return f"image_fk({scope.column})"
    if scope.kind is ScopeKind.VIA:
        return f"via({scope.parent}.{scope.column})"
    return scope.kind.value


def _row(entry) -> str:
    level = "" if entry.level is None else str(entry.level)
    nullify = ", ".join(entry.nullify)
    notes = entry.notes + (f" (blank on missing target: {nullify})" if nullify else "")
    notes += " (prunes rows deleted on dev)" if entry.prune else ""
    notes += " (pull before any promote)" if entry.pull_before_promote else ""
    return (
        f"| `{entry.name}` | {entry.mode.value} | {_scope(entry)} | {level} | {entry.digest_group or ''} "
        f"| {entry.prod_writer} | {notes} |"
    )


def render() -> str:
    out = [_HEADER]
    titles = {
        Klass.D: "Class D: dev-origin, shipped to prod",
        Klass.P: "Class P: prod-origin, pulled to dev",
        Klass.X: "Class X: dev-only, never synced",
    }
    for klass in (Klass.D, Klass.P, Klass.X):
        entries = sorted(
            (e for e in registry.REGISTRY.values() if e.klass == klass),
            key=lambda e: (-1 if e.level is None else e.level, e.name),
        )
        out.append(f"\n## {titles[klass]} ({len(entries)} tables)\n")
        out.append("| Table | Mode | Scope | Level | Digest group | Prod writer | Notes |")
        out.append("|---|---|---|---|---|---|---|")
        out.extend(_row(e) for e in entries)
    out.append("\n## Digest groups\n")
    for group, tables in registry.digest_groups().items():
        out.append(f"- `{group}`: " + ", ".join(f"`{t}`" for t in tables))
    out.append("\n## Digest exclusions\n")
    out.append("Columns left out of the per-image digest (a change of only these is not a change of the image):\n")
    for entry in sorted(registry.REGISTRY.values(), key=lambda e: e.name):
        out.extend(f"- `{entry.name}.{column}`: {reason}" for column, reason in entry.digest_exclude)
    out.append("\n## Prod write sets\n")
    for role in ("backend_app", "sync_writer"):
        tables = registry.prod_write_set(role)
        out.append(f"- `{role}` ({len(tables)}): " + ", ".join(f"`{t}`" for t in tables))
    out.append("\n## Integer-keyed tables (sequence moved past max(id) after a load)\n")
    out.append(", ".join(f"`{t}`" for t in registry.setval_tables()))
    return "\n".join(out) + "\n"


def main(argv: list[str]) -> int:
    text = render()
    if "--write" in argv:
        DOC.write_text(text, encoding="utf-8", newline="\n")
        return 0
    if "--check" in argv:
        current = DOC.read_text(encoding="utf-8") if DOC.exists() else ""
        if current != text:
            print("docs/sync-registry.md is stale: run `python -m sync.render --write`", file=sys.stderr)
            return 1
        return 0
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
