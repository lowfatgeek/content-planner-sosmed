"""UI admin: template caption, token API, hook registry, outbox notifikasi, audit log."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import enums
from app.auth import boss_wajib, cek_csrf
from app.db import get_db
from app.errors import AppError
from app.models import (
    ActivityLog,
    ApiToken,
    CaptionTemplate,
    HookRegistry,
    NotificationOutbox,
    Pilar,
    User,
)
from app.routers.ui import render, _redirect, form_str
from app.services import audit, hooks, notify, quota
from app.services import tokens as token_service
from app.timeutil import now_wib_naive

router = APIRouter()


def _pilar_list(db: Session) -> list[Pilar]:
    return list(db.scalars(select(Pilar).order_by(Pilar.urutan)))


@router.get("/templates", response_class=HTMLResponse)
def daftar_template(request: Request, db: Session = Depends(get_db), user: User = Depends(boss_wajib)):
    return render(
        request,
        "templates.html",
        judul="Template caption",
        user=user,
        items=list(db.scalars(select(CaptionTemplate).order_by(CaptionTemplate.nama))),
        pilar_list=_pilar_list(db),
    )


@router.post("/templates/new")
async def simpan_template(request: Request, db: Session = Depends(get_db), user: User = Depends(boss_wajib)):
    form = form_str(await request.form())
    cek_csrf(request, form.get("csrf"))
    nama = (form.get("nama") or "").strip()
    body = form.get("body") or ""
    if not nama or not body.strip():
        return _redirect("/templates", "Nama dan isi template wajib diisi", "err")
    if len(body.split()) > 200:
        return _redirect("/templates", "Template maksimal 200 kata (F5)", "err")
    pilar_id = None
    slug = form.get("pilar") or ""
    if slug:
        pilar = db.scalar(select(Pilar).where(Pilar.slug == slug))
        pilar_id = pilar.id if pilar else None
    db.add(
        CaptionTemplate(
            nama=nama[:120], body=body, pilar_id=pilar_id, is_active=True, dibuat=now_wib_naive()
        )
    )
    db.commit()
    return _redirect("/templates", "Template dibuat")


@router.post("/templates/{template_id}/toggle")
async def toggle_template(
    template_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(boss_wajib)
):
    form = form_str(await request.form())
    cek_csrf(request, form.get("csrf"))
    row = db.get(CaptionTemplate, template_id)
    if row is None:
        return _redirect("/templates", "Template tidak ditemukan", "err")
    row.is_active = not row.is_active
    db.commit()
    return _redirect("/templates", "Status template diubah")


@router.get("/settings/tokens", response_class=HTMLResponse)
def daftar_token(request: Request, db: Session = Depends(get_db), user: User = Depends(boss_wajib)):
    terpakai = quota.hitung(db)
    return render(
        request,
        "tokens.html",
        judul="Token API agent",
        user=user,
        items=token_service.daftar(db),
        token_baru=request.query_params.get("token"),
        kuota=terpakai,
    )


@router.post("/settings/tokens/new")
async def buat_token(
    request: Request,
    nama: str = Form(default="agent-content"),
    csrf: str = Form(default=""),
    db: Session = Depends(get_db),
    user: User = Depends(boss_wajib),
):
    cek_csrf(request, csrf)
    form = form_str(await request.form())
    scopes = form.getlist("scope") or [enums.SCOPE_READ, enums.SCOPE_WRITE]
    try:
        row, raw = token_service.buat_token(db, nama=nama, scopes=scopes)
        db.commit()
        return _redirect("/settings/tokens", raw)
    except AppError as exc:
        db.rollback()
        return _redirect("/settings/tokens", exc.message, "err")


@router.post("/settings/tokens/{token_id}/revoke")
async def revoke_token(
    token_id: int, request: Request, csrf: str = Form(default=""),
    db: Session = Depends(get_db), user: User = Depends(boss_wajib)
):
    cek_csrf(request, csrf)
    try:
        token_service.revoke(db, token_id)
        db.commit()
        return _redirect("/settings/tokens", "Token dicabut")
    except AppError as exc:
        db.rollback()
        return _redirect("/settings/tokens", exc.message, "err")


@router.get("/settings/hooks", response_class=HTMLResponse)
def daftar_hook(request: Request, db: Session = Depends(get_db), user: User = Depends(boss_wajib)):
    return render(
        request,
        "hooks.html",
        judul="Registry hook",
        user=user,
        items=hooks.daftar_registry(db, limit=500),
    )


@router.get("/settings/outbox", response_class=HTMLResponse)
def daftar_outbox(request: Request, db: Session = Depends(get_db), user: User = Depends(boss_wajib)):
    rows = list(
        db.scalars(select(NotificationOutbox).order_by(NotificationOutbox.id.desc()).limit(200))
    )
    return render(request, "outbox.html", judul="Outbox notifikasi", user=user, items=rows)


@router.post("/settings/outbox/{row_id}/sent")
async def tandai_terkirim(
    row_id: int, request: Request, csrf: str = Form(default=""),
    db: Session = Depends(get_db), user: User = Depends(boss_wajib)
):
    """Jalur manual: Boss menandai baris terkirim (pengirim normal = poller @hermes)."""
    cek_csrf(request, csrf)
    row = db.get(NotificationOutbox, row_id)
    if row is not None:
        row.status = "terkirim"
        row.sent_at = now_wib_naive()
        row.attempts = (row.attempts or 0) + 1
        db.commit()
    return _redirect("/settings/outbox", "Ditandai terkirim")


@router.get("/settings/activity", response_class=HTMLResponse)
def daftar_aktivitas(request: Request, db: Session = Depends(get_db), user: User = Depends(boss_wajib)):
    rows = list(db.scalars(select(ActivityLog).order_by(ActivityLog.id.desc()).limit(200)))
    return render(request, "activity.html", judul="Audit log", user=user, items=rows, parse_meta=audit.parse_meta)
