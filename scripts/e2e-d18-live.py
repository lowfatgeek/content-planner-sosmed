"""E2E hidup D18 — bukti outbox di instance yang benar-benar berjalan (bukan unit test).

Alur yang diuji lewat HTTP ke instance asli (default 127.0.0.1:8099):
  1. login Boss sungguhan (sandi dibaca dari var/.dev-boss-password, tidak pernah dicetak)
  2. agent submit draft lewat /api/v1 (token dari var/.token-agent) -> baris outbox lahir
  3. dashboard / menampilkan "Outbox menunggu kirim: 1"; /settings/outbox badge `menunggu`
  4. klaim atomik persis SQL di docs/POLLER-OUTBOX.md -> rowcount 1, klaim kedua rowcount 0
  5. dashboard tetap 1 saat baris berstatus `proses`; badge berubah jadi `proses (diklaim poller)`
  6. tutup klaim -> `terkirim`; dashboard 0; badge `terkirim`
  7. bersih-bersih: item uji + revisi + baris outbox dihapus, hitungan kembali seperti semula

Pakai:
    BASE=http://127.0.0.1:8099 .venv/bin/python scripts/e2e-d18-live.py
"""
from __future__ import annotations

import os
import re
import sys
import uuid
from pathlib import Path

import httpx
from sqlalchemy import create_engine, text

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

BASE = os.environ.get("BASE", "http://127.0.0.1:8099")
USERNAME = os.environ.get("BOSS_USERNAME", "boss")
REF = "uji:d18-live"

LULUS: list[str] = []
GAGAL: list[str] = []


def ok(p: str) -> None:
    LULUS.append(p)
    print(f"  OK   {p}")


def bad(p: str, d: str = "") -> None:
    GAGAL.append(f"{p} — {d}")
    print(f"  GAGAL {p} — {d}")


def cek(syarat: bool, p: str, d: str = "") -> bool:
    if syarat:
        ok(p)
    else:
        bad(p, d)
    return syarat


def menunggu(html: str) -> int:
    m = re.search(r"Outbox menunggu kirim:\s*<b>(\d+)</b>", html)
    if not m:
        raise AssertionError("pola 'Outbox menunggu kirim' tidak ada di dashboard")
    return int(m.group(1))


def badge_baris(html: str, row_id: int) -> str:
    """Teks badge pada baris tabel outbox dengan id tertentu (bukan cari kata di seluruh halaman)."""
    for b in re.findall(r"<tr>(.*?)</tr>", html, flags=re.S):
        if re.search(rf"<td>\s*{row_id}\s*</td>", b):
            m = re.search(r'class="badge[^"]*">([^<]+)<', b)
            return m.group(1).strip() if m else ""
    return ""


def main() -> int:
    sandi_path = BASE_DIR / "var/.dev-boss-password"
    token_path = BASE_DIR / "var/.token-agent"
    if not sandi_path.exists() or not token_path.exists():
        print(f"butuh {sandi_path.name} dan {token_path.name}", file=sys.stderr)
        return 2
    sandi = sandi_path.read_text(encoding="utf-8").strip()
    token = token_path.read_text(encoding="utf-8").strip()

    from app.config import settings

    print(f"[e2e] BASE={BASE} dialek DB={settings.DATABASE_URL.split('+')[0].split(':')[0]}")
    eng = create_engine(settings.DATABASE_URL)

    with eng.begin() as c:
        sisa_lama = c.execute(
            text("SELECT id FROM content_item WHERE request_ref = :r"), {"r": REF}
        ).scalars().all()
        for lama in sisa_lama:
            c.execute(text("DELETE FROM notification_outbox WHERE content_id = :i"), {"i": lama})
            c.execute(text("DELETE FROM activity_log WHERE entity_id = :i"), {"i": lama})
            for tbl in ("production_checklist", "content_revision", "schedule_slot", "metric_snapshot", "asset"):
                try:
                    c.execute(text(f"DELETE FROM {tbl} WHERE content_id = :i"), {"i": lama})
                except Exception:
                    pass
            c.execute(text("DELETE FROM content_item WHERE id = :i"), {"i": lama})
        # Kunci idempotency sisa run yang gagal: nomor item sudah hilang, jadi tidak bisa
        # ditelusuri lewat content_id — hapus berdasarkan pola kunci milik token uji ini.
        c.execute(
            text(
                "DELETE FROM idempotency_key WHERE `key` LIKE 'e2e-d18%'"
                " AND token_id = (SELECT id FROM api_token WHERE nama = 'agent-dev-8099')"
            )
        )
    if sisa_lama:
        print(f"[e2e] sisa item uji sebelumnya dibersihkan: {list(sisa_lama)}")

    with eng.connect() as c:
        awal_outbox = c.execute(text("SELECT COUNT(*) FROM notification_outbox")).scalar()
        awal_item = c.execute(text("SELECT COUNT(*) FROM content_item")).scalar()
    print(f"[e2e] sebelum: item={awal_item} outbox={awal_outbox}")

    boss = httpx.Client(base_url=BASE, follow_redirects=False, timeout=20.0)
    agent = httpx.Client(
        base_url=BASE, follow_redirects=False, timeout=20.0,
        headers={"Authorization": f"Bearer {token}"},
    )
    item_id: int | None = None

    try:
        print("== A. Login Boss sungguhan")
        r = boss.post("/login", data={"username": USERNAME, "password": sandi})
        cek(r.status_code == 303, "POST /login → 303 (sesi dibuat)", f"dapat {r.status_code}")

        r = boss.get("/")
        cek(r.status_code == 200, "GET / → 200 dashboard Boss", f"dapat {r.status_code}")
        n0 = menunggu(r.text)
        cek(n0 == awal_outbox, f"dashboard awal: menunggu={n0} == outbox di DB ({awal_outbox})", f"{n0}")

        print("== A2. Sapuan semua halaman Boss (jaring bug dialek MariaDB)")
        gagal_hal: list[str] = []
        for url in (
            "/", "/kanban", "/contents", "/contents/new", "/contents/1", "/review",
            "/calendar", "/metrics", "/templates", "/settings/tokens", "/settings/hooks",
            "/settings/outbox", "/settings/activity",
        ):
            rr = boss.get(url)
            if rr.status_code != 200:
                gagal_hal.append(f"{url} → {rr.status_code}")
        cek(not gagal_hal, f"{13 - len(gagal_hal)}/13 halaman Boss balas 200 di MariaDB",
            "; ".join(gagal_hal))

        print("== B. Agent submit draft lewat API (token baru)")
        r = agent.get("/api/v1/taxonomy")
        cek(r.status_code == 200, "GET /api/v1/taxonomy dengan token baru → 200", f"dapat {r.status_code} {r.text[:120]}")

        body = {
            "judul_hook": "Uji D18: baris outbox dari aksi nyata",
            "pilar": "pengingat",
            "format": "quote",
            "kanal": "fb_feed",
            "naskah_md": "Naskah uji D18. " + "Isi naskah yang cukup panjang. " * 3,
            "caption_fb": "CAPTION uji D18",
            "cta": "share",
            "hashtags": "#Uji",
            "fact_level": 1,
            "request_ref": REF,
            "source": "agent_brief",
        }
        kunci = f"e2e-d18-{uuid.uuid4().hex[:12]}"
        r = agent.post(
            "/api/v1/contents",
            json=body,
            headers={"Idempotency-Key": kunci},
        )
        cek(r.status_code == 201, "POST /api/v1/contents → 201 review", f"dapat {r.status_code} {r.text[:200]}")
        if r.status_code == 201:
            item_id = int(r.json()["data"]["id"])

        r2 = agent.post("/api/v1/contents", json=body, headers={"Idempotency-Key": kunci})
        cek(
            r2.status_code == 200 and r2.headers.get("Idempotent-Replay") == "true",
            "replay dengan kunci sama → 200 + Idempotent-Replay (D23, tidak bikin item kedua)",
            f"dapat {r2.status_code} {r2.headers.get('Idempotent-Replay')}",
        )

        with eng.connect() as c:
            baris_outbox = c.execute(
                text("SELECT id, kind, status FROM notification_outbox WHERE content_id = :i"),
                {"i": item_id},
            ).fetchone()
        cek(bool(baris_outbox), "baris outbox lahir dari transisi (kind draft_agent_masuk_review)",
            f"dapat {baris_outbox}")
        cek(bool(baris_outbox) and baris_outbox[1] == "draft_agent_masuk_review",
            "kind baris = draft_agent_masuk_review (D14)", f"{baris_outbox}")
        cek(bool(baris_outbox) and baris_outbox[2] == "kirim",
            "status awal baris outbox = 'kirim'", f"{baris_outbox}")
        outbox_id = int(baris_outbox[0]) if baris_outbox else -1

        print("== C. Dashboard & halaman outbox melihat baris itu")
        r = boss.get("/")
        cek(menunggu(r.text) == n0 + 1, f"dashboard: menunggu={n0 + 1}", f"{menunggu(r.text)}")
        r = boss.get("/settings/outbox")
        cek(r.status_code == 200, "GET /settings/outbox → 200", f"dapat {r.status_code}")
        cek(f"#{item_id}</a>" in r.text, "halaman outbox memuat tautan ke item uji")
        cek(badge_baris(r.text, outbox_id) == "menunggu",
            f"badge baris #{outbox_id} = 'menunggu'", f"dapat '{badge_baris(r.text, outbox_id)}'")

        print("== D. Klaim atomik (SQL persis docs/POLLER-OUTBOX.md)")
        token_klaim = "uji-klaim-1"
        sql_klaim = text(
            "UPDATE notification_outbox SET status='proses', claim_token=:t, claimed_at=NOW(),"
            " attempts=attempts+1 WHERE id=:id AND status='kirim'"
        )
        with eng.begin() as c:
            rc1 = c.execute(sql_klaim, {"t": token_klaim, "id": outbox_id}).rowcount
        cek(rc1 == 1, "klaim pertama rowcount=1", f"dapat {rc1}")
        with eng.begin() as c:
            rc2 = c.execute(sql_klaim, {"t": "uji-klaim-2", "id": outbox_id}).rowcount
        cek(rc2 == 0, "klaim kedua atas baris sama rowcount=0 (tidak dobel kirim)", f"dapat {rc2}")

        r = boss.get("/")
        cek(menunggu(r.text) == n0 + 1, "baris 'proses' tetap dihitung menunggu di dashboard (D18)", f"{menunggu(r.text)}")
        r = boss.get("/settings/outbox")
        cek(badge_baris(r.text, outbox_id) == "proses (diklaim poller)",
            "badge 'proses (diklaim poller)' tampil", f"dapat '{badge_baris(r.text, outbox_id)}'")

        print("== E. Tutup klaim → terkirim")
        sql_terkirim = text(
            "UPDATE notification_outbox SET status='terkirim', sent_at=NOW(), claim_token=NULL,"
            " claimed_at=NULL, last_error=NULL WHERE id=:id AND claim_token=:t"
        )
        with eng.begin() as c:
            rc3 = c.execute(sql_terkirim, {"id": outbox_id, "t": token_klaim}).rowcount
        cek(rc3 == 1, "tutup klaim dengan claim_token benar → rowcount=1", f"dapat {rc3}")
        with eng.begin() as c:
            rc4 = c.execute(sql_terkirim, {"id": outbox_id, "t": "uji-klaim-basi"}).rowcount
        cek(rc4 == 0, "UPDATE dengan claim_token salah tidak menyentuh baris (rowcount=0)", f"dapat {rc4}")

        r = boss.get("/")
        cek(menunggu(r.text) == n0, f"dashboard kembali menunggu={n0}", f"{menunggu(r.text)}")
        r = boss.get("/settings/outbox")
        cek(badge_baris(r.text, outbox_id) == "terkirim",
            "badge 'terkirim' tampil", f"dapat '{badge_baris(r.text, outbox_id)}'")
    finally:
        print("== F. Bersih-bersih (item uji + outbox + revisi)")
        with eng.begin() as c:
            c.execute(
                text(
                    "DELETE FROM idempotency_key WHERE `key` LIKE 'e2e-d18%'"
                    " AND token_id = (SELECT id FROM api_token WHERE nama = 'agent-dev-8099')"
                )
            )
            if item_id is not None:
                c.execute(text("DELETE FROM activity_log WHERE entity_id = :i"), {"i": item_id})
                c.execute(text("DELETE FROM notification_outbox WHERE content_id = :i"), {"i": item_id})
                for tbl in ("production_checklist", "content_revision", "schedule_slot", "metric_snapshot", "asset"):
                    try:
                        c.execute(text(f"DELETE FROM {tbl} WHERE content_id = :i"), {"i": item_id})
                    except Exception:
                        pass
                c.execute(text("DELETE FROM content_item WHERE id = :i"), {"i": item_id})
            sisa_outbox = c.execute(text("SELECT COUNT(*) FROM notification_outbox")).scalar()
            sisa_item = c.execute(text("SELECT COUNT(*) FROM content_item")).scalar()
        cek(sisa_outbox == awal_outbox, f"outbox kembali {awal_outbox} baris (pre-flight poller tetap valid)", f"{sisa_outbox}")
        cek(sisa_item == awal_item, f"content_item kembali {awal_item} baris", f"{sisa_item}")
        boss.close()
        agent.close()

    print(f"\nLULUS={len(LULUS)} GAGAL={len(GAGAL)}")
    for g in GAGAL:
        print("  -", g)
    return 1 if GAGAL else 0


if __name__ == "__main__":
    raise SystemExit(main())
