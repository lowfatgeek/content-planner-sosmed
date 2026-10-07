#!/usr/bin/env python
"""Verifikasi jalur POLLER (D18) di atas MariaDB + HTTP nyata.

Alur: (Boss) jadwalkan 1 item → (agent) queue?due= → POST /posted → hilang dari queue,
plus cek feed inkremental /api/v1/contents?cursor= setelah aksi Boss.

Jalankan dari root repo:
  BASE=http://127.0.0.1:8098 TOKEN=$(cat smoke-token.txt) .venv/bin/python probe_poller.py
"""
from __future__ import annotations

import os
import pathlib
import sys
from datetime import date, timedelta

import httpx

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import enums  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import ContentItem  # noqa: E402
from app.services import checklist, content as content_service  # noqa: E402
from app.timeutil import today_wib  # noqa: E402
from sqlalchemy import select  # noqa: E402

BASE = os.environ.get("BASE", "http://127.0.0.1:8098")
TOKEN = os.environ["TOKEN"]
HEAD = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"}

lulus = gagal = 0
gagal_nama: list[str] = []


def cek(nama: str, kondisi: bool, info: str = "") -> None:
    global lulus, gagal
    if kondisi:
        lulus += 1
        print(f"  OK   {nama}")
    else:
        gagal += 1
        gagal_nama.append(nama)
        print(f"  FAIL {nama} — {info}")


def lengkapi(db, item) -> None:
    for row in checklist.daftar(db, item):
        checklist.set_centang(
            db, item, butir=row.butir, tercentang=True, actor_type=enums.ACTOR_BOSS, actor_id=None
        )
    db.flush()


def main() -> int:
    klien = httpx.Client(base_url=BASE, timeout=20.0)

    # ---- 0. Siapkan: 1 item milik agent (paling baru) + jadwalkan oleh Boss
    with SessionLocal() as db:
        item = db.scalars(
            select(content_service.ContentItem)
            .where(content_service.ContentItem.owner_token_id.is_not(None))
            .order_by(content_service.ContentItem.id.desc())
        ).first()
        if item is None:
            print("FAIL tidak ada item milik agent di DB — jalankan probe fixture dulu")
            return 1
        cid = item.id
        lengkapi(db, item)
        tanggal = today_wib() + timedelta(days=1)
        try:
            content_service.approve(db, item, jadwal_tayang=tanggal, slot_waktu="pagi")
        except Exception as exc:  # slot terpakai → geser
            print(f"  (slot {tanggal} pagi terpakai: {type(exc).__name__}, geser +7 hari)")
            db.rollback()
            item = content_service.ambil(db, cid)
            lengkapi(db, item)
            tanggal = today_wib() + timedelta(days=7)
            content_service.approve(db, item, jadwal_tayang=tanggal, slot_waktu="pagi")
        db.commit()
        print(f"  (item #{cid} terjadwal {tanggal} pagi oleh Boss)")
        cid_tayang = cid

    print("== A. queue?due= memuat item terjadwal (D18) ==")
    due = f"{(today_wib() + timedelta(days=8)).isoformat()}T20:05:00+07:00"
    r = klien.get("/api/v1/queue", params={"due": due}, headers=HEAD)
    cek("queue 200", r.status_code == 200, r.text[:200])
    j = r.json()
    cek("due ber-offset +07:00", str(j.get("due", "")).endswith("+07:00"), str(j.get("due")))
    baris = [row for row in j["data"] if row["id"] == cid]
    cek("item terjadwal muncul di queue", bool(baris), str(j["data"])[:200])
    if baris:
        cek("jadwal_datetime offset +07:00", str(baris[0]["jadwal_datetime"]).endswith("+07:00"), str(baris[0]["jadwal_datetime"]))
        cek("manual=False", baris[0]["manual"] is False, str(baris[0]["manual"]))

    print("== B. POST /posted → tayang, lalu hilang dari queue ==")
    link = "https://facebook.com/gamisjumboid/posts/9001"
    r = klien.post(
        f"/api/v1/contents/{cid_tayang}/posted",
        json={"link_posting": link, "platform_post_id": "9001"},
        headers={**HEAD, "Idempotency-Key": "probe-posted-1"},
    )
    cek("posted 200", r.status_code == 200, r.text[:300])
    if r.status_code == 200:
        d = r.json()["data"]
        cek("status=tayang", d["status"] == enums.STATUS_TAYANG, d["status"])
        cek("link_posting tersimpan", d["link_posting"] == link, str(d.get("link_posting")))
        cek("posted_at offset +07:00", str(d.get("posted_at", "")).endswith("+07:00"), str(d.get("posted_at")))
    r = klien.get("/api/v1/queue", params={"due": due}, headers=HEAD)
    cek("hilang dari queue setelah tayang", all(row["id"] != cid_tayang for row in r.json()["data"]))
    r = klien.post(
        f"/api/v1/contents/{cid_tayang}/posted",
        json={"link_posting": link, "platform_post_id": "9001"},
        headers={**HEAD, "Idempotency-Key": "probe-posted-1"},
    )
    cek("replay idempoten → 200", r.status_code == 200, r.text[:200])

    print("== C. Feed inkremental /contents?cursor= (D11: aksi Boss saja) ==")
    r = klien.get("/api/v1/contents", params={"cursor": 0, "limit": 50}, headers=HEAD)
    cek("contents?cursor=0 → 200", r.status_code == 200, r.text[:200])
    j = r.json()
    cek("cursor naik > 0 setelah aksi Boss", j["cursor"] > 0, str(j["cursor"]))
    cek("data terisi", len(j["data"]) >= 1, str(len(j["data"])))
    cek(
        "item yang dijadwalkan Boss ada di feed",
        any(row["id"] == cid_tayang for row in j["data"]),
        str([row["id"] for row in j["data"]]),
    )
    r2 = klien.get("/api/v1/contents", params={"cursor": j["cursor"]}, headers=HEAD)
    cek("poll lanjutan (cursor terakhir) → 200 & kosong", r2.status_code == 200 and r2.json()["data"] == [], r2.text[:200])

    print()
    print("---------------------------------------------")
    print(f"LULUS={lulus}  GAGAL={gagal}")
    if gagal:
        print("Gagal:", ", ".join(gagal_nama))
        return 1
    print("Jalur poller lolos di atas MariaDB.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
