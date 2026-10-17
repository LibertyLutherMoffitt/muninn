"""Pictures before sending: phone photos are huge (4–12 MB) and Bluetooth plus
relays move ~10–50 kB/s, so a raw photo would take minutes. Big photos are
turned upright (EXIF), scaled to ≤ 1600 px, re-encoded as JPEG (~200–400 kB)
and stripped of metadata (EXIF location!). GIFs and small images go as they
are. Shared by the GUI, terminal client and bot; mirrored on Android in
Media.kt. Needs Pillow; without it files go out unchanged.
"""

from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path

MAX_DIM = 1600
QUALITY = 82
SMALL = 400_000  # bytes: smaller pictures are sent untouched
SETTING = "photo_quality"  # "compressed" (default) | "original"


def wants_original(storage) -> bool:
    return storage.get_setting(SETTING, "compressed") == "original"


def prepare(path: str, mime: str, original: bool = False) -> tuple[str, str, str]:
    """→ (path to send, mime, file name). May write a temp JPEG."""
    name = os.path.basename(path)
    if original or not mime.startswith("image/") or mime == "image/gif":
        return path, mime, name
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return path, mime, name
    try:
        with Image.open(path) as im:
            big = max(im.size) > MAX_DIM or os.path.getsize(path) > SMALL
            if not big:
                return path, mime, name
            im = ImageOps.exif_transpose(im)  # upright, then metadata is dropped
            im.thumbnail((MAX_DIM, MAX_DIM), Image.Resampling.LANCZOS)
            if im.mode not in ("RGB", "L"):
                bg = Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im, mask=im.getchannel("A") if "A" in im.getbands() else None)
                im = bg
            fd, out = tempfile.mkstemp(prefix="muninn-", suffix=".jpg")
            with os.fdopen(fd, "wb") as f:
                im.save(f, "JPEG", quality=QUALITY, optimize=True, progressive=True)
        if os.path.getsize(out) >= os.path.getsize(path):
            os.unlink(out)  # recompressing didn't help
            return path, mime, name
        return out, "image/jpeg", Path(name).stem + ".jpg"
    except Exception:
        return path, mime, name


def thumbnail(path: str) -> bytes:
    """≤ ~10 kB JPEG preview so the picture shows before it has downloaded."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return b""
    try:
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((160, 160))
            for q in (70, 50, 30):
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=q)
                if buf.tell() <= 10_000:
                    return buf.getvalue()
    except Exception:
        pass
    return b""


def send_file(
    mesh, conv: str, path: str, caption: str = "", reply_to: bytes | None = None
) -> bytes:
    """Send any file; pictures are prepared first (unless the user chose
    original quality) and get a preview thumbnail."""
    import mimetypes

    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    out, mime, name = prepare(path, mime, original=wants_original(mesh.storage))
    try:
        thumb = thumbnail(out) if mime.startswith("image/") else b""
        return mesh.send_file(
            conv, out, mime, name=name, caption=caption, thumb=thumb, reply_to=reply_to
        )
    finally:
        if out != path:
            try:
                os.unlink(out)
            except OSError:
                pass
