#!/usr/bin/env bash
# dev-reset.sh — reset DB dev (SQLite) + seed, opsional bikin token agent.
#
#   ./scripts/dev-reset.sh              # reset + seed saja
#   ./scripts/dev-reset.sh --with-token # reset + seed + cetak token agent sekali
#
# Kenapa perlu: kunci idempotency di fixture sudah terpakai setelah satu kali
# jalan, jadi smoke-api.sh berikutnya akan melihat 200 (replay) dan bukan 201.
# Reset dulu = uji bersih. HENTIKAN app dulu (Ctrl-C / systemctl stop).

set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"

VENV="$DIR/.venv/bin/python"
[[ -x "$VENV" ]] || { echo "venv belum ada: $VENV" >&2; exit 2; }

DB="./var/gamisjumbo.db"
if [[ -f "$DB" ]]; then
  if command -v fuser >/dev/null && fuser "$DB" >/dev/null 2>&1; then
    echo "PERINGATAN: sepertinya app masih memakai $DB. Hentikan dulu." >&2
  fi
  rm -f "$DB"
  echo "[dev-reset] $DB dihapus"
fi

"$VENV" scripts/bootstrap.py

if [[ "${1:-}" == "--with-token" ]]; then
  "$VENV" - <<'PY'
import os
from app.db import SessionLocal
from app.services import tokens as t
from app import enums

with SessionLocal() as db:
    row, raw = t.buat_token(db, nama="dev-lokal", scopes=list(enums.SCOPES))
    db.commit()
print("\nTOKEN AGENT (tampil sekali, simpan sendiri):\n" + raw + "\n")
PY
fi

echo "[dev-reset] selesai. Jalankan app:"
echo "  .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8099"
