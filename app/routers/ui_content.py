"""UI konten: bank ide, kanban, editor, revisi, aset, metrik, kalender."""
from __future__ import annotations

import calendar as pycalendar
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import enums
from app.auth import boss_wajib, cek_csrf, csrf_dari
from app.config import settings
from app.db import get_db
from app.errors import AppError
from app.models import (
    Asset,
    CaptionTemplate,
    ContentItem,
    ContentRevision,
    MetricSnapshot,
    Pilar,
    User,
)
from app.routers.ui import render, _redirect, form_str
from app.services import checklist, content as content_service, hooks, quota
from app.services import uploads, warnings as warn_service
from app.services.transitions import Aktor, transisi_legal_dari
from app.timeutil import now_wib_naive, parse_iso, slot_datetime, today_wib

router = APIRouter()


def _daftar_pilar(db: Session) -> list[Pilar]:
    return list(db.scalars(select(Pilar).where(Pilar.is_active.is_(True)).order_by(Pilar.urutan)))


def _form_payload(form) -> dict:
    payload = {
        "judul_hook": (form.get("judul_hook") or "").strip(),
        "pilar": form.get("pilar"),
        "format": form.get("format"),
        "kanal": form.get("kanal"),
        "naskah_md": form.get("naskah_md") or "",
        "caption_fb": form.get("caption_fb") or None,
        "cta": form.get("cta"),
        "hashtags": form.get("hashtags") or None,
        "visual_note": form.get("visual_note") or None,
        "sumber_dalil": form.get("sumber_dalil") or None,
        "fact_level": int(form.get("fact_level") or 1),
        "verifikator": form.get("verifikator") or None,
        "request_ref": form.get("request_ref") or None,
        "usulan_jadwal": form.get("usulan_jadwal") or None,
    }
    return payload


# ------------------------------------------------------------------ daftar & kanban
@router.get("/contents", response_class=HTMLResponse)
def daftar(
    request: Request,
    q: str = "",
    status: str = "",
    pilar: str = "",
    format: str = "",
    kanal: str = "",
    source: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(boss_wajib),
):
    stmt = select(ContentItem).where(ContentItem.deleted_at.is_(None))
    if q.strip():
        pola = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(ContentItem.judul_hook.like(pola), ContentItem.naskah_md.like(pola))
        )
    if status:
        stmt = stmt.where(ContentItem.status == status)
    if format:
        stmt = stmt.where(ContentItem.format == format)
    if kanal:
        stmt = stmt.where(ContentItem.kanal == kanal)
    if source:
        stmt = stmt.where(ContentItem.source == source)
    if pilar:
        stmt = stmt.join(Pilar).where(Pilar.slug == pilar)
    items = list(db.scalars(stmt.order_by(ContentItem.diubah.desc().nullslast()).limit(300)))
    return render(
        request,
        "contents.html",
        judul="Bank Ide",
        user=user,
        items=items,
        pilar_list=_daftar_pilar(db),
        filter={"q": q, "status": status, "pilar": pilar, "format": format, "kanal": kanal, "source": source},
    )


@router.get("/kanban", response_class=HTMLResponse)
def kanban(request: Request, db: Session = Depends(get_db), user: User = Depends(boss_wajib)):
    items = list(
        db.scalars(
            select(ContentItem)
            .where(ContentItem.deleted_at.is_(None))
            .order_by(ContentItem.diubah.desc().nullslast())
            .limit(500)
        )
    )
    kolom = {s: [] for s in enums.STATUSES}
    for item in items:
        kolom.setdefault(item.status, []).append(item)
    return render(request, "kanban.html", judul="Kanban", user=user, kolom=kolom)


# ------------------------------------------------------------------ form
@router.get("/contents/new", response_class=HTMLResponse)
def form_baru(request: Request, db: Session = Depends(get_db), user: User = Depends(boss_wajib)):
    return render(
        request,
        "content_form.html",
        judul="Konten baru",
        user=user,
        item=None,
        pilar_list=_daftar_pilar(db),
        template_list=list(db.scalars(select(CaptionTemplate).where(CaptionTemplate.is_active.is_(True)))),
        hook_list=hooks.daftar_registry(db, limit=60),
    )


@router.post("/contents/new")
async def simpan_baru(request: Request, db: Session = Depends(get_db), user: User = Depends(boss_wajib)):
    form = form_str(await request.form())
    cek_csrf(request, form.get("csrf"))
    try:
        payload = _form_payload(form)
        payload["usulan_jadwal"] = parse_iso(payload.get("usulan_jadwal"))
        item = content_service.buat_dari_boss(db, payload)
        db.commit()
        return _redirect(f"/contents/{item.id}", "Konten dibuat")
    except AppError as exc:
        db.rollback()
        return _redirect("/contents/new", exc.message, "err")


@router.get("/contents/{content_id}", response_class=HTMLResponse)
def detail(
    content_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(boss_wajib)
):
    item = content_service.ambil(db, content_id)
    revisi = list(
        db.scalars(
            select(ContentRevision)
            .where(ContentRevision.content_id == item.id)
            .order_by(ContentRevision.versi.desc())
        )
    )
    cek = checklist.daftar(db, item)
    db.commit()  # baris checklist baru (kalau ada) ikut tersimpan sebelum form dipakai
    return render(
        request,
        "content_detail.html",
        judul=item.judul_hook,
        user=user,
        item=item,
        pilar_list=_daftar_pilar(db),
        revisi=revisi,
        aset=list(db.scalars(select(Asset).where(Asset.content_id == item.id).order_by(Asset.urutan))),
        cek=checklist.daftar(db, item),
        warning=warn_service.evaluasi(db, item),
        hook_serupa=[h for h in hooks.cari_duplikat(db, item.judul_hook, kecuali_id=item.id)],
        transisi=transisi_legal_dari(item.status),
        metrik=list(
            db.scalars(
                select(MetricSnapshot)
                .where(MetricSnapshot.content_id == item.id)
                .order_by(MetricSnapshot.capture_date.desc())
            )
        ),
        template_list=list(db.scalars(select(CaptionTemplate).where(CaptionTemplate.is_active.is_(True)))),
    )


@router.post("/contents/{content_id}/edit")
async def simpan_edit(
    content_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(boss_wajib)
):
    form = form_str(await request.form())
    cek_csrf(request, form.get("csrf"))
    item = content_service.ambil(db, content_id)
    try:
        payload = _form_payload(form)
        payload["usulan_jadwal"] = parse_iso(payload.get("usulan_jadwal"))
        payload["catatan"] = form.get("catatan") or None
        content_service.update_dari_boss(db, item, payload)
        db.commit()
        return _redirect(f"/contents/{item.id}", "Tersimpan (versi baru dibuat)")
    except AppError as exc:
        db.rollback()
        return _redirect(f"/contents/{item.id}", exc.message, "err")


@router.post("/contents/{content_id}/status")
async def ubah_status(
    content_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(boss_wajib)
):
    form = form_str(await request.form())
    cek_csrf(request, form.get("csrf"))
    item = content_service.ambil(db, content_id)
    ke = form.get("status")
    try:
        if ke == enums.STATUS_TERJADWAL:
            tanggal = date.fromisoformat(form.get("jadwal_tayang") or today_wib().isoformat())
            content_service.jadwalkan(db, item, tanggal, form.get("slot_waktu") or "pagi")
        elif ke == enums.STATUS_TAYANG:
            content_service.tandai_tayang_boss(db, item, link_posting=form.get("link_posting") or "")
        elif ke == enums.STATUS_ARSIP:
            content_service.arsipkan(db, item)
        elif ke == enums.STATUS_SIAP_TAYANG and item.status == enums.STATUS_REVIEW:
            content_service.approve(db, item)
        elif ke == enums.STATUS_DITOLAK:
            content_service.reject(db, item, alasan=form.get("reject_reason") or "")
        elif item.status == enums.STATUS_TERJADWAL and ke == enums.STATUS_SIAP_TAYANG:
            content_service.batalkan_jadwal(db, item)
        else:
            content_service.pindah_status_boss(db, item, ke)
        db.commit()
        return _redirect(f"/contents/{item.id}", f"Status → {enums.STATUS_LABEL.get(ke, ke)}")
    except AppError as exc:
        db.rollback()
        return _redirect(f"/contents/{item.id}", exc.message, "err")


@router.post("/contents/{content_id}/checklist")
async def toggle_checklist(
    content_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(boss_wajib)
):
    form = form_str(await request.form())
    cek_csrf(request, form.get("csrf"))
    item = content_service.ambil(db, content_id)
    checklist.set_centang_id(
        db,
        item,
        checklist_id=int(form.get("checklist_id") or 0),
        tercentang=(form.get("tercentang") == "1"),
        actor_type=enums.ACTOR_BOSS,
        actor_id=None,
    )
    db.commit()
    return _redirect(f"/contents/{item.id}", "Checklist diperbarui")


@router.post("/contents/{content_id}/assets")
async def unggah_aset(
    content_id: int,
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(boss_wajib),
):
    item = content_service.ambil(db, content_id)
    form = form_str(await request.form())
    cek_csrf(request, form.get("csrf"))
    try:
        blob = await file.read()
        nama, sha, ext = uploads.simpan(file.filename or "aset", file.content_type, blob)
        urutan = int(
            db.scalar(select(func.count(Asset.id)).where(Asset.content_id == item.id)) or 0
        )
        db.add(
            Asset(
                content_id=item.id,
                jenis="gambar",
                path=nama,
                alt=form.get("alt") or None,
                urutan=urutan,
                ukuran=len(blob),
                dibuat=now_wib_naive(),
            )
        )
        db.commit()
        return _redirect(f"/contents/{item.id}", f"Aset diunggah ({uploads.ukuran_human(len(blob))})")
    except AppError as exc:
        db.rollback()
        return _redirect(f"/contents/{item.id}", exc.message, "err")


@router.post("/assets/{asset_id}/delete")
async def hapus_aset(
    asset_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(boss_wajib)
):
    form = form_str(await request.form())
    cek_csrf(request, form.get("csrf"))
    row = db.get(Asset, asset_id)
    if row is None:
        return _redirect("/contents", "Aset tidak ditemukan", "err")
    content_id = row.content_id
    uploads.hapus(row.path)
    db.delete(row)
    db.commit()
    return _redirect(f"/contents/{content_id}", "Aset dihapus")


@router.get("/uploads/{nama}")
def serve_upload(nama: str, request: Request, db: Session = Depends(get_db), user: User = Depends(boss_wajib)):
    from pathlib import Path

    target = (settings.UPLOAD_DIR / Path(nama).name).resolve()
    root = settings.UPLOAD_DIR.resolve()
    if root not in target.parents or not target.exists():
        return HTMLResponse("Tidak ditemukan", status_code=404)
    return FileResponse(target)


# ------------------------------------------------------------------ metrik
@router.get("/metrics", response_class=HTMLResponse)
def metrik(request: Request, db: Session = Depends(get_db), user: User = Depends(boss_wajib)):
    items = list(
        db.scalars(
            select(ContentItem)
            .where(
                ContentItem.status == enums.STATUS_TAYANG, ContentItem.deleted_at.is_(None)
            )
            .order_by(ContentItem.tayang_at.desc().nullslast())
            .limit(100)
        )
    )
    snapshots = list(
        db.scalars(
            select(MetricSnapshot).order_by(MetricSnapshot.capture_date.desc()).limit(100)
        )
    )
    return render(
        request, "metrics.html", judul="Metrik manual", user=user, items=items, snapshots=snapshots
    )


@router.post("/metrics/new")
async def simpan_metrik(
    request: Request, db: Session = Depends(get_db), user: User = Depends(boss_wajib)
):
    form = form_str(await request.form())
    cek_csrf(request, form.get("csrf"))
    try:
        item = content_service.ambil(db, int(form.get("content_id") or 0))
        capture = date.fromisoformat(form.get("capture_date") or today_wib().isoformat())
        sudah = db.scalar(
            select(MetricSnapshot).where(
                MetricSnapshot.content_id == item.id, MetricSnapshot.capture_date == capture
            )
        )
        if sudah is not None:
            raise AppError(f"Metrik #{item.id} tanggal {capture} sudah ada (1 snapshot/hari)")
        db.add(
            MetricSnapshot(
                content_id=item.id,
                capture_date=capture,
                reach=int(form.get("reach") or 0),
                likes=int(form.get("likes") or 0),
                komen=int(form.get("komen") or 0),
                share=int(form.get("share") or 0),
                dibuat=now_wib_naive(),
            )
        )
        db.commit()
        return _redirect("/metrics", "Metrik tersimpan")
    except AppError as exc:
        db.rollback()
        return _redirect("/metrics", exc.message, "err")


# ------------------------------------------------------------------ kalender
@router.get("/calendar", response_class=HTMLResponse)
def kalender(
    request: Request,
    bulan: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(boss_wajib),
):
    hari_ini = today_wib()
    try:
        tahun, bulan_i = (int(x) for x in bulan.split("-"))
        if not 1 <= bulan_i <= 12:
            raise ValueError
    except (ValueError, AttributeError):
        tahun, bulan_i = hari_ini.year, hari_ini.month

    pertama = date(tahun, bulan_i, 1)
    jumlah_hari = pycalendar.monthrange(tahun, bulan_i)[1]
    terakhir = date(tahun, bulan_i, jumlah_hari)
    items = list(
        db.scalars(
            select(ContentItem).where(
                ContentItem.jadwal_tayang.is_not(None),
                ContentItem.jadwal_tayang >= pertama,
                ContentItem.jadwal_tayang <= terakhir,
                ContentItem.deleted_at.is_(None),
            )
        )
    )
    peta: dict[tuple[str, str], ContentItem] = {}
    for item in items:
        if item.slot_waktu:
            peta[(item.jadwal_tayang.isoformat(), item.slot_waktu)] = item

    minggu = pycalendar.Calendar(firstweekday=0).monthdatescalendar(tahun, bulan_i)
    sel = []
    for pekan in minggu:
        baris = []
        for tgl in pekan:
            baris.append(
                {
                    "tanggal": tgl,
                    "dalam_bulan": tgl.month == bulan_i,
                    "pagi": peta.get((tgl.isoformat(), "pagi")),
                    "malam": peta.get((tgl.isoformat(), "malam")),
                }
            )
        sel.append(baris)

    return render(
        request,
        "calendar.html",
        judul="Kalender",
        user=user,
        minggu=sel,
        tahun=tahun,
        bulan=bulan_i,
        nama_bulan=pycalendar.month_name[bulan_i],
        hari_ini=hari_ini,
        slot_kosong=warn_service.slot_kosong_hplus(db, hari_ini, 2),
        kuota=quota.hitung(db),
    )
