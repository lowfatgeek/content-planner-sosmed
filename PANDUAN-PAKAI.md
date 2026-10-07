# Panduan Pakai — Gamis Jumbo CMS (Content Planner Sosmed)

Dokumen ini untuk **Boss** (pemakai UI) dan **agent `@content`** (pemakai API).
Ringkasan teknis ada di [`README.md`](README.md).

---

## 1. Menyalakan app (dev lokal)

```bash
cd /path/ke/content-planner-sosmed
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8099
```

- Buka <http://127.0.0.1:8099> → halaman login.
- Liveness tanpa token: `curl http://127.0.0.1:8099/healthz` → `{"status":"ok","db":"sqlite"}`.
- App hanya mendengarkan **127.0.0.1** (loopback). Publik hanya lewat nginx (belum diaktifkan).
- Ganti sandi Boss: `.venv/bin/python scripts/set-password.py` (hash masuk ke `.env`, chmod 600).
- Mulai dari nol (dev): `bash scripts/dev-reset.sh` — hapus DB dev, seed ulang, opsional cetak token.

---

## 2. Alur harian Boss (UI)

### 2.1 Menangkap ide
Menu **+ Konten** (`/contents/new`): isi hook, pilar, format, naskah, CTA, `fact_level`.
- `fact_level` **3** (dalil populer) dan **4** (hukum/fiqih) **wajib** `sumber_dalil`.
- `fact_level` **4** juga wajib `verifikator` (format bebas, tapi harus bisa diaudit:
  `ustadz X — youtu.be/...`) sebelum boleh **Siap Tayang**.
- Item Boss masuk sebagai **Ide**; Anda boleh memindahkan `draft → siap_tayang` langsung
  (approval implisit, tetap tercatat di audit log).

### 2.2 Review draft agent — layar **Butuh aksi Boss**
Semua item `review`, urut `submitted_at`. Tiap kartu menampilkan:
- naskah penuh, pilar/format/CTA/fact_level, `usulan_jadwal` agent (hanya saran),
- `warning[]` (3 pemicu) dan **hook mirip** (anti-duplikasi), riwayat revisi.

Dua tindakan:
- **Setujui** → centang **checklist produksi** (butir per format, lihat §11 PRD), isi
  `Jadwal tayang` + `Slot` (boleh dikosongkan dulu) → item jadi **Siap Tayang**
  (langsung **Terjadwal** kalau jadwal diisi).
- **Tolak** → alasan **wajib ≥10 karakter**. Item jadi **Ditolak**; agent bisa revisi
  (`POST`/`PATCH`) → masuk `review` lagi.

### 2.3 Kalender & slot
`/calendar` — 1 slot = **1 konten**. `pagi` (06:00) untuk doa/pengingat pendek;
`malam` (20:00) untuk cerita/quote/question-humor. Jadwal bentrok **ditolak** (slot sudah terisi).
Panel kanan: **slot kosong H+1/H+2** + kuota pilar minggu ini.

### 2.4 Kanban
`/kanban` — 8 kolom: Ide · Draft · Review · Ditolak · Siap Tayang · Terjadwal · Tayang · Arsip.
Klik kartu untuk membuka item; perpindahan status lewat halaman detail (`Ubah status`) dan
**divalidasi aturan §10** (transisi tidak sah ditolak dengan pesan, bukan diam-diam gagal).

### 2.5 Setelah tayang
- Agent melaporkan `POST /api/v1/contents/{id}/posted` → status **Tayang** + `link_posting`.
- Kalau gagal: `POST .../failed` → item tetap **Terjadwal** dengan `last_error` + badge merah.
- `reels`/`fb_reels` **tidak** masuk queue agent: Boss posting manual, agent lalu menandai `posted`.
- H+1 isi metrik di `/metrics` (reach/like/komen/share). Satu snapshot per konten per hari.
  Dashboard langsung menghitung **share rate**, **ER**, **ketepatan jadwal**, **median waktu review**.

### 2.6 Token agent
`/settings/tokens` → **Buat token** (pilih scope) → **plaintext tampil SEKALI**, salin lalu simpan.
Yang tersimpan hanya hash sha256. **Revoke** → token langsung mati (401).

### 2.7 Outbox notifikasi
App **hanya menulis** baris di `/settings/outbox`. Yang mengirim ke Telegram adalah
**poller @hermes** (bot profil `default`). Kalau ada baris `gagal`, lihat `last_error`;
tombol "tandai terkirim" tersedia untuk jalur manual. Notifikasi **tidak pernah** memblokir
approve/reject.

### 2.8 Audit log
`/settings/activity` — semua transisi & edit dengan aktor (`boss`/`agent #id`), `from → to`.
Sequence `id` di tabel ini juga **kursor** yang dipakai agent (`?cursor=`), dan feed-nya
**mengecualikan aksi agent** supaya agent tidak memproses ulang karyanya sendiri.

---

## 3. Alur agent `@content` (API)

```bash
BASE=http://127.0.0.1:8099        # loopback; WAJIB dari dalam box
TOKEN=<token dari /settings/tokens>
H=(-H "Authorization: Bearer $TOKEN")
```

### 3.1 Lihat aturan main (jangan hardcode enum)
```bash
curl -sS "${H[@]}" $BASE/api/v1/taxonomy | jq .
# → pilar (+ 3 angka kuota per pilar), format, kanal, cta, jam_slot, peran_slot, tz,
#   fact_level, batas (upload 20 MB, ttl idempoten 24 jam), warning_pemicu
```

Tiga angka kuota per pilar:
- `terpakai_minggu_ini` — sudah punya slot minggu ini (dibandingkan `kuota_mingguan`, target 14)
- `pipeline_aktif` — semua yang sedang jalan (`review`→`tayang`), tanpa batas minggu
- `belum_dijadwalkan` — `pipeline_aktif` yang belum punya slot

### 3.2 Cek kerjaan balik dari Boss
```bash
curl -sS "${H[@]}" "$BASE/api/v1/contents?cursor=0&limit=100" | jq .
# baris item sendiri memuat: status, reject_reason, versi_terakhir
# simpan `cursor` dari respons, pakai di panggilan berikutnya (jangan pakai timestamp)
```

### 3.3 Kirim draft
```bash
cat > body.json <<'JSON'
{
  "judul_hook": "Kalau hari ini berat, baca ini dulu",
  "pilar": "pengingat",
  "format": "quote",
  "kanal": "fb_feed",
  "naskah_md": "...",
  "caption_fb": "...",
  "cta": "share",
  "hashtags": "#GamisJumbo #Pengingat",
  "fact_level": 2,
  "sumber_dalil": "QS. Al-Baqarah: 201",
  "usulan_jadwal": "2026-10-09T20:00:00+07:00",
  "request_ref": "telegram:msg/18422"
}
JSON

curl -sS -X POST $BASE/api/v1/contents "${H[@]}" \
  -H 'Content-Type: application/json' -H "Idempotency-Key: $(uuidgen)" \
  --data-binary @body.json | jq .
```
- **201** = dibuat, status `review`. `source` otomatis: `agent_brief` kalau `request_ref` diisi,
  selain itu `agent_auto`.
- Retry aman: kirim ulang **key + body yang sama** → **200** + body identik +
  header `Idempotent-Replay: true`. Key sama dengan body **berbeda** → **409**.
- `fact_level` **4** → **422** (diparkir di MVP). Level 3 tanpa `sumber_dalil` → **422**.
- Pilar/format di luar taxonomy → **422** (tidak pernah dibuat otomatis).
- `warning[]` di respons: format sama di dua slot sehari · `question`/`meme_relatable` di slot
  `pagi` · usulan jadwal bertabrakan. **Tidak memblokir** — lanjutkan saja, atau geser usulan.

### 3.4 Revisi setelah ditolak
```bash
curl -sS -X PATCH $BASE/api/v1/contents/<id> "${H[@]}" \
  -H 'Content-Type: application/json' -H "Idempotency-Key: $(uuidgen)" \
  --data-binary @revisi.json | jq .
```
Hanya item milik token, hanya saat `draft`/`review`/`ditolak`. Setiap PATCH menambah **versi baru**
(append-only) dan mengembalikan status ke `review`. Item yang **sudah disetujui** beku → **403**.

### 3.5 Ambil queue & lapor hasil
```bash
DUE=$(date -d '+30 min' +%Y-%m-%dT%H:%M:00+07:00)
curl -sS "${H[@]}" "$BASE/api/v1/queue?due=$DUE" | jq .
# data[] = siap diposting (naskah_md, caption_fb, cta, hashtags, jadwal_datetime)
# butuh_boss = reels yang jadwalnya lewat → posting manual Boss

curl -sS -X POST $BASE/api/v1/contents/<id>/posted "${H[@]}" \
  -H 'Content-Type: application/json' -H "Idempotency-Key: $(uuidgen)" \
  -d '{"link_posting":"https://facebook.com/...","platform_post_id":"123"}' | jq .

curl -sS -X POST $BASE/api/v1/contents/<id>/failed "${H[@]}" \
  -H 'Content-Type: application/json' -H "Idempotency-Key: $(uuidgen)" \
  -d '{"reason":"Facebook menolak: media tidak lengkap"}' | jq .
```
`posted` dan `failed` **idempoten** — replay tetap 200, tidak menggandakan status atau notifikasi.

### 3.6 Unggah aset
```bash
curl -sS -X POST $BASE/api/v1/contents/<id>/assets "${H[@]}" \
  -H "Idempotency-Key: $(uuidgen)" -F "file=@banner.png;type=image/png" -F "alt=Banner" | jq .
```
Hanya gambar (jpg/jpeg/png/webp/gif), maks **20 MB**, divalidasi ekstensi + MIME + magic bytes.
Video hanya berupa **URL** (tulis di `visual_note`).

---

## 4. Checklist produksi per format (dicentang saat approve)

| Format | Butir |
|---|---|
| `quote` | hook ≤15 kata · font dari `fonts/` · watermark `facebook.com/gamisjumboid` kanan-bawah · kontras aman di HP · sumber bila mengutip ulama |
| `mini_story` | maks 3 slide · slide 1 masalah, 2 twist, 3 hikmah · 1 CTA komentar |
| `doa` | Arab + latin + arti lengkap · sumber dalil wajib · render `render_doa.py` |
| `meme_relatable` | 1 masalah keseharian ibu-ibu · tidak menyinggung fisik/mazhab/kelompok · punchline + pengingat lembut |
| `question` | 1 pertanyaan pancingan · 2–3 opsi jawaban · CTA komentar wajib |
| `carousel` | 3–5 slide · slide 1 hook, terakhir CTA · 1 ide per slide |
| `reels` | link referensi saja · 15–45 detik (ideal 30–45) · hook 3 detik pertama tertulis |
| `text` | ≤80 kata · paragraf pendek · 1 CTA |

Item **tidak bisa** `siap_tayang` sebelum semua butir tercentang.

---

## 5. Verifikasi & pemeliharaan

```bash
.venv/bin/python -m pytest tests/                    # 65 uji
TOKEN=<token> bash scripts/smoke-api.sh              # smoke /api/v1 pakai fixture asli @content
BOSS_PASSWORD=<sandi> .venv/bin/python scripts/live-verify.py --format reels --slot malam
bash scripts/verify-mariadb.sh                       # DRY-RUN jalur produksi (read-only + pratinjau DDL)
bash scripts/backup-db.sh                            # dump + retensi (retensi default 14 hari)
bash scripts/restore-test.sh var/backups/<arsip>.tar.gz   # uji restore ke scratch
```

Catatan: `smoke-api.sh` memakai kunci idempotency yang sama dari fixture, jadi jalankan pada DB
bersih untuk melihat `201` (setelah satu kali jalan, panggilan berikutnya wajar membalas `200`).
`bash scripts/dev-reset.sh` menyiapkan DB dev bersih.

---

## 6. Kalau ada masalah

| Gejala | Sebab & tindakan |
|---|---|
| `401` di semua endpoint API | token salah/revoked, atau tidak dikirim sebagai `Authorization: Bearer ...` |
| `403` saat PATCH | item itu milik Boss, atau sudah disetujui (beku) |
| `409` saat POST | `Idempotency-Key` sudah dipakai untuk body yang berbeda — pakai key baru |
| `422` dengan `sumber_dalil` | `fact_level` 3–4 wajib sumber |
| `422` "diparkir di MVP" | agent mencoba `fact_level=4` |
| `400` "Body bukan JSON yang valid" | payload terpotong/escape rusak — kirim JSON utuh (mis. `--data-binary @file`) |
| Queue kosong padahal ada `terjadwal` | jam slot belum lewat (`due`), atau formatnya `reels` (lihat `butuh_boss`) |
| Tombol Setujui balas "Checklist belum lengkap" | centang semua butir di kartu review |
| Approve/reject lambat | cek `/settings/outbox` — baris `gagal` tidak memblokir aksi, hanya butuh perhatian poller |
| Dashboard "token terakhir dipakai: belum pernah" | token agent belum dipakai, atau poller belum jalan |
| Item `terjadwal` lewat jadwal >60 menit | badge merah di dashboard — jalur agent tersendat, cek poller @hermes |
