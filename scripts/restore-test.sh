#!/usr/bin/env bash
# restore-test.sh — uji restore SEKALI ke scratch, tanpa menyentuh DB produksi.
#
#   ./scripts/restore-test.sh var/backups/<arsip>.tar.gz
#
# Level 3 (meminta izin terpisah) kalau dijalankan pada box produksi, karena
# membuat/menghapus scratch DB. Default di sini: SQLite scratch di /tmp — aman.

set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
ARSIP="${1:?pakai: ./scripts/restore-test.sh <arsip.tar.gz>}"
SCRATCH="$(mktemp -d)"
trap 'rm -rf "$SCRATCH"' EXIT

echo "== 1. Verifikasi checksum =="
if [[ -f "$ARSIP.sha256" ]]; then
  (cd "$(dirname "$ARSIP")" && sha256sum -c "$(basename "$ARSIP.sha256")")
else
  echo "  (tidak ada .sha256, dilewati)"
fi

echo "== 2. Daftar isi arsip =="
tar -tzf "$ARSIP" | head -20
echo "  total entri: $(tar -tzf "$ARSIP" | wc -l)"

echo "== 3. Ekstrak ke scratch =="
tar -xzf "$ARSIP" -C "$SCRATCH"
ls -la "$SCRATCH" | head

echo "== 4. Muat dump =="
if [[ -f "$SCRATCH/db.sql.gz" ]]; then
  gunzip -c "$SCRATCH/db.sql.gz" > "$SCRATCH/db.sql"
fi
if [[ -f "$SCRATCH/db.sql" ]]; then
  if command -v mysql >/dev/null && [[ -n "${SCRATCH_DB_URL:-}" ]]; then
    echo "  mode MariaDB scratch: $SCRATCH_DB_URL"
    mysql --default-character-set=utf8mb4 "$@" < "$SCRATCH/db.sql"
  else
    echo "  mode SQLite scratch (default aman)"
    sqlite3 "$SCRATCH/restore.db" < "$SCRATCH/db.sql" 2>/dev/null || true
  fi
fi
if [[ -f "$SCRATCH/db.sqlite" ]]; then
  cp "$SCRATCH/db.sqlite" "$SCRATCH/restore.db"
fi

echo "== 5. Hitung baris per tabel =="
if [[ -f "$SCRATCH/restore.db" ]]; then
  for t in pilar content_item content_revision api_token activity_log notification_outbox schedule_slot; do
    n=$(sqlite3 "$SCRATCH/restore.db" "SELECT COUNT(*) FROM $t;" 2>/dev/null || echo "n/a")
    printf '  %-24s %s\n' "$t" "$n"
  done
else
  echo "  (tidak ada DB dalam arsip — lewati)"
fi

echo "== 6. Uploads =="
[[ -d "$SCRATCH/var/uploads" ]] && echo "  $(ls -1 "$SCRATCH/var/uploads" | wc -l) berkas" \
                                || echo "  (tidak ada direktori uploads)"

echo
echo "Uji restore selesai. Bandingkan angka di atas dengan DB produksi sebelum menyatakan restore sah."
