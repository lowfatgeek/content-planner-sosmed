"""Fixture pytest: DB sementara + app + token agent.

PATUH: DATABASE_URL di-set SEBELUM `app.config` diimpor, jadi .env tidak pernah
dipakai saat pengujian (uji tidak menyentuh data nyata).
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_TMP = Path(tempfile.mkdtemp(prefix="gjtest-"))
os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{_TMP / 'test.db'}"
os.environ["SECRET_KEY"] = "test-secret-key-not-for-production"
os.environ["UPLOAD_DIR"] = str(_TMP / "uploads")
os.environ["APP_ENV"] = "test"
os.environ["COOKIE_SECURE"] = "false"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app import enums  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402
from app.models import ApiToken, User  # noqa: E402
from app.services import taxonomy_service as tax  # noqa: E402
from app.services import tokens as token_service  # noqa: E402

FIXTURE_PATH = ROOT / "tests" / "fixtures" / "agent-api-fixtures-v1.json"


@pytest.fixture(autouse=True)
def _db():
    """Skema + seed dibuat ulang tiap uji → uji tidak saling mencemari."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        tax.seed_semua(db)
    yield


@pytest.fixture(scope="session", autouse=True)
def _bersihkan_tmp():
    yield
    shutil.rmtree(_TMP, ignore_errors=True)


@pytest.fixture()
def db():
    with SessionLocal() as session:
        yield session
        session.rollback()


@pytest.fixture()
def client():
    with TestClient(fastapi_app) as c:
        yield c


@pytest.fixture()
def fixtures() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.fixture()
def fx(fixtures) -> dict:
    return {f["id"]: f for f in fixtures["positif"]}


@pytest.fixture()
def nx(fixtures) -> dict:
    return {f["id"]: f for f in fixtures["negatif"]}


@pytest.fixture()
def agent_token(db):
    """Token agent lengkap (read+write+asset) + header siap pakai."""
    row, raw = token_service.buat_token(
        db, nama="pytest-agent", scopes=list(enums.SCOPES)
    )
    db.commit()
    return {"id": row.id, "raw": raw, "header": {"Authorization": f"Bearer {raw}"}}


@pytest.fixture()
def readonly_token(db):
    row, raw = token_service.buat_token(db, nama="pytest-readonly", scopes=[enums.SCOPE_READ])
    db.commit()
    return {"id": row.id, "raw": raw, "header": {"Authorization": f"Bearer {raw}"}}


def key(index: str) -> dict:
    """Header Idempotency-Key unik per kasus uji."""
    return {"Idempotency-Key": f"test-{index}-{os.urandom(6).hex()}"}


@pytest.fixture()
def boss_password_hash():
    from app.auth import hash_password

    return hash_password("kata-sandi-uji-123")


@pytest.fixture()
def boss_client(client, db, boss_password_hash):
    """Klien dengan sesi Boss sudah login (cookie + csrf)."""
    user = db.scalar(select(User).where(User.username == "boss"))
    if user is None:
        user = User(username="boss", password_hash=boss_password_hash)
        db.add(user)
    else:
        user.password_hash = boss_password_hash
    db.commit()
    r = client.post("/login", data={"username": "boss", "password": "kata-sandi-uji-123"}, follow_redirects=False)
    assert r.status_code == 303, r.text
    return client


@pytest.fixture()
def boss_item(db):
    """Satu item milik Boss (owner_token_id NULL) untuk uji NX-07."""
    from app.services import content as content_service

    item = content_service.buat_dari_boss(
        db,
        {
            "judul_hook": "Item Boss untuk uji izin",
            "pilar": "pengingat",
            "format": "quote",
            "naskah_md": "Naskah milik Boss.",
            "cta": "share",
            "fact_level": 1,
        },
    )
    db.commit()
    return item


def pytest_report_header(config):  # pragma: no cover
    return f"gamisjumbo test DB: {_TMP / 'test.db'}"
