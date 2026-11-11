"""Saving uploaded pictures: covers, paint references and progress photos share these steps.

A picture is stored byte for byte; its preview (a small JPEG the browser or Telegram made) is stored next to it as
<name>_t.jpg and only if it really is a JPEG within the size limit. A missing or bad preview never stops the
picture itself: the phone layout shows the original instead.
"""
import base64
import os
import re
import uuid

from hub.core.i18n import tr
from hub.core.integrity import cover_thumb_name

JPEG_SIGNATURE = b"\xff\xd8\xff"


def detect_image_ext(raw: bytes):
    """Image format by the file content, not by what the client claims."""
    if raw.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    if _is_avif(raw):
        return "avif"
    return None


AVIF_BRANDS = (b"avif", b"avis")     # still image / image sequence


def _is_avif(raw: bytes) -> bool:
    """AVIF is an ISO BMFF file that starts with an 'ftyp' box naming avif/avis as the major or a compatible brand.
    HEIC uses the same container with other brands (heic, mif1 without avif) and is not accepted: browsers can't show it."""
    if len(raw) < 16 or raw[4:8] != b"ftyp":
        return False
    size = int.from_bytes(raw[:4], "big")
    if size < 16 or size > min(len(raw), 4096):
        return False
    if raw[8:12] in AVIF_BRANDS:
        return True
    brands = raw[16:size]                 # after major brand (4) and minor version (4)
    return any(brands[i:i + 4] in AVIF_BRANDS for i in range(0, len(brands) - 3, 4))


def decode_data_url(data_url: str) -> bytes:
    """'data:image/...;base64,XXXX' -> bytes. ValueError carries a translated message for the user."""
    m = re.match(r"^data:[^;,]*;base64,(.+)$", data_url or "", re.S)
    if not m:
        raise ValueError(tr("err.bad_image_data"))
    try:
        return base64.b64decode(m.group(1), validate=True)
    except Exception:
        raise ValueError(tr("err.image_unreadable"))


def is_jpeg(raw: bytes) -> bool:
    return bool(raw) and raw.startswith(JPEG_SIGNATURE)


def usable_thumb(raw, max_bytes: int) -> bool:
    return bool(raw) and is_jpeg(raw) and len(raw) <= max_bytes


def thumb_from_data_url(data_url, max_bytes: int):
    """Preview bytes from a data URL, or None when absent, unreadable, not a JPEG or too big."""
    if not data_url:
        return None
    try:
        raw = decode_data_url(data_url)
    except ValueError:
        return None
    return raw if usable_thumb(raw, max_bytes) else None


def random_base(project_id: str) -> str:
    """File name without extension: the project id and a random suffix (two uploads never overwrite each other)."""
    return f"{project_id}_{uuid.uuid4().hex[:8]}"


def save_with_thumb(folder: str, filename: str, raw: bytes, thumb_raw=None, max_thumb_bytes: int = 0) -> str:
    """Writes the picture and, when the preview is usable, its <name>_t.jpg. Returns the preview's file name ('' if none)."""
    with open(os.path.join(folder, filename), "wb") as f:
        f.write(raw)
    if not usable_thumb(thumb_raw, max_thumb_bytes):
        return ""
    thumb_name = cover_thumb_name(filename)
    with open(os.path.join(folder, thumb_name), "wb") as f:
        f.write(thumb_raw)
    return thumb_name
