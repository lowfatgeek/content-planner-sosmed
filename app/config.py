"""Konfigurasi aplikasi — semua nilai dibaca dari environment (.env).

Tidak ada kredensial yang ditulis di kode. Lihat `.env.example`.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# .env di root project (di produksi: /opt/gamisjumbo/.env, chmod 600)
load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


class Settings:
    # --- Identitas app -------------------------------------------------
    APP_NAME: str = os.getenv("APP_NAME", "Gamis Jumbo CMS")
    APP_ENV: str = os.getenv("APP_ENV", "dev")
    BASE_URL: str = os.getenv("BASE_URL", "http://127.0.0.1:8099")

    # --- Database ------------------------------------------------------
    # Produksi : mysql+pymysql://contentplanner:***@127.0.0.1:3306/content_planner?charset=utf8mb4
    # Dev/uji  : sqlite+pysqlite:////path/var/gamisjumbo.db
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        f"sqlite+pysqlite:///{BASE_DIR / 'var' / 'gamisjumbo.db'}",
    )

    # --- Keamanan ------------------------------------------------------
    SECRET_KEY: str = os.getenv("SECRET_KEY", "")
    SESSION_COOKIE: str = os.getenv("SESSION_COOKIE", "gj_session")
    SESSION_MAX_AGE: int = _int("SESSION_MAX_AGE", 14 * 24 * 3600)
    COOKIE_SECURE: bool = _bool("COOKIE_SECURE", False)
    BOSS_USERNAME: str = os.getenv("BOSS_USERNAME", "boss")
    # Hash bcrypt diisi lewat script `scripts/set-password.py` (tidak pernah plaintext)
    BOSS_PASSWORD_HASH: str = os.getenv("BOSS_PASSWORD_HASH", "")

    # --- Path ----------------------------------------------------------
    UPLOAD_DIR: Path = Path(os.getenv("UPLOAD_DIR", str(BASE_DIR / "var" / "uploads")))
    LOG_DIR: Path = Path(os.getenv("LOG_DIR", str(BASE_DIR / "var" / "logs")))
    SEED_DIR: Path = Path(os.getenv("SEED_DIR", str(BASE_DIR / "seed")))

    # --- Batas unggahan -------------------------------------------------
    MAX_UPLOAD_BYTES: int = _int("MAX_UPLOAD_BYTES", 20 * 1024 * 1024)  # 20 MB
    ALLOWED_IMAGE_EXT: tuple = ("jpg", "jpeg", "png", "webp", "gif")
    ALLOWED_IMAGE_MIME: tuple = ("image/jpeg", "image/png", "image/webp", "image/gif")

    # --- Idempotency ----------------------------------------------------
    IDEMPOTENCY_TTL_HOURS: int = _int("IDEMPOTENCY_TTL_HOURS", 24)

    # --- Magic numbers (juga ada di seed/taxonomy.yaml) -----------------
    SLOT_HARI: int = 2
    TARGET_MINGGUAN: int = _int("TARGET_MINGGUAN", 14)

    # --- Jalur loopback API (nginx membalas 404 untuk /api/ publik) -----
    API_HOST: str = os.getenv("API_HOST", "127.0.0.1")
    API_PORT: int = _int("API_PORT", 8099)


settings = Settings()

if not settings.SECRET_KEY:
    # Tanpa SECRET_KEY, cookie sesi tidak bisa dipercaya. Dev memakai nilai
    # acak-per-proses (semua sesi batal saat restart) supaya tidak ada
    # default lemah yang ikut ke produksi.
    import secrets

    settings.SECRET_KEY = secrets.token_urlsafe(48)

if settings.APP_ENV == "prod" and not os.getenv("DATABASE_URL"):
    # Jangan pernah diam-diam jatuh ke SQLite di produksi: datanya tertulis ke file
    # lokal, bukan ke MariaDB. Gagal cepat di startup lebih baik daripada data nyasar.
    raise RuntimeError("DATABASE_URL wajib diisi eksplisit saat APP_ENV=prod")
