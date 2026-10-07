"""Aturan transisi status (PRD §10) — T1…T12, dijalankan server-side.

Satu enum `status`, bukan `status` + `review_state`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from app import enums
from app.errors import Forbidden, TransitionError
from app.models import ApiToken, ContentItem

# Transisi yang sah: (dari, ke) → keterangan
LEGAL: set[tuple[str, str]] = {
    (enums.STATUS_IDE, enums.STATUS_DRAFT),
    (enums.STATUS_DRAFT, enums.STATUS_REVIEW),
    (enums.STATUS_DRAFT, enums.STATUS_SIAP_TAYANG),
    (enums.STATUS_REVIEW, enums.STATUS_SIAP_TAYANG),
    (enums.STATUS_REVIEW, enums.STATUS_DITOLAK),
    (enums.STATUS_DITOLAK, enums.STATUS_REVIEW),
    (enums.STATUS_DITOLAK, enums.STATUS_ARSIP),
    (enums.STATUS_DRAFT, enums.STATUS_ARSIP),
    (enums.STATUS_IDE, enums.STATUS_ARSIP),
    (enums.STATUS_SIAP_TAYANG, enums.STATUS_TERJADWAL),
    (enums.STATUS_SIAP_TAYANG, enums.STATUS_DRAFT),
    (enums.STATUS_TERJADWAL, enums.STATUS_TAYANG),
    (enums.STATUS_TERJADWAL, enums.STATUS_SIAP_TAYANG),
    (enums.STATUS_TAYANG, enums.STATUS_ARSIP),
}

# Siapa yang boleh memicu transisi (§10)
AKTOR_SAH: dict[tuple[str, str], tuple[str, ...]] = {
    (enums.STATUS_IDE, enums.STATUS_DRAFT): (enums.ACTOR_BOSS,),
    (enums.STATUS_DRAFT, enums.STATUS_REVIEW): (enums.ACTOR_AGENT, enums.ACTOR_BOSS),
    (enums.STATUS_DRAFT, enums.STATUS_SIAP_TAYANG): (enums.ACTOR_BOSS,),
    (enums.STATUS_REVIEW, enums.STATUS_SIAP_TAYANG): (enums.ACTOR_BOSS,),
    (enums.STATUS_REVIEW, enums.STATUS_DITOLAK): (enums.ACTOR_BOSS,),
    (enums.STATUS_DITOLAK, enums.STATUS_REVIEW): (enums.ACTOR_AGENT, enums.ACTOR_BOSS),
    (enums.STATUS_DITOLAK, enums.STATUS_ARSIP): (enums.ACTOR_BOSS,),
    (enums.STATUS_DRAFT, enums.STATUS_ARSIP): (enums.ACTOR_BOSS,),
    (enums.STATUS_IDE, enums.STATUS_ARSIP): (enums.ACTOR_BOSS,),
    (enums.STATUS_SIAP_TAYANG, enums.STATUS_TERJADWAL): (enums.ACTOR_BOSS,),
    (enums.STATUS_SIAP_TAYANG, enums.STATUS_DRAFT): (enums.ACTOR_BOSS,),
    (enums.STATUS_TERJADWAL, enums.STATUS_TAYANG): (enums.ACTOR_AGENT, enums.ACTOR_BOSS),
    (enums.STATUS_TERJADWAL, enums.STATUS_SIAP_TAYANG): (enums.ACTOR_BOSS,),
    (enums.STATUS_TAYANG, enums.STATUS_ARSIP): (enums.ACTOR_SYSTEM, enums.ACTOR_BOSS),
}

# Status yang TIDAK punya jalur agent sama sekali (agent selalu 403).
# `tayang` sengaja TIDAK di sini: agent boleh mencapai `tayang` lewat T9
# (`POST /contents/{id}/posted`) — laporan hasil posting, bukan penetapan status.
STATUS_BOSS_ONLY = (
    enums.STATUS_SIAP_TAYANG,
    enums.STATUS_TERJADWAL,
    enums.STATUS_ARSIP,
    enums.STATUS_DITOLAK,
)

FIELD_BOSS_ONLY = ("approved_at", "reject_reason", "deleted_at")

WAJIB_SUBMIT = ("judul_hook", "naskah_md", "pilar_id", "format", "cta", "fact_level")

MIN_ALASAN_TOLAK = 10


@dataclass
class Aktor:
    type: str
    id: int | None = None
    token: ApiToken | None = None

    @property
    def is_agent(self) -> bool:
        return self.type == enums.ACTOR_AGENT

    @property
    def is_boss(self) -> bool:
        return self.type == enums.ACTOR_BOSS


def boleh_ubah(item: ContentItem, aktor: Aktor) -> bool:
    """Agent hanya boleh menyentuh item miliknya (D9/§7d.2)."""
    if aktor.is_boss:
        return True
    if aktor.is_agent:
        return item.owner_token_id is not None and item.owner_token_id == aktor.id
    return False


def pastikan_boleh_ubah(item: ContentItem, aktor: Aktor) -> None:
    if not boleh_ubah(item, aktor):
        raise Forbidden("Item ini bukan milik token Anda (atau item milik Boss)")


def pastikan_tidak_beku(item: ContentItem, aktor: Aktor) -> None:
    """Setelah `approved_at` terisi, item BEKU untuk agent (§7d.3)."""
    if aktor.is_agent and item.approved_at is not None:
        raise Forbidden(
            "Item sudah disetujui Boss dan beku untuk agent. Ajukan catatan lewat chat, "
            "Boss yang membuka kembali."
        )


def periksa_syarat_submit(item: ContentItem) -> list[str]:
    """Syarat T2/T3: kelengkapan field wajib + aturan fact_level."""
    masalah: list[str] = []
    for field in WAJIB_SUBMIT:
        nilai = getattr(item, field, None)
        if nilai is None or (isinstance(nilai, str) and not nilai.strip()):
            masalah.append(f"Field wajib belum terisi: {field}")
    if item.fact_level and item.fact_level >= 3 and not (item.sumber_dalil or "").strip():
        masalah.append("fact_level 3–4 wajib punya sumber_dalil (gate server-side, bukan UI)")
    return masalah


def periksa_level4(item: ContentItem) -> list[str]:
    """F21: level 4 butuh `verifikator` (nama + tautan) sebelum `siap_tayang`."""
    if item.fact_level == 4 and not (item.verifikator or "").strip():
        return [
            "fact_level=4 wajib punya `verifikator` (nama + tautan) sebelum bisa siap_tayang "
            "(D19: level 4 diparkir di MVP)"
        ]
    return []


def validasi(
    db: Session,
    item: ContentItem,
    ke: str,
    aktor: Aktor,
    *,
    checklist_ok: bool = True,
) -> None:
    """Raises TransitionError/Forbidden kalau transisi tidak sah."""
    dari = item.status

    if dari == ke:
        raise TransitionError(f"Item sudah berstatus '{ke}'", details=[f"{dari} → {ke}"])

    # Hak kepemilikan diperiksa lebih dulu: "bukan barang Anda" (403) lebih tepat
    # daripada "transisi tidak sah" (422) untuk item milik Boss.
    if aktor.is_agent and not boleh_ubah(item, aktor):
        raise Forbidden("Item ini bukan milik token Anda (item milik Boss tidak bisa diubah)")

    if aktor.is_agent and ke in STATUS_BOSS_ONLY:
        raise Forbidden(
            f"Agent tidak boleh menyetel status '{ke}' (hanya Boss yang bisa approve/reject/"
            "jadwalkan/arsip)"
        )

    # Otorisasi dulu, baru legalitas transisi: agent yang menyentuh item milik Boss
    # harus dapat 403 (bukan bocor info transisi apa yang sah).
    if aktor.is_agent:
        pastikan_boleh_ubah(item, aktor)

    if (dari, ke) not in LEGAL:
        raise TransitionError(
            f"Transisi {dari} → {ke} tidak sah",
            details=[f"Transisi sah dari '{dari}': {_sah_dari(dari)}"],
        )

    aktor_sah = AKTOR_SAH.get((dari, ke), ())
    if aktor.type not in aktor_sah:
        raise Forbidden(f"Aktor '{aktor.type}' tidak boleh menjalankan {dari} → {ke}")

    if aktor.is_agent:
        # §7d.3: setelah `approved_at` terisi, item BEKU untuk agent — tetapi hanya
        # untuk MENGUBAH ISI (submit/re-submit ke `review`). Melaporkan hasil posting
        # (T9 `terjadwal → tayang` lewat /posted) justru terjadi SETELAH approve,
        # jadi tidak boleh ikut terkunci.
        if ke == enums.STATUS_REVIEW:
            pastikan_tidak_beku(item, aktor)

    # Syarat khusus per transisi
    if (dari, ke) in {
        (enums.STATUS_DRAFT, enums.STATUS_REVIEW),
        (enums.STATUS_DITOLAK, enums.STATUS_REVIEW),
    }:
        masalah = periksa_syarat_submit(item)
        if masalah:
            raise TransitionError("Draft belum memenuhi syarat submit", details=masalah)

    if ke == enums.STATUS_SIAP_TAYANG:
        masalah = periksa_syarat_submit(item) + periksa_level4(item)
        if not checklist_ok:
            masalah.append("Checklist produksi belum semua tercentang")
        if masalah:
            raise TransitionError("Belum bisa siap_tayang", details=masalah)

    if ke == enums.STATUS_DITOLAK:
        alasan = (item.reject_reason or "").strip()
        if len(alasan) < MIN_ALASAN_TOLAK:
            raise TransitionError(
                f"Alasan tolak wajib, minimal {MIN_ALASAN_TOLAK} karakter",
                details=[f"Panjang sekarang: {len(alasan)}"],
            )

    if ke == enums.STATUS_TERJADWAL:
        if not isinstance(item.jadwal_tayang, date):
            raise TransitionError("jadwal_tayang wajib diisi saat menjadwalkan")
        if item.slot_waktu not in enums.SLOTS:
            raise TransitionError("slot_waktu wajib 'pagi' atau 'malam'")
        if not checklist_ok:
            raise TransitionError(
                "Checklist produksi belum semua tercentang", details=["§11"]
            )

    if ke == enums.STATUS_TAYANG:
        if not (item.link_posting or "").strip():
            raise TransitionError("link_posting wajib diisi saat menandai tayang")


def _sah_dari(dari: str) -> str:
    return ", ".join(sorted({k[1] for k in LEGAL if k[0] == dari})) or "(tidak ada)"


def transisi_legal_dari(status: str) -> list[str]:
    return sorted({k[1] for k in LEGAL if k[0] == status})


__all__ = [
    "Aktor",
    "LEGAL",
    "AKTOR_SAH",
    "STATUS_BOSS_ONLY",
    "FIELD_BOSS_ONLY",
    "boleh_ubah",
    "pastikan_boleh_ubah",
    "pastikan_tidak_beku",
    "periksa_syarat_submit",
    "periksa_level4",
    "validasi",
    "transisi_legal_dari",
]
