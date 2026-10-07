"""Orkestrasi siklus hidup konten: create → review → approve/reject → jadwal → tayang.

Semua perubahan status lewat sini supaya aturan §10, audit (F19), hook registry (F11),
warning (D22), dan outbox (F20) tidak pernah terlewat.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import enums
from app.errors import BadRequest, Conflict, Forbidden, NotFound, ValidationError
from app.models import Asset, ContentItem, ContentRevision, Pilar, ScheduleSlot
from app.seed_loader import pilar_by_slug
from app.services import audit, checklist, hooks, notify, quota, warnings
from app.services.transitions import Aktor, pastikan_tidak_beku, validasi
from app.timeutil import today_wib, to_db


# ---------------------------------------------------------------- lookup
def ambil(db: Session, content_id: int) -> ContentItem:
    item = db.get(ContentItem, content_id)
    if item is None or item.deleted_at is not None:
        raise NotFound(f"Konten #{content_id} tidak ditemukan")
    return item


def pilar_dari_slug(db: Session, slug: str) -> Pilar:
    pilar = db.scalar(select(Pilar).where(Pilar.slug == (slug or "").strip()))
    if pilar is None:
        raise ValidationError(
            f"Pilar '{slug}' tidak ada di taxonomy. "
            f"Slug yang sah: {', '.join(sorted(pilar_by_slug()))}"
        )
    return pilar


# ---------------------------------------------------------------- revisi
def tambah_revisi(
    db: Session,
    item: ContentItem,
    *,
    naskah_md: str,
    caption: str | None,
    catatan: str | None,
    aktor: Aktor,
) -> ContentRevision:
    """Append-only: versi lama TIDAK PERNAH ditimpa (F3)."""
    versi = (
        db.scalar(
            select(ContentRevision.versi)
            .where(ContentRevision.content_id == item.id)
            .order_by(ContentRevision.versi.desc())
            .limit(1)
        )
        or 0
    ) + 1
    from app.timeutil import now_wib_naive

    row = ContentRevision(
        content_id=item.id,
        versi=versi,
        naskah_md=naskah_md,
        caption=caption,
        catatan=catatan,
        actor_type=aktor.type,
        actor_id=aktor.id,
        dibuat=now_wib_naive(),
    )
    db.add(row)
    db.flush()
    return row


def catat_catatan_revisi(db: Session, item: ContentItem, catatan: str) -> ContentRevision | None:
    """Tempel catatan (mis. alasan tolak) pada revisi TERAKHIR.

    T5 menulis "`reject_reason` … disalin ke `catatan_revisi`". Menambah baris versi
    BARU dengan naskah identik akan menggeser arti `versi` (yang dimaksudkan sebagai
    jumlah versi naskah) dan membingungkan agent yang memakai `versi_terakhir`
    sebagai penanda; karena itu catatannya ditempel ke revisi terakhir, bukan
    menaikkan versi. Interpretasi ini dicatat di README untuk dikonfirmasi @content.
    """
    row = db.scalar(
        select(ContentRevision)
        .where(ContentRevision.content_id == item.id)
        .order_by(ContentRevision.versi.desc())
        .limit(1)
    )
    if row is None:
        return None
    lama = (row.catatan or "").strip()
    baru = f"{lama}\n{catatan}".strip() if lama else catatan
    row.catatan = baru[:2000]
    db.flush()
    return row


def versi_terakhir(db: Session, content_id: int) -> int:
    return int(
        db.scalar(
            select(ContentRevision.versi)
            .where(ContentRevision.content_id == content_id)
            .order_by(ContentRevision.versi.desc())
            .limit(1)
        )
        or 0
    )


# ---------------------------------------------------------------- payload
def _set_pilar(db: Session, item: ContentItem, slug: str | None) -> None:
    if slug is None:
        return
    pilar = pilar_dari_slug(db, slug)
    item.pilar_id = pilar.id


def _validasi_enum(item: ContentItem) -> None:
    if item.format not in enums.FORMATS:
        raise ValidationError(f"Format '{item.format}' tidak ada di taxonomy")
    if item.kanal not in enums.KANALS:
        raise ValidationError(f"Kanal '{item.kanal}' tidak ada di taxonomy")
    if item.cta not in enums.CTAS:
        raise ValidationError(f"CTA '{item.cta}' tidak ada di taxonomy")
    if item.fact_level not in enums.FACT_LEVELS:
        raise ValidationError("fact_level harus 1–4")
    if item.judul_hook and len(item.judul_hook) > 200:
        raise ValidationError("judul_hook maksimal 200 karakter")


def _terapkan(item: ContentItem, payload: dict, boleh: tuple[str, ...]) -> None:
    for field in boleh:
        if field in payload and payload[field] is not None:
            setattr(item, field, payload[field])


FIELD_ISI = (
    "judul_hook",
    "format",
    "kanal",
    "naskah_md",
    "caption_fb",
    "cta",
    "hashtags",
    "visual_note",
    "sumber_dalil",
    "fact_level",
    "verifikator",
    "request_ref",
)


def _warning_untuk(db: Session, item: ContentItem) -> list[dict]:
    return warnings.evaluasi(db, item)


# ---------------------------------------------------------------- AGENT
def buat_dari_agent(db: Session, tok, payload: dict) -> tuple[ContentItem, list[dict]]:
    """POST /api/v1/contents — selalu masuk `review` (agent mentok di review, D6)."""
    if payload.get("fact_level") == 4:
        raise ValidationError(
            "fact_level=4 (hukum Islam/fiqih) diparkir di MVP (D19) — agent tidak boleh "
            "membuat item level 4. Ajukan lewat chat ke Boss."
        )
    if not (payload.get("judul_hook") or "").strip():
        raise ValidationError("judul_hook wajib diisi")
    if not (payload.get("naskah_md") or "").strip():
        raise ValidationError("naskah_md wajib diisi")
    if not (payload.get("cta") or "").strip():
        raise ValidationError("cta wajib diisi")
    if payload.get("fact_level") is None:
        raise ValidationError("fact_level wajib diisi")
    if not (payload.get("pilar") or "").strip():
        raise ValidationError("pilar wajib diisi")
    if not (payload.get("format") or "").strip():
        raise ValidationError("format wajib diisi")

    sumber = "agent_brief" if (payload.get("request_ref") or "").strip() else "agent_auto"
    item = ContentItem(
        judul_hook=payload["judul_hook"].strip()[:200],
        format=payload.get("format"),
        kanal=payload.get("kanal") or "fb_feed",
        naskah_md=payload.get("naskah_md") or "",
        caption_fb=payload.get("caption_fb"),
        cta=payload.get("cta"),
        hashtags=payload.get("hashtags"),
        visual_note=payload.get("visual_note"),
        sumber_dalil=payload.get("sumber_dalil"),
        fact_level=int(payload.get("fact_level") or 1),
        status=enums.STATUS_DRAFT,
        source=sumber,
        owner_token_id=tok.id,
        request_ref=payload.get("request_ref"),
        usulan_jadwal=to_db(payload.get("usulan_jadwal")),
        hook_norm=hooks.normalisasi_hook(payload["judul_hook"]),
    )
    _set_pilar(db, item, payload.get("pilar"))
    _validasi_enum(item)

    from app.timeutil import now_wib_naive

    item.dibuat = now_wib_naive()
    item.diubah = now_wib_naive()
    db.add(item)
    db.flush()

    tambah_revisi(
        db,
        item,
        naskah_md=item.naskah_md,
        caption=item.caption_fb,
        catatan="Draft awal dari agent",
        aktor=Aktor(enums.ACTOR_AGENT, tok.id, tok),
    )
    audit.catat(
        db,
        actor_type=enums.ACTOR_AGENT,
        actor_id=tok.id,
        action="create",
        entity="content_item",
        entity_id=item.id,
        from_status=None,
        to_status=enums.STATUS_DRAFT,
        meta={"source": sumber, "request_ref": item.request_ref},
    )

    # draft → review (T2)
    validasi(db, item, enums.STATUS_REVIEW, Aktor(enums.ACTOR_AGENT, tok.id, tok))
    _pindah(db, item, enums.STATUS_REVIEW, Aktor(enums.ACTOR_AGENT, tok.id, tok), aksi="submit")
    return item, _warning_untuk(db, item)


def patch_dari_agent(db: Session, tok, content_id: int, payload: dict) -> tuple[ContentItem, list[dict]]:
    """PATCH /api/v1/contents/{id} — hanya item milik token, dan revisi → `review`."""
    item = ambil(db, content_id)
    aktor = Aktor(enums.ACTOR_AGENT, tok.id, tok)
    if item.owner_token_id != tok.id:
        raise Forbidden("Item ini bukan milik token Anda (item milik Boss tidak bisa diubah)")
    # §7d.3 diperiksa LEBIH DULU: item yang sudah disetujui beku untuk agent,
    # apa pun statusnya (403 lebih informatif daripada 409 "status tidak boleh diubah").
    pastikan_tidak_beku(item, aktor)
    if item.status not in (enums.STATUS_DRAFT, enums.STATUS_REVIEW, enums.STATUS_DITOLAK):
        raise Conflict(
            f"Item berstatus '{item.status}' tidak bisa diubah agent "
            "(hanya draft/review/ditolak)"
        )

    if payload.get("fact_level") == 4:
        raise ValidationError("fact_level=4 diparkir di MVP (D19) — agent dilarang.")

    _terapkan(item, payload, FIELD_ISI)
    _set_pilar(db, item, payload.get("pilar"))
    if "usulan_jadwal" in payload:
        item.usulan_jadwal = to_db(payload["usulan_jadwal"])
    _validasi_enum(item)
    item.hook_norm = hooks.normalisasi_hook(item.judul_hook)
    if item.status == enums.STATUS_DITOLAK:
        item.reject_reason = payload.get("catatan") or item.reject_reason

    from app.timeutil import now_wib_naive

    item.diubah = now_wib_naive()
    tambah_revisi(
        db,
        item,
        naskah_md=item.naskah_md,
        caption=item.caption_fb,
        catatan=payload.get("catatan") or "Revisi dari agent",
        aktor=aktor,
    )
    audit.catat(
        db,
        actor_type=enums.ACTOR_AGENT,
        actor_id=tok.id,
        action="patch",
        entity="content_item",
        entity_id=item.id,
        from_status=enums.STATUS_DITOLAK,
        to_status=enums.STATUS_REVIEW,
        meta={"field": sorted(k for k in payload if payload[k] is not None)},
    )
    validasi(db, item, enums.STATUS_REVIEW, aktor)
    _pindah(db, item, enums.STATUS_REVIEW, aktor, aksi="submit_ulang")
    return item, _warning_untuk(db, item)


def posted_dari_agent(
    db: Session, tok, content_id: int, *, link_posting: str, platform_post_id=None, posted_at=None
) -> tuple[ContentItem, bool]:
    """POST /contents/{id}/posted — idempoten: replay pada item `tayang` → state sama."""
    item = ambil(db, content_id)
    if item.owner_token_id != tok.id:
        raise Forbidden("Item ini bukan milik token Anda")
    if item.status == enums.STATUS_TAYANG:
        return item, False  # replay: 200 + state sama
    if item.status != enums.STATUS_TERJADWAL:
        raise Conflict(f"Hanya item `terjadwal` yang bisa ditandai tayang (sekarang '{item.status}')")
    if platform_post_id and item.platform_post_id and item.platform_post_id != platform_post_id:
        raise Conflict(
            f"platform_post_id berbeda untuk item yang sama "
            f"(sudah '{item.platform_post_id}')"
        )
    aktor = Aktor(enums.ACTOR_AGENT, tok.id, tok)
    item.link_posting = link_posting.strip()[:300]
    if platform_post_id:
        item.platform_post_id = str(platform_post_id)[:100]
    item.posted_at = to_db(posted_at) or _now()
    item.last_error = None
    validasi(db, item, enums.STATUS_TAYANG, aktor)
    _pindah(db, item, enums.STATUS_TAYANG, aktor, aksi="posted")
    item.tayang_at = item.posted_at
    notify.notif_posted(db, item)
    return item, True


def failed_dari_agent(db: Session, tok, content_id: int, *, reason: str) -> ContentItem:
    """POST /contents/{id}/failed — TIDAK mengubah status; tetap `terjadwal` + last_error."""
    item = ambil(db, content_id)
    if item.owner_token_id != tok.id:
        raise Forbidden("Item ini bukan milik token Anda")
    if not (reason or "").strip():
        raise ValidationError("reason wajib diisi")
    item.last_error = reason.strip()[:500]
    item.failed_at = _now()
    db.flush()
    audit.catat(
        db,
        actor_type=enums.ACTOR_AGENT,
        actor_id=tok.id,
        action="failed",
        entity="content_item",
        entity_id=item.id,
        from_status=item.status,
        to_status=item.status,
        meta={"reason": item.last_error},
    )
    notify.notif_failed(db, item)
    return item


# ---------------------------------------------------------------- BOSS
def buat_dari_boss(db: Session, payload: dict, *, langsung_siap: bool = False) -> ContentItem:
    item = ContentItem(
        judul_hook=(payload.get("judul_hook") or "").strip()[:200],
        format=payload.get("format") or "quote",
        kanal=payload.get("kanal") or "fb_feed",
        naskah_md=payload.get("naskah_md") or "",
        caption_fb=payload.get("caption_fb"),
        cta=payload.get("cta") or "tanpa_cta",
        hashtags=payload.get("hashtags"),
        visual_note=payload.get("visual_note"),
        sumber_dalil=payload.get("sumber_dalil"),
        fact_level=int(payload.get("fact_level") or 1),
        verifikator=payload.get("verifikator"),
        status=enums.STATUS_IDE,
        source="boss_manual",
        request_ref=payload.get("request_ref"),
        usulan_jadwal=to_db(payload.get("usulan_jadwal")),
        hook_norm=hooks.normalisasi_hook(payload.get("judul_hook") or ""),
        dibuat=_now(),
        diubah=_now(),
    )
    _set_pilar(db, item, payload.get("pilar"))
    _validasi_enum(item)
    db.add(item)
    db.flush()
    audit.catat(
        db,
        actor_type=enums.ACTOR_BOSS,
        actor_id=None,
        action="create",
        entity="content_item",
        entity_id=item.id,
        from_status=None,
        to_status=item.status,
        meta={"source": item.source},
    )
    if payload.get("naskah_md"):
        tambah_revisi(
            db, item, naskah_md=item.naskah_md, caption=item.caption_fb,
            catatan="Draft awal Boss", aktor=Aktor(enums.ACTOR_BOSS),
        )
    return item


def update_dari_boss(db: Session, item: ContentItem, payload: dict) -> ContentItem:
    aktor = Aktor(enums.ACTOR_BOSS)
    _terapkan(item, payload, FIELD_ISI)
    _set_pilar(db, item, payload.get("pilar"))
    if "usulan_jadwal" in payload:
        item.usulan_jadwal = to_db(payload["usulan_jadwal"])
    _validasi_enum(item)
    item.hook_norm = hooks.normalisasi_hook(item.judul_hook)
    item.diubah = _now()
    if payload.get("naskah_md"):
        tambah_revisi(
            db, item, naskah_md=item.naskah_md, caption=item.caption_fb,
            catatan=payload.get("catatan") or "Edit Boss", aktor=aktor,
        )
    audit.catat(
        db, actor_type=enums.ACTOR_BOSS, actor_id=None, action="update", entity="content_item",
        entity_id=item.id, from_status=item.status, to_status=item.status,
    )
    return item


def pindah_status_boss(db: Session, item: ContentItem, ke: str, *, catatan: str | None = None) -> ContentItem:
    """Transisi yang murni wewenang Boss: T1, T3, T10, T11, T12."""
    aktor = Aktor(enums.ACTOR_BOSS)
    if ke == enums.STATUS_DRAFT and item.status == enums.STATUS_IDE:
        pass  # T1
    validasi(db, item, ke, aktor, checklist_ok=checklist.semua_tercentang(db, item))
    if ke == enums.STATUS_SIAP_TAYANG:
        from app.timeutil import now_wib_naive

        item.approved_at = now_wib_naive()
        item.submitted_at = item.submitted_at or now_wib_naive()
        item.reviewed_at = now_wib_naive()
    _pindah(db, item, ke, aktor, aksi="status_manual", catatan=catatan)
    return item


def approve(
    db: Session,
    item: ContentItem,
    *,
    jadwal_tayang: date | None = None,
    slot_waktu: str | None = None,
) -> ContentItem:
    """Setujui (T4) + opsional langsung jadwalkan (T8) — dua baris audit terpisah."""
    aktor = Aktor(enums.ACTOR_BOSS)
    belum = checklist.belum_tercentang(db, item)
    if belum:
        raise ValidationError(
            "Checklist produksi belum lengkap", details=belum
        )
    validasi(db, item, enums.STATUS_SIAP_TAYANG, aktor, checklist_ok=True)
    item.approved_at = _now()
    item.reviewed_at = item.approved_at
    item.reject_reason = None
    _pindah(db, item, enums.STATUS_SIAP_TAYANG, aktor, aksi="approve")
    notify.notif_approve(db, item)

    if jadwal_tayang and slot_waktu:
        jadwalkan(db, item, jadwal_tayang, slot_waktu)
    return item


def reject(db: Session, item: ContentItem, *, alasan: str) -> ContentItem:
    if len((alasan or "").strip()) < 10:
        raise ValidationError("Alasan tolak wajib, minimal 10 karakter")
    aktor = Aktor(enums.ACTOR_BOSS)
    item.reject_reason = alasan.strip()[:500]
    item.reviewed_at = _now()
    validasi(db, item, enums.STATUS_DITOLAK, aktor)
    _pindah(db, item, enums.STATUS_DITOLAK, aktor, aksi="reject")
    catat_catatan_revisi(db, item, f"Reject: {item.reject_reason}")
    notify.notif_tolak(db, item)
    return item


def jadwalkan(db: Session, item: ContentItem, tanggal: date, slot: str) -> ContentItem:
    """T8: 1 slot = 1 konten, UNIQUE(tanggal, platform, slot)."""
    aktor = Aktor(enums.ACTOR_BOSS)
    if slot not in enums.SLOTS:
        raise ValidationError("slot_waktu harus 'pagi' atau 'malam'")
    if isinstance(tanggal, datetime):
        tanggal = tanggal.date()
    if not isinstance(tanggal, date):
        raise ValidationError("jadwal_tayang wajib tanggal")

    terpakai = _slot_terpakai(db, tanggal, slot)
    if terpakai is not None and terpakai.id != item.id:
        raise Conflict(
            f"Slot {slot} tanggal {tanggal.isoformat()} sudah terisi oleh "
            f"#{terpakai.id} — {terpakai.judul_hook}"
        )

    item.jadwal_tayang = tanggal
    item.slot_waktu = slot
    validasi(db, item, enums.STATUS_TERJADWAL, aktor, checklist_ok=checklist.semua_tercentang(db, item))
    _pindah(db, item, enums.STATUS_TERJADWAL, aktor, aksi="jadwalkan")
    _isi_slot(db, item)
    return item


def batalkan_jadwal(db: Session, item: ContentItem) -> ContentItem:
    """T10: terjadwal → siap_tayang, slot dilepas."""
    aktor = Aktor(enums.ACTOR_BOSS)
    validasi(db, item, enums.STATUS_SIAP_TAYANG, aktor)
    _lepas_slot(db, item)
    item.jadwal_tayang = None
    item.slot_waktu = None
    _pindah(db, item, enums.STATUS_SIAP_TAYANG, aktor, aksi="batal_jadwal")
    return item


def tandai_tayang_boss(db: Session, item: ContentItem, *, link_posting: str) -> ContentItem:
    aktor = Aktor(enums.ACTOR_BOSS)
    item.link_posting = (link_posting or "").strip()[:300]
    validasi(db, item, enums.STATUS_TAYANG, aktor)
    _pindah(db, item, enums.STATUS_TAYANG, aktor, aksi="tandai_tayang")
    item.tayang_at = item.posted_at or _now()
    notify.notif_posted(db, item)
    return item


def arsipkan(db: Session, item: ContentItem) -> ContentItem:
    aktor = Aktor(enums.ACTOR_BOSS)
    validasi(db, item, enums.STATUS_ARSIP, aktor)
    _pindah(db, item, enums.STATUS_ARSIP, aktor, aksi="arsip")
    hooks.lepas_hook(db, item.id)
    _lepas_slot(db, item)
    return item


def soft_delete(db: Session, item: ContentItem) -> ContentItem:
    """Hanya Boss yang boleh menghapus (§10) — soft delete."""
    item.deleted_at = _now()
    item.diubah = item.deleted_at
    audit.catat(
        db, actor_type=enums.ACTOR_BOSS, actor_id=None, action="soft_delete",
        entity="content_item", entity_id=item.id, from_status=item.status, to_status=item.status,
    )
    _lepas_slot(db, item)
    return item


# ---------------------------------------------------------------- slot
def _slot_terpakai(db: Session, tanggal: date, slot: str) -> ContentItem | None:
    row = db.scalar(
        select(ScheduleSlot).where(
            ScheduleSlot.tanggal == tanggal,
            ScheduleSlot.platform == "facebook",
            ScheduleSlot.slot_waktu == slot,
        )
    )
    if row is None or row.content_id is None:
        return None
    return db.get(ContentItem, row.content_id)


def _isi_slot(db: Session, item: ContentItem) -> None:
    row = db.scalar(
        select(ScheduleSlot).where(
            ScheduleSlot.tanggal == item.jadwal_tayang,
            ScheduleSlot.platform == "facebook",
            ScheduleSlot.slot_waktu == item.slot_waktu,
        )
    )
    if row is None:
        db.add(
            ScheduleSlot(
                tanggal=item.jadwal_tayang,
                platform="facebook",
                slot_waktu=item.slot_waktu,
                content_id=item.id,
                status_slot="terisi",
            )
        )
    else:
        row.content_id = item.id
        row.status_slot = "terisi"
    db.flush()


def _lepas_slot(db: Session, item: ContentItem) -> None:
    for row in db.scalars(select(ScheduleSlot).where(ScheduleSlot.content_id == item.id)):
        row.content_id = None
        row.status_slot = "kosong"
    db.flush()


def slot_terisi(db: Session, tanggal: date) -> dict[str, ContentItem | None]:
    rows = db.scalars(
        select(ScheduleSlot).where(
            ScheduleSlot.tanggal == tanggal, ScheduleSlot.platform == "facebook"
        )
    )
    hasil: dict[str, ContentItem | None] = {s: None for s in enums.SLOTS}
    for row in rows:
        hasil[row.slot_waktu] = db.get(ContentItem, row.content_id) if row.content_id else None
    return hasil


# ---------------------------------------------------------------- queue
def queue_due(db: Session, due: datetime, *, include_manual: bool = False) -> list[ContentItem]:
    """PRD §7b: `terjadwal` + jam slot ≤ due + belum posted. Reels = jalur manual (D20)."""
    from app.timeutil import slot_datetime

    items = list(
        db.scalars(
            select(ContentItem).where(
                ContentItem.deleted_at.is_(None),
                ContentItem.status == enums.STATUS_TERJADWAL,
                ContentItem.jadwal_tayang.is_not(None),
                ContentItem.slot_waktu.is_not(None),
            )
        )
    )
    hasil: list[ContentItem] = []
    for item in items:
        if not include_manual and (
            item.format in enums.MANUAL_FORMATS or item.kanal in enums.MANUAL_KANALS
        ):
            continue
        jadwal = slot_datetime(item.jadwal_tayang, item.slot_waktu)
        if jadwal <= due.replace(tzinfo=None):
            hasil.append(item)
    hasil.sort(key=lambda i: (i.jadwal_tayang, i.slot_waktu == "malam"))
    return hasil


def item_manual_lewat_jadwal(db: Session, due: datetime) -> list[ContentItem]:
    """Badge "butuh Boss": reels yang jadwalnya sudah lewat (D20)."""
    from app.timeutil import slot_datetime

    items = list(
        db.scalars(
            select(ContentItem).where(
                ContentItem.deleted_at.is_(None),
                ContentItem.status == enums.STATUS_TERJADWAL,
                ContentItem.jadwal_tayang.is_not(None),
            )
        )
    )
    hasil = []
    for item in items:
        if item.format in enums.MANUAL_FORMATS or item.kanal in enums.MANUAL_KANALS:
            jadwal = slot_datetime(item.jadwal_tayang, item.slot_waktu or "malam")
            if jadwal <= due.replace(tzinfo=None):
                hasil.append(item)
    return hasil


def telat_dari_jadwal(db: Session, menit: int = 60) -> list[ContentItem]:
    """Item `terjadwal` yang lewat jadwal > `menit` menit (badge kesehatan jalur agent)."""
    from datetime import timedelta

    from app.timeutil import slot_datetime

    batas = _now() - timedelta(minutes=menit)
    items = list(
        db.scalars(
            select(ContentItem).where(
                ContentItem.deleted_at.is_(None),
                ContentItem.status == enums.STATUS_TERJADWAL,
                ContentItem.jadwal_tayang.is_not(None),
            )
        )
    )
    return [
        i
        for i in items
        if i.slot_waktu and slot_datetime(i.jadwal_tayang, i.slot_waktu) < batas
    ]


# ---------------------------------------------------------------- internal
def _now() -> datetime:
    from app.timeutil import now_wib_naive

    return now_wib_naive()


def _pindah(db: Session, item: ContentItem, ke: str, aktor: Aktor, *, aksi: str, catatan=None) -> None:
    dari = item.status
    item.status = ke
    item.diubah = _now()
    if ke == enums.STATUS_REVIEW:
        item.submitted_at = _now()
        item.reviewed_at = None
    if ke in (enums.STATUS_SIAP_TAYANG, enums.STATUS_DITOLAK):
        item.reviewed_at = item.reviewed_at or _now()
    db.flush()
    audit.catat(
        db,
        actor_type=aktor.type,
        actor_id=aktor.id,
        action=aksi,
        entity="content_item",
        entity_id=item.id,
        from_status=dari,
        to_status=ke,
        meta={"catatan": catatan} if catatan else None,
    )
    hooks.daftarkan_hook(db, item)
    if ke in (enums.STATUS_DRAFT, enums.STATUS_REVIEW):
        # Baris checklist ikut dibuat di transaksi yang SAMA dan ikut di-commit.
        # Kalau hanya dibuat saat halaman dirender (request GET), barisnya
        # ter-rollback dan ID yang tampil di form menunjuk baris yang tidak ada.
        checklist.sinkron_butir(db, item)
    if ke == enums.STATUS_REVIEW and aktor.is_agent:
        notify.notif_draft_masuk_review(db, item)


__all__ = [
    "ambil",
    "approve",
    "arsipkan",
    "batalkan_jadwal",
    "buat_dari_agent",
    "buat_dari_boss",
    "failed_dari_agent",
    "jadwalkan",
    "patch_dari_agent",
    "pindah_status_boss",
    "posted_dari_agent",
    "queue_due",
    "reject",
    "slot_terisi",
    "soft_delete",
    "tandai_tayang_boss",
    "update_dari_boss",
    "versi_terakhir",
]
