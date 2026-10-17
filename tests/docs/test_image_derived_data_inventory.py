"""docs/image-derived-data.md classifies every table that hangs off images; a new table fails until classified."""
import re
from pathlib import Path

from sqlalchemy import Column, ForeignKey, MetaData, Table
from sqlalchemy.dialects.postgresql import UUID

from Storage.models import Base

DOC = Path(__file__).resolve().parents[2] / "docs" / "image-derived-data.md"


def tables_off_images(metadata: MetaData) -> set[str]:
    """Tables with a foreign key to images, plus tables with a foreign key to any such table (transitively)."""
    found = {
        table.name for table in metadata.tables.values()
        if table.name != "images" and any(fk.column.table.name == "images" for fk in table.foreign_keys)
    }
    grew = True
    while grew:
        grew = False
        for table in metadata.tables.values():
            if table.name in found or table.name == "images":
                continue
            if any(fk.column.table.name in found for fk in table.foreign_keys):
                found.add(table.name)
                grew = True
    return found


def documented_tables(markdown: str) -> dict[str, str]:
    """Table name -> group cell, from the rows under '## Inventory'."""
    rows, inside = {}, False
    for line in markdown.splitlines():
        if line.startswith("## "):
            inside = line.strip() == "## Inventory"
            continue
        if inside:
            match = re.match(r"\|\s*`([^`]+)`[^|]*\|\s*([^|]+?)\s*\|", line)
            if match:
                rows.setdefault(match.group(1), match.group(2))
    return rows


def unclassified(metadata: MetaData, documented: dict[str, str]) -> list[str]:
    return sorted(tables_off_images(metadata) - set(documented))


def test_every_table_off_images_is_classified():
    assert unclassified(Base.metadata, documented_tables(DOC.read_text(encoding="utf-8"))) == []


def test_the_inventory_names_no_table_that_does_not_exist():
    documented = set(documented_tables(DOC.read_text(encoding="utf-8")))
    assert documented <= set(Base.metadata.tables), sorted(documented - set(Base.metadata.tables))


def test_every_row_has_a_group():
    for table, group in documented_tables(DOC.read_text(encoding="utf-8")).items():
        assert group, table


def test_the_new_relation_and_nested_children_are_found():
    found = tables_off_images(Base.metadata)
    assert {"image_cuts", "meme_profile_values", "description_lemmas"} <= found
    assert "images" not in found and "batch_runs" not in found


def test_an_unclassified_future_table_is_reported_by_name():
    """Negative control on a toy schema: a table nobody documented must be named, including a nested one."""
    metadata = MetaData()
    Table("images", metadata, Column("id", UUID, primary_key=True))
    widgets = Table("widgets", metadata, Column("id", UUID, primary_key=True),
                    Column("image_id", UUID, ForeignKey("images.id")))
    Table("gadgets", metadata, Column("widget_id", UUID, ForeignKey(widgets.c.id)))
    assert unclassified(metadata, {"widgets": "2"}) == ["gadgets"]
    assert unclassified(metadata, {}) == ["gadgets", "widgets"]
