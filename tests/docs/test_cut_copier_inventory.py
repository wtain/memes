"""The cut copier must account for every table in docs/image-derived-data.md: copied, or deliberately not."""
from repository.cut_copier import COPIED_TABLES, NOT_COPIED_TABLES
from tests.docs.test_image_derived_data_inventory import DOC, documented_tables


def test_every_inventory_table_is_copied_or_deliberately_not():
    documented = set(documented_tables(DOC.read_text(encoding="utf-8")))
    assert not (COPIED_TABLES & NOT_COPIED_TABLES)
    assert COPIED_TABLES | NOT_COPIED_TABLES == documented, sorted((COPIED_TABLES | NOT_COPIED_TABLES) ^ documented)
