"""Bootstrap dev/uji: buat skema + seed taxonomy + akun Boss.

Idempoten — aman dijalankan ulang. Untuk produksi, skema dibuat lewat Alembic
(`alembic upgrade head`), bukan `create_all`.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import enums  # noqa: E402
from app.auth import hash_password  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import SessionLocal, create_all, engine  # noqa: E402
from app.models import User  # noqa: E402
from app.seed_loader import validasi_taxonomy  # noqa: E402
from app.services import taxonomy_service as tax  # noqa: E402
from app.timeutil import now_wib_naive  # noqa: E402
from sqlalchemy import select  # noqa: E402


def main() -> int:
    print(f"[bootstrap] DB = {engine.dialect.name} ({settings.DATABASE_URL.split('@')[-1]})")

    masalah = validasi_taxonomy()
    if masalah:
        print("[bootstrap] seed taxonomy bermasalah:")
        for m in masalah:
            print("  -", m)
        return 2

    settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    settings.LOG_DIR.mkdir(parents=True, exist_ok=True)
    Path(settings.DATABASE_URL.split("///")[-1]).parent.mkdir(parents=True, exist_ok=True) if settings.DATABASE_URL.startswith("sqlite") else None

    create_all()
    print("[bootstrap] skema siap")

    with SessionLocal() as db:
        hasil = tax.seed_semua(db)
        print(f"[bootstrap] seed: {hasil}")

        username = settings.BOSS_USERNAME
        user = db.scalar(select(User).where(User.username == username))
        if user is None:
            pw_hash = settings.BOSS_PASSWORD_HASH
            if not pw_hash:
                import secrets

                sementara = secrets.token_urlsafe(9)
                pw_hash = hash_password(sementara)
                print(
                    "[bootstrap] BOSS_PASSWORD_HASH kosong → password sementara dibuat. "
                    f"Password sementara: {sementara}  (SEGERA ganti: python scripts/set-password.py)"
                )
            db.add(User(username=username, password_hash=pw_hash, last_login=None))
            db.commit()
            print(f"[bootstrap] user Boss '{username}' dibuat")
        else:
            if settings.BOSS_PASSWORD_HASH and user.password_hash != settings.BOSS_PASSWORD_HASH:
                user.password_hash = settings.BOSS_PASSWORD_HASH
                db.commit()
                print("[bootstrap] password Boss disinkronkan dari .env")
            print(f"[bootstrap] user Boss '{username}' sudah ada")

    print("[bootstrap] selesai")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
