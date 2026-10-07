"""Rencana uji #2 (PRD §17) — Alembic `upgrade head` + `downgrade base` di scratch DB."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

ROOT = Path(__file__).resolve().parent.parent
ALEMBIC = ROOT / ".venv" / "bin" / "alembic"


def _alembic(scratch: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["DATABASE_URL"] = f"sqlite+pysqlite:///{scratch}"
    exe = str(ALEMBIC) if ALEMBIC.exists() else sys.executable
    cmd = [exe, *args] if ALEMBIC.exists() else [sys.executable, "-m", "alembic", *args]
    return subprocess.run(
        cmd, cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=180
    )


def _tabel(scratch: Path) -> set[str]:
    eng = create_engine(f"sqlite+pysqlite:///{scratch}")
    with eng.connect() as conn:
        return set(inspect(conn).get_table_names())


def test_alembic_upgrade_downgrade_bersih(tmp_path):
    scratch = tmp_path / "scratch.db"
    scratch.write_bytes(b"")

    r = _alembic(scratch, "upgrade", "head")
    assert r.returncode == 0, r.stderr

    tabel = _tabel(scratch)
    wajib = {
        "pilar",
        "caption_template",
        "user",
        "api_token",
        "activity_log",
        "idempotency_key",
        "notification_outbox",
        "content_item",
        "content_revision",
        "asset",
        "schedule_slot",
        "metric_snapshot",
        "hook_registry",
        "production_checklist",
        "alembic_version",
    }
    assert wajib.issubset(tabel), wajib - tabel

    r = _alembic(scratch, "downgrade", "base")
    assert r.returncode == 0, r.stderr
    sisa = _tabel(scratch) - {"alembic_version"}
    assert sisa == set(), sisa


def test_alembic_migrasi_idempoten(tmp_path):
    """`upgrade head` dua kali tidak error (dipakai saat deploy ulang)."""
    scratch = tmp_path / "ulang.db"
    scratch.write_bytes(b"")
    assert _alembic(scratch, "upgrade", "head").returncode == 0
    r = _alembic(scratch, "upgrade", "head")
    assert r.returncode == 0, r.stderr
