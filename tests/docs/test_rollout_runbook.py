"""The rollout runbook must list exactly the migrations a behind environment receives, and be linked from the use case."""
import re
from pathlib import Path

from tools.rollout_preflight import FIRST_RECUT_REVISION, REVISION_LABELS, chain

ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = ROOT / "docs" / "batches" / "use-cases" / "screenshot-recut-rollout.md"


def recut_chain_ids():
    ids = [r.revision for r in chain()]
    return ids[ids.index(FIRST_RECUT_REVISION):]


def test_runbook_lists_exactly_the_pending_revisions():
    section = RUNBOOK.read_text(encoding="utf-8").split("## The migrations", 1)[1].split("\n## ", 1)[0]
    listed = re.findall(r"`([0-9a-f]{12})`", section)
    assert listed == recut_chain_ids()


def test_every_revision_has_a_label_in_the_tool():
    assert set(recut_chain_ids()) <= set(REVISION_LABELS)


def test_use_case_links_to_the_runbook():
    text = (ROOT / "docs" / "batches" / "use-cases" / "screenshot-recut.md").read_text(encoding="utf-8")
    assert "screenshot-recut-rollout.md" in text
