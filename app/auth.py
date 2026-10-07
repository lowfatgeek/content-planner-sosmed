"""Auth dua permukaan (PRD §14):

- UI Boss  : bcrypt + signed cookie (itsdangerous), CSRF pada semua form POST.
- API agent: Bearer token hash sha256 + scope, ditegakkan di SERVER (§7d).
"""
from __future__ import annotations

import hmac
import secrets
from dataclasses import dataclass

import bcrypt
from fastapi import Depends, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app import enums
from app.config import settings
from app.db import get_db
from app.errors import Forbidden, Unauthorized
from app.models import ApiToken, User
from app.services import tokens as token_service


# ------------------------------------------------------------------ UI Boss
def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.SECRET_KEY, salt="gj-session")


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def buat_sesi(user: User) -> str:
    return _serializer().dumps({"uid": user.id, "u": user.username, "csrf": secrets.token_urlsafe(24)})


def baca_sesi(request: Request) -> dict | None:
    raw = request.cookies.get(settings.SESSION_COOKIE)
    if not raw:
        return None
    try:
        return _serializer().loads(raw, max_age=settings.SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


def set_cookie(response, nilai: str) -> None:
    response.set_cookie(
        settings.SESSION_COOKIE,
        nilai,
        max_age=settings.SESSION_MAX_AGE,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


def clear_cookie(response) -> None:
    response.delete_cookie(settings.SESSION_COOKIE, path="/")


def user_dari_sesi(db: Session, sesi: dict | None) -> User | None:
    if not sesi or "uid" not in sesi:
        return None
    return db.get(User, int(sesi["uid"]))


def boss_wajib(request: Request, db: Session = Depends(get_db)) -> User:
    """Dependency UI: tanpa login → redirect /login (dipetakan di exception handler)."""
    user = user_dari_sesi(db, baca_sesi(request))
    if user is None:
        raise Unauthorized("Silakan login dulu")
    return user


def csrf_dari(request: Request) -> str:
    sesi = baca_sesi(request) or {}
    return str(sesi.get("csrf", ""))


def cek_csrf(request: Request, nilai: str | None) -> None:
    sesi = baca_sesi(request) or {}
    harap = str(sesi.get("csrf", ""))
    if not harap or not nilai or not hmac.compare_digest(harap, nilai):
        raise Forbidden("CSRF token tidak valid — muat ulang halaman lalu coba lagi")


def pastikan_login_dan_csrf(request: Request, db: Session) -> User:
    """Kombinasi untuk form POST HTML."""
    return boss_wajib(request, db)


# ------------------------------------------------------------------ API agent
@dataclass
class TokenCtx:
    token: ApiToken
    db: Session

    @property
    def id(self) -> int:
        return self.token.id


def _raw_bearer(request: Request) -> str:
    header = request.headers.get("authorization") or ""
    if not header.lower().startswith("bearer "):
        raise Unauthorized("Header Authorization: Bearer <token> wajib")
    return header[7:].strip()


def token_ctx(request: Request, db: Session = Depends(get_db)) -> TokenCtx:
    raw = _raw_bearer(request)
    if not raw:
        raise Unauthorized("Token kosong")
    row = token_service.cari_aktif(db, raw)
    if row is None:
        raise Unauthorized("Token tidak dikenal atau sudah dicabut (revoked)")
    token_service.tandai_dipakai(db, row)
    db.commit()
    return TokenCtx(token=row, db=db)


def butuh_scope(scope: str):
    def _dep(ctx: TokenCtx = Depends(token_ctx)) -> TokenCtx:
        if not ctx.token.has_scope(scope):
            raise Forbidden(f"Token tidak punya scope '{scope}'")
        return ctx

    return _dep


__all__ = [
    "TokenCtx",
    "boss_wajib",
    "baca_sesi",
    "buat_sesi",
    "butuh_scope",
    "cek_csrf",
    "clear_cookie",
    "csrf_dari",
    "hash_password",
    "set_cookie",
    "token_ctx",
    "user_dari_sesi",
    "verify_password",
    "enums",
]
