"""Rencana uji #3 & #4 (PRD §17) — smoke UI Boss + kasus negatif keamanan."""
from __future__ import annotations

import re

from app import enums
from app.services import content as content_service

CSRF_RE = re.compile(r'name="csrf" value="([^"]+)"')
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 128


def csrf_dari(client, url: str) -> str:
    r = client.get(url)
    assert r.status_code == 200, (url, r.status_code)
    m = CSRF_RE.search(r.text)
    assert m, f"token CSRF tidak ditemukan di {url}"
    return m.group(1)


# ------------------------------------------------------------------ uji #4 negatif
def test_tanpa_login_diarahkan_ke_login(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"

    r = client.get("/kanban", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_password_salah_ditolak(client, db):
    from sqlalchemy import select

    from app.auth import hash_password
    from app.models import User

    db.add(User(username="boss", password_hash=hash_password("benar-benar-panjang")))
    db.commit()

    r = client.post("/login", data={"username": "boss", "password": "salah"})
    assert r.status_code == 200
    assert "salah" in r.text.lower()

    r = client.post(
        "/login",
        data={"username": "boss", "password": "benar-benar-panjang"},
        follow_redirects=False,
    )
    assert r.status_code == 303


def test_cookie_tamper_ditolak(client):
    client.cookies.set("gj_session", "ini.bukan.cookie.sah")
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_csrf_wajib_pada_form_post(boss_client):
    r = boss_client.post(
        "/contents/new",
        data={
            "csrf": "token-palsu",
            "judul_hook": "Percobaan tanpa CSRF",
            "pilar": "pengingat",
            "format": "quote",
            "naskah_md": "x",
            "cta": "share",
            "fact_level": "1",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "csrf" in r.headers["location"].lower()


def test_upload_berbahaya_ditolak(client, boss_client, db):
    csrf = csrf_dari(boss_client, "/contents/new")
    r = boss_client.post(
        "/contents/new",
        data={
            "csrf": csrf,
            "judul_hook": "Konten untuk uji unggahan",
            "pilar": "pengingat",
            "format": "quote",
            "naskah_md": "x",
            "cta": "share",
            "fact_level": "1",
        },
        follow_redirects=False,
    )
    cid = int(r.headers["location"].split("/")[-1].split("?")[0])

    for nama, isi, tipe in (
        ("shell.php", b"<?php system($_GET['c']); ?>", "application/x-php"),
        ("jahat.exe", b"MZ\x90\x00", "application/octet-stream"),
        ("gambar.png", b"bukan gambar sebenarnya", "image/png"),  # magic bytes salah
    ):
        r = boss_client.post(
            f"/contents/{cid}/assets",
            files={"file": (nama, isi, tipe)},
            data={"csrf": csrf, "alt": "x"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "level=err" in r.headers["location"], (nama, r.headers["location"])

    # yang sah lolos
    r = boss_client.post(
        f"/contents/{cid}/assets",
        files={"file": ("gambar.png", PNG, "image/png")},
        data={"csrf": csrf, "alt": "ok"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "level=err" not in r.headers["location"]
    from sqlalchemy import select

    from app.models import Asset

    assert db.scalar(select(Asset.id).where(Asset.content_id == cid)) is not None


def test_api_upload_ekstensi_berbahaya_422(client, db, agent_token, fixtures):
    spec = fixtures["positif"][0]
    cid = client.post(
        "/api/v1/contents",
        json=spec["body"],
        headers={**agent_token["header"], "Idempotency-Key": "upl-1"},
    ).json()["data"]["id"]

    r = client.post(
        f"/api/v1/contents/{cid}/assets",
        files={"file": ("shell.php", b"<?php echo 1; ?>", "application/x-php")},
        headers=agent_token["header"],
    )
    assert r.status_code == 422

    r = client.post(
        f"/api/v1/contents/{cid}/assets",
        files={"file": ("foto.png", PNG, "image/png")},
        headers={**agent_token["header"], "Idempotency-Key": "upl-2"},
    )
    assert r.status_code == 201, r.text


def test_path_traversal_dan_env_tidak_bisa_diakses(boss_client):
    for url in ("/uploads/..%2F..%2F..%2Fetc%2Fpasswd", "/uploads/%2e%2e%2f.env", "/.env", "/var/gamisjumbo.db"):
        r = boss_client.get(url, follow_redirects=False)
        assert r.status_code in (404, 400, 405), (url, r.status_code)


# ------------------------------------------------------------------ uji #3 alur UI
def test_smoke_ui_boss_kanban_slot_metrik_hook(boss_client, db):
    csrf = csrf_dari(boss_client, "/contents/new")

    # 1) buat ide
    r = boss_client.post(
        "/contents/new",
        data={
            "csrf": csrf,
            "judul_hook": "Kadang bukan rezeki kita yang kurang",  # mirip seed bible §21 no.1
            "pilar": "pengingat",
            "format": "quote",
            "kanal": "fb_feed",
            "naskah_md": "Naskah awal dari Boss.",
            "cta": "share",
            "fact_level": "1",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    cid = int(r.headers["location"].split("/")[-1].split("?")[0])

    # 2) hook duplikat diperingatkan (baris seed sudah ada sejak bootstrap)
    halaman = boss_client.get(f"/contents/{cid}").text
    assert "hook mirip" in halaman
    assert "kadang bukan rezeki" in halaman

    # 3) simpan 2× → 2 versi
    for i, naskah in enumerate(["Naskah revisi satu.", "Naskah revisi dua."], start=1):
        r = boss_client.post(
            f"/contents/{cid}/edit",
            data={
                "csrf": csrf,
                "judul_hook": "Kadang bukan rezeki kita yang kurang",
                "pilar": "pengingat",
                "format": "quote",
                "kanal": "fb_feed",
                "naskah_md": naskah,
                "cta": "share",
                "fact_level": "1",
                "catatan": f"edit ke-{i}",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
    db.expire_all()
    assert content_service.versi_terakhir(db, cid) == 3  # v1 awal + 2 edit

    # 4) kanban 8 kolom
    r = boss_client.get("/kanban")
    assert r.status_code == 200
    for label in ("Ide", "Draft", "Review", "Ditolak", "Siap Tayang", "Terjadwal", "Tayang", "Arsip"):
        assert label in r.text

    # 5) checklist kosong → siap_tayang ditolak (pesan kembali)
    r = boss_client.post(
        f"/contents/{cid}/status",
        data={"csrf": csrf, "status": "siap_tayang"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.expire_all()
    item = content_service.ambil(db, cid)
    assert item.status != enums.STATUS_SIAP_TAYANG  # dari ide, transisi ide→siap_tayang tidak sah

    # 6) jalur Boss yang sah: ide → draft → siap_tayang (checklist dicentang)
    from tests.helpers import lengkapi_checklist
    from app import enums as E

    content_service.pindah_status_boss(db, item, E.STATUS_DRAFT)
    lengkapi_checklist(db, item)
    content_service.pindah_status_boss(db, item, E.STATUS_SIAP_TAYANG)
    db.commit()

    # 7) jadwalkan slot pagi & malam
    r = boss_client.post(
        f"/contents/{cid}/status",
        data={"csrf": csrf, "status": "terjadwal", "jadwal_tayang": "2033-07-04", "slot_waktu": "pagi"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.expire_all()
    item = content_service.ambil(db, cid)
    assert item.slot_waktu == "pagi" and str(item.jadwal_tayang) == "2033-07-04"

    r = boss_client.get("/calendar?bulan=2033-07")
    assert r.status_code == 200
    assert "2033" in r.text

    # 8) tandai tayang + isi metrik
    r = boss_client.post(
        f"/contents/{cid}/status",
        data={"csrf": csrf, "status": "tayang", "link_posting": "https://facebook.com/gamisjumboid/posts/9"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    r = boss_client.post(
        "/metrics/new",
        data={"csrf": csrf, "content_id": str(cid), "capture_date": "2033-07-05",
              "reach": "1000", "likes": "50", "komen": "20", "share": "30"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "level=err" not in r.headers["location"]

    # snapshot kedua di hari yang sama ditolak
    r = boss_client.post(
        "/metrics/new",
        data={"csrf": csrf, "content_id": str(cid), "capture_date": "2033-07-05",
              "reach": "1", "likes": "0", "komen": "0", "share": "0"},
        follow_redirects=False,
    )
    assert "level=err" in r.headers["location"]

    # 9) dashboard menampilkan share rate/ER terhitung
    r = boss_client.get("/")
    assert "Share rate" in r.text and "0.03" in r.text  # 30/1000

    # 10) template caption tersedia + bisa dipakai
    r = boss_client.get("/templates")
    assert r.status_code == 200 and "Template caption" in r.text

    # 11) halaman admin lain hidup
    for url in ("/settings/tokens", "/settings/hooks", "/settings/outbox", "/settings/activity", "/contents", "/review", "/metrics"):
        assert boss_client.get(url).status_code == 200, url
