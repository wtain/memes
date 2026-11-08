"""Unit tests for file-name sanitising of the inbox upload service (B1)."""
import re

import pytest

from Backend.app.services.inbox_upload_service import sanitize_filename


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
