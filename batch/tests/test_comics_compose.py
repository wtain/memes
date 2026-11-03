import io
import os
import random

import pytest
from PIL import Image as PILImage

from batch.utils.comics_compose import ComposeError, compose_preview, compose_to_file, plan_layout
from batch.utils.file_hash import sha256_file


def _make(tmp_path, name, size, color, fmt=None, **kw):
    path = str(tmp_path / name)
    PILImage.new("RGB", size, color).save(path, format=fmt, **kw)
    return path


def _near(pixel, color, tol=12):
    return all(abs(a - b) <= tol for a, b in zip(pixel[:3], color))


def test_two_jpegs_are_tiled_with_white_gutter(tmp_path):
    a = _make(tmp_path, "a.jpg", (200, 100), (255, 0, 0))
    b = _make(tmp_path, "b.jpg", (100, 100), (0, 0, 255))
    dst = str(tmp_path / "out.jpg")
    res = compose_to_file([a, b], dst, 90)
    layout = plan_layout([(200, 100), (100, 100)])
    assert res.format == "JPEG"
    assert (res.width, res.height) == (layout.canvas_width, layout.canvas_height)
    assert res.content_hash == sha256_file(dst)
    with PILImage.open(dst) as im:
        assert im.format == "JPEG"
        assert im.size == (layout.canvas_width, layout.canvas_height)
        rgb = im.convert("RGB")
        for p, color in zip(layout.placements, [(255, 0, 0), (0, 0, 255)]):
            assert _near(rgb.getpixel((p.x + p.width // 2, p.y + p.height // 2)), color)
        gx = layout.placements[0].x + layout.placements[0].width + layout.gutter // 2
        assert all(c >= 250 for c in rgb.getpixel((gx, layout.placements[0].y + 10)))


def test_png_input_gives_png(tmp_path):
    a = _make(tmp_path, "a.png", (50, 50), (1, 2, 3))
    dst = str(tmp_path / "o.png")
    assert compose_to_file([a], dst, 90).format == "PNG"
    with PILImage.open(dst) as im:
        assert im.format == "PNG"


def test_mixed_jpeg_and_png_gives_png(tmp_path):
    a = _make(tmp_path, "a.jpg", (50, 50), (1, 2, 3))
    b = _make(tmp_path, "b.png", (50, 50), (1, 2, 3))
    dst = str(tmp_path / "o.out")
    assert compose_to_file([a, b], dst, 90).format == "PNG"


def test_webp_with_jpeg_gives_jpeg(tmp_path):
    a = _make(tmp_path, "a.webp", (50, 50), (1, 2, 3))
    b = _make(tmp_path, "b.jpg", (50, 50), (1, 2, 3))
    dst = str(tmp_path / "o.out")
    assert compose_to_file([a, b], dst, 90).format == "JPEG"
    dst2 = str(tmp_path / "o2.out")
    assert compose_to_file([a], dst2, 90).format == "JPEG"


def test_transparency_is_flattened_on_white(tmp_path):
    im = PILImage.new("RGBA", (100, 100), (255, 0, 0, 255))
    for x in range(50):
        for y in range(100):
            im.putpixel((x, y), (0, 0, 0, 0))
    p = str(tmp_path / "t.png")
    im.save(p)
    dst = str(tmp_path / "o.png")
    compose_to_file([p], dst, 90)
    pl = plan_layout([(100, 100)]).placements[0]
    with PILImage.open(dst) as out:
        out = out.convert("RGB")
        assert out.getpixel((pl.x + 10, pl.y + 50)) == (255, 255, 255)
        assert _near(out.getpixel((pl.x + 90, pl.y + 50)), (255, 0, 0))


def test_exif_rotation_is_applied(tmp_path):
    im = PILImage.new("RGB", (300, 100), (10, 200, 10))
    exif = im.getexif()
    exif[0x0112] = 6
    p = str(tmp_path / "r.jpg")
    im.save(p, exif=exif)
    b = _make(tmp_path, "b.jpg", (100, 300), (10, 10, 10))
    dst = str(tmp_path / "o.jpg")
    res = compose_to_file([p, b], dst, 90)
    layout = plan_layout([(100, 300), (100, 300)])
    assert (res.width, res.height) == (layout.canvas_width, layout.canvas_height)


def test_jpeg_quality_is_used(tmp_path):
    rnd = random.Random(1)
    im = PILImage.frombytes("RGB", (200, 200), bytes(rnd.randrange(256) for _ in range(200 * 200 * 3)))
    p = str(tmp_path / "n.jpg")
    im.save(p, quality=100)
    hi, lo = str(tmp_path / "hi.jpg"), str(tmp_path / "lo.jpg")
    compose_to_file([p], hi, 95)
    compose_to_file([p], lo, 40)
    assert os.path.getsize(hi) > os.path.getsize(lo)


def _refusal_inputs(tmp_path):
    ok = _make(tmp_path, "ok.jpg", (50, 50), (1, 2, 3))
    gif = str(tmp_path / "anim.gif")
    f1 = PILImage.new("RGB", (20, 20), "red")
    f2 = PILImage.new("RGB", (20, 20), "blue")
    f1.save(gif, save_all=True, append_images=[f2], duration=50, loop=0)
    bmp = _make(tmp_path, "x.bmp", (20, 20), "red", fmt="BMP")
    txt = str(tmp_path / "x.jpg")
    with open(txt, "w") as f:
        f.write("not an image")
    return ok, gif, bmp, txt


@pytest.mark.parametrize("case", ["animated", "unsupported_format", "unreadable", "no_panels"])
def test_refusals(tmp_path, case):
    ok, gif, bmp, txt = _refusal_inputs(tmp_path)
    dst = str(tmp_path / "o.jpg")
    paths, reason, position = {
        "animated": ([ok, gif], "animated", 1),
        "unsupported_format": ([bmp, ok], "unsupported_format", 0),
        "unreadable": ([ok, ok, txt], "unreadable", 2),
        "no_panels": ([], "no_panels", None),
    }[case]
    with pytest.raises(ComposeError) as e:
        compose_to_file(paths, dst, 90)
    assert e.value.reason == reason
    assert e.value.position == position
    assert not os.path.exists(dst)


def test_a_failure_leaves_nothing_behind(tmp_path):
    ok, gif, bmp, txt = _refusal_inputs(tmp_path)
    dst = str(tmp_path / "o.jpg")
    with open(dst, "wb") as f:
        f.write(b"old")
    with pytest.raises(ComposeError):
        compose_to_file([ok, txt], dst, 90)
    with open(dst, "rb") as f:
        assert f.read() == b"old"
    assert not os.path.exists(dst + ".tmp")
    dst2 = str(tmp_path / "o2.jpg")
    with pytest.raises(ComposeError):
        compose_to_file([ok, txt], dst2, 90)
    assert not os.path.exists(dst2) and not os.path.exists(dst2 + ".tmp")


def test_a_failure_while_writing_leaves_nothing_behind(tmp_path, monkeypatch):
    ok = _make(tmp_path, "ok.jpg", (50, 50), (1, 2, 3))
    dst = str(tmp_path / "o.jpg")

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(PILImage.Image, "save", boom)
    with pytest.raises(ComposeError) as e:
        compose_to_file([ok], dst, 90)
    assert e.value.reason == "write_error"
    assert not os.path.exists(dst) and not os.path.exists(dst + ".tmp")


def test_an_existing_destination_is_replaced(tmp_path):
    ok = _make(tmp_path, "ok.jpg", (50, 50), (1, 2, 3))
    dst = str(tmp_path / "o.jpg")
    with open(dst, "wb") as f:
        f.write(b"old")
    res = compose_to_file([ok], dst, 90)
    assert res.content_hash == sha256_file(dst)
    with PILImage.open(dst) as im:
        assert im.format == "JPEG"
    assert not os.path.exists(dst + ".tmp")


def test_icc_profile_kept_only_when_all_panels_share_it(tmp_path):
    from PIL import ImageCms
    prof = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    a = str(tmp_path / "a.jpg")
    PILImage.new("RGB", (50, 50), "red").save(a, icc_profile=prof)
    b = str(tmp_path / "b.jpg")
    PILImage.new("RGB", (50, 50), "red").save(b, icc_profile=prof)
    c = _make(tmp_path, "c.jpg", (50, 50), "red")
    same, mixed = str(tmp_path / "s.jpg"), str(tmp_path / "m.jpg")
    compose_to_file([a, b], same, 90)
    compose_to_file([a, c], mixed, 90)
    with PILImage.open(same) as im:
        assert im.info.get("icc_profile") == prof
    with PILImage.open(mixed) as im:
        assert im.info.get("icc_profile") is None


def test_preview_matches_the_layout_at_a_smaller_scale(tmp_path):
    a = _make(tmp_path, "a.jpg", (400, 200), (255, 0, 0))
    b = _make(tmp_path, "b.jpg", (200, 200), (0, 0, 255))
    c = _make(tmp_path, "c.png", (300, 200), (0, 255, 0))
    layout = plan_layout([(400, 200), (200, 200), (300, 200)])
    data = compose_preview([a, b, c], layout.canvas_width // 2)
    with PILImage.open(io.BytesIO(data)) as im:
        assert im.format == "JPEG"
        assert abs(im.width - layout.canvas_width / 2) <= 1
        assert abs(im.height - layout.canvas_height / 2) <= 1
    big = compose_preview([a, b, c], layout.canvas_width * 3)
    with PILImage.open(io.BytesIO(big)) as im:
        assert im.size == (layout.canvas_width, layout.canvas_height)


def test_mpo_photo_is_accepted_as_jpeg(tmp_path):
    first = PILImage.new("RGB", (120, 80), (200, 30, 30))
    second = PILImage.new("RGB", (120, 80), (30, 30, 200))
    mpo = str(tmp_path / "phone.jpg")
    first.save(mpo, format="MPO", save_all=True, append_images=[second])
    with PILImage.open(mpo) as im:
        assert im.format == "MPO" and im.n_frames > 1
    b = _make(tmp_path, "b.jpg", (120, 80), (0, 0, 255))
    dst = str(tmp_path / "o.out")
    res = compose_to_file([mpo, b], dst, 90)
    assert res.format == "JPEG"
    pl = plan_layout([(120, 80), (120, 80)]).placements[0]
    with PILImage.open(dst) as out:
        assert out.format == "JPEG"
        assert _near(out.convert("RGB").getpixel((pl.x + 60, pl.y + 40)), (200, 30, 30))


def test_cmyk_icc_profile_is_not_embedded_in_rgb_output(tmp_path):
    paths = []
    for n in ("a", "b"):
        p = str(tmp_path / f"{n}.jpg")
        PILImage.new("CMYK", (50, 50), (0, 0, 0, 0)).save(p, icc_profile=b"fake-cmyk-profile")
        paths.append(p)
    dst = str(tmp_path / "o.jpg")
    compose_to_file(paths, dst, 90)
    with PILImage.open(dst) as im:
        assert im.info.get("icc_profile") is None


def test_preview_of_a_png_set_is_jpeg(tmp_path):
    a = _make(tmp_path, "a.png", (100, 100), (255, 0, 0))
    with PILImage.open(io.BytesIO(compose_preview([a], 1000))) as im:
        assert im.format == "JPEG"


def test_preview_of_a_large_set_does_not_build_the_full_canvas(tmp_path, monkeypatch):
    paths = [_make(tmp_path, f"p{i}.jpg", (1000, 800), (i * 20, 50, 50)) for i in range(8)]
    layout = plan_layout([(1000, 800)] * 8)
    max_width = 200
    scale = max_width / layout.canvas_width
    sizes = []
    real_new = PILImage.new

    def spy(mode, size, *a, **k):
        sizes.append(size)
        return real_new(mode, size, *a, **k)

    monkeypatch.setattr(PILImage, "new", spy)
    data = compose_preview(paths, max_width)
    assert sizes
    limit_w, limit_h = round(layout.canvas_width * scale) + 1, round(layout.canvas_height * scale) + 1
    assert all(w <= limit_w and h <= limit_h for w, h in sizes)
    with PILImage.open(io.BytesIO(data)) as im:
        assert im.width <= limit_w
