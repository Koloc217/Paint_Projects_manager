"""Image format detection by content (common.detect_image_ext)."""
import pytest

from common import detect_image_ext


def ftyp(major: bytes, *compatible: bytes) -> bytes:
    body = major + b"\x00\x00\x00\x00" + b"".join(compatible)
    box = (8 + len(body)).to_bytes(4, "big") + b"ftyp" + body
    return box + b"\x00\x00\x00\x08meta" + b"\x00" * 32


@pytest.mark.parametrize("raw, ext", [
    (b"\xff\xd8\xff\xe0" + b"\x00" * 16, "jpg"),
    (b"\x89PNG\r\n\x1a\n" + b"\x00" * 16, "png"),
    (b"GIF89a" + b"\x00" * 16, "gif"),
    (b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 16, "webp"),
    (ftyp(b"avif", b"mif1", b"miaf"), "avif"),
    (ftyp(b"avis", b"msf1", b"avif"), "avif"),
    (ftyp(b"mif1", b"avif", b"miaf"), "avif"),          # avif only among the compatible brands
])
def test_known_formats(raw, ext):
    assert detect_image_ext(raw) == ext


@pytest.mark.parametrize("raw", [
    ftyp(b"heic", b"mif1", b"heic"),                     # HEIC: same container, browsers can't show it
    ftyp(b"mif1", b"heic"),
    ftyp(b"isom", b"mp41"),                              # MP4 video
    b"\x00\x00\x00\x18ftyp",                             # cut short
    b"\x00\x00\x10\x00ftypavif" + b"\x00" * 8,           # box size beyond the data
    b"<svg xmlns='http://www.w3.org/2000/svg'></svg>",
    b"",
])
def test_rejected(raw):
    assert detect_image_ext(raw) is None


def test_avif_reference_upload_and_serving(client):
    """An AVIF reference is stored as .avif and served as image/avif (not text/plain or octet-stream)."""
    import base64
    from helpers import make_project, project

    pid = make_project(client)
    raw = ftyp(b"avif", b"mif1", b"miaf")
    res = client.post(f"/api/projects/{pid}/refs",
                      json={"image": "data:image/avif;base64," + base64.b64encode(raw).decode()}).json()
    assert res["status"] == "ok", res
    url = project(client, pid)["Refs"][0]["Url"]
    assert url.endswith(".avif")
    got = client.get(url)
    assert got.status_code == 200
    assert got.headers["content-type"] == "image/avif"
    assert got.content == raw                      # the original is kept byte for byte
