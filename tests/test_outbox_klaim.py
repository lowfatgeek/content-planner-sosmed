"""D18 — klaim atomik outbox: tanpa kirim dobel, attempts terbatas, klaim basi dipulihkan."""
from __future__ import annotations

from datetime import timedelta
from typing import Iterator

import pytest
from sqlalchemy import func, select, update

from app import enums
from app.db import SessionLocal
from app.models import NotificationOutbox
from app.services import notify
from app.timeutil import now_wib_naive


@pytest.fixture()
def outbox_uji_bersih() -> Iterator[int]:
    """DB uji dipakai bersama satu sesi, jadi baris yang lahir di uji ini dibersihkan lagi.

    Sisa baris `kirim` di outbox bukan sekadar jorok: poller akan mengirimnya ke
    Telegram Boss (pernah kejadian: 9 baris sisa probe).
    """
    with SessionLocal() as s:
        id_awal = int(s.scalar(select(func.max(NotificationOutbox.id))) or 0)
    yield id_awal
    with SessionLocal() as s:
        for row in s.scalars(select(NotificationOutbox).where(NotificationOutbox.id > id_awal)):
            s.delete(row)
        s.commit()


def _tulis(n: int) -> list[int]:
    with SessionLocal() as s:
        ids = [
            notify.tulis(
                s, kind=notify.KIND_APPROVE, content_id=None, payload={"pesan": f"uji {i}"}
            ).id
            for i in range(n)
        ]
        s.commit()
    return ids


def test_klaim_atomik_tidak_dobelan(outbox_uji_bersih):
    ids = _tulis(3)

    with SessionLocal() as a, SessionLocal() as b:
        ka = notify.klaim(a, limit=1, hanya_ids=ids)
        a.commit()
        kb = notify.klaim(b, limit=1, hanya_ids=ids)
        b.commit()

    assert len(ka) == 1 and len(kb) == 1
    assert ka[0].id != kb[0].id, "dua pengklaim mendapat baris yang sama"
    assert {ka[0].status, kb[0].status} == {enums.OUTBOX_PROSES}
    assert ka[0].attempts == 1 and kb[0].attempts == 1
    assert ka[0].claim_token and ka[0].claim_token != kb[0].claim_token

    # klaim ketiga hanya boleh mendapat baris yang masih `kirim`
    with SessionLocal() as c:
        kc = notify.klaim(c, limit=5, hanya_ids=ids)
        c.commit()
    assert [r.id for r in kc] == [
        i for i in ids if i not in {ka[0].id, kb[0].id}
    ]
    assert c.scalar(
        select(func.count(NotificationOutbox.id)).where(
            NotificationOutbox.id.in_(ids), NotificationOutbox.status == enums.OUTBOX_KIRIM
        )
    ) == 0


def test_baris_diklaim_keluar_dari_antrean_tampilan(outbox_uji_bersih):
    ids = _tulis(2)
    with SessionLocal() as s:
        diklaim = notify.klaim(s, limit=1, hanya_ids=ids)[0]
        s.commit()
        menunggu = [r.id for r in notify.ambil_menunggu(s, limit=50)]
    assert diklaim.id not in menunggu
    assert ids[1] in menunggu


def test_terkirim_dan_gagal_melepas_klaim(outbox_uji_bersih):
    ids = _tulis(1)
    with SessionLocal() as s:
        row = notify.klaim(s, limit=1, hanya_ids=ids)[0]
        s.commit()
        notify.tandai_terkirim(s, row)
        s.commit()
        assert row.status == enums.OUTBOX_TERKIRIM
        assert row.sent_at is not None
        assert row.claim_token is None and row.claimed_at is None and row.last_error is None

    ids2 = _tulis(1)
    with SessionLocal() as s:
        row = notify.klaim(s, limit=1, hanya_ids=ids2)[0]
        s.commit()
        notify.tandai_gagal(s, row, "bot diblokir Boss")
        s.commit()
        assert row.status == enums.OUTBOX_GAGAL
        assert row.last_error == "bot diblokir Boss"
        assert row.claim_token is None and row.claimed_at is None


def test_attempts_terbatas_lalu_gagal(outbox_uji_bersih):
    ids = _tulis(1)
    with SessionLocal() as s:
        for putaran in range(1, notify.MAX_ATTEMPTS + 1):
            rows = notify.klaim(s, limit=1, hanya_ids=ids)
            assert len(rows) == 1, f"putaran {putaran}: baris tidak bisa diklaim ulang"
            notify.lepas_klaim(s, rows[0], f"gagal sementara {putaran}")
            s.commit()

        row = s.get(NotificationOutbox, ids[0])
        assert row.attempts == notify.MAX_ATTEMPTS
        assert row.status == enums.OUTBOX_GAGAL
        assert "habis" in (row.last_error or "")
        # sudah `gagal` → tidak boleh bisa diklaim lagi
        assert notify.klaim(s, limit=1, hanya_ids=ids) == []


def test_pulihkan_klaim_basi(outbox_uji_bersih):
    ids = _tulis(2)
    with SessionLocal() as s:
        notify.klaim(s, limit=2, hanya_ids=ids)
        s.commit()
        basi = now_wib_naive() - timedelta(minutes=30)
        s.execute(
            update(NotificationOutbox)
            .where(NotificationOutbox.id == ids[0])
            .values(claimed_at=basi, attempts=1)
        )
        s.execute(
            update(NotificationOutbox)
            .where(NotificationOutbox.id == ids[1])
            .values(claimed_at=basi, attempts=notify.MAX_ATTEMPTS)
        )
        s.commit()

        ringkas = notify.pulihkan_klaim_basi(s, menit=10)
        s.commit()
        assert ringkas["diperiksa"] >= 2

        r0 = s.get(NotificationOutbox, ids[0])
        r1 = s.get(NotificationOutbox, ids[1])
        assert r0.status == enums.OUTBOX_KIRIM and r0.claim_token is None
        assert r1.status == enums.OUTBOX_GAGAL


def test_klaim_basi_tidak_menyerobot_yang_masih_segar(outbox_uji_bersih):
    """Poller yang masih hidup (klaim baru) tidak boleh kehilangan barisnya."""
    ids = _tulis(1)
    with SessionLocal() as s:
        notify.klaim(s, limit=1, hanya_ids=ids)
        s.commit()
        notify.pulihkan_klaim_basi(s, menit=10)
        s.commit()
        row = s.get(NotificationOutbox, ids[0])
        assert row.status == enums.OUTBOX_PROSES
        assert row.claim_token is not None


def test_hitung_status_memuat_semua_status_baku(db):
    ringkas = notify.hitung_status(db)
    assert set(ringkas) == set(enums.OUTBOX_STATUSES)
    assert all(isinstance(v, int) for v in ringkas.values())
