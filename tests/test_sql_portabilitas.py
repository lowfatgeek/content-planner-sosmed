"""Portabilitas SQL — MariaDB tidak mengenal `NULLS FIRST` / `NULLS LAST`.

Bug nyata yang ditangkap uji e2e hidup di instance MariaDB: dashboard Boss balas
**500** dengan `pymysql.err.ProgrammingError (1064, ... near 'NULLS FIRST')`, karena
SQLAlchemy menerjemahkan `.nullsfirst()` / `.nullslast()` menjadi klausa itu dan
MariaDB (juga MySQL) tidak mendukungnya. Uji pytest berbasis SQLite **tidak** pernah
menangkapnya karena SQLite mendukung sintaks itu.

Aturan: jangan pakai `.nullsfirst()` / `.nullslast()` di kode app. Di MariaDB/MySQL —
dan juga SQLite — NULL sudah otomatis di depan untuk `ASC` dan di belakang untuk
`DESC`, jadi perilakunya sama tanpa modifier tersebut.
"""
from __future__ import annotations

import re
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"

TERLARANG = re.compile(r"nullsfirst|nullslast|NULLS\s+(FIRST|LAST)", re.IGNORECASE)


def test_tidak_ada_modifier_nulls_di_kode_app() -> None:
    temuan: list[str] = []
    for berkas in sorted(APP.rglob("*.py")):
        for nomor, baris in enumerate(berkas.read_text(encoding="utf-8").splitlines(), 1):
            if TERLARANG.search(baris):
                temuan.append(f"{berkas.relative_to(APP.parent)}:{nomor}: {baris.strip()}")
    assert not temuan, (
        "MariaDB error 1064 pada klausa NULLS FIRST/LAST — pakai urutan ASC/DESC biasa:\n  "
        + "\n  ".join(temuan)
    )
