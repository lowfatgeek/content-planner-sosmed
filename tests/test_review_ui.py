"""Regresi: form checklist di layar /review benar-benar menyetujui item.

Bug yang dijaga di sini (ditemukan lewat verifikasi hidup, bukan lewat unit test):
1. Nilai form checklist dulu memakai TEKS butir, yang di-escape HTML saat dirender
   (`<=` jadi `&lt;=`), sehingga POST balik tidak cocok dengan butir asli di DB.
2. Baris checklist dibuat saat halaman GET dibangun tetapi tidak di-commit, jadi
   ID yang tampil di form menunjuk baris yang sudah ter-rollback.
Akibatnya tombol "Setujui" selalu balas "Checklist produksi belum lengkap".
"""
from __future__ import annotations

import re

from app import enums
from app.services import content as content_service
from tests.conftest import key
from tests.test_ui import csrf_dari


def _form_setujui(html: str, content_id: int) -> tuple[str, list[str]]:
    m = re.search(
        r'<form[^>]*action="/review/%d/approve"[^>]*>(.*?)</form>' % content_id, html, re.S
    )
    assert m, f"form Setujui untuk #{content_id} tidak ditemukan"
    blok = m.group(1)
    csrf = re.search(r'name="csrf"\s+value="([^"]+)"', blok).group(1)
    ids = re.findall(r'name="checklist_id"\s+value="([^"]*)"', blok)
    return csrf, ids


def test_approve_dari_layar_review_boss(boss_client, db, agent_token, fixtures):
    spec = fixtures["positif"][3]  # FX-04 (question) — formatnya punya butir checklist
    cid = boss_client.post(
        "/api/v1/contents",
        json=spec["body"],
        headers={**agent_token["header"], **key("ui-approve")},
    )
    assert cid.status_code in (200, 201), cid.text
    content_id = cid.json()["data"]["id"]

    halaman = boss_client.get("/review")
    assert halaman.status_code == 200
    csrf, ids = _form_setujui(halaman.text, content_id)
    assert ids, "form Setujui tidak memuat satu butir checklist pun"
    assert all(i.isdigit() for i in ids), f"nilai checklist harus ID, dapat: {ids}"

    r = boss_client.post(
        f"/review/{content_id}/approve",
        data={
            "csrf": csrf,
            "jadwal_tayang": "2034-02-01",
            "slot_waktu": "malam",
            "checklist_id": ids,
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    lokasi = r.headers["location"]
    assert "level=err" not in lokasi, lokasi

    db.expire_all()
    item = content_service.ambil(db, content_id)
    assert item.status == enums.STATUS_TERJADWAL, item.status
    assert item.approved_at is not None
    assert str(item.jadwal_tayang) == "2034-02-01"
    assert item.slot_waktu == "malam"

    from app.services import checklist as checklist_service

    rows = checklist_service.daftar(db, item)
    assert rows and all(x.tercentang for x in rows)
    assert all(x.actor_type == enums.ACTOR_BOSS for x in rows)

    # item hilang dari layar review setelah disetujui
    assert f'action="/review/{content_id}/approve"' not in boss_client.get("/review").text


def test_approve_tanpa_checklist_tetap_ditolak(boss_client, db, agent_token, fixtures):
    spec = fixtures["positif"][0]
    cid = boss_client.post(
        "/api/v1/contents",
        json=spec["body"],
        headers={**agent_token["header"], **key("ui-approve-neg")},
    ).json()["data"]["id"]

    csrf, _ids = _form_setujui(boss_client.get("/review").text, cid)
    r = boss_client.post(
        f"/review/{cid}/approve",
        data={"csrf": csrf, "jadwal_tayang": "", "slot_waktu": ""},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "level=err" in r.headers["location"]

    db.expire_all()
    assert content_service.ambil(db, cid).status == enums.STATUS_REVIEW


def test_tolak_dari_layar_review_tanpa_alasan_ditolak(boss_client, db, agent_token, fixtures):
    spec = fixtures["positif"][1]
    cid = boss_client.post(
        "/api/v1/contents",
        json=spec["body"],
        headers={**agent_token["header"], **key("ui-reject")},
    ).json()["data"]["id"]

    csrf = csrf_dari(boss_client, "/review")
    r = boss_client.post(
        f"/review/{cid}/reject", data={"csrf": csrf, "alasan": "kurang"}, follow_redirects=False
    )
    assert r.status_code == 303 and "level=err" in r.headers["location"]
    db.expire_all()
    assert content_service.ambil(db, cid).status == enums.STATUS_REVIEW

    r = boss_client.post(
        f"/review/{cid}/reject",
        data={"csrf": csrf, "alasan": "Dalil belum mencantumkan nomor kitab yang jelas"},
        follow_redirects=False,
    )
    assert r.status_code == 303 and "level=err" not in r.headers["location"]
    db.expire_all()
    item = content_service.ambil(db, cid)
    assert item.status == enums.STATUS_DITOLAK
    assert item.reject_reason.startswith("Dalil belum")
