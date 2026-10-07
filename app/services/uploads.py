"""Validasi unggahan aset: ekstensi + MIME + magic bytes (F8).

Jalur UI dan jalur API memakai fungsi yang SAMA — jadi tidak ada celah
"UI aman, API longgar".
"""
from __future__ import annotations

import hashlib
import re
import secrets
from pathlib import Path

from app.config import settings
from app.errors import ValidationError

_NAMA_AMAN = re.compile(r"[^A-Za-z0-9._-]+")

MAGIC = {
    "jpg": (b"\xff\xd8\xff",),
    "jpeg": (b"\xff\xd8\xff",),
    "png": (b"\x89PNG\r\n\x1a\n",),
    "gif": (b"GIF87a", b"GIF89a"),
    "webp": (b"RIFF",),  # dicek lebih lanjut: byte 8..12 == 'WEBP'
}

# Ekstensi yang mesti ditolak walaupun MIME dipalsukan
EKSTENSI_BERBAHAYA = {
    "php",
    "php3",
    "php4",
    "php5",
    "phtml",
    "phar",
    "exe",
    "dll",
    "sh",
    "bash",
    "py",
    "pl",
    "rb",
    "jsp",
    "asp",
    "aspx",
    "cgi",
    "htaccess",
    "js",
    "html",
    "htm",
    "svg",
    "xml",
}


def _ext(nama: str) -> str:
    return (nama.rsplit(".", 1)[-1] if "." in nama else "").lower().strip()


def periksa(nama_file: str, content_type: str | None, data: bytes) -> str:
    """Kembalikan ekstensi yang sudah tervalidasi, atau raise ValidationError."""
    ext = _ext(nama_file or "")
    if not ext:
        raise ValidationError("Nama berkas tanpa ekstensi ditolak")
    if ext in EKSTENSI_BERBAHAYA or ext not in settings.ALLOWED_IMAGE_EXT:
        raise ValidationError(
            f"Ekstensi '.{ext}' tidak diizinkan. Hanya: "
            f"{', '.join(settings.ALLOWED_IMAGE_EXT)}"
        )
    if len(data) == 0:
        raise ValidationError("Berkas kosong")
    if len(data) > settings.MAX_UPLOAD_BYTES:
        raise ValidationError(
            f"Ukuran berkas melebihi batas {settings.MAX_UPLOAD_BYTES // (1024 * 1024)} MB"
        )

    mime = (content_type or "").split(";")[0].strip().lower()
    if mime and mime not in settings.ALLOWED_IMAGE_MIME:
        raise ValidationError(f"Content-Type '{mime}' tidak diizinkan")

    if not _magic_cocok(ext, data):
        raise ValidationError(
            f"Isi berkas tidak cocok dengan ekstensi '.{ext}' (magic bytes tidak sesuai)"
        )
    return ext


def _magic_cocok(ext: str, data: bytes) -> bool:
    signatures = MAGIC.get(ext, ())
    if not any(data.startswith(sig) for sig in signatures):
        return False
    if ext == "webp":
        return len(data) >= 12 and data[8:12] == b"WEBP"
    return True


def simpan(nama_file: str, content_type: str | None, data: bytes) -> tuple[str, str, str]:
    """Validasi lalu tulis berkas ke UPLOAD_DIR. Kembalikan (nama_berkas, sha256, ext)."""
    ext = periksa(nama_file, content_type, data)
    bersih = _NAMA_AMAN.sub("_", Path(nama_file).stem)[:60] or "aset"
    token = secrets.token_hex(6)
    nama = f"{bersih}-{token}.{ext}"
    settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    target = settings.UPLOAD_DIR / nama
    target.write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    return nama, sha, ext


def hapus(path_relatif: str) -> bool:
    target = (settings.UPLOAD_DIR / Path(path_relatif).name).resolve()
    root = settings.UPLOAD_DIR.resolve()
    if root not in target.parents:  # anti path traversal
        return False
    if target.exists():
        target.unlink()
        return True
    return False


def ukuran_human(n: int) -> str:
    ukuran = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if ukuran < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(ukuran)} B"
            return f"{ukuran:.1f} {unit}"
        ukuran /= 1024
    return f"{ukuran:.1f} GB"
