"""Seed dari YAML ke DB + payload `GET /api/v1/taxonomy` (agent tidak boleh hardcode enum)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import enums
from app.models import CaptionTemplate, Pilar
from app.seed_loader import taxonomy
from app.services import hooks, quota
from app.timeutil import now_wib_naive


def seed_pilar(db: Session) -> int:
    """Idempoten: pilar dibuat kalau belum ada, nilai kuota disinkronkan."""
    dibuat = 0
    for entry in taxonomy().get("pilar", []):
        row = db.scalar(select(Pilar).where(Pilar.slug == entry["slug"]))
        if row is None:
            row = Pilar(slug=entry["slug"], dibuat=now_wib_naive())
            db.add(row)
            dibuat += 1
        row.nama = entry.get("nama", entry["slug"])
        row.warna = entry.get("warna", "#666666")
        row.urutan = int(entry.get("urutan", 0))
        row.kuota_persen = int(entry.get("kuota_persen", 0))
        row.kuota_mingguan = int(entry.get("kuota_mingguan", 0))
        row.is_active = True
    db.flush()
    return dibuat


def seed_template_contoh(db: Session) -> int:
    """3 template caption awal per pilar utama supaya layar template tidak kosong."""
    if db.scalar(select(CaptionTemplate.id).limit(1)) is not None:
        return 0
    contoh = [
        (
            "Pengingat — formula relatable",
            "pengingat",
            "Hook: <satu kalimat relatable>\n\nSituasi kehidupan: <2-3 baris>\n\nPengingat: <1-2 baris>\n\nApa satu hal yang kamu syukuri hari ini?",
        ),
        (
            "Doa — Arab + latin + arti",
            "doa_dzikir",
            "Judul: Doa ketika hati sedang gelisah\n\nArab: <lafaz utuh, jangan dipotong>\n\nLatin: <transliterasi>\n\nArti: \"<terjemahan>\"\n\nSumber: <QS/HR lengkap>",
        ),
        (
            "Interaksi — question card",
            "interaksi_humor",
            "<Pertanyaan pancingan>\n\n1. <opsi A>\n2. <opsi B>\n3. <opsi C>\n\nTulis angkanya di komentar.",
        ),
    ]
    pilar = {p.slug: p for p in db.scalars(select(Pilar))}
    n = 0
    for nama, slug, body in contoh:
        if slug not in pilar:
            continue
        db.add(
            CaptionTemplate(
                nama=nama,
                body=body,
                pilar_id=pilar[slug].id,
                is_active=True,
                dibuat=now_wib_naive(),
            )
        )
        n += 1
    db.flush()
    return n


def seed_semua(db: Session) -> dict:
    """Seed idempoten: pilar + hook registry + template contoh."""
    hasil = {
        "pilar_baru": seed_pilar(db),
        "hook_seed_baru": hooks.seed_registry(db),
        "template_baru": seed_template_contoh(db),
    }
    db.commit()
    return hasil


def payload_taxonomy(db: Session) -> dict:
    """Bentuk respons `GET /api/v1/taxonomy`."""
    tx = taxonomy()
    kuota_rows = {k.pilar_slug: k for k in quota.hitung(db)}
    pilars = []
    for entry in tx.get("pilar", []):
        k = kuota_rows.get(entry["slug"])
        pilars.append(
            {
                "slug": entry["slug"],
                "nama": entry.get("nama"),
                "kuota_persen": entry.get("kuota_persen"),
                "kuota_mingguan": entry.get("kuota_mingguan"),
                "peran_slot": {
                    slot: tx.get("peran_slot", {}).get(slot) for slot in enums.SLOTS
                },
                "kuota_terpakai": {
                    "terpakai_minggu_ini": k.terpakai_minggu_ini if k else 0,
                    "pipeline_aktif": k.pipeline_aktif if k else 0,
                    "belum_dijadwalkan": k.belum_dijadwalkan if k else 0,
                },
            }
        )
    return {
        "tz": tx.get("tz"),
        "slot_hari": tx.get("slot_hari"),
        "target_mingguan": tx.get("target_mingguan"),
        "jam_slot": tx.get("jam_slot"),
        "peran_slot": tx.get("peran_slot"),
        "pilar": pilars,
        "format": tx.get("format"),
        "kanal": tx.get("kanal"),
        "cta": tx.get("cta"),
        "fact_level": {str(k): v for k, v in (tx.get("fact_level") or {}).items()},
        "batas": tx.get("batas"),
        "warning_pemicu": [w.get("kode") for w in (tx.get("warning") or [])],
        "definisi_kuota": {
            "terpakai_minggu_ini": "status ∈ {siap_tayang,terjadwal,tayang} dengan jadwal_tayang di minggu berjalan (Senin 00:00 – Minggu 23:59 WIB)",
            "pipeline_aktif": "status ∈ {review,siap_tayang,terjadwal,tayang} tanpa batas minggu",
            "belum_dijadwalkan": "pipeline_aktif tanpa jadwal_tayang",
        },
        "level4_diparkir": True,
    }
