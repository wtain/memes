"""default_comics_name mirrors Frontend/memes-frontend/src/comics/comicsNaming.ts (defaultComicsName)."""
from repository.comics_name import MAX_NAME_LENGTH, default_comics_name, normalize_name

_ID = "0123abcd-0000-0000-0000-000000000000"


def test_uses_file_name_without_extension():
    assert default_comics_name("IMG_20200923_110706_156.jpg", _ID) == "Comics IMG_20200923_110706_156"
    assert default_comics_name("a.b.png", _ID) == "Comics a.b"
    assert default_comics_name(".hidden", _ID) == "Comics .hidden"


def test_removes_forbidden_characters_and_collapses_spaces():
    assert default_comics_name('a<b>:"c"/d\\e|f?g*  h.jpg', _ID) == "Comics abcdefg h"
    assert default_comics_name("tab\tname\x00 x.jpg", _ID) == "Comics tabname x"  # controls are removed, as in the frontend


def test_falls_back_to_the_id_prefix():
    assert default_comics_name(None, _ID) == "Comics 0123abcd"
    assert default_comics_name("", _ID) == "Comics 0123abcd"
    assert default_comics_name("???.jpg", _ID) == "Comics 0123abcd"


def test_id_suffix_and_length_cap():
    assert default_comics_name("a.jpg", _ID, with_id_suffix=True) == "Comics a 0123abcd"
    assert len(default_comics_name("x" * 200 + ".jpg", _ID)) == MAX_NAME_LENGTH
    suffixed = default_comics_name("x" * 200 + ".jpg", _ID, with_id_suffix=True)
    assert len(suffixed) == MAX_NAME_LENGTH and suffixed.endswith(" 0123abcd")


def test_never_ends_with_a_dot_or_space_and_always_validates():
    assert not default_comics_name("x" * 70 + "... .jpg", _ID).endswith((".", " "))
    for file in ["IMG 1.jpg", "CON.jpg", "a" * 300, "​z.jpg", "...", "x" * 70 + "... .jpg"]:
        for suffix in (False, True):
            normalize_name(default_comics_name(file, _ID, suffix))
