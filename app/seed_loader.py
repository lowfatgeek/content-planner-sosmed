"""Seed taxonomy — dimiliki app (D15), di-generate sekali dari content bible.

`taxonomy.yaml` = pilar, kuota, format, cta, jam slot, peran slot, TZ, kanal.
`hooks.yaml`    = 20–30 hook awal (bible §21) → mengisi `hook_registry` sejak Fase 1 (D24).

Modul ini juga jadi cache in-memory supaya request tidak membaca YAML berulang.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from app.config import settings


def _load(path: Path) -> dict:
    if not path.exists():
        raise RuntimeError(
            f"Seed tidak ditemukan: {path}. Jalankan `python -m app.seed_loader --init` "
            "atau pastikan direktori seed/ ada."
        )
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@lru_cache(maxsize=1)
def taxonomy() -> dict:
    return _load(settings.SEED_DIR / "taxonomy.yaml")


@lru_cache(maxsize=1)
def hooks_seed() -> list[dict]:
    data = _load(settings.SEED_DIR / "hooks.yaml")
    return data.get("hooks", [])


def reload_cache() -> None:
    taxonomy.cache_clear()
    hooks_seed.cache_clear()


def pilar_by_slug() -> dict[str, dict]:
    return {p["slug"]: p for p in taxonomy().get("pilar", [])}


def pilar_slugs() -> tuple[str, ...]:
    return tuple(p["slug"] for p in taxonomy().get("pilar", []))


def formulas() -> tuple[str, ...]:
    return tuple(taxonomy().get("format", []))


def ctas() -> tuple[str, ...]:
    return tuple(taxonomy().get("cta", []))


def kanals() -> tuple[str, ...]:
    return tuple(taxonomy().get("kanal", []))


def slot_hours(slot: str) -> tuple[int, int]:
    jam = taxonomy().get("jam_slot", {})
    entry = jam.get(slot)
    if not entry:
        raise KeyError(f"Slot tidak dikenal: {slot}")
    hh, mm = str(entry).split(":")
    return int(hh), int(mm)


def slot_peran() -> dict[str, str]:
    return taxonomy().get("peran_slot", {})


def timezone_name() -> str:
    return taxonomy().get("tz", "Asia/Jakarta")


def validasi_taxonomy() -> list[str]:
    """Kembalikan daftar masalah; kosong = seed sehat."""
    masalah: list[str] = []
    tx = taxonomy()
    if sum(p.get("kuota_mingguan", 0) for p in tx.get("pilar", [])) != settings.TARGET_MINGGUAN:
        masalah.append(
            "Total kuota_mingguan pilar != TARGET_MINGGUAN "
            f"({settings.TARGET_MINGGUAN})"
        )
    persen = sum(p.get("kuota_persen", 0) for p in tx.get("pilar", []))
    if persen != 100:
        masalah.append(f"Total kuota_persen = {persen}, seharusnya 100")
    for slot in ("pagi", "malam"):
        if slot not in tx.get("jam_slot", {}):
            masalah.append(f"jam_slot.{slot} tidak ada")
    if tx.get("tz") != "Asia/Jakarta":
        masalah.append("tz harus Asia/Jakarta (D12)")
    return masalah


__all__ = [
    "taxonomy",
    "hooks_seed",
    "reload_cache",
    "pilar_by_slug",
    "pilar_slugs",
    "formulas",
    "ctas",
    "kanals",
    "slot_hours",
    "slot_peran",
    "timezone_name",
    "validasi_taxonomy",
]
