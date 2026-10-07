"""Idempotency (F17, D10).

Aturan (D23):
- `POST /contents` pertama  → 201 (objek dibuat)
- replay key + body SAMA    → 200 + body identik + header `Idempotent-Replay: true`
- key sama, body BEDA       → 409
- `PATCH`, `posted`, `failed` → 200 (replay tetap 200)
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.config import settings
from app.errors import BadRequest, Conflict
from app.models import IdempotencyKey
from app.timeutil import now_wib_naive


@dataclass
class Replay:
    status: int
    body: dict


def request_hash(payload: dict) -> str:
    canon = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _bersihkan_kedaluwarsa(db: Session) -> None:
    db.execute(delete(IdempotencyKey).where(IdempotencyKey.expires_at < now_wib_naive()))


def cek(db: Session, *, key: str | None, token_id: int, payload: dict) -> Replay | None:
    """None = lanjutkan; Replay = sudah pernah diproses; Conflict = key dipakai body lain."""
    if not key or not key.strip():
        raise BadRequest("Header Idempotency-Key wajib untuk POST/PATCH agent")
    key = key.strip()[:120]
    _bersihkan_kedaluwarsa(db)

    row = db.get(IdempotencyKey, key)
    if row is None:
        return None

    if row.token_id != token_id:
        raise Conflict("Idempotency-Key sudah dipakai token lain")
    if row.request_hash != request_hash(payload):
        raise Conflict(
            "Idempotency-Key sama dengan body berbeda — kirim key baru untuk permintaan baru"
        )
    # D23: replay SELALU 200 + body identik + header `Idempotent-Replay: true`,
    # walaupun respons pertama 201 (objek memang tidak dibuat dua kali).
    return Replay(status=200, body=json.loads(row.response_json))


def simpan(
    db: Session,
    *,
    key: str | None,
    token_id: int,
    payload: dict,
    status: int,
    body: dict,
) -> None:
    if not key:
        return
    key = key.strip()[:120]
    db.add(
        IdempotencyKey(
            key=key,
            token_id=token_id,
            request_hash=request_hash(payload),
            response_status=status,
            response_json=json.dumps(body, ensure_ascii=False),
            created_at=now_wib_naive(),
            expires_at=now_wib_naive() + timedelta(hours=settings.IDEMPOTENCY_TTL_HOURS),
        )
    )
    db.flush()
