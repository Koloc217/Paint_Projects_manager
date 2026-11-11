"""Picture saving shared by covers, references and progress photos (hub/core/imagestore.py)."""
import base64

import pytest

from hub.core import imagestore

JPEG = b"\xff\xd8\xff" + b"x" * 20


def data_url(raw: bytes, mime="image/jpeg") -> str:
    return f"data:{mime};base64," + base64.b64encode(raw).decode()


def test_decode_roundtrip_and_errors():
    assert imagestore.decode_data_url(data_url(JPEG)) == JPEG
    with pytest.raises(ValueError):
        imagestore.decode_data_url("not a data url")
    with pytest.raises(ValueError):
        imagestore.decode_data_url("data:image/png;base64,@@@")


def test_save_keeps_the_original_and_adds_a_preview(tmp_path):
    thumb = imagestore.save_with_thumb(str(tmp_path), "p1_abc.png", b"\x89PNGdata", JPEG, max_thumb_bytes=100)
    assert thumb == "p1_abc_t.jpg"
    assert (tmp_path / "p1_abc.png").read_bytes() == b"\x89PNGdata"      # byte for byte
    assert (tmp_path / thumb).read_bytes() == JPEG


@pytest.mark.parametrize("bad_thumb", [None, b"", b"GIF89a....", JPEG * 10])
def test_bad_preview_is_skipped_but_the_picture_is_kept(tmp_path, bad_thumb):
    assert imagestore.save_with_thumb(str(tmp_path), "a.jpg", JPEG, bad_thumb, max_thumb_bytes=50) == ""
    assert [p.name for p in tmp_path.iterdir()] == ["a.jpg"]


def test_thumb_from_data_url_never_raises():
    assert imagestore.thumb_from_data_url(data_url(JPEG), 100) == JPEG
    assert imagestore.thumb_from_data_url("garbage", 100) is None
    assert imagestore.thumb_from_data_url("", 100) is None
    assert imagestore.thumb_from_data_url(data_url(JPEG), 5) is None          # over the limit


def test_random_base_is_unique_and_keeps_the_project_id():
    a, b = imagestore.random_base("PRJ-1"), imagestore.random_base("PRJ-1")
    assert a != b and a.startswith("PRJ-1_")
