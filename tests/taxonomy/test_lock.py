import json
import shutil
from pathlib import Path

import pytest

from taxonomy.loader import DEFAULT_DIR, TaxonomyError, load
from taxonomy.lock import LOCK_NAME, check, write_lock

FIXTURE = Path(__file__).parent / "fixtures" / "mini"


@pytest.fixture
def directory(tmp_path) -> Path:
    target = tmp_path / "tax"
    shutil.copytree(FIXTURE, target)
    return target


def _edit_definition(directory: Path) -> None:
    emotion = directory / "emotion.yaml"
    emotion.write_text(emotion.read_text(encoding="utf-8").replace("Plain happiness.", "Pure delight."),
                       encoding="utf-8")


def _bump_version(directory: Path) -> None:
    path = directory / "taxonomy.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("version: 7", "version: 8"), encoding="utf-8")


def test_missing_lock_is_reported_with_the_command_to_create_it(directory):
    message = check(load(directory), directory)
    assert "python -m taxonomy.lock" in message


def test_write_then_check_is_clean(directory):
    write_lock(directory)
    assert check(load(directory), directory) is None
    stored = json.loads((directory / LOCK_NAME).read_text(encoding="utf-8"))
    assert stored["version"] == 7
    assert stored["content_hash"] == load(directory).content_hash


def test_content_change_without_version_bump_is_reported(directory):
    write_lock(directory)
    _edit_definition(directory)
    assert "bump" in check(load(directory), directory)


def test_write_lock_refuses_content_change_without_version_bump(directory):
    write_lock(directory)
    _edit_definition(directory)
    with pytest.raises(TaxonomyError, match="bump"):
        write_lock(directory)


def test_version_bump_with_stale_lock_asks_for_a_refresh(directory):
    write_lock(directory)
    _edit_definition(directory)
    _bump_version(directory)
    assert "python -m taxonomy.lock" in check(load(directory), directory)
    write_lock(directory)
    assert check(load(directory), directory) is None


def test_write_lock_is_idempotent(directory):
    write_lock(directory)
    first = (directory / LOCK_NAME).read_bytes()
    write_lock(directory)
    assert (directory / LOCK_NAME).read_bytes() == first


def test_shipped_lock_is_current():
    message = check(load(), DEFAULT_DIR)
    assert message is None, message
