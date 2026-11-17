"""The only two uploads in the app: profile photo and visa photo (FR-1.5, CLAUDE rule 9).
Saved under UPLOAD_DIR (outside static/) and served only through the auth-checked /app/uploads route."""
import os
import secrets
from pathlib import Path

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "uploads")).resolve()
MAX_BYTES = 5 * 1024 * 1024
# first bytes of each allowed type – the browser's content-type is not trusted
SIGNATURES = {b"\xff\xd8\xff": ".jpg", b"\x89PNG\r\n\x1a\n": ".png"}


def _kind(head):
    for sig, ext in SIGNATURES.items():
        if head.startswith(sig):
            return ext
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    return None


def save_photo(upload, folder):
    """Validate and save one photo. Returns (relative_path, None) or (None, error message).
    An empty file input returns (None, None)."""
    if upload is None or not getattr(upload, "filename", ""):
        return None, None
    data = upload.file.read(MAX_BYTES + 1)
    if not data:
        return None, None
    if len(data) > MAX_BYTES:
        return None, "Photo must be 5 MB or smaller."
    ext = _kind(data[:16])
    if not ext:
        return None, "Only JPG, PNG or WEBP photos are allowed."
    rel = Path(folder) / f"{secrets.token_hex(12)}{ext}"
    dest = UPLOAD_DIR / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return str(rel), None


def resolve(rel):
    """Absolute path of a stored upload, or None if it escapes UPLOAD_DIR or is missing."""
    p = (UPLOAD_DIR / rel).resolve()
    if UPLOAD_DIR not in p.parents or not p.is_file():
        return None
    return p
