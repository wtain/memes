"""Unit tests for file-name sanitising of the inbox upload service (B1)."""
import re
from unittest.mock import patch

import pytest

from Backend.app.services import inbox_upload_service as service_module
from Backend.app.services.inbox_upload_service import _publish_once, sanitize_filename


@pytest.mark.parametrize("raw, ext, expected", [
    ("../../x.jpg", ".jpg", "x.jpg"),
    ("a/b.jpg", ".jpg", "b.jpg"),
    ("a\\b.jpg", ".jpg", "b.jpg"),
    ("C:\\Users\\me\\pic.png", ".png", "pic.png"),
    ("CON.jpg", ".jpg", "_CON.jpg"),
    ("lpt3.png", ".png", "_lpt3.png"),
    ("nul.tar.png", ".png", "_nul.tar.png"),
    (".hidden.png", ".png", "hidden.png"),
    ("...png.png", ".png", "png.png"),
    ("Мем про кота 1.JPG", ".jpg", "Мем про кота 1.jpg"),
    ("a<b>:c\"d|e?f*.png", ".png", "a_b__c_d_e_f_.png"),
    ("name. .png", ".png", "name.png"),
    ("20241108_123456.jpg", ".jpg", "20241108_123456.jpg"),
    ("a\x00b\x01c\x7f.png", ".png", "abc.png"),
    ("COM10.png", ".png", "COM10.png"),
])
def test_sanitize_filename(raw, ext, expected):
    assert sanitize_filename(raw, ext) == expected


def test_long_cyrillic_stem_is_capped_in_bytes_on_a_character_boundary():
    name = sanitize_filename("я" * 300 + ".jpeg", ".jpeg")
    assert name.endswith(".jpeg")
    stem = name[: -len(".jpeg")]
    assert 0 < len(stem.encode("utf-8")) <= 200
    assert set(stem) == {"я"}


@pytest.mark.parametrize("raw", ["", ".png", "...png", "/.png", "<>.png".replace("<>", "")])
def test_empty_stem_gets_a_generated_name(raw):
    assert re.fullmatch(r"upload-[0-9a-f]{8}\.png", sanitize_filename(raw, ".png"))


def test_none_name_gets_a_generated_name():
    assert re.fullmatch(r"upload-[0-9a-f]{8}\.png", sanitize_filename(None, ".png"))


@pytest.mark.parametrize("raw", ["‮gpj.png", "a​b.png", "a b.png", "a b.png", "a‍b.png"])
def test_bidi_and_invisible_characters_are_dropped(raw):
    assert sanitize_filename(raw, ".png") in {"gpj.png", "ab.png"}


def test_cyrillic_survives_the_invisible_character_filter():
    assert sanitize_filename("Привет​ мир.png", ".png") == "Привет мир.png"


class TestPublishOnce:
    def test_existing_target_raises_and_is_untouched(self, tmp_path):
        staged, final = tmp_path / "staged", tmp_path / "final.png"
        staged.write_bytes(b"new")
        final.write_bytes(b"old")

        with pytest.raises(FileExistsError):
            _publish_once(staged, final)

        assert final.read_bytes() == b"old" and staged.read_bytes() == b"new"

    def test_free_target_gets_the_content_and_staging_is_consumed(self, tmp_path):
        staged, final = tmp_path / "staged", tmp_path / "final.png"
        staged.write_bytes(b"new")

        _publish_once(staged, final)

        assert final.read_bytes() == b"new" and not staged.exists()

    def test_fallback_when_hard_links_are_unsupported(self, tmp_path):
        staged, final = tmp_path / "staged", tmp_path / "final.png"
        staged.write_bytes(b"new")

        with patch.object(service_module.os, "link", side_effect=OSError(1, "not supported")):
            _publish_once(staged, final)

        assert final.read_bytes() == b"new" and not staged.exists()

    def test_fallback_never_overwrites_either(self, tmp_path):
        staged, final = tmp_path / "staged", tmp_path / "final.png"
        staged.write_bytes(b"new")
        final.write_bytes(b"old")

        with patch.object(service_module.os, "link", side_effect=OSError(1, "not supported")):
            with pytest.raises(FileExistsError):
                _publish_once(staged, final)

        assert final.read_bytes() == b"old"

    def test_exclusive_copy_fallback_never_overwrites(self, tmp_path):
        staged, final = tmp_path / "staged", tmp_path / "final.png"
        staged.write_bytes(b"new")
        final.write_bytes(b"old")

        with pytest.raises(FileExistsError):
            service_module._exclusive_copy(staged, final)

        assert final.read_bytes() == b"old"

    def test_exclusive_copy_publishes_to_a_free_name(self, tmp_path):
        staged, final = tmp_path / "staged", tmp_path / "final.png"
        staged.write_bytes(b"new")

        service_module._exclusive_copy(staged, final)

        assert final.read_bytes() == b"new" and not staged.exists()
