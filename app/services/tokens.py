"""Token API agent: pembuatan (plaintext tampil SEKALI), hash sha256, revoke (F16)."""
from __future__ import annotations

import hashlib
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import enums
from app.errors import Forbidden, NotFound
from app.models import ApiToken
from app.timeutil import now_wib_naive

PREFIX = "gja_"  # gamisjumbo-agent


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def buat_token(db: Session, *, nama: str, scopes: list[str]) -> tuple[ApiToken, str]:
    invalid = [s for s in scopes if s not in enums.SCOPES]
    if invalid:
        raise Forbidden(f"Scope tidak dikenal: {', '.join(invalid)}")
    if not scopes:
        raise Forbidden("Minimal satu scope wajib dipilih")
    raw = PREFIX + secrets.token_urlsafe(32)
    row = ApiToken(
        nama=nama.strip()[:120] or "agent",
        token_hash=hash_token(raw),
        scope=",".join(scopes),
        owner_actor=enums.ACTOR_AGENT,
        dibuat=now_wib_naive(),
    )
    db.add(row)
    db.flush()
    return row, raw


def revoke(db: Session, token_id: int) -> ApiToken:
    row = db.get(ApiToken, token_id)
    if row is None:
        raise NotFound("Token tidak ditemukan")
    row.revoked_at = now_wib_naive()
    db.flush()
    return row


def cari_aktif(db: Session, raw: str) -> ApiToken | None:
    row = db.scalar(select(ApiToken).where(ApiToken.token_hash == hash_token(raw)))
    if row is None or row.revoked_at is not None:
        return None
    return row


def tandai_dipakai(db: Session, token: ApiToken) -> None:
    token.last_used_at = now_wib_naive()


def daftar(db: Session) -> list[ApiToken]:
    return list(db.scalars(select(ApiToken).order_by(ApiToken.id.desc())))
