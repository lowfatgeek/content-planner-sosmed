"""Serialisasi content_item → dict untuk API & UI (dua tingkat akses, D9/§7d.4)."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import enums
from app.models import ContentItem, ContentRevision
from app.timeutil import iso_wib

# Item milik Boss yang dibaca agent: metadata saja (cukup untuk mencegah tabrakan tema)
METADATA_ONLY_FIELDS = (
    "id",
    "judul_hook",
    "pilar",
    "pilar_nama",
    "format",
    "kanal",
    "status",
    "jadwal_tayang",
    "slot_waktu",
)


def versi_terakhir(db: Session, content_id: int) -> int:
    return int(
        db.scalar(
            select(func.max(ContentRevision.versi)).where(ContentRevision.content_id == content_id)
        )
        or 0
    )


def _base(item: ContentItem) -> dict:
    return {
        "id": item.id,
        "judul_hook": item.judul_hook,
        "hook_norm": item.hook_norm,
        "pilar": item.pilar.slug if item.pilar else None,
        "pilar_nama": item.pilar.nama if item.pilar else None,
        "format": item.format,
        "kanal": item.kanal,
        "cta": item.cta,
        "hashtags": item.hashtags,
        "fact_level": item.fact_level,
        "sumber_dalil": item.sumber_dalil,
        "verifikator": item.verifikator,
        "visual_note": item.visual_note,
        "status": item.status,
        "source": item.source,
        "owner_token_id": item.owner_token_id,
        "request_ref": item.request_ref,
        "usulan_jadwal": iso_wib(item.usulan_jadwal),
        "jadwal_tayang": item.jadwal_tayang.isoformat() if item.jadwal_tayang else None,
        "slot_waktu": item.slot_waktu,
        "submitted_at": iso_wib(item.submitted_at),
        "reviewed_at": iso_wib(item.reviewed_at),
        "approved_at": iso_wib(item.approved_at),
        "posted_at": iso_wib(item.posted_at),
        "tayang_at": iso_wib(item.tayang_at),
        "failed_at": iso_wib(item.failed_at),
        "link_posting": item.link_posting,
        "platform_post_id": item.platform_post_id,
        "last_error": item.last_error,
        "reject_reason": item.reject_reason,
        "deleted_at": iso_wib(item.deleted_at),
        "dibuat": iso_wib(item.dibuat),
        "diubah": iso_wib(item.diubah),
    }


def content_dict(
    db: Session,
    item: ContentItem,
    *,
    viewer: str = enums.ACTOR_BOSS,
    token_id: int | None = None,
    warnings: list[dict] | None = None,
) -> dict:
    """viewer='agent' → item Boss hanya metadata; naskah penuh hanya item milik token."""
    milik_pemanggil = token_id is not None and item.owner_token_id == token_id

    if viewer == enums.ACTOR_AGENT and not milik_pemanggil:
        data = {k: v for k, v in _base(item).items() if k in METADATA_ONLY_FIELDS}
        data["read_mode"] = "metadata_saja"
        return data

    data = _base(item)
    data["naskah_md"] = item.naskah_md
    data["caption_fb"] = item.caption_fb
    data["versi_terakhir"] = versi_terakhir(db, item.id)
    if warnings is not None:
        data["warning"] = warnings
    return data


def feed_item(item: ContentItem, *, versi: int, milik_pemanggil: bool, warnings=None) -> dict:
    """Baris feed kursor (§7b) — item sendiri ikut membawa reject_reason + versi_terakhir."""
    data = {
        "id": item.id,
        "judul_hook": item.judul_hook,
        "pilar": item.pilar.slug if item.pilar else None,
        "format": item.format,
        "kanal": item.kanal,
        "status": item.status,
        "source": item.source,
        "jadwal_tayang": item.jadwal_tayang.isoformat() if item.jadwal_tayang else None,
        "slot_waktu": item.slot_waktu,
        "approved_at": iso_wib(item.approved_at),
        "reviewed_at": iso_wib(item.reviewed_at),
        "diubah": iso_wib(item.diubah),
        "milik_pemanggil": milik_pemanggil,
        "versi_terakhir": versi,
    }
    if milik_pemanggil:
        data["reject_reason"] = item.reject_reason
        data["last_error"] = item.last_error
        data["usulan_jadwal"] = iso_wib(item.usulan_jadwal)
    if warnings:
        data["warning"] = warnings
    return data


def queue_item(item: ContentItem) -> dict:
    """Baris `queue?due=` — meja kerja agent (payload yang benar-benar dibutuhkan untuk posting)."""
    return {
        "id": item.id,
        "judul_hook": item.judul_hook,
        "pilar": item.pilar.slug if item.pilar else None,
        "format": item.format,
        "kanal": item.kanal,
        "naskah_md": item.naskah_md,
        "caption_fb": item.caption_fb,
        "cta": item.cta,
        "hashtags": item.hashtags,
        "visual_note": item.visual_note,
        "jadwal_tayang": item.jadwal_tayang.isoformat() if item.jadwal_tayang else None,
        "slot_waktu": item.slot_waktu,
        "jadwal_datetime": iso_wib(
            _jadwal_dt(item)
        ),
        "approved_at": iso_wib(item.approved_at),
        "manual": item.format in enums.MANUAL_FORMATS or item.kanal in enums.MANUAL_KANALS,
    }


def _jadwal_dt(item: ContentItem):
    from app.timeutil import slot_datetime

    if item.jadwal_tayang and item.slot_waktu:
        return slot_datetime(item.jadwal_tayang, item.slot_waktu)
    return None
