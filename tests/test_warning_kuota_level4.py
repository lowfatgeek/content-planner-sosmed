"""Rencana uji #20 + #19 + #15/#16 (PRD §17) — warning 3 pemicu, kuota, level 4."""
from __future__ import annotations

from datetime import date

from sqlalchemy import func, select

from app import enums
from app.models import ContentItem, Pilar
from app.services import content as content_service
from app.services import quota, warnings as warn_service
from tests.conftest import key
from tests.helpers import lengkapi_checklist


# ------------------------------------------------------------------ warning
def test_warning_pemicu_iii_tabrakan_usulan_jadwal(client, agent_token, fixtures):
    """Urutan FX-05 lebih dulu, lalu FX-04 → FX-04 dapat warning (pembacaan §19.6).

    FX-05 mengusulkan 2026-10-11T20:30, FX-04 mengusulkan 2026-10-11T20:00 →
    satu hari, satu slot (malam), selisih < 3 jam.
    """
    fx05 = [s for s in fixtures["positif"] if s["id"] == "FX-05"][0]
    fx04 = [s for s in fixtures["positif"] if s["id"] == "FX-04"][0]

    r5 = client.post("/api/v1/contents", json=fx05["body"], headers={**agent_token["header"], **key("fx05")})
    assert r5.status_code == 201

    r4 = client.post("/api/v1/contents", json=fx04["body"], headers={**agent_token["header"], **key("fx04")})
    assert r4.status_code == 201, r4.text  # warning TIDAK memblokir

    kode = [w["kode"] for w in r4.json()["data"]["warning"]]
    assert "usulan_jadwal_tabrakan" in kode, r4.json()["data"]["warning"]


def test_warning_pemicu_i_format_sama_dua_slot(client, db, agent_token, fixtures):
    fx01 = fixtures["positif"][0]  # quote, usulan 09 Okt 06:00
    r1 = client.post("/api/v1/contents", json=fx01["body"], headers={**agent_token["header"], **key("w1")})
    cid = r1.json()["data"]["id"]
    item = content_service.ambil(db, cid)
    lengkapi_checklist(db, item)
    content_service.approve(db, item, jadwal_tayang=date(2031, 5, 6), slot_waktu="pagi")
    db.commit()

    body = dict(fx01["body"])
    body["judul_hook"] = "Hook berbeda untuk format quote di slot malam"
    body["usulan_jadwal"] = "2031-05-06T20:00:00+07:00"
    r2 = client.post("/api/v1/contents", json=body, headers={**agent_token["header"], **key("w2")})
    assert r2.status_code == 201
    kode = [w["kode"] for w in r2.json()["data"]["warning"]]
    assert "format_sama_dua_slot" in kode, r2.json()["data"]["warning"]


def test_warning_pemicu_ii_engagement_di_slot_pagi(client, agent_token, fixtures):
    body = dict(fixtures["positif"][3]["body"])  # question
    body["usulan_jadwal"] = "2032-01-03T06:00:00+07:00"  # slot pagi
    body["judul_hook"] = "Pertanyaan ringan untuk pagi hari"
    r = client.post("/api/v1/contents", json=body, headers={**agent_token["header"], **key("w3")})
    assert r.status_code == 201
    kode = [w["kode"] for w in r.json()["data"]["warning"]]
    assert "engagement_di_slot_pagi" in kode, r.json()["data"]["warning"]


def test_warning_nol_blokir_semua_item_tetap_tersimpan(client, db, agent_token, fixtures):
    n = 0
    for spec in fixtures["positif"]:
        r = client.post(
            "/api/v1/contents", json=spec["body"], headers={**agent_token["header"], **key("wb" + spec["id"])}
        )
        assert r.status_code == 201
        n += 1
    total = db.scalar(select(func.count(ContentItem.id)))
    assert total == n
    assert r.json()["data"]["warning"] is not None  # warning[] selalu ada di respons


# ------------------------------------------------------------------ kuota
def test_kq01_tiga_angka_kuota_konsisten_dengan_sql(client, db, agent_token, fixtures):
    """Uji #19: angka dari GET /taxonomy == hitungan manual (dua angka sama)."""
    for spec in fixtures["positif"]:
        assert (
            client.post(
                "/api/v1/contents",
                json=spec["body"],
                headers={**agent_token["header"], **key("kq" + spec["id"])},
            ).status_code
            == 201
        )

    db.expire_all()
    tax = client.get("/api/v1/taxonomy", headers=agent_token["header"]).json()

    # hitungan independen langsung dari SQL
    manual = db.execute(
        select(Pilar.slug, func.count(ContentItem.id))
        .join(ContentItem, ContentItem.pilar_id == Pilar.id)
        .where(
            ContentItem.status.in_(quota.STATUS_PIPELINE),
            ContentItem.deleted_at.is_(None),
        )
        .group_by(Pilar.slug)
    ).all()
    manual_map = {slug: int(n) for slug, n in manual}

    for p in tax["pilar"]:
        assert p["kuota_terpakai"]["pipeline_aktif"] == manual_map.get(p["slug"], 0), p

    total_pipeline = sum(p["kuota_terpakai"]["pipeline_aktif"] for p in tax["pilar"])
    assert total_pipeline == 6  # 6 item fixture positif, semuanya `review`
    assert total_pipeline == db.scalar(
        select(func.count(ContentItem.id)).where(ContentItem.status == enums.STATUS_REVIEW)
    )


def test_kuota_terpakai_minggu_ini_butuh_jadwal(client, db, agent_token, fixtures):
    """Item `review` TIDAK dihitung terpakai; setelah dijadwalkan minggu ini → dihitung."""
    spec = fixtures["positif"][0]
    cid = client.post(
        "/api/v1/contents", json=spec["body"], headers={**agent_token["header"], **key("kq-slot")}
    ).json()["data"]["id"]

    def angka_pengingat():
        db.expire_all()
        tax = client.get("/api/v1/taxonomy", headers=agent_token["header"]).json()
        return next(p for p in tax["pilar"] if p["slug"] == "pengingat")["kuota_terpakai"]

    assert angka_pengingat()["terpakai_minggu_ini"] == 0

    from app.timeutil import today_wib

    item = content_service.ambil(db, cid)
    lengkapi_checklist(db, item)
    content_service.approve(db, item, jadwal_tayang=today_wib(), slot_waktu="pagi")
    db.commit()

    a = angka_pengingat()
    assert a["terpakai_minggu_ini"] == 1
    assert a["belum_dijadwalkan"] == 0


# ------------------------------------------------------------------ level 4
def test_kq02_level4_item_boss_butuh_verifikator(client, db, boss_client):
    """Uji #16: item Boss level 4 tanpa verifikator tidak bisa siap_tayang."""
    from tests.test_ui import csrf_dari

    csrf = csrf_dari(boss_client, "/contents/new")
    r = boss_client.post(
        "/contents/new",
        data={
            "csrf": csrf,
            "judul_hook": "Hukum fiqih contoh untuk uji level 4",
            "pilar": "pengingat",
            "format": "quote",
            "kanal": "fb_feed",
            "naskah_md": "Naskah level 4",
            "cta": "share",
            "fact_level": "4",
            "sumber_dalil": "kitab X hal. 12",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    cid = int(r.headers["location"].split("/")[-1].split("?")[0])

    item = content_service.ambil(db, cid)
    lengkapi_checklist(db, item)
    content_service.pindah_status_boss(db, item, enums.STATUS_DRAFT)
    db.commit()

    r = boss_client.post(
        f"/contents/{cid}/status",
        data={"csrf": csrf, "status": "siap_tayang"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "pesan=" in r.headers["location"]
    db.expire_all()
    item = content_service.ambil(db, cid)
    assert item.status == enums.STATUS_DRAFT  # ditolak gate verifikator

    # isi verifikator lewat form edit → lolos
    r = boss_client.post(
        f"/contents/{cid}/edit",
        data={
            "csrf": csrf,
            "judul_hook": item.judul_hook,
            "pilar": "pengingat",
            "format": "quote",
            "kanal": "fb_feed",
            "naskah_md": item.naskah_md,
            "cta": "share",
            "fact_level": "4",
            "sumber_dalil": "kitab X hal. 12",
            "verifikator": "ustadz X — youtu.be/contoh",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.expire_all()
    item = content_service.ambil(db, cid)
    assert item.verifikator.startswith("ustadz X")

    r = boss_client.post(
        f"/contents/{cid}/status",
        data={"csrf": csrf, "status": "siap_tayang"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.expire_all()
    item = content_service.ambil(db, cid)
    assert item.status == enums.STATUS_SIAP_TAYANG
    assert item.approved_at is not None


def test_agent_level4_ditolak_422(client, agent_token):
    r = client.post(
        "/api/v1/contents",
        json={
            "judul_hook": "Hukum riba dalam jual beli",
            "pilar": "pengingat",
            "format": "quote",
            "naskah_md": "x",
            "cta": "share",
            "fact_level": 4,
            "sumber_dalil": "kitab Y",
        },
        headers={**agent_token["header"], **key("lvl4")},
    )
    assert r.status_code == 422
