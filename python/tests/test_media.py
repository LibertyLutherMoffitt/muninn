"""Photos are shrunk, turned upright and stripped of location before sending."""

import os

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from muninn import media  # noqa: E402


def _phone_photo(path, w=4000, h=3000):
    im = Image.effect_noise((w, h), 60).convert("RGB")  # noisy = big like a real photo
    exif = Image.Exif()
    exif[0x0112] = 6  # orientation: rotate 90° (portrait shot)
    exif[0x8825] = {2: (52.0, 31.0, 0.0)}  # GPS latitude
    im.save(path, "JPEG", quality=95, exif=exif)


def test_big_photo_is_shrunk_upright_and_stripped(tmp_path):
    src = tmp_path / "IMG_0001.jpeg"
    _phone_photo(src)
    out, mime, name = media.prepare(str(src), "image/jpeg")
    assert out != str(src) and mime == "image/jpeg" and name == "IMG_0001.jpg"
    with Image.open(out) as im:
        assert max(im.size) == 1600
        assert im.size[1] > im.size[0]  # portrait: EXIF rotation applied
        assert not im.getexif()  # no metadata (no location) left
    assert os.path.getsize(out) < os.path.getsize(src) / 3
    os.unlink(out)


def test_small_images_gifs_and_original_setting_untouched(tmp_path):
    small = tmp_path / "s.png"
    Image.new("RGB", (300, 200), "red").save(small)
    assert media.prepare(str(small), "image/png")[0] == str(small)
    gif = tmp_path / "a.gif"
    Image.new("P", (2000, 2000)).save(gif)
    assert media.prepare(str(gif), "image/gif")[0] == str(gif)
    big = tmp_path / "b.jpg"
    _phone_photo(big)
    assert media.prepare(str(big), "image/jpeg", original=True)[0] == str(big)
    assert 0 < len(media.thumbnail(str(big))) <= 10_000
