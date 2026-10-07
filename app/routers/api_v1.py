"""REST API v1 untuk agent @content (PRD §7).

Base URL: http://127.0.0.1:8099 (loopback). nginx membalas 404 untuk /api/ publik.
Semua endpoint butuh `Authorization: Bearer <token>` + scope.
"""
from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, File, Header, Request, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import enums
from app.auth import TokenCtx, butuh_scope, token_ctx
from app.db import get_db
from app.errors import BadRequest, Forbidden, NotFound, ValidationError
from app.models import ApiToken, Asset, ContentItem
from app.schemas import ContentCreate, ContentPatch, FailedPayload, PostedPayload
from app.services import audit, checklist, content as content_service, idempotency, quota
from app.services import serializers, taxonomy_service, uploads
from app.timeutil import iso_wib, now_wib, now_wib_naive, parse_iso, today_wib

router = APIRouter(prefix="/api/v1", tags=["api-v1"])


def _json(body: dict, status: int = 200, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(content=body, status_code=status, headers=headers or {})


def _raw_json(request: Request) -> dict:
    return getattr(request.state, "raw_json", None) or {}


async def _baca_json(request: Request) -> dict:
    """Body JSON mentah — dipakai untuk hash idempotency dan validasi pydantic.

    Body yang bukan JSON valid dijawab 400 (bukan 500): agent yang salah kirim
    payload harus dapat pesan yang bisa ditindaklanjuti, bukan stack trace.
    """
    try:
        data = await request.json()
    except Exception as exc:
        raise BadRequest(
            "Body bukan JSON yang valid",
            details=[str(exc)],
        ) from exc
    if not isinstance(data, dict):
        raise BadRequest("Body harus objek JSON (bukan array/skalar)")
    return data


def _body_dict(model) -> dict:
    return {k: v for k, v in model.model_dump().items() if v is not None}


# ------------------------------------------------------------------ meta
@router.get("/health")
def health(ctx: TokenCtx = Depends(butuh_scope(enums.SCOPE_READ))):
    """Prasyarat poller: 200 siap / 503 tidak siap."""
    db = ctx.db
    siap = True
    pesan = "ok"
    try:
        n_pilar = db.scalar(select(ApiToken.id).limit(1))
        if n_pilar is None:  # pragma: no cover - token sendiri sudah ada
            siap, pesan = False, "tabel api_token kosong"
        from app.models import Pilar

        if db.scalar(select(Pilar.id).limit(1)) is None:
            siap, pesan = False, "seed taxonomy belum dijalankan"
    except Exception as exc:  # pragma: no cover - DB mati
        siap, pesan = False, f"database tidak siap: {exc}"
    return _json(
        {
            "status": "ok" if siap else "degraded",
            "pesan": pesan,
            "tz": "Asia/Jakarta",
            "waktu": iso_wib(now_wib_naive()),
            "versi": "1.0",
        },
        status=200 if siap else 503,
    )


@router.get("/me")
def me(ctx: TokenCtx = Depends(butuh_scope(enums.SCOPE_READ))):
    tok = ctx.token
    return _json(
        {
            "nama": tok.nama,
            "scope": tok.scope_list,
            "owner_actor": tok.owner_actor,
            "last_used_at": iso_wib(tok.last_used_at),
            "revoked": tok.revoked_at is not None,
        }
    )


@router.get("/taxonomy")
def taxonomy_view(ctx: TokenCtx = Depends(butuh_scope(enums.SCOPE_READ))):
    return _json(taxonomy_service.payload_taxonomy(ctx.db))


# ------------------------------------------------------------------ contents
@router.post("/contents")
async def create_content(
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ctx: TokenCtx = Depends(butuh_scope(enums.SCOPE_WRITE)),
):
    raw = await _baca_json(request)
    replay = idempotency.cek(ctx.db, key=idempotency_key, token_id=ctx.id, payload=raw)
    if replay is not None:
        return _json(replay.body, status=replay.status, headers={"Idempotent-Replay": "true"})

    data = ContentCreate(**raw)
    item, warns = content_service.buat_dari_agent(ctx.db, ctx.token, data.model_dump())
    body = {
        "data": serializers.content_dict(
            ctx.db, item, viewer=enums.ACTOR_AGENT, token_id=ctx.id, warnings=warns
        )
    }
    idempotency.simpan(
        ctx.db, key=idempotency_key, token_id=ctx.id, payload=raw, status=201, body=body
    )
    ctx.db.commit()
    return _json(body, status=201, headers={"X-Request-Id": x_request_id or ""})


@router.patch("/contents/{content_id}")
async def patch_content(
    content_id: int,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ctx: TokenCtx = Depends(butuh_scope(enums.SCOPE_WRITE)),
):
    raw = await _baca_json(request)
    replay = idempotency.cek(ctx.db, key=idempotency_key, token_id=ctx.id, payload=raw)
    if replay is not None:
        return _json(replay.body, status=replay.status, headers={"Idempotent-Replay": "true"})

    data = ContentPatch(**raw)
    item, warns = content_service.patch_dari_agent(ctx.db, ctx.token, content_id, data.model_dump())
    body = {
        "data": serializers.content_dict(
            ctx.db, item, viewer=enums.ACTOR_AGENT, token_id=ctx.id, warnings=warns
        )
    }
    idempotency.simpan(
        ctx.db, key=idempotency_key, token_id=ctx.id, payload=raw, status=200, body=body
    )
    ctx.db.commit()
    return _json(body, status=200)


@router.get("/contents")
def list_contents(
    status: str | None = None,
    cursor: int = 0,
    limit: int = 100,
    ctx: TokenCtx = Depends(butuh_scope(enums.SCOPE_READ)),
):
    """Sinkronisasi inkremental dari `activity_log.id`, exclude aksi agent (D11)."""
    limit = max(1, min(int(limit or 100), 500))
    if status and status not in enums.STATUSES:
        raise ValidationError(f"Status '{status}' tidak dikenal")

    logs = audit.feed(ctx.db, cursor=int(cursor or 0), limit=limit * 3, exclude_agent=True)
    terakhir: dict[int, int] = {}
    for log in logs:
        if log.entity == "content_item" and log.entity_id:
            terakhir[log.entity_id] = log.id

    data = []
    for content_id, log_id in sorted(terakhir.items(), key=lambda kv: kv[1]):
        item = ctx.db.get(ContentItem, content_id)
        if item is None or item.deleted_at is not None:
            continue
        if status and item.status != status:
            continue
        milik = item.owner_token_id == ctx.id
        warns = None
        if item.status == enums.STATUS_DITOLAK and milik:
            from app.services import warnings as warn_service

            warns = warn_service.evaluasi(ctx.db, item)
        data.append(
            serializers.feed_item(
                item,
                versi=serializers.versi_terakhir(ctx.db, item.id),
                milik_pemanggil=milik,
                warnings=warns,
            )
        )

    next_cursor = max([log.id for log in logs], default=int(cursor or 0))
    return _json({"data": data, "cursor": next_cursor, "limit": limit})

@router.get("/contents/{content_id}")
def get_content(content_id: int, ctx: TokenCtx = Depends(butuh_scope(enums.SCOPE_READ))):
    item = content_service.ambil(ctx.db, content_id)
    milik = item.owner_token_id == ctx.id
    if not milik and item.owner_token_id is not None:
        raise Forbidden("Item ini bukan milik token Anda")
    dari = ctx.db.scalars(
        select(Asset).where(Asset.content_id == item.id).order_by(Asset.urutan, Asset.id)
    )
    data = serializers.content_dict(
        ctx.db,
        item,
        viewer=enums.ACTOR_BOSS if milik else enums.ACTOR_AGENT,
        token_id=ctx.id,
    )
    data["checklist"] = (
        [
            {"butir": r.butir, "tercentang": r.tercentang}
            for r in checklist.daftar(ctx.db, item)
        ]
        if milik
        else None
    )
    data["aset"] = [{"id": a.id, "jenis": a.jenis, "path": a.path, "urutan": a.urutan} for a in dari]
    return _json({"data": data})


@router.get("/queue")
def queue(
    due: str | None = None,
    include_manual: int = 0,
    ctx: TokenCtx = Depends(butuh_scope(enums.SCOPE_READ)),
):
    """Meja kerja agent: `terjadwal` + jam slot ≤ due. `reels` TIDAK ikut kecuali include_manual=1."""
    due_dt = now_wib()
    if due and due.strip():
        try:
            due_dt = parse_iso(due) or now_wib()
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
    items = content_service.queue_due(ctx.db, due_dt, include_manual=bool(int(include_manual or 0)))
    manual = content_service.item_manual_lewat_jadwal(ctx.db, due_dt)
    return _json(
        {
            "due": iso_wib(due_dt),
            "tz": "Asia/Jakarta",
            "count": len(items),
            "data": [serializers.queue_item(i) for i in items],
            "butuh_boss": {
                "count": len(manual),
                "data": [
                    {
                        "id": i.id,
                        "judul_hook": i.judul_hook,
                        "format": i.format,
                        "kanal": i.kanal,
                        "jadwal_tayang": i.jadwal_tayang.isoformat() if i.jadwal_tayang else None,
                        "slot_waktu": i.slot_waktu,
                    }
                    for i in manual
                ],
            },
        }
    )


@router.post("/contents/{content_id}/posted")
async def posted(
    content_id: int,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ctx: TokenCtx = Depends(butuh_scope(enums.SCOPE_WRITE)),
):
    """Idempoten: replay pada item `tayang` → 200 + state sama, bukan 4xx (D10)."""
    raw = await _baca_json(request)
    item = content_service.ambil(ctx.db, content_id)
    if item.owner_token_id != ctx.id:
        raise Forbidden("Item ini bukan milik token Anda")

    if item.status == enums.STATUS_TAYANG:
        body = {
            "data": serializers.content_dict(
                ctx.db, item, viewer=enums.ACTOR_AGENT, token_id=ctx.id
            ),
            "idempotent": True,
        }
        ctx.db.commit()
        return _json(body, status=200, headers={"Idempotent-Replay": "true"})

    replay = idempotency.cek(ctx.db, key=idempotency_key, token_id=ctx.id, payload=raw)
    if replay is not None:
        return _json(replay.body, status=replay.status, headers={"Idempotent-Replay": "true"})

    data = PostedPayload(**raw)
    item, _ = content_service.posted_dari_agent(
        ctx.db,
        ctx.token,
        content_id,
        link_posting=data.link_posting,
        platform_post_id=data.platform_post_id,
        posted_at=parse_iso(data.posted_at) if data.posted_at else None,
    )
    body = {
        "data": serializers.content_dict(
            ctx.db, item, viewer=enums.ACTOR_AGENT, token_id=ctx.id
        )
    }
    idempotency.simpan(
        ctx.db, key=idempotency_key, token_id=ctx.id, payload=raw, status=200, body=body
    )
    ctx.db.commit()
    return _json(body, status=200)


@router.post("/contents/{content_id}/failed")
async def failed(
    content_id: int,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ctx: TokenCtx = Depends(butuh_scope(enums.SCOPE_WRITE)),
):
    """Tidak mengubah status: `last_error` + `failed_at` + badge merah ke Boss (F18)."""
    raw = await _baca_json(request)
    replay = idempotency.cek(ctx.db, key=idempotency_key, token_id=ctx.id, payload=raw)
    if replay is not None:
        return _json(replay.body, status=replay.status, headers={"Idempotent-Replay": "true"})

    data = FailedPayload(**raw)
    item = content_service.failed_dari_agent(ctx.db, ctx.token, content_id, reason=data.reason)
    body = {
        "data": serializers.content_dict(
            ctx.db, item, viewer=enums.ACTOR_AGENT, token_id=ctx.id
        ),
        "status_tetap": item.status,
    }
    idempotency.simpan(
        ctx.db, key=idempotency_key, token_id=ctx.id, payload=raw, status=200, body=body
    )
    ctx.db.commit()
    return _json(body, status=200)


@router.post("/contents/{content_id}/assets")
async def upload_asset(
    content_id: int,
    file: UploadFile = File(...),
    alt: str | None = None,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ctx: TokenCtx = Depends(butuh_scope(enums.SCOPE_ASSET)),
):
    item = content_service.ambil(ctx.db, content_id)
    if item.owner_token_id != ctx.id:
        raise Forbidden("Item ini bukan milik token Anda")

    blob = await file.read()
    nama, sha, ext = uploads.simpan(file.filename or "aset", file.content_type, blob)
    payload = {"filename": file.filename, "sha256": sha, "content_id": content_id}
    if idempotency_key:
        replay = idempotency.cek(
            ctx.db, key=idempotency_key, token_id=ctx.id, payload=payload
        )
        if replay is not None:
            return _json(replay.body, status=replay.status, headers={"Idempotent-Replay": "true"})

    urutan = len(list(ctx.db.scalars(select(Asset).where(Asset.content_id == item.id))))
    row = Asset(
        content_id=item.id,
        jenis="gambar" if ext in ("jpg", "jpeg", "png", "webp", "gif") else "lain",
        path=nama,
        alt=alt,
        urutan=urutan,
        ukuran=len(blob),
        uploaded_by_token_id=ctx.id,
        dibuat=now_wib_naive(),
    )
    ctx.db.add(row)
    ctx.db.flush()
    audit.catat(
        ctx.db,
        actor_type=enums.ACTOR_AGENT,
        actor_id=ctx.id,
        action="upload_asset",
        entity="asset",
        entity_id=row.id,
        meta={"content_id": item.id, "sha256": sha, "ukuran": len(blob)},
    )
    body = {
        "data": {
            "id": row.id,
            "content_id": item.id,
            "jenis": row.jenis,
            "path": row.path,
            "ukuran": row.ukuran,
            "sha256": sha,
            "url": f"/uploads/{row.path}",
        }
    }
    if idempotency_key:
        idempotency.simpan(
            ctx.db, key=idempotency_key, token_id=ctx.id, payload=payload, status=201, body=body
        )
    ctx.db.commit()
    return _json(body, status=201)
