# Hasil Verifikasi — 7 Oktober 2026 (WIB)

Semua angka di bawah adalah **keluaran nyata** dari perintah yang tertulis, dijalankan di box
yang sama. Tidak ada hasil yang diketik manual atau disimulasikan.

| # | Yang diverifikasi | Perintah | Hasil |
|---|---|---|---|
| 1 | Uji otomatis (unit + integrasi + UI + migrasi) | `.venv/bin/python -m pytest tests/` | **65 passed** (≈15 s) |
| 2 | Smoke API dengan fixture asli `@content` | `TOKEN=<token> bash scripts/smoke-api.sh` | **LULUS=31 GAGAL=0 DILEWATI=1** |
| 3 | Alur hidup lewat UI (login → review → kalender → queue) | `BOSS_PASSWORD=<sandi> .venv/bin/python scripts/live-verify.py --format reels --slot malam` | **LULUS=21 GAGAL=0 DILEWATI=0** |
| 4 | Migrasi Alembic naik & turun di scratch DB | `pytest tests/test_migration.py` | **2 passed** (`upgrade head` → 15 tabel; `downgrade base` → 0 tabel) |
| 5 | Jalur produksi MariaDB (baca) | `bash scripts/verify-mariadb.sh` (dry-run) | schema `content_planner`: **15 tabel**, `alembic_version = e7b764ce4a01`, `utf8mb4_unicode_ci` |
| 6 | App di jalur MariaDB (tulis/baca) | uvicorn port 8098 + `DATABASE_URL` dari `.env.mariadb` | health **200**, taxonomy **5 pilar/14 target**, POST item ber-Arab **201**, teks Arab utuh saat dibaca kembali, `submitted_at` berakhiran **+07:00**, `queue` **200** |

---

## 1. Uji pytest — 65 pemeriksaan

```
$ .venv/bin/python -m pytest tests/
.................................................................        [100%]
65 passed in 15.50s
```

Cakupan per berkas:

| Berkas | Isi |
|---|---|
| `test_transitions.py` | normalisasi hook; transisi tidak sah ditolak; agent ditolak untuk status di luar wilayahnya; agent hanya item miliknya; tolak wajib ≥10 karakter; `ditolak → review`; versioning revisi (2× simpan = 2 versi); level 3/4; satu slot satu konten |
| `test_api.py` | FX-01..FX-06 → 201 status `review`; alur approve→jadwal→`queue?due=`→`/posted`→`tayang`; jalur gagal tetap `terjadwal`; NX-01..NX-04; NX-07 (PATCH item Boss → 403); tanpa token/revoked → 401; scope kurang → 403; field Boss-only ditolak; reels & `include_manual`; TZ slot malam |
| `test_idempotency.py` | NX-05 (201 lalu 200 + body identik); NX-06 (409); `Idempotency-Key` wajib (400); `posted` 2× tetap 200 & notifikasi tidak dobel; `platform_post_id` berbeda → 409; `failed` replay |
| `test_kursor.py` | aksi agent tidak muncul di feed; KV-01 (`reject_reason` + `versi_terakhir`); KV-02 (versi naik, kembali `review`); item approved beku (403); feed hanya memuat aksi Boss |
| `test_warning_kuota_level4.py` | 3 pemicu warning (format sama dua slot · engagement di slot pagi · usulan jadwal bertabrakan) + bukti **nol blokir**; KQ-01 (angka API == hitungan SQL manual); `terpakai_minggu_ini` butuh jadwal; KQ-02 (level 4 butuh `verifikator`) |
| `test_ui.py` | login wajib; password salah; cookie tamper; CSRF wajib; `.php`/`.exe`/magic-bytes salah ditolak; upload sah lolos; path traversal & `/.env` → 404; alur UI lengkap (kanban 8 kolom, 2× edit = 3 versi, jadwal, metrik, share rate) |
| `test_review_ui.py` | **regresi bug nyata**: form checklist /review dulu memakai teks butir yang di-escape HTML dan barisnya tidak di-commit → "Checklist belum lengkap" terus. Kini memakai ID baris + commit. |
| `test_audit_outbox.py` | outbox ditulis tanpa memblokir aksi; `ambil_menunggu` untuk poller; audit mencatat aktor & transisi; checklist wajib; hook seed (30) tidak pernah dilepas, hook konten dilepas saat arsip; soft delete |
| `test_migration.py` | `alembic upgrade head` → 15 tabel; `downgrade base` → bersih; upgrade 2× tanpa error |

## 2. Smoke API — fixture asli dari `@content`

```
$ TOKEN=<token> bash scripts/smoke-api.sh
== 1. Positif: FX-01..FX-06 (uji #5) ==        OK ×12
== 2. Idempotency (uji #7): NX-05 & NX-06 ==   OK ×3
== 3. Negatif: NX-01..NX-04 (uji #6, #16) ==   OK ×4 (422 semua)
== 4. Warning pemicu (iii): FX-05 lalu FX-04 == OK ×2 (201 + warning)
== 5. Queue & reels (uji #17) ==               OK ×1, SKIP ×1
== 6..9 kuota, TZ, JSON rusak → 400, auth ==   OK ×6
LULUS=31  GAGAL=0  DILEWATI=1
```

`SKIP` di bagian 5 disengaja dan jujur: item `reels` masih berstatus `review`, sedangkan
approve+jadwal itu keputusan Boss yang **tidak punya jalur API**. Versi otomatisnya ada di
pytest, dan versi hidupnya diverifikasi di §3 di bawah (setelah item benar-benar dijadwalkan).

## 3. Verifikasi alur hidup (UI + API pada app yang berjalan)

```
$ TOKEN=<token> BOSS_PASSWORD=<sandi> .venv/bin/python scripts/live-verify.py --format reels --slot malam
A. Gerbang login            OK ×3  (tanpa sesi → /login; sandi salah ditolak; login berhasil)
B. Halaman utama            OK ×9  (dashboard, kanban, kalender, bank ide, metrik,
                                    token, hook, outbox, audit)
C. Layar butuh aksi Boss    OK ×3  (8 item review; item #5 disetujui + dijadwalkan malam
                                    dengan 3 butir checklist; halaman item menampilkan status baru)
D. Kalender slot terisi     OK ×1
E. Upload .php ditolak      OK ×1  + PNG sah diterima OK ×1
F. Queue agent (D20)        OK ×3  (reels TIDAK masuk queue?due=; muncul di butuh_boss;
                                    include_manual=1 menampilkannya)
LULUS=21  GAGAL=0  DILEWATI=0
```

## 4. Jalur produksi MariaDB — `content_planner`

Schema + user dibuat oleh `@hermes` (`.env.mariadb`, ter-ignore dari repo; sandi tidak pernah
dicetak). Pembacaan langsung ke DB:

```
tabel (15): activity_log, alembic_version, api_token, asset, caption_template, content_item,
            content_revision, hook_registry, idempotency_key, metric_snapshot,
            notification_outbox, pilar, production_checklist, schedule_slot, user
alembic_version : e7b764ce4a01
collation       : utf8mb4_unicode_ci (schema & kolom content_item.naskah_md)
isi saat ini    : pilar=5, hook_registry=37, content_item=6 (data uji @hermes)
```

App dijalankan pada jalur ini (port 8098, DB MariaDB):

```
healthz 200 | health: {"status":"ok","tz":"Asia/Jakarta","waktu":"2026-10-07T10:46:30+07:00"}
taxonomy: pilar=5 tz=Asia/Jakarta target=14
POST /contents (doa ber-Arab, level 2) → 201
baca kembali: teks Arab utuh = True | status=review | submitted_at=...+07:00
queue?due=2026-12-31T23:59:00+07:00 → 200
```

Artefak uji saya (1 item `request_ref='uji:mariadb'`, 1 token `uji-mariadb`) sudah **dihapus**;
yang tersisa hanya data uji milik @hermes (6 item, 1 token).

## 5. Kendala nyata yang ditemukan & diperbaiki saat verifikasi

| Temuan | Bukti | Perbaikan |
|---|---|---|
| **Body JSON rusak → 500** | `json.decoder.JSONDecodeError ... 500` di log uvicorn saat payload fixture dikirim lewat variabel shell | `_baca_json()` → **400** + pesan; skrip smoke memakai `--data-binary @file` |
| **`?due=...+0700` → 422** | `date +%z` menghasilkan offset tanpa titik dua | skrip memakai `+07:00`; `parse_iso` juga memaklumi spasi hasil `+` yang tidak di-encode |
| **`+` di query string** | parser query mengubah `+07:00` jadi spasi | `parse_iso` menormalkan ` 07:00` → `+07:00` (contoh PRD bekerja apa adanya) |
| **Agent terhalang di T9** | `403 "Agent tidak boleh menyetel status 'tayang'"` saat `/posted` | `tayang` dikeluarkan dari daftar status terlarang agent: jalur resminya `/posted` |
| **Item approved malah 409** | beku diperiksa setelah cek status | `pastikan_tidak_beku` diperiksa lebih dulu → **403** yang informatif |
| **Replay 201** | replay mengembalikan status tersimpan (201) | replay **selalu 200** + `Idempotent-Replay: true` (D23) |
| **Checklist selalu "belum lengkap"** | tombol Setujui balas error walau kotak dicentang | nilai form = **ID baris** (bukan teks butir yang ter-escape HTML) + baris checklist di-commit di transaksi transisi |
| **`siap_tayang → arsip`** | diuji lalu ditolak: tidak ada di tabel §10 | dipertahankan sesuai PRD; membuang item memakai soft delete / jalur `tayang → arsip` |

## 6. Yang BELUM diverifikasi (jujur, bukan diklaim)

| Hal | Kenapa | Cara memverifikasi nanti |
|---|---|---|
| `location /api/ → 404` dari publik | vhost nginx belum diaktifkan (Level 2) | setelah paket deploy: `curl -i https://fp.kelaswfa.my.id/api/v1/health` → 404, dan `curl -H 'Authorization: Bearer ...' http://127.0.0.1:8099/api/v1/health` → 200 (uji #15) |
| systemd unit (`MemoryMax`, restart) | belum diaktifkan | `systemctl enable --now gamisjumbo` → uji #11 (puncak RSS 24 jam) & #13 (restart, sesi login tetap) |
| Uji restore dari dump | butuh scratch DB (Level 3) | `scripts/restore-test.sh` setelah paket backup |
| Pemakaian RAM ≥24 jam | uji #11 butuh waktu | pantau RSS setelah deploy |
| Redaksi header `Authorization` di access log | butuh vhost hidup | `grep -c 'Bearer' /var/log/nginx/fp.kelaswfa.my.id.access.log` → 0 |
| `platform_post_id` dobel dari Facebook nyata | butuh posting sungguhan | jalur agent saat operasi pertama |

---

## 7. Verifikasi hidup di jalur MariaDB lewat UI Boss (7 Okt 2026, ~12:0x WIB)

Yang **tidak** bisa ditangkap pytest berbasis SQLite: halaman Boss di MariaDB. Skrip
`scripts/e2e-d18-live.py` menjalankan seluruh rantai lewat HTTP ke instance yang benar-benar
berjalan di atas MariaDB (`127.0.0.1:8098`, `DATABASE_URL` dari `.env.mariadb`):

```
$ BASE=http://127.0.0.1:8098 .venv/bin/python scripts/e2e-d18-live.py
A.  Login Boss sungguhan                          OK ×3   (POST /login 303; dashboard 200; menunggu=0 == outbox di DB)
A2. Sapuan 13 halaman Boss                        OK ×1   (13/13 balas 200 di MariaDB)
B.  Agent submit draft (token baru)               OK ×6   (taxonomy 200; POST 201 review; replay 200 + Idempotent-Replay;
                                                          baris outbox kind=draft_agent_masuk_review status=kirim)
C.  Dashboard & halaman outbox                    OK ×4   (menunggu=1; tautan #item; badge baris #13 = 'menunggu')
D.  Klaim atomik (SQL docs/POLLER-OUTBOX.md)       OK ×4   (klaim 1 rowcount=1; klaim 2 rowcount=0;
                                                          baris 'proses' tetap dihitung menunggu; badge 'proses (diklaim poller)')
E.  Tutup klaim                                   OK ×4   (claim_token benar rowcount=1; token salah rowcount=0;
                                                          dashboard menunggu=0; badge 'terkirim')
F.  Bersih-bersih                                 OK ×2   (outbox kembali 0 baris; content_item kembali 6 baris)
LULUS=24 GAGAL=0
```

### 7a. Bug nyata yang ditemukan (UI Boss mustahil dibuka di MariaDB)

| Temuan | Bukti | Perbaikan |
|---|---|---|
| **`GET /` → 500 di MariaDB** | log uvicorn: `pymysql.err.ProgrammingError (1064, "... near 'NULLS FIRST' at line 3")`; SQL `... ORDER BY content_item.submitted_at ASC NULLS FIRST` | `.nullsfirst()`/`.nullslast()` dibuang dari 5 tempat (`app/routers/ui.py` ×2, `app/routers/ui_content.py` ×3). Di MariaDB/MySQL **dan** SQLite NULL sudah otomatis di depan untuk `ASC`, di belakang untuk `DESC` — diukur langsung: keduanya `ASC → [NULL,1,2]`, `DESC → [2,1,NULL]`. Dijaga `tests/test_sql_portabilitas.py` (gagal kalau pola itu muncul lagi di `app/`). |

Lolos dari semua uji sebelumnya karena pytest memakai SQLite, yang **mendukung** `NULLS FIRST`.
Verifikasi MariaDB sebelumnya hanya menyentuh `healthz`, `taxonomy`, `POST /contents`, `queue` —
tidak satu pun halaman HTML. Karena itu sapuan 13 halaman (A2) sekarang jadi bagian tetap skrip ini.

### 7b. Dua penghalang e2e yang dibuka (state dev, bukan produksi)

| Hal | Keadaan sebelumnya | Sekarang |
|---|---|---|
| Baris `user` Boss di MariaDB | **0 baris** → login mustahil (verifikasi ke hash baris `user`) | dibuat 1 baris `boss` memakai `BOSS_PASSWORD_HASH` dari `.env` (hash-nya diverifikasi cocok dengan sandi dev; sandi tidak pernah dicetak/store) |
| Token agent untuk probe | token di `var/` sudah basi → `401` | token baru `api_token.id=3` `agent-dev-8099` (scope `content:read,content:write,asset:write`), plaintext di `var/.token-agent` mode 600 |

