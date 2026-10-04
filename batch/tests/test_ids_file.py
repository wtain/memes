import json
import uuid

import pytest

from batch.utils.ids_file import MAX_IDS, load_ids_file

A, B = uuid.uuid4(), uuid.uuid4()


def test_reads_a_json_list(tmp_path):
    path = tmp_path / "ids.json"
    path.write_text(json.dumps([str(A), str(B), str(A)]), encoding="utf-8")

    assert load_ids_file(str(path)) == frozenset({A, B})


def test_reads_one_uuid_per_line_ignoring_blank_lines_and_comments(tmp_path):
    path = tmp_path / "ids.txt"
    path.write_text(f"# corpus ids\n{A}\n\n  {B}  \n", encoding="utf-8")

    assert load_ids_file(str(path)) == frozenset({A, B})


def test_invalid_uuid_empty_file_and_non_list_json_are_refused(tmp_path):
    bad = tmp_path / "bad.txt"
    bad.write_text("not-a-uuid\n", encoding="utf-8")
    empty = tmp_path / "empty.txt"
    empty.write_text("  \n", encoding="utf-8")
    obj = tmp_path / "obj.json"
    obj.write_text('{"a": 1}', encoding="utf-8")
    broken = tmp_path / "broken.json"
    broken.write_text("[1, 2", encoding="utf-8")

    with pytest.raises(ValueError, match="not a UUID"):
        load_ids_file(str(bad))
    with pytest.raises(ValueError, match="empty"):
        load_ids_file(str(empty))
    with pytest.raises(ValueError, match="list"):
        load_ids_file(str(obj))
    with pytest.raises(ValueError):
        load_ids_file(str(broken))


def test_more_than_the_maximum_number_of_ids_is_refused(tmp_path):
    path = tmp_path / "many.txt"
    path.write_text("\n".join(str(uuid.uuid4()) for _ in range(MAX_IDS + 1)), encoding="utf-8")

    with pytest.raises(ValueError, match="at most"):
        load_ids_file(str(path))
