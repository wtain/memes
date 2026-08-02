import pytest

from batch.utils.image_format_filter import has_unsupported_image_extension


@pytest.mark.parametrize("filename", [
    "meme.webp",
    "meme.WEBP",
    "meme.gif",
    "meme.svg",
    "meme.avif",
    "weird.name.with.dots.avif",
])
def test_flags_unsupported_extensions(filename):
    assert has_unsupported_image_extension(f"/base/{filename}") is True


@pytest.mark.parametrize("filename", [
    "meme.jpg",
    "meme.jpeg",
    "meme.png",
    "meme.bmp",
    "noextension",
])
def test_allows_supported_extensions(filename):
    assert has_unsupported_image_extension(f"/base/{filename}") is False


def test_does_not_false_positive_on_substring_match():
    # Regression guard: the old `path.lower().endswith("webp")` check would
    # also match a filename that merely contains "webp" without it being the
    # actual extension.
    assert has_unsupported_image_extension("/base/notwebp.jpg") is False
