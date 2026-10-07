"""Helper uji (bukan bagian aplikasi)."""
from __future__ import annotations

from app import enums
from app.services import checklist, content as content_service
from app.services.transitions import Aktor


def lengkapi_checklist(db, item) -> None:
    for row in checklist.daftar(db, item):
        checklist.set_centang(
            db, item, butir=row.butir, tercentang=True, actor_type=enums.ACTOR_BOSS, actor_id=None
        )
    db.flush()


def siapkan_sampai_terjadwal(db, item, tanggal, slot="pagi") -> None:
    """Boss: draft → (approve + jadwalkan)."""
    lengkapi_checklist(db, item)
    content_service.approve(db, item, jadwal_tayang=tanggal, slot_waktu=slot)
    db.flush()


def ringkas(item) -> dict:
    return {"id": item.id, "status": item.status, "judul": item.judul_hook}
