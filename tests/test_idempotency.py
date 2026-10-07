"""Rencana uji #7 (PRD §17) — idempotency: NX-05, NX-06, posted/failed replay."""
from __future__ import annotations

from app import enums
from app.services import content as content_service
from app.timeutil import now_wib, today_wib
from tests.conftest import key
from tests.helpers import siapkan_sampai_terjadwal


def test_nx05_replay_key_body_sama_201_lalu_200_identik(client, agent_token, fixtures):
    spec = fixtures["positif"][0]  # FX-01
    h = {**agent_token["header"], "Idempotency-Key": spec["idempotency_key"]}

    r1 = client.post("/api/v1/contents", json=spec["body"], headers=h)
    assert r1.status_code == 201, r1.text

    r2 = client.post("/api/v1/contents", json=spec["body"], headers=h)
    assert r2.status_code == 200, r2.text
    assert r2.headers.get("Idempotent-Replay") == "true"
    assert r2.json() == r1.json()  # body identik

    # hanya satu baris konten
    from sqlalchemy import func, select

    from app.db import SessionLocal
    from app.models import ContentItem

    with SessionLocal() as db:
        n = db.scalar(
            select(func.count(ContentItem.id)).where(ContentItem.judul_hook == spec["body"]["judul_hook"])
        )
    assert n == 1


def test_nx06_key_sama_body_beda_409(client, agent_token, fixtures, nx):
    spec = fixtures["positif"][0]
    h = {**agent_token["header"], "Idempotency-Key": spec["idempotency_key"]}
    assert client.post("/api/v1/contents", json=spec["body"], headers=h).status_code == 201

    r = client.post("/api/v1/contents", json=nx["NX-06"]["body"], headers=h)
    assert r.status_code == 409, r.text
    assert "berbeda" in r.text or "berbeda" in r.text.lower()


def test_idempotency_key_wajib(client, agent_token, fixtures):
    spec = fixtures["positif"][3]
    r = client.post("/api/v1/contents", json=spec["body"], headers=agent_token["header"])
    assert r.status_code == 400, r.text
    assert "Idempotency-Key" in r.text


def test_posted_dua_kali_tetap_200_tidak_dobel(client, db, agent_token, fixtures):
    spec = fixtures["positif"][2]  # FX-03
    r = client.post(
        "/api/v1/contents", json=spec["body"], headers={**agent_token["header"], **key("idem-fx03")}
    )
    cid = r.json()["data"]["id"]
    item = content_service.ambil(db, cid)
    siapkan_sampai_terjadwal(db, item, today_wib(), "pagi")
    db.commit()

    k = key("posted-replay")
    payload = {"link_posting": "https://facebook.com/gamisjumboid/posts/7", "platform_post_id": "7"}
    r1 = client.post(f"/api/v1/contents/{cid}/posted", json=payload, headers={**agent_token["header"], **k})
    assert r1.status_code == 200
    r2 = client.post(f"/api/v1/contents/{cid}/posted", json=payload, headers={**agent_token["header"], **k})
    assert r2.status_code == 200, r2.text
    assert r2.json()["data"]["status"] == enums.STATUS_TAYANG

    # replay pada item yang SUDAH tayang, key berbeda → tetap 200 (bukan 4xx)
    r3 = client.post(
        f"/api/v1/contents/{cid}/posted", json=payload, headers={**agent_token["header"], **key("posted-3")}
    )
    assert r3.status_code == 200
    assert r3.headers.get("Idempotent-Replay") == "true"

    from sqlalchemy import func, select

    from app.db import SessionLocal
    from app.models import ContentItem, NotificationOutbox

    with SessionLocal() as s:
        item = s.get(ContentItem, cid)
        assert item.status == enums.STATUS_TAYANG
        notif = s.scalar(
            select(func.count(NotificationOutbox.id)).where(
                NotificationOutbox.content_id == cid, NotificationOutbox.kind == "tayang"
            )
        )
    assert notif == 1  # notifikasi tidak dikirim berulang


def test_platform_post_id_berbeda_409(client, db, agent_token, fixtures):
    spec = fixtures["positif"][5]  # FX-06
    r = client.post(
        "/api/v1/contents", json=spec["body"], headers={**agent_token["header"], **key("idem-fx06")}
    )
    cid = r.json()["data"]["id"]
    item = content_service.ambil(db, cid)
    siapkan_sampai_terjadwal(db, item, today_wib(), "malam")
    db.commit()

    r1 = client.post(
        f"/api/v1/contents/{cid}/posted",
        json={"link_posting": "https://fb.com/1", "platform_post_id": "AAA"},
        headers={**agent_token["header"], **key("pid-1")},
    )
    assert r1.status_code == 200
    db.expire_all()
    # paksa state jadi terjadwal lagi untuk menguji konflik platform_post_id
    item = content_service.ambil(db, cid)
    item.status = enums.STATUS_TERJADWAL
    db.commit()
    r2 = client.post(
        f"/api/v1/contents/{cid}/posted",
        json={"link_posting": "https://fb.com/2", "platform_post_id": "BBB"},
        headers={**agent_token["header"], **key("pid-2")},
    )
    assert r2.status_code == 409, r2.text


def test_failed_replay_dan_isi_outbox(client, db, agent_token, fixtures):
    spec = fixtures["positif"][1]  # FX-02
    r = client.post(
        "/api/v1/contents", json=spec["body"], headers={**agent_token["header"], **key("idem-fx02")}
    )
    cid = r.json()["data"]["id"]
    item = content_service.ambil(db, cid)
    siapkan_sampai_terjadwal(db, item, today_wib(), "pagi")
    db.commit()

    k = key("failed-replay")
    body = {"reason": "Token halaman FB kedaluwarsa"}
    r1 = client.post(f"/api/v1/contents/{cid}/failed", json=body, headers={**agent_token["header"], **k})
    r2 = client.post(f"/api/v1/contents/{cid}/failed", json=body, headers={**agent_token["header"], **k})
    assert (r1.status_code, r2.status_code) == (200, 200)
    assert r1.json() == r2.json()

    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import NotificationOutbox

    with SessionLocal() as s:
        kinds = [o.kind for o in s.scalars(select(NotificationOutbox).where(NotificationOutbox.content_id == cid))]
    assert "gagal_posting" in kinds
