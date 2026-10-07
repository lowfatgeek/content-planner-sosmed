"""Tampilan SSR untuk Boss (Jinja2 + HTMX).

Bukan API — semua aksi di sini divalidasi ulang oleh service layer yang sama
dengan API agent, jadi aturan §10 tidak bisa dilewati lewat UI.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import enums
from app.auth import boss_wajib, baca_sesi, cek_csrf, clear_cookie, verify_password, buat_sesi, set_cookie, csrf_dari
from app.config import settings
from app.db import get_db
from app.errors import AppError
from app.models import (
    ActivityLog,
    ApiToken,
    Asset,
    CaptionTemplate,
    ContentItem,
    ContentRevision,
    HookRegistry,
    MetricSnapshot,
    NotificationOutbox,
    Pilar,
    ScheduleSlot,
    User,
)
from app.services import audit, checklist, content as content_service, hooks, notify, quota
from app.services import tokens as token_service, uploads, warnings as warn_service
from app.services.transitions import Aktor, transisi_legal_dari
from app.timeutil import now_wib_naive, parse_iso, today_wib, week_bounds

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
router = APIRouter()

TEMPLATES.env.globals.update(
    STATUSES=enums.STATUSES,
    STATUS_LABEL=enums.STATUS_LABEL,
    FORMATS=enums.FORMATS,
    KANALS=enums.KANALS,
    CTAS=enums.CTAS,
    SLOTS=enums.SLOTS,
    FACT_LEVELS=enums.FACT_LEVELS,
    SCOPES=enums.SCOPES,
    app_name=settings.APP_NAME,
)


def render(request: Request, nama: str, **ctx) -> HTMLResponse:
    ctx.setdefault("csrf", csrf_dari(request))
    ctx.setdefault("path", request.url.path)
    return TEMPLATES.TemplateResponse(request, nama, ctx)


def _redirect(url: str, pesan: str | None = None, level: str = "ok") -> RedirectResponse:
    if pesan:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}pesan={pesan}&level={level}"
    return RedirectResponse(url, status_code=303)


class FormStr:
    """Pembungkus `await request.form()` yang selalu mengembalikan str.

    `FormData.get()` bisa mengembalikan `UploadFile` untuk field berkas; helper ini
    memastikan hanya teks yang masuk ke layer validasi.
    """

    def __init__(self, form):
        self._form = form

    def get(self, key: str, default=None):
        nilai = self._form.get(key, default)
        if nilai is None or isinstance(nilai, str):
            return nilai
        return str(nilai)

    def getlist(self, key: str):
        return [str(v) for v in self._form.getlist(key)]

    def __contains__(self, key: str) -> bool:
        return key in self._form

    def __iter__(self):
        return iter(self._form)


def form_str(form) -> FormStr:
    return FormStr(form)


# ------------------------------------------------------------------ auth
@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, db: Session = Depends(get_db)):
    if baca_sesi(request):
        return _redirect("/")
    return render(request, "login.html", judul="Masuk")


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.scalar(select(User).where(User.username == username.strip()))
    if user is None or not verify_password(password, user.password_hash):
        return render(
            request, "login.html", judul="Masuk", error="Username atau password salah"
        )
    user.last_login = now_wib_naive()
    db.commit()
    response = _redirect("/", "Selamat datang kembali")
    set_cookie(response, buat_sesi(user))
    return response


@router.get("/logout")
def logout(request: Request):
    response = _redirect("/login")
    clear_cookie(response)
    return response


# ------------------------------------------------------------------ dashboard
@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db), user: User = Depends(boss_wajib)):
    kuota = quota.hitung(db)
    total = quota.total(db)
    tayang = quota.hitung_tayang_minggu_ini(db)
    slot_terisi = quota.slot_terisi_minggu_ini(db)

    review_q = list(
        db.scalars(
            select(ContentItem)
            .where(ContentItem.status == enums.STATUS_REVIEW, ContentItem.deleted_at.is_(None))
            .order_by(ContentItem.submitted_at.asc().nullsfirst())
        )
    )
    gagal = list(
        db.scalars(
            select(ContentItem).where(
                ContentItem.last_error.is_not(None),
                ContentItem.status == enums.STATUS_TERJADWAL,
                ContentItem.deleted_at.is_(None),
            )
        )
    )
    manual = content_service.item_manual_lewat_jadwal(db, now_wib_naive())
    telat = content_service.telat_dari_jadwal(db, 60)
    slot_kosong = warn_service.slot_kosong_hplus(db, today_wib(), 2)
    metrik_bolong = list(
        db.scalars(
            select(ContentItem).where(
                ContentItem.status == enums.STATUS_TAYANG,
                ContentItem.deleted_at.is_(None),
                ~ContentItem.metrik.any(),
            )
        )
    )
    token_badge = db.scalar(
        select(func.max(ApiToken.last_used_at)).where(ApiToken.revoked_at.is_(None))
    )
    outbox_gagal = int(
        db.scalar(
            select(func.count(NotificationOutbox.id)).where(NotificationOutbox.status == "gagal")
        )
        or 0
    )
    outbox_menunggu = int(
        db.scalar(
            select(func.count(NotificationOutbox.id)).where(NotificationOutbox.status == "kirim")
        )
        or 0
    )
    kpi = hitung_kpi(db)

    return render(
        request,
        "dashboard.html",
        judul="Dashboard",
        user=user,
        kuota=kuota,
        total=total,
        tayang=tayang,
        slot_terisi=slot_terisi,
        review_q=review_q,
        gagal=gagal,
        manual=manual,
        telat=telat,
        slot_kosong=slot_kosong,
        metrik_bolong=metrik_bolong,
        token_terakhir=token_badge,
        outbox_gagal=outbox_gagal,
        outbox_menunggu=outbox_menunggu,
        kpi=kpi,
    )


def hitung_kpi(db: Session) -> dict:
    """PRD §12 — KPI dihitung dari data, bukan diketik."""
    from sqlalchemy import and_

    total_tayang = int(
        db.scalar(
            select(func.count(ContentItem.id)).where(
                ContentItem.status == enums.STATUS_TAYANG, ContentItem.deleted_at.is_(None)
            )
        )
        or 0
    )
    metrik = db.execute(
        select(func.sum(MetricSnapshot.reach), func.sum(MetricSnapshot.likes),
               func.sum(MetricSnapshot.komen), func.sum(MetricSnapshot.share))
    ).one()
    reach, likes, komen, share = (int(v or 0) for v in metrik)
    share_rate = round(share / reach, 4) if reach else 0.0
    er = round((likes + komen + share) / reach, 4) if reach else 0.0

    # Ketepatan jadwal: posted_at <= jadwal slot + 60 menit
    from app.timeutil import slot_datetime

    tepat = telat_n = 0
    for item in db.scalars(
        select(ContentItem).where(
            ContentItem.posted_at.is_not(None),
            ContentItem.jadwal_tayang.is_not(None),
            ContentItem.slot_waktu.is_not(None),
        )
    ):
        from datetime import timedelta

        batas = slot_datetime(item.jadwal_tayang, item.slot_waktu) + timedelta(minutes=60)
        if item.posted_at <= batas:
            tepat += 1
        else:
            telat_n += 1

    # Waktu review: hanya item yang BENAR-BENAR melewati `review` (§10 rekonsiliasi)
    lewat_review = {
        row.entity_id
        for row in db.scalars(
            select(ActivityLog).where(
                ActivityLog.entity == "content_item",
                ActivityLog.from_status == enums.STATUS_REVIEW,
                ActivityLog.to_status.in_(
                    (enums.STATUS_SIAP_TAYANG, enums.STATUS_DITOLAK)
                ),
            )
        )
    }
    durasi = []
    for content_id in lewat_review:
        item = db.get(ContentItem, content_id)
        if item and item.submitted_at and item.reviewed_at:
            durasi.append((item.reviewed_at - item.submitted_at).total_seconds() / 3600.0)
    durasi.sort()
    median = 0.0
    if durasi:
        mid = len(durasi) // 2
        median = durasi[mid] if len(durasi) % 2 else (durasi[mid - 1] + durasi[mid]) / 2

    return {
        "total_tayang": total_tayang,
        "share_rate": share_rate,
        "er": er,
        "reach": reach,
        "share": share,
        "like": likes,
        "komen": komen,
        "tepat_jadwal": tepat,
        "telat_jadwal": telat_n,
        "median_review_jam": round(median, 2),
        "jumlah_lewat_review": len(lewat_review),
    }


# ------------------------------------------------------------------ review queue
@router.get("/review", response_class=HTMLResponse)
def review_queue(request: Request, db: Session = Depends(get_db), user: User = Depends(boss_wajib)):
    items = list(
        db.scalars(
            select(ContentItem)
            .where(ContentItem.status == enums.STATUS_REVIEW, ContentItem.deleted_at.is_(None))
            .order_by(ContentItem.submitted_at.asc().nullsfirst())
        )
    )
    detail = []
    for item in items:
        detail.append(
            {
                "item": item,
                "warning": warn_service.evaluasi(db, item),
                "checklist": checklist.daftar(db, item),
                "hook_serupa": [
                    h
                    for h in hooks.cari_duplikat(db, item.judul_hook, kecuali_id=item.id)
                ],
                "revisi": db.scalars(
                    select(ContentRevision)
                    .where(ContentRevision.content_id == item.id)
                    .order_by(ContentRevision.versi.desc())
                    .limit(3)
                ).all(),
            }
        )
    # Checklist bisa saja baru dibuat saat daftar ini dibangun (item lama / format
    # baru) → commit supaya ID yang dipakai form benar-benar ada saat diposting.
    db.commit()
    return render(request, "review.html", judul="Butuh aksi Boss", user=user, detail=detail)


@router.post("/review/{content_id}/approve")
def review_approve(
    content_id: int,
    request: Request,
    jadwal_tayang: str = Form(default=""),
    slot_waktu: str = Form(default=""),
    checklist_id: list[str] = Form(default=[]),
    csrf: str = Form(default=""),
    db: Session = Depends(get_db),
    user: User = Depends(boss_wajib),
):
    cek_csrf(request, csrf)
    item = content_service.ambil(db, content_id)
    for cid in checklist_id:
        checklist.set_centang_id(
            db,
            item,
            checklist_id=int(cid),
            tercentang=True,
            actor_type=enums.ACTOR_BOSS,
            actor_id=None,
        )
    tanggal = None
    if jadwal_tayang.strip():
        tanggal = parse_iso(jadwal_tayang)
        tanggal = tanggal.date() if isinstance(tanggal, datetime) else date.fromisoformat(jadwal_tayang)
    try:
        content_service.approve(db, item, jadwal_tayang=tanggal, slot_waktu=slot_waktu or None)
        db.commit()
        return _redirect("/review", f"Disetujui: {item.judul_hook[:40]}")
    except AppError as exc:
        db.rollback()
        return _redirect("/review", exc.message, "err")


@router.post("/review/{content_id}/reject")
def review_reject(
    content_id: int,
    request: Request,
    alasan: str = Form(default=""),
    csrf: str = Form(default=""),
    db: Session = Depends(get_db),
    user: User = Depends(boss_wajib),
):
    cek_csrf(request, csrf)
    item = content_service.ambil(db, content_id)
    try:
        content_service.reject(db, item, alasan=alasan)
        db.commit()
        return _redirect("/review", "Ditolak dengan alasan")
    except AppError as exc:
        db.rollback()
        return _redirect("/review", exc.message, "err")
