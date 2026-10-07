"""Kontrol checklist produksi per format (F7, PRD §11)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ContentItem, ProductionChecklist
from app.timeutil import now_wib_naive


def butir_untuk(fmt: str) -> list[str]:
    from app.seed_loader import taxonomy

    aturan = taxonomy().get("format_aturan", {}) or {}
    entry = aturan.get(fmt) or {}
    return list(entry.get("butir", []))


def sinkron_butir(db: Session, item: ContentItem) -> list[ProductionChecklist]:
    """Pastikan baris checklist sesuai format terkini; butir lama yang tak relevan dibuang."""
    diinginkan = butir_untuk(item.format)
    rows = {r.butir: r for r in db.scalars(
        select(ProductionChecklist).where(ProductionChecklist.content_id == item.id)
    )}
    hasil: list[ProductionChecklist] = []
    for butir in diinginkan:
        row = rows.pop(butir, None)
        if row is None:
            row = ProductionChecklist(
                content_id=item.id, butir=butir[:255], tercentang=False, diubah=now_wib_naive()
            )
            db.add(row)
            db.flush()
        hasil.append(row)
    for sisa in rows.values():
        db.delete(sisa)
    db.flush()
    return hasil


def daftar(db: Session, item: ContentItem) -> list[ProductionChecklist]:
    return sinkron_butir(db, item)


def set_centang(
    db: Session, item: ContentItem, *, butir: str, tercentang: bool, actor_type: str, actor_id: int | None
) -> ProductionChecklist:
    rows = sinkron_butir(db, item)
    target = next((r for r in rows if r.butir == butir), None)
    if target is None:
        # butir bebas (mis. dari UI) tetap diizinkan
        target = ProductionChecklist(
            content_id=item.id, butir=butir[:255], tercentang=tercentang, diubah=now_wib_naive()
        )
        db.add(target)
    target.tercentang = bool(tercentang)
    target.actor_type = actor_type
    target.actor_id = actor_id
    target.diubah = now_wib_naive()
    db.flush()
    return target


def set_centang_id(
    db: Session, item: ContentItem, *, checklist_id: int, tercentang: bool,
    actor_type: str, actor_id: int | None,
) -> ProductionChecklist | None:
    """Set centang berdasarkan ID baris checklist, bukan teks butir.

    Penting: teks butir memuat karakter seperti `<=` dan `—` yang di-escape HTML
    saat dirender ke form. Memakai teks sebagai nilai form membuat perbandingan
    gagal di server (butir "baru" yang ter-escape dibuat, butir asli tetap kosong).
    ID tidak punya masalah itu.
    """
    row = db.get(ProductionChecklist, int(checklist_id))
    if row is None or row.content_id != item.id:
        return None
    row.tercentang = bool(tercentang)
    row.actor_type = actor_type
    row.actor_id = actor_id
    row.diubah = now_wib_naive()
    db.flush()
    return row


def semua_tercentang(db: Session, item: ContentItem) -> bool:
    rows = sinkron_butir(db, item)
    if not rows:
        # format tanpa checklist (mis. belum didefinisikan di seed) tidak memblokir
        return True
    return all(r.tercentang for r in rows)


def belum_tercentang(db: Session, item: ContentItem) -> list[str]:
    return [r.butir for r in sinkron_butir(db, item) if not r.tercentang]
