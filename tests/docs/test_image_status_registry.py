"""docs/image-status.md is the authoritative registry of images.status values; it must list exactly ImageStatus."""
import re
from pathlib import Path

from Storage.models import ImageStatus

DOC = Path(__file__).resolve().parents[2] / "docs" / "image-status.md"


def documented_statuses(markdown: str) -> set[str]:
    """First backticked token of the first cell of each table row under '## Statuses'."""
    found, inside = set(), False
    for line in markdown.splitlines():
        if line.startswith("## "):
            inside = line.strip() == "## Statuses"
            continue
        if inside:
            match = re.match(r"\|\s*`([^`]+)`", line)
            if match:
                found.add(match.group(1))
    return found


def test_the_registry_lists_exactly_the_image_statuses():
    assert documented_statuses(DOC.read_text(encoding="utf-8")) == set(ImageStatus.ALL)


def test_the_parser_reads_only_the_statuses_section():
    table = ("## Statuses\n\n| Status | Meaning |\n|---|---|\n| `active` | x |\n| `bogus` | y |\n\n"
             "## Other\n| `pending` | z |\n")
    assert documented_statuses(table) == {"active", "bogus"}


def test_every_status_appears_in_the_transitions_section():
    text = DOC.read_text(encoding="utf-8")
    section = text.split("## Transitions", 1)[1].split("\n## ", 1)[0]
    for status in ImageStatus.ALL:
        assert f"`{status}`" in section, status
