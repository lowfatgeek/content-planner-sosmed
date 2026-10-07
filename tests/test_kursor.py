"""Rencana uji #8 (PRD §17) — kursor & anti-loop: KV-01, KV-02."""
from __future__ import annotations

from app import enums
from app.services import audit, content as content_service
from tests.conftest import key


def _post(client, tok, spec, suffix=""):
    return client.post(
        "/api/v1/contents",
        json=spec["body"],
        headers={**tok["header"], **key(spec["id"] + suffix)},
    )


def test_aksi_agent_sendiri_tidak_muncul_di_feed(client, agent_token, fixtures):
    """D11: tanpa exclude, agent akan memproses ulang karyanya sendiri."""
    r = _post(client, agent_token, fixtures["positif"][0])
    assert r.status_code == 201

    r = client.get("/api/v1/contents?cursor=0", headers=agent_token["header"])
    assert r.status_code == 200
    assert r.json()["data"] == []
    assert r.json()["cursor"] == 0  # tidak ada baris Boss → kursor tidak maju


def test_kv01_item_ditolak_membawa_alasan_dan_versi(client, db, agent_token, fixtures):
    spec = fixtures["positif"][0]
    cid = _post(client, agent_token, spec).json()["data"]["id"]

    item = content_service.ambil(db, cid)
    content_service.reject(db, item, alasan="Hook terlalu umum, tidak ada nilai baru")
    db.commit()

    r = client.get("/api/v1/contents?cursor=0", headers=agent_token["header"])
    data = r.json()["data"]
    assert len(data) == 1
    row = data[0]
    assert row["id"] == cid
    assert row["status"] == enums.STATUS_DITOLAK
    assert row["reject_reason"].startswith("Hook terlalu umum")
    assert row["versi_terakhir"] == 1
    assert row["milik_pemanggil"] is True
    assert r.json()["cursor"] > 0

    # kursor berikutnya tidak mengulang baris yang sama
    r2 = client.get(f"/api/v1/contents?cursor={r.json()['cursor']}", headers=agent_token["header"])
    assert r2.json()["data"] == []


def test_kv02_patch_menaikkan_versi_dan_kembali_review(client, db, agent_token, fixtures):
    spec = fixtures["positif"][0]
    cid = _post(client, agent_token, spec).json()["data"]["id"]
    item = content_service.ambil(db, cid)
    content_service.reject(db, item, alasan="Naskah terlalu panjang untuk format quote")
    db.commit()

    r = client.patch(
        f"/api/v1/contents/{cid}",
        json={"naskah_md": "Versi ringkas setelah ditolak.", "catatan": "Diringkas jadi 3 baris"},
        headers={**agent_token["header"], **key("kv02")},
    )
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["status"] == enums.STATUS_REVIEW
    assert d["versi_terakhir"] == 2
    assert d["submitted_at"] is not None

    from sqlalchemy import func, select

    from app.models import ContentRevision

    n = db.scalar(
        select(func.count(ContentRevision.id)).where(ContentRevision.content_id == cid)
    )
    assert n == 2  # append-only: versi lama tetap ada
    revisi = list(
        db.scalars(select(ContentRevision).where(ContentRevision.content_id == cid).order_by(ContentRevision.versi))
    )
    assert revisi[0].naskah_md == spec["body"]["naskah_md"]
    assert revisi[1].actor_type == enums.ACTOR_AGENT


def test_patch_item_yang_sudah_disetujui_ditolak_beku(client, db, agent_token, fixtures):
    """§7d.3 — setelah approved_at terisi, item beku untuk agent."""
    from tests.helpers import lengkapi_checklist

    spec = fixtures["positif"][2]
    cid = _post(client, agent_token, spec).json()["data"]["id"]
    item = content_service.ambil(db, cid)
    lengkapi_checklist(db, item)
    content_service.approve(db, item)
    db.commit()

    r = client.patch(
        f"/api/v1/contents/{cid}",
        json={"naskah_md": "coba ubah setelah approve"},
        headers={**agent_token["header"], **key("beku")},
    )
    assert r.status_code == 403, r.text
    assert "beku" in r.text.lower()


def test_cursor_melompati_aksi_agent_ke_aksi_boss(client, db, agent_token, fixtures):
    """Sesudah aksi Boss, cursor harus naik melewati id log aksi Boss."""
    spec = fixtures["positif"][1]
    cid = _post(client, agent_token, spec).json()["data"]["id"]
    item = content_service.ambil(db, cid)
    content_service.reject(db, item, alasan="Sumber dalil kurang spesifik untuk level 2")
    db.commit()

    semua = audit.feed(db, cursor=0, limit=100, exclude_agent=False)
    hanya_boss = audit.feed(db, cursor=0, limit=100, exclude_agent=True)
    assert len(hanya_boss) < len(semua)
    assert all(r.actor_type != enums.ACTOR_AGENT for r in hanya_boss)
    assert any(r.actor_type == enums.ACTOR_AGENT for r in semua)
