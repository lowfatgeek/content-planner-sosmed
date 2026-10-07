"""Helper waktu WIB (Asia/Jakarta, UTC+07:00 tanpa DST).

Aturan PRD D12:
- DB menyimpan DATETIME "naive" dalam WIB (session MariaDB di-set time_zone='+07:00').
- Semua nilai yang keluar/masuk API memakai offset eksplisit +07:00.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

WIB = timezone(timedelta(hours=7))


def now_wib() -> datetime:
    """Waktu sekarang, aware WIB."""
    return datetime.now(tz=WIB)


def now_wib_naive() -> datetime:
    """Waktu sekarang yang disimpan ke DB (naive WIB)."""
    return now_wib().replace(tzinfo=None)


def to_db(dt: datetime | str | None) -> datetime | None:
    """Normalisasi nilai masuk (datetime atau string ISO) → naive WIB untuk DB."""
    if dt is None:
        return None
    if isinstance(dt, str):
        dt = parse_iso(dt)
        if dt is None:
            return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(WIB).replace(tzinfo=None)


def from_db(dt: datetime | None) -> datetime | None:
    """Nilai dari DB → aware WIB untuk serialisasi API."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=WIB)
    return dt.astimezone(WIB)


def iso_wib(dt: datetime | None) -> str | None:
    """ISO 8601 dengan offset +07:00."""
    aware = from_db(dt)
    return aware.isoformat() if aware else None


_OFFSET_SPASI = re.compile(r"\s(\d{2}:\d{2})$")


def parse_iso(value: str | None) -> datetime | None:
    """Terima ISO 8601 (dengan atau tanpa offset). Naive = dianggap WIB.

    Toleransi disengaja: di query string URL, `+07:00` tanpa percent-encoding
    terbaca sebagai spasi oleh parser query (`+` = space pada form-encoding).
    Karena itu `... 07:00` diperlakukan sama dengan `...+07:00`, supaya contoh
    pemakaian di PRD (`?due=2026-10-09T20:05:00+07:00`) tetap bekerja apa adanya.
    """
    if value is None:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    text = _OFFSET_SPASI.sub(r"+\1", text)
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"Waktu tidak valid: {value!r}") from exc
    return to_db(dt)


def today_wib() -> date:
    return now_wib().date()


def week_bounds(day: date | None = None) -> tuple[datetime, datetime]:
    """Senin 00:00 – Minggu 23:59:59.999999 (WIB) dari minggu berjalan."""
    d = day or today_wib()
    monday = d - timedelta(days=d.weekday())
    start = datetime.combine(monday, datetime.min.time())
    end = start + timedelta(days=7) - timedelta(microseconds=1)
    return start, end


def slot_datetime(tanggal: date, slot_waktu: str) -> datetime:
    """DATETIME jadwal dari tanggal + slot (jam dari seed)."""
    from app.seed_loader import slot_hours

    jam, menit = slot_hours(slot_waktu)
    return datetime(tanggal.year, tanggal.month, tanggal.day, jam, menit)
