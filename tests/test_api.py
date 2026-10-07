"""Rencana uji #5, #6, #9, #17 (PRD §17) — API positif/negatif, queue, reels, TZ."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app import enums
from app.services import content as content_service
from app.timeutil import now_wib, now_wib_naive, today_wib
from tests.conftest import key
from tests.helpers import siapkan_sampai_terjadwal


def _post(client, tok, spec):
    return client.post(
        "/api/v1/contents",
        json=spec["body"],
        headers={**tok["header"], **key(spec["id"])},
    )


# ------------------------------------------------------------------ positif
def test_fx01_sampai_fx06_masuk_review(client, agent_token, fixtures):
    assert client.get("/api/v1/health", headers=agent_token["header"]).status_code == 200

    ids = {}
    for spec in fixtures["positif"]:
        r = _post(client, agent_token, spec)
        assert r.status_code == spec["expected_http"], (spec["id"], r.status_code, r.text)
        body = r.json()["data"]
        assert body["status"] == enums.STATUS_REVIEW
        assert body["source"] in ("agent_brief", "agent_auto")
        assert body["versi_terakhir"] == 1
        ids[spec["id"]] = body["id"]

    # level 3 dengan sumber dalil lolos (FX-03)
    r = client.get(f"/api/v1/contents/{ids['FX-03']}", headers=agent_token["header"])
    assert r.json()["data"]["sumber_dalil"].startswith("HR. Bukhari")

    # reels dapat penanda manual (FX-05)
    r = client.get(f"/api/v1/contents/{ids['FX-05']}", headers=agent_token["header"])
    assert r.json()["data"]["format"] == "reels"


def test_me_taxonomy_dan_openapi(client, agent_token):
    r = client.get("/api/v1/me", headers=agent_token["header"])
    assert set(r.json()["scope"]) == set(enums.SCOPES)

    r = client.get("/api/v1/taxonomy", headers=agent_token["header"])
    tax = r.json()
    assert tax["tz"] == "Asia/Jakarta"
    assert tax["jam_slot"] == {"pagi": "06:00", "malam": "20:00"}
    assert len(tax["pilar"]) == 5
    assert sum(p["kuota_mingguan"] for p in tax["pilar"]) == 14
    assert "peran_slot" in tax and tax["level4_diparkir"] is True
    for p in tax["pilar"]:
        assert set(p["kuota_terpakai"]) == {
            "terpakai_minggu_ini",
            "pipeline_aktif",
            "belum_dijadwalkan",
        }


def test_alur_lengkap_sampai_tayang(client, db, agent_token, fixtures):
    """Boss approve + jadwalkan → agent lihat di queue?due= → POST /posted → tayang."""
    spec = fixtures["positif"][0]  # FX-01
    r = _post(client, agent_token, spec)
    cid = r.json()["data"]["id"]

    item = content_service.ambil(db, cid)
    siapkan_sampai_terjadwal(db, item, today_wib(), "pagi")
    db.commit()
    assert item.status == enums.STATUS_TERJADWAL

    due = (now_wib() + timedelta(days=2)).isoformat()
    r = client.get(f"/api/v1/queue?due={due}", headers=agent_token["header"])
    assert r.status_code == 200
    isi = r.json()
    assert any(row["id"] == cid for row in isi["data"]), isi
    baris = next(row for row in isi["data"] if row["id"] == cid)
    assert baris["jadwal_datetime"].endswith("+07:00")
    assert baris["manual"] is False

    link = "https://facebook.com/gamisjumboid/posts/1001"
    r = client.post(
        f"/api/v1/contents/{cid}/posted",
        json={"link_posting": link, "platform_post_id": "1001"},
        headers={**agent_token["header"], **key("posted-1")},
    )
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["status"] == enums.STATUS_TAYANG
    assert d["link_posting"] == link
    assert d["posted_at"].endswith("+07:00")

    # item hilang dari queue
    r = client.get(f"/api/v1/queue?due={due}", headers=agent_token["header"])
    assert all(row["id"] != cid for row in r.json()["data"])


def test_jalur_gagal_posting_tetap_terjadwal(client, db, agent_token, fixtures):
    spec = fixtures["positif"][3]  # FX-04
    r = _post(client, agent_token, spec)
    cid = r.json()["data"]["id"]
    item = content_service.ambil(db, cid)
    siapkan_sampai_terjadwal(db, item, today_wib(), "malam")
    db.commit()

    r = client.post(
        f"/api/v1/contents/{cid}/failed",
        json={"reason": "Facebook menolak: media tidak lengkap"},
        headers={**agent_token["header"], **key("failed-1")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == enums.STATUS_TERJADWAL
    assert r.json()["status_tetap"] == enums.STATUS_TERJADWAL

    db.expire_all()
    item = content_service.ambil(db, cid)
    assert item.status == enums.STATUS_TERJADWAL
    assert item.last_error.startswith("Facebook menolak")
    assert item.failed_at is not None


# ------------------------------------------------------------------ negatif
def test_nx01_level3_tanpa_sumber_dalil_422(client, agent_token, nx):
    r = client.post(
        "/api/v1/contents", json=nx["NX-01"]["body"], headers={**agent_token["header"], **key("nx01")}
    )
    assert r.status_code == 422, r.text


def test_nx02_agent_dilarang_level4_422(client, agent_token, nx):
    r = client.post(
        "/api/v1/contents", json=nx["NX-02"]["body"], headers={**agent_token["header"], **key("nx02")}
    )
    assert r.status_code == 422
    assert "level 4" in r.text.lower() or "d19" in r.text.lower()


def test_nx03_pilar_asing_422(client, agent_token, nx):
    r = client.post(
        "/api/v1/contents", json=nx["NX-03"]["body"], headers={**agent_token["header"], **key("nx03")}
    )
    assert r.status_code == 422, r.text
    assert "hukum_fiqih" in r.text


def test_nx04_format_asing_422(client, agent_token, nx):
    r = client.post(
        "/api/v1/contents", json=nx["NX-04"]["body"], headers={**agent_token["header"], **key("nx04")}
    )
    assert r.status_code == 422, r.text


def test_nx07_patch_item_boss_403(client, agent_token, boss_item, nx):
    r = client.patch(
        f"/api/v1/contents/{boss_item.id}",
        json=nx["NX-07"]["body"],
        headers={**agent_token["header"], **key("nx07")},
    )
    assert r.status_code == 403, r.text


def test_tanpa_token_401_dan_token_revoked_401(client, db, agent_token, fixtures):
    assert client.get("/api/v1/taxonomy").status_code == 401
    assert client.get("/api/v1/taxonomy", headers={"Authorization": "Bearer ngawur"}).status_code == 401

    from app.services import tokens as token_service

    token_service.revoke(db, agent_token["id"])
    db.commit()
    assert client.get("/api/v1/taxonomy", headers=agent_token["header"]).status_code == 401


def test_scope_kurang_403(client, readonly_token, fixtures):
    spec = fixtures["positif"][0]
    r = client.post(
        "/api/v1/contents",
        json=spec["body"],
        headers={**readonly_token["header"], **key("scope")},
    )
    assert r.status_code == 403


def test_agent_tidak_bisa_menyetel_field_boss_only(client, agent_token, fixtures):
    body = dict(fixtures["positif"][0]["body"])
    body["status"] = "siap_tayang"
    body["approved_at"] = "2026-10-07T10:00:00+07:00"
    r = client.post(
        "/api/v1/contents", json=body, headers={**agent_token["header"], **key("forbid-field")}
    )
    assert r.status_code == 422  # extra="forbid" pada ContentCreate

    r = client.patch(
        f"/api/v1/contents/1",
        json={"judul_hook": "x", "deleted_at": "2026-10-07T10:00:00+07:00"},
        headers={**agent_token["header"], **key("forbid-field-patch")},
    )
    assert r.status_code == 422


# ------------------------------------------------------------------ reels & TZ
def test_reels_tidak_masuk_queue_kecuali_include_manual(client, db, agent_token, fixtures):
    spec = [s for s in fixtures["positif"] if s["id"] == "FX-05"][0]
    r = _post(client, agent_token, spec)
    cid = r.json()["data"]["id"]
    item = content_service.ambil(db, cid)
    # tanggal unik: DB uji dipakai bersama satu sesi, jangan berebut slot (tanggal, jam)
    siapkan_sampai_terjadwal(db, item, today_wib() + timedelta(days=3), "malam")
    db.commit()

    due = (now_wib() + timedelta(days=5)).isoformat()
    r = client.get(f"/api/v1/queue?due={due}", headers=agent_token["header"])
    assert all(row["id"] != cid for row in r.json()["data"])
    assert any(row["id"] == cid for row in r.json()["butuh_boss"]["data"])

    r = client.get(f"/api/v1/queue?due={due}&include_manual=1", headers=agent_token["header"])
    assert any(row["id"] == cid for row in r.json()["data"])
    assert next(row for row in r.json()["data"] if row["id"] == cid)["manual"] is True


def test_tz_slot_malam_muncul_di_due_2005_wib(client, db, agent_token, fixtures):
    """Uji #9: item slot malam (20:00 WIB) muncul di queue?due=...T20:05:00+07:00."""
    spec = fixtures["positif"][1]  # FX-02 doa
    r = _post(client, agent_token, spec)
    cid = r.json()["data"]["id"]
    item = content_service.ambil(db, cid)

    besok = today_wib() + timedelta(days=1)
    siapkan_sampai_terjadwal(db, item, besok, "malam")
    db.commit()

    due_pas = f"{besok.isoformat()}T20:05:00+07:00"
    r = client.get(
        f"/api/v1/queue?due={due_pas}", headers=agent_token["header"]
    )
    assert any(row["id"] == cid for row in r.json()["data"]), r.json()

    # sebelum 20:00 belum jatuh tempo
    due_awal = f"{besok.isoformat()}T19:00:00+07:00"
    r = client.get(f"/api/v1/queue?due={due_awal}", headers=agent_token["header"])
    assert all(row["id"] != cid for row in r.json()["data"])

    assert r.json()["due"].endswith("+07:00")
    # slot pagi tidak ikut muncul untuk due 19:00 (06:00 < 19:00 → justru muncul)
    baris = [
        row
        for row in client.get(
            f"/api/v1/queue?due={due_pas}", headers=agent_token["header"]
        ).json()["data"]
        if row["id"] == cid
    ][0]
    assert baris["slot_waktu"] == "malam"
