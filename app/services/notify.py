"""Outbox notifikasi (D14/D18) — app HANYA menulis baris.

Pengirim ke Telegram = poller @hermes (bot profil `default`). Nol kredensial bot
di .env app. Kegagalan kirim tercatat sebagai data (`attempts`, `last_error`),
bukan kegagalan aksi Boss.

Mesin status (D18) — lihat `docs/POLLER-OUTBOX.md`:

    kirim ──klaim()──> proses ──tandai_terkirim()──> terkirim
                        │
                        ├──tandai_gagal()──> gagal (attempts habis / error permanen)
                        └──pulihkan_klaim_basi()──> kirim (poller mati di tengah jalan)

Klaim dilakukan satu per satu dengan `UPDATE ... WHERE id=:id AND status='kirim'`
(portabel SQLite/MariaDB, dan menghindari MySQL error 1093 soal subquery ke
tabel yang sama). Siapa pun yang `rowcount == 0` berarti kalah balapan.
"""
from __future__ import annotations

import json
import secrets

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import enums
from app.models import ContentItem, NotificationOutbox
from app.timeutil import now_wib_naive

# Batas percobaan kirim sebelum baris dinyatakan `gagal` (bukan dictatap selamanya).
MAX_ATTEMPTS = 3
# Umur klaim sebelum dianggap poller mati di tengah jalan (menit).
UMUR_KLAIM_MENIT = 10

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
        status=enums.OUTBOX_KIRIM,
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
    """Baris yang masih perlu dikirim — HANYA untuk tampilan/uji.

    Poller jangan memakai ini untuk mengirim (rawan dobel); pakai `klaim()`.
    """
    return list(
        db.scalars(
            select(NotificationOutbox)
            .where(NotificationOutbox.status == enums.OUTBOX_KIRIM)
            .order_by(NotificationOutbox.id.asc())
            .limit(limit)
        )
    )


def hitung_status(db: Session) -> dict[str, int]:
    """Ringkasan jumlah baris per status (dipakai UI/dashboard)."""
    from sqlalchemy import func

    baris = db.execute(
        select(NotificationOutbox.status, func.count(NotificationOutbox.id)).group_by(
            NotificationOutbox.status
        )
    ).all()
    hasil = {s: 0 for s in enums.OUTBOX_STATUSES}
    for status, jumlah in baris:
        hasil[str(status)] = int(jumlah)
    return hasil


# ---------------------------------------------------------------- klaim atomik
def klaim(
    db: Session, *, limit: int = 5, hanya_ids: list[int] | None = None
) -> list[NotificationOutbox]:
    """Klaim sampai `limit` baris `kirim` untuk SATU pengirim (poller).

    Setiap baris diklaim dengan satu UPDATE bersyarat: siapa pun yang berhasil
    (`rowcount == 1`) yang boleh mengirim. Baris yang diklaim pindah ke `proses`
    + `claim_token` unik + `claimed_at`, dan `attempts` naik saat klaim (jadi
    poller yang mati berkali-kali tidak bisa mengulang tanpa batas).

    `hanya_ids` membatasi kandidat ke baris tertentu (dipakai uji dan backfill);
    default None = seluruh antrean.
    """
    hasil: list[NotificationOutbox] = []
    for _ in range(max(1, int(limit))):
        q = (
            select(NotificationOutbox.id)
            .where(NotificationOutbox.status == enums.OUTBOX_KIRIM)
            .order_by(NotificationOutbox.id.asc())
            .limit(1)
        )
        if hanya_ids is not None:
            q = q.where(NotificationOutbox.id.in_(list(hanya_ids)))
        kandidat = db.execute(q).scalar()
        if kandidat is None:
            break
        token = secrets.token_urlsafe(24)[:36]
        res = db.execute(
            update(NotificationOutbox)
            .where(
                NotificationOutbox.id == kandidat,
                NotificationOutbox.status == enums.OUTBOX_KIRIM,
            )
            .values(
                status=enums.OUTBOX_PROSES,
                claim_token=token,
                claimed_at=now_wib_naive(),
                attempts=NotificationOutbox.attempts + 1,
            )
        )
        if not res.rowcount:
            continue  # kalah balapan; coba baris berikutnya
        row = db.scalar(
            select(NotificationOutbox).where(NotificationOutbox.claim_token == token)
        )
        if row is not None:
            hasil.append(row)
    db.flush()
    return hasil


def tandai_terkirim(db: Session, row: NotificationOutbox) -> NotificationOutbox:
    """Pengiriman sukses: `terkirim` + `sent_at`; klaim dilepas."""
    row.status = enums.OUTBOX_TERKIRIM
    row.sent_at = now_wib_naive()
    row.claim_token = None
    row.claimed_at = None
    row.last_error = None
    db.flush()
    return row


def tandai_gagal(db: Session, row: NotificationOutbox, pesan: str) -> NotificationOutbox:
    """Gagal permanen (attempts habis / error yang tidak bisa diulang)."""
    row.status = enums.OUTBOX_GAGAL
    row.last_error = (pesan or "")[:500]
    row.claim_token = None
    row.claimed_at = None
    db.flush()
    return row


def lepas_klaim(db: Session, row: NotificationOutbox, pesan: str | None = None) -> NotificationOutbox:
    """Kirim gagal tapi masih layak dicoba lagi → kembali ke `kirim`."""
    if row.attempts >= MAX_ATTEMPTS:
        alasan = f"attempts habis ({row.attempts})"
        return tandai_gagal(db, row, f"{pesan} — {alasan}" if pesan else alasan)
    row.status = enums.OUTBOX_KIRIM
    row.claim_token = None
    row.claimed_at = None
    if pesan:
        row.last_error = pesan[:500]
    db.flush()
    return row


def pulihkan_klaim_basi(
    db: Session, *, menit: int = UMUR_KLAIM_MENIT, max_attempts: int = MAX_ATTEMPTS
) -> dict[str, int]:
    """Selamatkan baris `proses` yang klaimnya sudah basi (poller mati di tengah).

    Bukan pengiriman ulang otomatis: baris hanya dikembalikan ke `kirim` kalau
    `attempts` masih di bawah batas, selebihnya ditandai `gagal` supaya kelihatan
    di UI. Ambang `menit` sengaja lebih besar daripada waktu kirim normal supaya
    poller yang masih hidup tidak direbut.
    """
    from datetime import timedelta

    batas = now_wib_naive() - timedelta(minutes=int(menit))
    row_basi = list(
        db.scalars(
            select(NotificationOutbox).where(
                NotificationOutbox.status == enums.OUTBOX_PROSES,
                NotificationOutbox.claimed_at < batas,
            )
        )
    )
    lagi = gagal = 0
    for row in row_basi:
        if row.attempts >= int(max_attempts):
            tandai_gagal(db, row, f"klaim basi & attempts habis ({row.attempts})")
            gagal += 1
        else:
            row.status = enums.OUTBOX_KIRIM
            row.claim_token = None
            row.claimed_at = None
            lagi += 1
    db.flush()
    return {"dikembalikan": lagi, "digagalkan": gagal, "diperiksa": len(row_basi)}
