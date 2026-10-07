#!/usr/bin/env bash
# verify-mariadb.sh — periksa jalur produksi (MariaDB) untuk schema app.
#
#   ./scripts/verify-mariadb.sh              # DRY-RUN (default): read-only + pratinjau DDL
#   ./scripts/verify-mariadb.sh --apply      # jalankan alembic upgrade + seed (Level 2)
#
# Mengapa dipisah: membuat tabel di MariaDB bersama = perubahan produksi (PRD §16
# Level 2). Defaultnya karena itu read-only: ia hanya membaca keadaan schema dan
# mencetak DDL yang AKAN dijalankan (`alembic upgrade head --sql`), sebagai bahan
# persetujuan. `--apply` dijalankan hanya setelah Boss menyetujui.
#
# Yang TIDAK pernah dilakukan skrip ini: ALTER/CREATE/DROP ke schema lain, reload
# nginx, sentuh systemd. Kredensial tidak pernah dicetak.

set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"

MODE="dry-run"
case "${1:-}" in
  --apply) MODE="apply" ;;
  --dry-run|"") MODE="dry-run" ;;
  *) echo "pakai: $0 [--dry-run|--apply]" >&2; exit 2;;
esac

VENV="$DIR/.venv/bin/python"
ALEMBIC="$DIR/.venv/bin/alembic"
[[ -x "$VENV" ]] || { echo "venv belum ada" >&2; exit 2; }

if [[ -f .env.mariadb ]]; then
  set -a
  # shellcheck disable=SC1090
  . <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' .env.mariadb | sed 's/\r$//')
  set +a
  echo "[verify] memakai .env.mariadb"
fi

[[ -n "${DATABASE_URL:-}" ]] || { echo "DATABASE_URL tidak ada (dan .env.mariadb tidak ada)" >&2; exit 2; }
case "$DATABASE_URL" in
  mysql*) ;;
  *) echo "DATABASE_URL bukan MariaDB/MySQL — skrip ini untuk jalur produksi." >&2; exit 2;;
esac

creds="${DATABASE_URL#*://}"; creds="${creds%%\?*}"
userpass="${creds%%@*}"; hostpart="${creds#*@}"
DBUSER="${userpass%%:*}"; DBPASS="${userpass#*:}"
hostport="${hostpart%%/*}"; DBNAME="${hostpart#*/}"
DBHOST="${hostport%%:*}"; DBPORT="${hostport##*:}"
[[ "$DBPORT" == "$DBHOST" ]] && DBPORT=3306

echo "[verify] mode=$MODE host=$DBHOST port=$DBPORT schema=$DBNAME user=$DBUSER (sandi disembunyikan)"

q() { MYSQL_PWD="$DBPASS" mysql --default-character-set=utf8mb4 -h "$DBHOST" -P "$DBPORT" -u "$DBUSER" -N -B -e "$1" "$DBNAME"; }

echo "== 1. Keadaan schema sekarang (read-only) =="
q "SELECT table_name, table_collation FROM information_schema.tables WHERE table_schema='$DBNAME' ORDER BY table_name;" | sed 's/^/    /'
printf '    jumlah tabel: '; q "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='$DBNAME';"
printf '    charset schema: '; q "SELECT CONCAT(default_character_set_name,' / ',default_collation_name) FROM information_schema.schemata WHERE schema_name='$DBNAME';"
printf '    sesi: '; q "SELECT CONCAT('@@session.time_zone=',@@session.time_zone,' NOW()=',NOW());"

echo "== 2. Pratinjau DDL (alembic upgrade head --sql, TIDAK dieksekusi) =="
PREVIEW="$DIR/var/preview-mariadb.sql"
mkdir -p "$DIR/var"
"$ALEMBIC" upgrade head --sql > "$PREVIEW" 2> >(sed 's/^/  /' >&2) || true
echo "  DDL ditulis ke: $PREVIEW ($(wc -l < "$PREVIEW") baris)"
grep -c 'CREATE TABLE' "$PREVIEW" | sed 's/^/  CREATE TABLE: /'
grep -E 'CREATE (UNIQUE )?INDEX|CREATE FULLTEXT INDEX' "$PREVIEW" | sed 's/^/    /' | head -12

echo "== 3. Skema lain di server ini (bukti tidak tersentuh) =="
q "SELECT table_schema, COUNT(*) FROM information_schema.tables GROUP BY table_schema ORDER BY table_schema;" | sed 's/^/    /'

if [[ "$MODE" == "dry-run" ]]; then
  echo
  echo "[verify] DRY-RUN selesai — belum ada perubahan apa pun di MariaDB."
  echo "[verify] Untuk menerapkan (Level 2, butuh persetujuan Boss):"
  echo "  ./scripts/verify-mariadb.sh --apply"
  echo
  echo "[verify] Isi penuh pratinjau: $PREVIEW"
  exit 0
fi

echo "== 4. Terapkan: alembic upgrade head =="
"$ALEMBIC" upgrade head 2>&1 | sed 's/^/  /'

echo "== 5. Keadaan sesudah migrasi =="
q "SELECT table_name FROM information_schema.tables WHERE table_schema='$DBNAME' ORDER BY table_name;" | sed 's/^/    /'

echo "== 6. Seed taxonomy + hook + akun Boss (idempoten) =="
"$VENV" scripts/bootstrap.py 2>&1 | sed 's/^/  /'

echo "== 7. Uji tulis/baca teks Arab (utf8mb4) =="
q "SELECT CONCAT('collation kolom naskah_md: ', character_set_name,'/',collation_name) FROM information_schema.columns WHERE table_schema='$DBNAME' AND table_name='content_item' AND column_name='naskah_md';" | sed 's/^/    /'

echo
echo "[verify] APPLY selesai. Jalankan app di jalur ini:"
echo "  set -a; . ./.env.mariadb; set +a"
echo "  .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8098"
