import pytest

from repository.comics_name import ComicsNameError, normalize_name


def test_trim_and_key():
    assert normalize_name("  Мой комикс  ") == ("Мой комикс", "мой комикс")


def test_nfc():
    a = normalize_name("Café")
    b = normalize_name("Café")
    assert a == b
    assert a[0] == "Café"


def test_casefold_key():
    assert normalize_name("Straße")[1] == normalize_name("STRASSE")[1]


def test_length_boundary():
    assert normalize_name("a" * 80)[0] == "a" * 80


@pytest.mark.parametrize(
    "raw,reason",
    [
        ("", "empty"),
        ("   ", "empty"),
        ("a" * 81, "too_long"),
        *[(f"a{c}b", "forbidden_character") for c in '<>:"/\\|?*'],
        ("a\tb", "forbidden_character"),
        ("\u200b", "forbidden_character"),
        ("a\u200bb", "forbidden_character"),
        ("a\u202eb", "forbidden_character"),
        ("a\u2028b", "forbidden_character"),
        ("a\u2029b", "forbidden_character"),
        ("abc.", "trailing_dot_or_space"),
        ("abc. ", "trailing_dot_or_space"),
        ("con", "reserved_name"),
        ("NUL.txt", "reserved_name"),
        ("com1", "reserved_name"),
        ("LPT9", "reserved_name"),
    ],
)
def test_refusals(raw, reason):
    with pytest.raises(ComicsNameError) as ei:
        normalize_name(raw)
    assert ei.value.reason == reason
    assert isinstance(ei.value, ValueError)


@pytest.mark.parametrize("raw", ["console", "com10"])
def test_allowed(raw):
    assert normalize_name(raw)[0] == raw


def test_key_is_equal_for_composed_and_decomposed_accents():
    assert normalize_name("Caf\u00e9")[1] == normalize_name("Cafe\u0301")[1]
