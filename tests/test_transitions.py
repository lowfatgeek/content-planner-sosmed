"""Rencana uji #1 (PRD §17): normalisasi hook, aturan transisi, versioning revisi."""
from __future__ import annotations

from datetime import date

import pytest

from app import enums
from app.errors import Forbidden, TransitionError, ValidationError
from app.services import content as content_service
from app.services import hooks
from app.services.transitions import Aktor, transisi_legal_dari, validasi
from tests.helpers import lengkapi_checklist

AGENT = Aktor(enums.ACTOR_AGENT, 1)
BOSS = Aktor(enums.ACTOR_BOSS)


# ---------------------------------------------------------------- hook
def test_normalisasi_hook_ambil_tiga_kata():
    assert hooks.normalisasi_hook("Kalau hari ini berat, baca ini dulu") == "kalau hari ini"
    assert hooks.normalisasi_hook("  DOA,  pagi!! yang sering kita lewatkan ") == "doa pagi yang"
    assert hooks.normalisasi_hook("Satu") == "satu"
    assert hooks.normalisasi_hook("") == ""


def test_normalisasi_hook_stabil_untuk_unicode_arab():
    # Teks Arab tidak bikin exception dan tetap menghasilkan sesuatu yang deterministik
    a = hooks.normalisasi_hook("رَبَّنَا آتِنَا فِي الدُّنْيَا")
    b = hooks.normalisasi_hook("رَبَّنَا آتِنَا فِي الدُّنْيَا")
    assert a == b


# ---------------------------------------------------------------- transisi
def test_transisi_tidak_sah_ditolak(db, boss_item):
    with pytest.raises(TransitionError):
        validasi(db, boss_item, enums.STATUS_TAYANG, BOSS)  # ide → tayang
    with pytest.raises(TransitionError):
        validasi(db, boss_item, enums.STATUS_TERJADWAL, BOSS)  # ide → terjadwal


def test_agent_tidak_boleh_menyetel_status_boss_only(db):
    """Agent selalu 403 untuk status yang bukan wilayahnya (di luar T9 /posted)."""
    from app.services import tokens as token_service

    tok, _raw = token_service.buat_token(db, nama="uji-boss-only", scopes=[enums.SCOPE_WRITE])
    db.flush()
    item = content_service.buat_dari_boss(
        db,
        {
            "judul_hook": "Item uji wilayah status",
            "pilar": "pengingat",
            "format": "quote",
            "naskah_md": "isi",
            "cta": "share",
            "fact_level": 1,
        },
    )
    db.flush()
    item.owner_token_id = tok.id
    content_service.pindah_status_boss(db, item, enums.STATUS_DRAFT)
    content_service.pindah_status_boss(db, item, enums.STATUS_REVIEW)
    db.flush()

    agent = Aktor(enums.ACTOR_AGENT, tok.id)
    for status in (
        enums.STATUS_SIAP_TAYANG,
        enums.STATUS_TERJADWAL,
        enums.STATUS_ARSIP,
        enums.STATUS_DITOLAK,
    ):
        with pytest.raises(Forbidden):
            validasi(db, item, status, agent)


def test_agent_only_untuk_item_miliknya(db, boss_item):
    agent = Aktor(enums.ACTOR_AGENT, 999)
    with pytest.raises(Forbidden):
        validasi(db, boss_item, enums.STATUS_REVIEW, agent)


def test_transisi_legal_dari_memuat_revisi_agent():
    assert enums.STATUS_REVIEW in transisi_legal_dari(enums.STATUS_DITOLAK)
    assert enums.STATUS_ARSIP in transisi_legal_dari(enums.STATUS_TAYANG)


def test_tolak_wajib_alasan_minimal_10_karakter(db):
    item = content_service.buat_dari_boss(
        db,
        {
            "judul_hook": "Item untuk uji tolak",
            "pilar": "pengingat",
            "format": "quote",
            "naskah_md": "isi",
            "cta": "share",
            "fact_level": 1,
        },
    )
    db.flush()
    content_service.pindah_status_boss(db, item, enums.STATUS_DRAFT)
    content_service.pindah_status_boss(db, item, enums.STATUS_REVIEW)
    with pytest.raises(ValidationError):
        content_service.reject(db, item, alasan="pendek")
    item2 = content_service.reject(db, item, alasan="Hook terlalu panjang dan tidak fokus")
    assert item2.status == enums.STATUS_DITOLAK
    assert item2.reject_reason.startswith("Hook terlalu")


def test_ditolak_ke_review_menambah_revisi(db):
    from app.services import tokens as token_service

    tok, _raw = token_service.buat_token(db, nama="uji-revisi", scopes=[enums.SCOPE_WRITE])
    db.flush()
    item = content_service.buat_dari_boss(
        db,
        {
            "judul_hook": "Item revisi agent",
            "pilar": "pengingat",
            "format": "quote",
            "naskah_md": "v1",
            "cta": "share",
            "fact_level": 1,
        },
    )
    db.flush()
    content_service.pindah_status_boss(db, item, enums.STATUS_DRAFT)
    content_service.pindah_status_boss(db, item, enums.STATUS_REVIEW)
    content_service.reject(db, item, alasan="Dalil belum dicantumkan dengan benar")
    assert item.status == enums.STATUS_DITOLAK

    item.owner_token_id = tok.id  # pura-pura milik agent agar boleh PATCH
    db.flush()
    from tests.helpers import lengkapi_checklist as _lc  # noqa: F401

    validasi(db, item, enums.STATUS_REVIEW, Aktor(enums.ACTOR_AGENT, tok.id))
    content_service._pindah(
        db, item, enums.STATUS_REVIEW, Aktor(enums.ACTOR_AGENT, tok.id), aksi="submit_ulang"
    )
    assert item.status == enums.STATUS_REVIEW
    assert item.submitted_at is not None


# ---------------------------------------------------------------- revisi
def test_dua_kali_simpan_menghasilkan_dua_versi(db):
    item = content_service.buat_dari_boss(
        db,
        {
            "judul_hook": "Versioning",
            "pilar": "pengingat",
            "format": "quote",
            "naskah_md": "versi satu",
            "cta": "share",
            "fact_level": 1,
        },
    )
    db.flush()
    assert content_service.versi_terakhir(db, item.id) == 1

    content_service.update_dari_boss(db, item, {"naskah_md": "versi dua", "judul_hook": "Versioning"})
    db.flush()
    assert content_service.versi_terakhir(db, item.id) == 2

    content_service.update_dari_boss(db, item, {"naskah_md": "versi tiga", "judul_hook": "Versioning"})
    db.flush()
    assert content_service.versi_terakhir(db, item.id) == 3

    from app.models import ContentRevision
    from sqlalchemy import select

    rows = list(
        db.scalars(
            select(ContentRevision)
            .where(ContentRevision.content_id == item.id)
            .order_by(ContentRevision.versi)
        )
    )
    assert [r.naskah_md for r in rows] == ["versi satu", "versi dua", "versi tiga"]
    assert rows[0].actor_type == enums.ACTOR_BOSS


# ---------------------------------------------------------------- level 3–4
def test_level3_tanpa_sumber_dalil_gagal(db):
    item = content_service.buat_dari_boss(
        db,
        {
            "judul_hook": "Level tiga tanpa sumber",
            "pilar": "pengingat",
            "format": "quote",
            "naskah_md": "isi",
            "cta": "share",
            "fact_level": 3,
        },
    )
    db.flush()
    content_service.pindah_status_boss(db, item, enums.STATUS_DRAFT)
    with pytest.raises(TransitionError):
        content_service.pindah_status_boss(db, item, enums.STATUS_REVIEW)


def test_level4_butuh_verifikator(db):
    item = content_service.buat_dari_boss(
        db,
        {
            "judul_hook": "Level empat butuh verifikator",
            "pilar": "pengingat",
            "format": "quote",
            "naskah_md": "isi",
            "cta": "share",
            "fact_level": 4,
            "sumber_dalil": "kitab X hal. 12",
        },
    )
    db.flush()
    lengkapi_checklist(db, item)
    with pytest.raises(TransitionError) as exc:
        content_service.pindah_status_boss(db, item, enums.STATUS_DRAFT)
        content_service.pindah_status_boss(db, item, enums.STATUS_SIAP_TAYANG)
    assert "verifikator" in str(exc.value).lower() or "verifikator" in " ".join(exc.value.details)

    item.verifikator = "ustadz X — youtu.be/contoh"
    db.flush()
    content_service.pindah_status_boss(db, item, enums.STATUS_SIAP_TAYANG)
    assert item.status == enums.STATUS_SIAP_TAYANG
    assert item.approved_at is not None


# ---------------------------------------------------------------- slot
def test_satu_slot_satu_konten(db):
    from tests.helpers import siapkan_sampai_terjadwal

    tgl = date(2030, 3, 4)
    a = content_service.buat_dari_boss(
        db, {"judul_hook": "Slot A", "pilar": "pengingat", "format": "quote",
             "naskah_md": "a", "cta": "share", "fact_level": 1}
    )
    b = content_service.buat_dari_boss(
        db, {"judul_hook": "Slot B", "pilar": "pengingat", "format": "text",
             "naskah_md": "b", "cta": "share", "fact_level": 1}
    )
    db.flush()
    for item in (a, b):
        content_service.pindah_status_boss(db, item, enums.STATUS_DRAFT)
        lengkapi_checklist(db, item)
        content_service.pindah_status_boss(db, item, enums.STATUS_SIAP_TAYANG)

    content_service.jadwalkan(db, a, tgl, "pagi")
    from app.errors import Conflict

    with pytest.raises(Conflict):
        content_service.jadwalkan(db, b, tgl, "pagi")

    # slot lain masih boleh
    content_service.jadwalkan(db, b, tgl, "malam")
    assert b.slot_waktu == "malam"

    # batalkan jadwal melepas slot
    content_service.batalkan_jadwal(db, b)
    c = content_service.buat_dari_boss(
        db, {"judul_hook": "Slot C", "pilar": "pengingat", "format": "text",
             "naskah_md": "c", "cta": "share", "fact_level": 1}
    )
    db.flush()
    content_service.pindah_status_boss(db, c, enums.STATUS_DRAFT)
    lengkapi_checklist(db, c)
    content_service.pindah_status_boss(db, c, enums.STATUS_SIAP_TAYANG)
    content_service.jadwalkan(db, c, tgl, "malam")
    assert c.slot_waktu == "malam"
