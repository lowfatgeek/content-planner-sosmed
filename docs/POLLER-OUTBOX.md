# Outbox → Telegram: protokol klaim untuk poller

Kontrak antara **app** (penulis baris) dan **poller @hermes** (pengirim). App tidak
pernah memegang token Telegram dan tidak pernah mengirim; poller tidak pernah
membuat baris. Detail D14/D18 di PRD.

## Mesin status

```
kirim ──klaim──> proses ──sukses──> terkirim   (sent_at diisi)
                    │
                    ├──gagal sementara (attempts < 3)──> kirim   (dicoba lagi nanti)
                    └──gagal permanen / attempts >= 3──> gagal   (kelihatan di UI)
proses yang claimnya basi > 10 menit  ──> kirim  (attempts < 3) atau gagal
```

`status` = `VARCHAR(20)`; nilai baku ada di `app/enums.py`
(`OUTBOX_KIRIM/PROSES/TERKIRIM/GAGAL`). Menambah nilai baru **tidak** butuh ALTER tipe.

Kolom klaim (migrasi `3b7d1c40f9a2`):

| kolom | arti |
|---|---|
| `claim_token` | token acak pengklaim; satu-satunya bukti "baris ini milik saya" |
| `claimed_at` | waktu klaim; dasar penyelamatan klaim basi |
| `attempts` | naik **saat klaim** (bukan saat kirim) supaya percobaan terbatas |

## Kenapa tidak `SELECT ... WHERE status='kirim'` lalu kirim?

Dua poller (atau poller yang di-restart cepat) bisa mengambil baris yang sama dan
mengirim **dua notifikasi** ke Telegram. Karena itu klaim harus satu UPDATE
bersyarat, dan hanya `rowcount = 1` yang boleh mengirim:

```sql
-- 1. kandidat (tanpa lock, boleh kalah balapan)
SELECT id FROM notification_outbox WHERE status = 'kirim' ORDER BY id LIMIT 1;

-- 2. KLAIM ATOMIK — inilah satu-satunya langkah penentu
UPDATE notification_outbox
   SET status = 'proses',
       claim_token = :token,          -- acak, unik per klaim
       claimed_at  = NOW(),
       attempts    = attempts + 1
 WHERE id = :id AND status = 'kirim';
-- rowcount = 0  -> kalah balapan, ulangi dari langkah 1 (jangan kirim!)
-- rowcount = 1  -> baris ini milik kita

-- 3. ambil isi baris yang benar-benar kita klaim
SELECT id, kind, content_id, payload_json, attempts
  FROM notification_outbox WHERE claim_token = :token;
```

> Catatan portabilitas: jangan menulis `UPDATE ... WHERE id = (SELECT id FROM
> notification_outbox ...)` — MySQL/MariaDB error 1093 (subquery ke tabel yang
> sedang di-update). Pola pilih-lalu-klaim di atas aman di MariaDB **dan** SQLite,
> dan dipakai oleh `notify.klaim()` di app.

Setiap UPDATE penutup **wajib** memakai `claim_token` sebagai syarat kedua, supaya
baris yang sudah diselamatkan proses lain (klaim basi) tidak tertimpa:

```sql
-- 4a. sukses
UPDATE notification_outbox
   SET status='terkirim', sent_at=NOW(), claim_token=NULL, claimed_at=NULL, last_error=NULL
 WHERE id=:id AND claim_token=:token;

-- 4b. gagal sementara (masih ada attempts) -> kembali ke antrean
UPDATE notification_outbox
   SET status='kirim', claim_token=NULL, claimed_at=NULL, last_error=:err
 WHERE id=:id AND claim_token=:token;

-- 4c. gagal permanen (attempts habis / error yang tidak bisa diulang)
UPDATE notification_outbox
   SET status='gagal', claim_token=NULL, claimed_at=NULL, last_error=:err
 WHERE id=:id AND claim_token=:token;
```

## Jalankan di awal setiap siklus: selamatkan klaim basi

Poller yang mati setelah klaim (mis. `SIGKILL`, reboot) meninggalkan baris `proses`.
Tanpa langkah ini baris itu tidak akan pernah terkirim:

```sql
UPDATE notification_outbox
   SET status = IF(attempts >= 3, 'gagal', 'kirim'),
       claim_token = NULL,
       claimed_at  = NULL,
       last_error  = IF(attempts >= 3, 'klaim basi & attempts habis', last_error)
 WHERE status = 'proses' AND claimed_at < NOW() - INTERVAL 10 MINUTE;
```

Ambang 10 menit sengaja lebih besar daripada waktu kirim normal supaya poller yang
masih hidup tidak kehilangan barisnya. Pengiriman Telegram **tidak idempoten**:
kalau poller mati tepat setelah Telegram menerima pesan tapi sebelum UPDATE 4a,
baris akan dikembalikan ke `kirim` dan terkirim **dua kali** — karena itu `attempts`
dibatasi 3, bukan diulang tanpa batas. Duplikat langka jauh lebih ringan daripada
notifikasi hilang; kalau perlu nol duplikat, simpan `message_id` Telegram ke
`payload_json` dan periksa sebelum kirim.

## Pre-flight sebelum poller di-arm

1. **Purge baris uji dulu.** Pernah kejadian: 9 baris `kirim` sisa probe ikut siap
   terkirim ke Telegram Boss. Sebelum menyalakan poller:
   `SELECT COUNT(*) FROM notification_outbox WHERE status IN ('kirim','proses');`
   harus **0** (atau isinya memang notifikasi nyata). Backup dulu, baru hapus.
2. Instance API harus MariaDB: `GET /healthz` → `{"db":"mysql"}`. Poller sebaiknya
   **menolak jalan** kalau bukan `mysql` (skenario SQLite-diam-diam pernah terjadi).
3. Lockfile tunggal (satu poller per box), dan user DB poller hanya butuh
   `SELECT, UPDATE` pada `notification_outbox` — bukan kredensial app.

## Isi pesan

`payload_json` memuat kunci `pesan` (siap kirim) plus detail mentah
(`judul_hook`, `jadwal_tayang`, `link_posting`, dst). `kind` ∈
`draft_agent_masuk_review`, `disetujui`, `ditolak`, `tayang`, `gagal_posting`,
`item_lewat_jadwal`.

## Helper app-side

`app/services/notify.py` menyediakan `klaim()`, `tandai_terkirim()`,
`tandai_gagal()`, `lepas_klaim()`, `pulihkan_klaim_basi()`, dan `hitung_status()`
(dipakai UI/dashboard dan uji). `ambil_menunggu()` hanya untuk tampilan/uji —
jangan dipakai poller untuk mengirim.
