"""3 pemicu warning, NOL blokir (D22).

(i)  format sama dipakai di dua slot pada tanggal yang sama
(ii) `question`/`meme_relatable` di slot `pagi`
(iii) dua item mengusulkan `usulan_jadwal` yang bertabrakan

Semua hasil muncul sebagai `warning[]` di respons API + badge di UI. Validator
tidak pernah menolak: app ini milik Boss.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import enums
from app.models import ContentItem
from app.seed_loader import slot_hours, slot_peran

FORMAT_ENGAGEMENT = ("question", "meme_relatable")
STATUS_ABAI = (enums.STATUS_ARSIP,)
JENDELA_TABRAKAN_JAM = 3


def _slot_dari_datetime(dt: datetime) -> str:
    """Slot ditentukan dari jam: sebelum jam slot malam → `pagi`."""
    hh, _ = slot_hours("malam")
    return "pagi" if dt.hour < hh else "malam"


def _tanggal_efektif(item: ContentItem) -> date | None:
    if item.jadwal_tayang:
        return item.jadwal_tayang
    if item.usulan_jadwal:
        return item.usulan_jadwal.date()
    return None


def _slot_efektif(item: ContentItem) -> str | None:
    if item.slot_waktu:
        return item.slot_waktu
    if item.usulan_jadwal:
        return _slot_dari_datetime(item.usulan_jadwal)
    return None


def _kandidat(db: Session, kecuali_id: int | None) -> list[ContentItem]:
    stmt = select(ContentItem).where(
        ContentItem.deleted_at.is_(None),
        ContentItem.status.not_in(STATUS_ABAI),
    )
    if kecuali_id is not None:
        stmt = stmt.where(ContentItem.id != kecuali_id)
    return list(db.scalars(stmt))


def evaluasi(db: Session, item: ContentItem) -> list[dict]:
    """Kembalikan daftar warning untuk item (belum tentu tersimpan)."""
    warnings: list[dict] = []
    tanggal = _tanggal_efektif(item)
    slot = _slot_efektif(item)
    lain = _kandidat(db, item.id)

    # (i) format sama di dua slot sehari
    if tanggal and slot:
        for other in lain:
            if other.format != item.format:
                continue
            if _tanggal_efektif(other) != tanggal:
                continue
            if _slot_efektif(other) == slot:
                continue
            warnings.append(
                {
                    "kode": "format_sama_dua_slot",
                    "pesan": (
                        f"Format '{item.format}' juga dipakai di slot "
                        f"{_slot_efektif(other)} pada {tanggal.isoformat()} "
                        f"(#{other.id} — {other.judul_hook})"
                    ),
                    "content_id": other.id,
                }
            )
            break

    # (ii) format engagement di slot pagi
    if item.format in FORMAT_ENGAGEMENT and slot == "pagi":
        warnings.append(
            {
                "kode": "engagement_di_slot_pagi",
                "pesan": (
                    f"Format '{item.format}' di slot pagi (jam 06:00) biasanya "
                    "engagement-nya rendah; slot malam lebih cocok. "
                    f"Peran slot pagi: {slot_peran().get('pagi', 'doa/pengingat pendek')}"
                ),
                "content_id": None,
            }
        )

    # (iii) dua item mengusulkan usulan_jadwal yang bertabrakan
    if item.usulan_jadwal:
        for other in lain:
            if other.usulan_jadwal is None:
                continue
            if other.usulan_jadwal.date() != item.usulan_jadwal.date():
                continue
            if _slot_dari_datetime(other.usulan_jadwal) != _slot_dari_datetime(item.usulan_jadwal):
                continue
            beda_jam = abs(
                (other.usulan_jadwal - item.usulan_jadwal).total_seconds()
            ) / 3600.0
            if beda_jam > JENDELA_TABRAKAN_JAM:
                continue
            warnings.append(
                {
                    "kode": "usulan_jadwal_tabrakan",
                    "pesan": (
                        f"Usulan jadwal bertabrakan dengan #{other.id} "
                        f"({other.usulan_jadwal.isoformat()} — {other.judul_hook})"
                    ),
                    "content_id": other.id,
                }
            )
            break

    return warnings


def evaluasi_slot(db: Session, item: ContentItem) -> list[dict]:
    """Warning khusus saat menjadwalkan (slot pagi/malam tgl sama dengan format sama)."""
    if not item.jadwal_tayang or not item.slot_waktu:
        return []
    return [w for w in evaluasi(db, item) if w["kode"] == "format_sama_dua_slot"]


def slot_kosong_hplus(db: Session, hari: date, jumlah_hari: int = 2) -> list[dict]:
    """Slot H+1..H+n yang masih kosong (PRD §13.2)."""
    from app.models import ScheduleSlot
    from app.timeutil import today_wib

    kosong: list[dict] = []
    dasar = today_wib()
    for delta in range(1, jumlah_hari + 1):
        tgl = dasar + timedelta(days=delta)
        terisi = {
            s.slot_waktu
            for s in db.scalars(
                select(ScheduleSlot).where(
                    ScheduleSlot.tanggal == tgl, ScheduleSlot.platform == "facebook"
                )
            )
        }
        for slot in enums.SLOTS:
            if slot not in terisi:
                kosong.append({"tanggal": tgl.isoformat(), "slot": slot})
    return kosong
