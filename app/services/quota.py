"""Tiga angka kuota per pilar (D24/PRD §7b) — satu-satunya sumber hitungan.

| angka                | isi                                                                  |
|----------------------|----------------------------------------------------------------------|
| terpakai_minggu_ini  | status ∈ {siap_tayang,terjadwal,tayang} & jadwal_tayang minggu ini    |
| pipeline_aktif       | status ∈ {review,siap_tayang,terjadwal,tayang} tanpa batas minggu     |
| belum_dijadwalkan    | pipeline_aktif tanpa jadwal_tayang                                    |
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import enums
from app.models import ContentItem, Pilar
from app.timeutil import week_bounds

STATUS_TERPAKAI = (enums.STATUS_SIAP_TAYANG, enums.STATUS_TERJADWAL, enums.STATUS_TAYANG)
STATUS_PIPELINE = (
    enums.STATUS_REVIEW,
    enums.STATUS_SIAP_TAYANG,
    enums.STATUS_TERJADWAL,
    enums.STATUS_TAYANG,
)


@dataclass
class KuotaPilar:
    pilar_slug: str
    pilar_nama: str
    kuota_persen: int
    kuota_mingguan: int
    terpakai_minggu_ini: int = 0
    pipeline_aktif: int = 0
    belum_dijadwalkan: int = 0
    terpakai_minggu_ini_ids: list[int] = field(default_factory=list)
    pipeline_aktif_ids: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "pilar": self.pilar_slug,
            "nama": self.pilar_nama,
            "kuota_persen": self.kuota_persen,
            "kuota_mingguan": self.kuota_mingguan,
            "terpakai_minggu_ini": self.terpakai_minggu_ini,
            "pipeline_aktif": self.pipeline_aktif,
            "belum_dijadwalkan": self.belum_dijadwalkan,
            "sisa_kuota_minggu_ini": max(self.kuota_mingguan - self.terpakai_minggu_ini, 0),
        }


def hitung(db: Session, *, hari: date | None = None) -> list[KuotaPilar]:
    """Hitung tiga angka untuk semua pilar aktif."""
    awal, akhir = week_bounds(hari)
    pilars = list(
        db.scalars(select(Pilar).where(Pilar.is_active.is_(True)).order_by(Pilar.urutan))
    )
    hasil: dict[int, KuotaPilar] = {
        p.id: KuotaPilar(
            pilar_slug=p.slug,
            pilar_nama=p.nama,
            kuota_persen=p.kuota_persen,
            kuota_mingguan=p.kuota_mingguan,
        )
        for p in pilars
    }

    rows = db.execute(
        select(
            ContentItem.id,
            ContentItem.pilar_id,
            ContentItem.status,
            ContentItem.jadwal_tayang,
        ).where(
            ContentItem.deleted_at.is_(None),
            ContentItem.status.in_(STATUS_PIPELINE),
        )
    ).all()

    for cid, pilar_id, status, jadwal in rows:
        slot = hasil.get(pilar_id)
        if slot is None:
            continue
        slot.pipeline_aktif += 1
        slot.pipeline_aktif_ids.append(cid)
        if jadwal is None:
            slot.belum_dijadwalkan += 1
        elif status in STATUS_TERPAKAI and awal.date() <= jadwal <= akhir.date():
            slot.terpakai_minggu_ini += 1
            slot.terpakai_minggu_ini_ids.append(cid)

    return list(hasil.values())


def total(db: Session, *, hari: date | None = None) -> dict:
    rows = hitung(db, hari=hari)
    return {
        "target_mingguan": sum(r.kuota_mingguan for r in rows),
        "terpakai_minggu_ini": sum(r.terpakai_minggu_ini for r in rows),
        "pipeline_aktif": sum(r.pipeline_aktif for r in rows),
        "belum_dijadwalkan": sum(r.belum_dijadwalkan for r in rows),
    }


def hitung_tayang_minggu_ini(db: Session, *, hari: date | None = None) -> int:
    awal, akhir = week_bounds(hari)
    return int(
        db.scalar(
            select(func.count(ContentItem.id)).where(
                ContentItem.deleted_at.is_(None),
                ContentItem.status == enums.STATUS_TAYANG,
                ContentItem.jadwal_tayang.is_not(None),
                ContentItem.jadwal_tayang >= awal.date(),
                ContentItem.jadwal_tayang <= akhir.date(),
            )
        )
        or 0
    )


def slot_terisi_minggu_ini(db: Session, *, hari: date | None = None) -> int:
    awal, akhir = week_bounds(hari)
    return int(
        db.scalar(
            select(func.count(ContentItem.id)).where(
                ContentItem.deleted_at.is_(None),
                ContentItem.jadwal_tayang.is_not(None),
                ContentItem.jadwal_tayang >= awal.date(),
                ContentItem.jadwal_tayang <= akhir.date(),
                ContentItem.status.in_((enums.STATUS_TERJADWAL, enums.STATUS_TAYANG)),
            )
        )
        or 0
    )
