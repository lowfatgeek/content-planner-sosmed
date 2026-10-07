"""Hook: normalisasi + registry anti-duplikasi (F11, D24)."""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import ContentItem, HookRegistry
from app.timeutil import now_wib_naive

_KATA_BANDING = 3
_PUNCT = re.compile(r"[^\w\s]", flags=re.UNICODE)
_SPASI = re.compile(r"\s+")


def normalisasi_hook(judul: str, kata: int = _KATA_BANDING) -> str:
    """3 kata pertama, lowercase, tanpa tanda baca, spasi dirapikan."""
    text = unicodedata.normalize("NFKC", judul or "").lower()
    text = _PUNCT.sub(" ", text)
    text = _SPASI.sub(" ", text).strip()
    return " ".join(text.split()[:kata])


def kata_pembanding() -> int:
    """Jumlah kata pembanding — dibaca dari seed, bukan hardcode di kode."""
    try:
        from app.seed_loader import taxonomy

        return int(taxonomy().get("batas", {}).get("hook_kata_pembanding", _KATA_BANDING))
    except Exception:  # pragma: no cover - seed rusak ditangani validasi seed
        return _KATA_BANDING


def cari_duplikat(db: Session, judul: str, *, kecuali_id: int | None = None) -> list[HookRegistry]:
    """Baris registry yang punya hook_norm sama dengan judul ini."""
    norm = normalisasi_hook(judul, kata_pembanding())
    if not norm:
        return []
    stmt = select(HookRegistry).where(HookRegistry.hook_norm == norm)
    if kecuali_id is not None:
        stmt = stmt.where((HookRegistry.content_id.is_(None)) | (HookRegistry.content_id != kecuali_id))
    return list(db.scalars(stmt))


def daftarkan_hook(db: Session, item: ContentItem) -> HookRegistry:
    """Catat hook item ke registry (idempoten per content_id)."""
    norm = normalisasi_hook(item.judul_hook, kata_pembanding())
    if not norm:
        norm = f"konten-{item.id}"
    row = db.get(HookRegistry, norm)
    if row is None:
        row = HookRegistry(
            hook_norm=norm,
            content_id=item.id,
            asal="konten",
            dibuat=now_wib_naive(),
        )
        db.add(row)
    return row


def lepas_hook(db: Session, content_id: int) -> int:
    """Saat konten masuk `arsip`: lepas baris `asal='konten'` (baris seed tetap tinggal)."""
    rows = list(
        db.scalars(
            select(HookRegistry).where(
                HookRegistry.content_id == content_id, HookRegistry.asal == "konten"
            )
        )
    )
    for row in rows:
        db.delete(row)
    return len(rows)


def daftar_registry(db: Session, limit: int = 500) -> list[HookRegistry]:
    return list(db.scalars(select(HookRegistry).order_by(HookRegistry.hook_norm).limit(limit)))


def info_konten(db: Session, hook_row: HookRegistry) -> ContentItem | None:
    if hook_row.content_id is None:
        return None
    return db.get(ContentItem, hook_row.content_id)


def seed_registry(db: Session) -> int:
    """Isi `hook_registry` dari seed/hooks.yaml (D24). Idempoten."""
    from app.seed_loader import hooks_seed

    dibuat = 0
    now = now_wib_naive()
    for entry in hooks_seed():
        judul = entry.get("judul") if isinstance(entry, dict) else str(entry)
        if not judul:
            continue
        norm = normalisasi_hook(str(judul), kata_pembanding())
        if not norm or db.get(HookRegistry, norm) is not None:
            continue
        db.add(HookRegistry(hook_norm=norm, content_id=None, asal="seed", dibuat=now))
        dibuat += 1
    return dibuat


def _stamp(dt: datetime | None = None) -> datetime:
    return dt or now_wib_naive()
