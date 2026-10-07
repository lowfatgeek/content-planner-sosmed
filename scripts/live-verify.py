#!/usr/bin/env python3
"""live-verify.py — verifikasi ALUR LENGKAP pada app yang sedang hidup.

Bukan unit test: skrip ini masuk lewat halaman login sungguhan, menekan tombol
"Setujui" di layar review, lalu memeriksa /api/v1/queue — memakai fixture konten
asli dari @content yang sudah tersubmit.

Pakai:
    BOSS_PASSWORD=<sandi Boss> .venv/bin/python scripts/live-verify.py
    BOSS_PASSWORD=... BASE=http://127.0.0.1:8099 .venv/bin/python scripts/live-verify.py --format reels

Sandi TIDAK pernah dicetak. Keluar != 0 kalau ada pemeriksaan gagal.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date

import httpx

BASE = os.environ.get("BASE", "http://127.0.0.1:8099")
PASSWORD = os.environ.get("BOSS_PASSWORD", "")
USERNAME = os.environ.get("BOSS_USERNAME", "boss")

LULUS: list[str] = []
GAGAL: list[str] = []
LEWAT: list[str] = []


def ok(pesan: str) -> None:
    LULUS.append(pesan)
    print(f"  \033[32mOK\033[0m   {pesan}")


def bad(pesan: str, detail: str = "") -> None:
    GAGAL.append(pesan)
    print(f"  \033[31mFAIL\033[0m {pesan} — {detail}")


def skip(pesan: str) -> None:
    LEWAT.append(pesan)
    print(f"  \033[33mSKIP\033[0m {pesan}")


def form_untuk_item(html: str, content_id: int) -> dict | None:
    """Ambil field form `Setujui` milik satu item dari halaman /review."""
    pola = re.compile(
        r'<form[^>]*action="/review/%d/approve"[^>]*>(.*?)</form>' % content_id, re.S
    )
    m = pola.search(html)
    if not m:
        return None
    blok = m.group(1)
    data: dict[str, str] = {}
    csrf = re.search(r'name="csrf"\s+value="([^"]+)"', blok)
    if csrf:
        data["csrf"] = csrf.group(1)
    butir = re.findall(r'name="checklist_id"\s+value="([^"]*)"', blok)
    return {"csrf": data.get("csrf", ""), "butir": butir}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--format", default="reels", help="format item yang ingin diuji di queue")
    ap.add_argument("--slot", default="malam", choices=("pagi", "malam"))
    ap.add_argument("--jadwal", default=date.today().isoformat())
    args = ap.parse_args()

    if not PASSWORD:
        print("BOSS_PASSWORD belum diset (sandi Boss, tidak akan dicetak).", file=sys.stderr)
        return 2

    klien = httpx.Client(base_url=BASE, follow_redirects=False, timeout=20.0)

    print("== A. Gerbang login ==")
    r = klien.get("/")
    if r.status_code == 303 and r.headers.get("location") == "/login":
        ok("tanpa sesi → redirect /login")
    else:
        bad("tanpa sesi → redirect /login", f"{r.status_code} {r.headers.get('location')}")

    r = klien.post("/login", data={"username": USERNAME, "password": PASSWORD + "-salah"})
    if r.status_code == 200 and "salah" in r.text.lower():
        ok("password salah ditolak (halaman login kembali dengan pesan)")
    else:
        bad("password salah ditolak", str(r.status_code))

    r = klien.post("/login", data={"username": USERNAME, "password": PASSWORD})
    if r.status_code == 303:
        ok("login Boss berhasil (cookie sesi terpasang)")
    else:
        bad("login Boss", str(r.status_code))
        return 1

    print("== B. Halaman utama ==")
    for url, penanda in (
        ("/", "Dashboard"),
        ("/kanban", "Kanban"),
        ("/calendar", "Kalender"),
        ("/contents", "Bank Ide"),
        ("/metrics", "Metrik"),
        ("/settings/hooks", "Registry hook"),
        ("/settings/tokens", "Token API agent"),
        ("/settings/outbox", "Outbox notifikasi"),
        ("/settings/activity", "Audit log"),
    ):
        r = klien.get(url)
        if r.status_code == 200 and penanda in r.text:
            ok(f"{url} → 200 ({penanda})")
        else:
            bad(f"{url} → 200", f"{r.status_code}")

    print("== C. Layar 'Butuh aksi Boss' + tombol Setujui ==")
    r = klien.get("/review")
    halaman = r.text
    ids = sorted({int(x) for x in re.findall(r'/review/(\d+)/approve', halaman)})
    if ids:
        ok(f"layar review memuat {len(ids)} item: {ids}")
    else:
        skip("tidak ada item di layar review (jalankan smoke-api.sh dulu)")

    target = None
    for cid in ids:
        detail = klien.get(f"/contents/{cid}")
        # badge di halaman detail: <span class="badge">reels</span>
        if re.search(r'class="badge">\s*' + re.escape(args.format) + r'\s*<', detail.text):
            target = cid
            break

    if target is None:
        skip(f"tidak menemukan item berformat '{args.format}' di antara {ids}")
    else:
        form = form_untuk_item(halaman, target)
        if form is None:
            bad("form Setujui ditemukan", f"item #{target}")
        else:
            r = klien.post(
                f"/review/{target}/approve",
                data={
                    "csrf": form["csrf"],
                    "jadwal_tayang": args.jadwal,
                    "slot_waktu": args.slot,
                    "checklist_id": form["butir"],
                },
            )
            lokasi = r.headers.get("location", "")
            if r.status_code == 303 and "level=err" not in lokasi:
                ok(
                    f"item #{target} disetujui + dijadwalkan slot {args.slot} {args.jadwal} "
                    f"(checklist {len(form['butir'])} butir)"
                )
            else:
                bad("approve dari UI", f"{r.status_code} {lokasi}")

            r = klien.get(f"/contents/{target}")
            if "Siap Tayang" in r.text or "Terjadwal" in r.text:
                ok(f"halaman item #{target} menampilkan status baru")
            else:
                bad("status item setelah approve", "label tidak ditemukan")

    print("== D. Kalender menampilkan slot ==")
    bulan = args.jadwal[:7]
    r = klien.get(f"/calendar?bulan={bulan}")
    if r.status_code == 200 and (f"/contents/{target}" in r.text if target else True):
        ok(f"/calendar?bulan={bulan} → 200 dan slot terisi terlihat")
    else:
        bad("/calendar slot terisi", str(r.status_code))

    print("== E. Upload berbahaya ditolak (jalur UI) ==")
    if ids and target:
        r = klien.get(f"/contents/{target}")
        m = re.search(r'name="csrf"\s+value="([^"]+)"', r.text)
        csrf = m.group(1) if m else ""
        r = klien.post(
            f"/contents/{target}/assets",
            data={"csrf": csrf, "alt": "x"},
            files={"file": ("shell.php", b"<?php echo 1; ?>", "application/x-php")},
        )
        if r.status_code == 303 and "level=err" in r.headers.get("location", ""):
            ok("unggah .php ditolak lewat UI")
        else:
            bad("unggah .php ditolak", f"{r.status_code} {r.headers.get('location')}")
        r = klien.post(
            f"/contents/{target}/assets",
            data={"csrf": csrf, "alt": "ok"},
            files={"file": ("uji.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, "image/png")},
        )
        if r.status_code == 303 and "level=err" not in r.headers.get("location", ""):
            ok("unggah PNG sah diterima")
        else:
            bad("unggah PNG sah", f"{r.status_code} {r.headers.get('location')}")
    else:
        skip("uji unggahan dilewati (tidak ada item target)")

    print("== F. Queue agent setelah approve (D20) ==")
    token = os.environ.get("TOKEN", "")
    if not token:
        skip("TOKEN tidak diberikan → uji queue dilewati")
    else:
        h = {"Authorization": f"Bearer {token}"}
        due = f"{args.jadwal}T23:59:00+07:00"
        r = httpx.get(f"{BASE}/api/v1/queue", params={"due": due}, headers=h, timeout=20.0)
        if r.status_code != 200:
            bad("queue?due= → 200", f"{r.status_code} {r.text[:200]}")
        else:
            data = r.json()
            if target is None:
                skip("uji queue reels dilewati (tidak ada item target)")
            else:
                hadir = [x["id"] for x in data["data"] if x["id"] == target]
                manual = [x["id"] for x in data["butuh_boss"]["data"] if x["id"] == target]
                if args.format == "reels":
                    ok("reels TIDAK masuk queue?due= (D20)") if not hadir else bad("reels tidak masuk queue", str(hadir))
                    ok("reels muncul di butuh_boss") if manual else bad("reels di butuh_boss", "kosong")
                    r2 = httpx.get(
                        f"{BASE}/api/v1/queue",
                        params={"due": due, "include_manual": "1"},
                        headers=h,
                        timeout=20.0,
                    )
                    hadir2 = [x["id"] for x in r2.json()["data"] if x["id"] == target]
                    ok("include_manual=1 menampilkan reels") if hadir2 else bad("include_manual=1", str(hadir2))
                else:
                    ok("item non-reels muncul di queue?due=") if hadir else bad("item masuk queue", str(hadir))

    print()
    print("---------------------------------------------")
    print(f"LULUS={len(LULUS)}  GAGAL={len(GAGAL)}  DILEWATI={len(LEWAT)}")
    if GAGAL:
        print("Gagal: " + "; ".join(GAGAL))
        return 1
    print("Verifikasi alur hidup: semua pemeriksaan lolos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
