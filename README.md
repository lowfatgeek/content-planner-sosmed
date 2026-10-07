# Content Planner Sosmed — Gamis Jumbo CMS

Web app internal untuk mengelola pipeline ide konten **Facebook Page Gamis Jumbo**:
tangkap ide → tulis naskah → cek fakta → **review/approve Boss** → jadwalkan (2 slot/hari) →
**agent memposting** → catat performa.

Satu tempat untuk **dua aktor**:

| Aktor | Jalur | Hak |
|---|---|---|
| **Boss (manusia)** | UI SSR (Jinja2 + HTMX) | semua: approve, reject, jadwalkan, hapus, verifikasi level 4 |
| **agent `@content`** | REST `/api/v1` (Bearer token) | buat draft, revisi draft sendiri, baca bank ide (metadata), baca queue, lapor posted/failed |

Implementasi dari **PRD v1.4 — Gamis Jumbo Content Management System** (Fase 1 / MVP).
App **tidak** memposting ke Facebook: itu pekerjaan agent. App mencatat hasilnya.

- Stack: **FastAPI + Jinja2 + HTMX + REST JSON**, SQLAlchemy 2.0 + Alembic, MariaDB 10.11 (produksi) / SQLite (dev & uji)
- Jalur API agent: **loopback** `http://127.0.0.1:8099` — `/api/` **404** untuk publik di nginx (D17)
- Notifikasi: app hanya menulis tabel `notification_outbox`; **pengirimnya poller @hermes** memakai bot Telegram profil `default` (D18). Tidak ada kredensial bot di repo ini.

---

## Status

| Bagian | Keadaan |
|---|---|
| Fase 1 (MVP) F1–F21 | **selesai & teruji** — 65 uji pytest hijau |
| Smoke API dengan fixture asli `@content` | **hijau** — `LULUS=31 GAGAL=0` |
| Verifikasi alur hidup (login → review → queue) | **hijau** — `LULUS=21 GAGAL=0` |
| Jalur MariaDB (`content_planner`) | schema + seed **sudah ada** (dibuat @hermes); app dijalankan & diuji end-to-end di sana: health 200, teks Arab utuh (utf8mb4), waktu `+07:00` |
| nginx vhost + TLS + systemd | **belum diaktifkan** — paket Level 2, menunggu approval Boss (berkas siap di `deploy/`) |
| Uji restore dari dump | **belum dijalankan** (Butir Level 3, buat/hapus scratch DB) |
| Level 4 (fiqih) | **diparkir** sesuai D19 — agent → 422; item Boss butuh `verifikator` |

Rincian bukti: [`docs/HASIL-VERIFIKASI.md`](docs/HASIL-VERIFIKASI.md).

---

## Mulai cepat (dev, SQLite — nol dampak produksi)

```bash
python3.10 -m venv .venv            # WAJIB /usr/bin/python3.10 (3.10.12), BUKAN python3 milik Hermes
.venv/bin/pip install -r requirements.txt
cp .env.example .env
.venv/bin/python scripts/set-password.py    # menulis hash bcrypt ke .env (chmod 600)
.venv/bin/python scripts/bootstrap.py       # skema + seed taxonomy/hook + akun Boss
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8099
```

Buka <http://127.0.0.1:8099> → login → beli token agent di **/settings/tokens** (plaintext
hanya tampil sekali) → jalankan smoke test:

```bash
TOKEN=<token> bash scripts/smoke-api.sh
```

Cara pakai sehari-hari: **[`PANDUAN-PAKAI.md`](PANDUAN-PAKAI.md)**.

---

## Struktur

```
app/
  main.py            entrypoint FastAPI + exception handler (JSON vs redirect)
  config.py          semua konfigurasi dari environment (.env); tidak ada rahasia di kode
  db.py              engine/sesi SQLAlchemy; SET time_zone='+07:00' di MariaDB
  timeutil.py        helper WIB (naive di DB, offset +07:00 di API)
  enums.py           enum baku PRD §9 (status, format, kanal, cta, source, slot, scope)
  models.py          tabel PRD §8 (+ production_checklist, lihat catatan di bawah)
  schemas.py         skema request API v1 (extra="forbid" → field Boss-only mustahil dikirim agent)
  auth.py            sesi Boss (bcrypt + signed cookie + CSRF) & Bearer token + scope
  errors.py          error domain → kode HTTP (D23)
  seed_loader.py     pembaca seed/taxonomy.yaml & seed/hooks.yaml (cache)
  routers/
    api_v1.py        seluruh endpoint §7b
    ui.py            login, dashboard, layar review (approve/reject)
    ui_content.py    bank ide, kanban, editor, revisi, aset, metrik, kalender
    ui_admin.py      template caption, token, hook registry, outbox, audit log
  services/
    content.py       orkestrasi siklus hidup konten (satu pintu untuk semua perubahan status)
    transitions.py   tabel transisi T1–T12 + otorisasi aktor (§10)
    checklist.py     checklist produksi per format (F7)
    warnings.py      3 pemicu warning, nol blokir (D22)
    quota.py         tiga angka kuota per pilar (§7b)
    hooks.py         normalisasi hook + registry anti-duplikasi (F11, D24)
    idempotency.py   Idempotency-Key: 201 → replay 200 identik → 409 (D23)
    audit.py         activity_log + feed kursor (F19, D11)
    notify.py        penulis notification_outbox (F20)
    uploads.py       validasi unggahan: ekstensi + MIME + magic bytes (F8)
    serializers.py   dua tingkat akses: Boss (lengkap) vs agent (metadata) (D9)
    taxonomy_service.py  seed dari YAML + payload GET /taxonomy
  templates/  static/  (htmx.min.js di-vendor, tanpa CDN)

seed/taxonomy.yaml    pilar, kuota, format, cta, jam slot, peran slot, TZ, batas  (D15)
seed/hooks.yaml       30 hook awal dari content bible §21                          (D24)
migrations/           Alembic (upgrade + downgrade teruji)
scripts/              bootstrap, set-password, smoke-api, live-verify, dev-reset,
                      verify-mariadb, backup-db, restore-test
deploy/               unit systemd + vhost nginx (SIAP, BELUM diaktifkan)
tests/                65 uji pytest, memakai fixture konten asli dari @content
```

---

## Kontrak API agent (ringkas)

Semua endpoint butuh `Authorization: Bearer <token>` + scope; `POST`/`PATCH` wajib
`Idempotency-Key`. Base URL loopback `http://127.0.0.1:8099`.

| Method & path | Scope | Catatan |
|---|---|---|
| `GET /api/v1/health` | read | 200 siap / 503 tidak siap (prasyarat poller) |
| `GET /api/v1/me` | read | nama, scope, `last_used_at` |
| `GET /api/v1/taxonomy` | read | pilar + 3 angka kuota, format, cta, jam & peran slot, TZ, batas |
| `POST /api/v1/contents` | write | **201**; submit draft → status `review` |
| `PATCH /api/v1/contents/{id}` | write | hanya item milik token; `draft`/`review`/`ditolak`; → `review` |
| `GET /api/v1/contents?status=&cursor=&limit=` | read | kursor `activity_log.id`, **exclude aksi agent** |
| `GET /api/v1/contents/{id}` | read | item agent lengkap; item Boss **metadata saja** |
| `GET /api/v1/queue?due=&include_manual=` | read | `terjadwal` + jam slot ≤ due; `reels` tidak ikut (D20) |
| `POST /api/v1/contents/{id}/posted` | write | → `tayang`; idempoten (replay → 200 state sama) |
| `POST /api/v1/contents/{id}/failed` | write | tetap `terjadwal` + `last_error` + badge merah |
| `POST /api/v1/contents/{id}/assets` | asset:write | multipart; validasi sama dengan jalur UI |

Kode status (D23): `POST` → **201** · replay key+body sama → **200** + `Idempotent-Replay: true` ·
`PATCH`/`posted`/`failed` → **200** · key sama body beda → **409** · validasi → **422** ·
auth/scope/akses item Boss → **401/403** · body bukan JSON → **400**.

Contoh:

```bash
curl -sS -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8099/api/v1/taxonomy | jq .

curl -sS -X POST http://127.0.0.1:8099/api/v1/contents \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -H "Idempotency-Key: $(uuidgen)" \
  --data-binary @body.json | jq .
```

> **Tanda `+` di query string:** `?due=2026-10-09T20:05:00+07:00` tanpa percent-encoding
> terbaca sebagai spasi oleh parser query. App **memaklumi** bentuk itu (spasi sebelum
> offset diterima), jadi contoh di PRD bekerja apa adanya. Kalau ragu, kirim `%2B`.

---

## Aturan yang ditegakkan di server (bukan cuma disembunyikan dari UI)

1. Field `approved_at`, `reject_reason`, `deleted_at`, `status`, `owner_token_id`, `source`
   **ditolak sebagai input agent** (skema `extra="forbid"`) dan divalidasi ulang per-request.
2. Agent hanya menyentuh item `owner_token_id` miliknya; item Boss → **403** (bahkan sebelum
   info transisi dibocorkan).
3. Setelah `approved_at` terisi, item **beku** untuk agent (kecuali laporan T9 `posted`, yang
   memang terjadi setelah approve).
4. `fact_level` 3–4 wajib `sumber_dalil`; level 4 ⇒ agent **422**, item Boss butuh `verifikator`.
5. `reels`/`fb_reels` tidak masuk `queue?due=` — badge "butuh Boss", bukan `failed` palsu.
6. Satu slot = satu konten (`UNIQUE(tanggal, platform, slot_waktu)`).
7. Tiga pemicu `warning[]`, **nol blokir** (D22) — app ini milik Boss.

---

## Catatan implementasi (bukan keputusan scope)

- **`production_checklist`** — PRD §6 F7 mewajibkan checklist tercentang sebelum `siap_tayang`,
  tetapi §8 tidak menetapkan tabelnya. Dibuat tabel sendiri agar "siapa mencentang" terekam
  (alasan yang sama dengan `verifikator` menggantikan checkbox di F21).
  Nilai form memakai **ID baris**, bukan teks butir: teks butir memuat `<=` dan `—` yang
  di-escape HTML saat dirender, sehingga POST balik tidak cocok.
- **Alasan tolak ditempel ke revisi terakhir** (`catatan`), **tidak** menaikkan `versi`.
  T5 menulis "disalin ke `catatan_revisi`"; menambah baris versi dengan naskah identik akan
  menggeser arti `versi_terakhir` yang dipakai agent. **Mohon dikonfirmasi @content.**
- **`decode` JSON rusak → 400**, bukan 500 (pernah 500 sebelum diperbaiki; lihat
  `docs/HASIL-VERIFIKASI.md`).
- **Jangan pakai `.nullsfirst()` / `.nullslast()`** di `order_by`. SQLAlchemy menerjemahkannya
  jadi klausa `NULLS FIRST/LAST` yang **tidak dikenal MariaDB** (`pymysql ... 1064`) sehingga
  halaman Boss balas **500** — bug nyata yang ditemukan dari uji hidup MariaDB
  (`docs/HASIL-VERIFIKASI.md` §7a), lolos dari pytest karena SQLite mendukung sintaks itu.
  Di MariaDB/MySQL **dan** SQLite NULL sudah otomatis di depan untuk `ASC` dan di belakang
  untuk `DESC`. Dijaga oleh `tests/test_sql_portabilitas.py`.
- **Uji hidup jalur MariaDB** (termasuk 13 halaman Boss, bukan cuma API):
  `BASE=http://127.0.0.1:8098 .venv/bin/python scripts/e2e-d18-live.py`. Butuh baris `user` Boss
  dan token agent di DB — lihat `docs/HASIL-VERIFIKASI.md` §7b.
- **Uji reels/queue di `smoke-api.sh` dilaporkan SKIP** kalau item belum `terjadwal` — karena
  approve+jadwal adalah keputusan Boss yang tidak punya jalur API. Versi otomatisnya ada di
  pytest (`tests/test_api.py`, `tests/test_review_ui.py`).

---

## Keamanan

- `.env` **tidak** di-commit (`.gitignore`), isinya `chmod 600`. Tidak ada kredensial,
  token, atau sandi di dalam repo — hanya `.env.example` dengan penanda `BELUM_DIISI_...`.
- Kredensial MariaDB jalur produksi disimpan terpisah oleh @hermes di `.env.mariadb`
  (juga ter-ignore) dan **tidak pernah dicetak** oleh skrip mana pun.
- Token agent: yang tersimpan hanya **hash sha256**; plaintext tampil sekali di UI.
  Revoke → request berikutnya 401. `last_used_at` + badge di dashboard.
- Semua form POST UI wajib **CSRF**; cookie sesi `HttpOnly` + `Secure` (produksi) + `SameSite=Lax`.
- Unggahan divalidasi ekstensi **+ MIME + magic bytes** di jalur UI **dan** API.
- `deploy/` sudah memuat: `location /api/ → 404`, `limit_req` pada `/login`,
  `client_max_body_size 20m`, `server_tokens off`, dan format log tanpa header `Authorization`.

## Lisensi & kepemilikan

Kode internal Gamis Jumbo — dibuat untuk Boss Fariez. Repo ini publik sebagai arsip kerja;
tidak ada lisensi terbuka yang diberikan. Content bible editorial **tidak** disertakan di repo
ini (`seed/taxonomy.yaml` hanya memuat turunan nilai yang dibutuhkan kode).
