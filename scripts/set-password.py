"""Ganti password Boss: tulis hash bcrypt ke .env (file, bukan database saja).

Pakai:  python scripts/set-password.py
        (password diminta lewat stdin, TIDAK pernah masuk argumen shell/history)
"""
from __future__ import annotations

import getpass
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.auth import hash_password  # noqa: E402
from app.config import BASE_DIR  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import User  # noqa: E402
from sqlalchemy import select  # noqa: E402

MIN_PANJANG = 12


def main() -> int:
    pw1 = getpass.getpass("Password baru Boss: ")
    pw2 = getpass.getpass("Ulangi: ")
    if pw1 != pw2:
        print("Password tidak sama.")
        return 1
    if len(pw1) < MIN_PANJANG:
        print(f"Minimal {MIN_PANJANG} karakter.")
        return 1

    hashed = hash_password(pw1)
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        env_path.write_text("", encoding="utf-8")
    teks = env_path.read_text(encoding="utf-8")
    if re.search(r"^BOSS_PASSWORD_HASH=.*$", teks, flags=re.M):
        teks = re.sub(r"^BOSS_PASSWORD_HASH=.*$", f"BOSS_PASSWORD_HASH={hashed}", teks, flags=re.M)
    else:
        teks = teks.rstrip("\n") + f"\nBOSS_PASSWORD_HASH={hashed}\n"
    env_path.write_text(teks, encoding="utf-8")
    env_path.chmod(0o600)
    print(f"Hash ditulis ke {env_path} (chmod 600).")

    username = "boss"
    for baris in teks.splitlines():
        if baris.startswith("BOSS_USERNAME="):
            username = baris.split("=", 1)[1].strip() or "boss"
    try:
        with SessionLocal() as db:
            user = db.scalar(select(User).where(User.username == username))
            if user is None:
                db.add(User(username=username, password_hash=hashed))
                print(f"User '{username}' dibuat.")
            else:
                user.password_hash = hashed
                print(f"User '{username}' diperbarui.")
            db.commit()
    except Exception as exc:  # pragma: no cover - DB mungkin belum siap
        print(f"[peringatan] gagal menulis ke DB ({exc}); .env sudah benar, jalankan scripts/bootstrap.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
