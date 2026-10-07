"""Outbox notifikasi (D14/D18) — app HANYA menulis baris.

Pengirim ke Telegram = poller @hermes (bot profil `default`). Nol kredensial bot
di .env app. Kegagalan kirim tercatat sebagai data (`attempts`, `last_error`),
bukan kegagalan aksi Boss.
"""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ContentItem, NotificationOutbox
from app.timeutil import now_wib_naive

KIND_DRAFT_AGENT = "draft_agent_masuk_review"
KIND_APPROVE = "disetujui"
KIND_TOLAK = "ditolak"
KIND_POSTED = "tayang"
KIND_FAILED = "gagal_posting"
KIND_TERLAMBAT = "item_lewat_jadwal"


def tulis(
    db: Session,
    *,
    kind: str,
    content_id: int | None,
    payload: dict | None = None,
) -> NotificationOutbox:
    row = NotificationOutbox(
        kind=kind,
        content_id=content_id,
        payload_json=json.dumps(payload or {}, ensure_ascii=False),
        status="kirim",
        attempts=0,
        created_at=now_wib_naive(),
    )
    db.add(row)
    db.flush()
    return row


def notif_draft_masuk_review(db: Session, item: ContentItem) -> NotificationOutbox:
    return tulis(
        db,
        kind=KIND_DRAFT_AGENT,
        content_id=item.id,
        payload={
            "judul_hook": item.judul_hook,
            "pilar": item.pilar.slug if item.pilar else None,
            "format": item.format,
            "source": item.source,
            "usulan_jadwal": item.usulan_jadwal.isoformat() if item.usulan_jadwal else None,
            "request_ref": item.request_ref,
            "pesan": f"Draft agent masuk review: {item.judul_hook}",
            "tautan": f"/review",
        },
    )


def notif_approve(db: Session, item: ContentItem) -> NotificationOutbox:
    return tulis(
        db,
        kind=KIND_APPROVE,
        content_id=item.id,
        payload={
            "judul_hook": item.judul_hook,
            "jadwal_tayang": item.jadwal_tayang.isoformat() if item.jadwal_tayang else None,
            "slot_waktu": item.slot_waktu,
            "pesan": f"Disetujui: {item.judul_hook}",
        },
    )


def notif_tolak(db: Session, item: ContentItem) -> NotificationOutbox:
    return tulis(
        db,
        kind=KIND_TOLAK,
        content_id=item.id,
        payload={
            "judul_hook": item.judul_hook,
            "reject_reason": item.reject_reason,
            "pesan": f"Ditolak: {item.judul_hook} — {item.reject_reason}",
        },
    )


def notif_posted(db: Session, item: ContentItem) -> NotificationOutbox:
    return tulis(
        db,
        kind=KIND_POSTED,
        content_id=item.id,
        payload={
            "judul_hook": item.judul_hook,
            "link_posting": item.link_posting,
            "platform_post_id": item.platform_post_id,
            "pesan": f"Tayang: {item.judul_hook}",
        },
    )


def notif_failed(db: Session, item: ContentItem) -> NotificationOutbox:
    return tulis(
        db,
        kind=KIND_FAILED,
        content_id=item.id,
        payload={
            "judul_hook": item.judul_hook,
            "last_error": item.last_error,
            "pesan": f"GAGAL posting: {item.judul_hook} — {item.last_error}",
        },
    )


def ambil_menunggu(db: Session, limit: int = 50) -> list[NotificationOutbox]:
    """Dipakai poller @hermes: baris yang masih perlu dikirim."""
    return list(
        db.scalars(
            select(NotificationOutbox)
            .where(NotificationOutbox.status == "kirim")
            .order_by(NotificationOutbox.id.asc())
            .limit(limit)
        )
    )
