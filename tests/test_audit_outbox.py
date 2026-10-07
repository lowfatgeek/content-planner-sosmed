"""F20/D18 — outbox notifikasi & F19 audit; checklist F7; hook registry D24."""
from __future__ import annotations

from datetime import date

from sqlalchemy import func, select

from app import enums
from app.db import SessionLocal
from app.models import ActivityLog, HookRegistry, NotificationOutbox
from app.services import checklist, content as content_service, hooks, notify
from tests.conftest import key
from tests.helpers import lengkapi_checklist


def test_outbox_ditulis_tapi_tidak_diblokir(client, db, agent_token, fixtures):
    """App hanya menulis baris; approve tetap sukses walau notifikasi belum terkirim."""
    spec = fixtures["positif"][0]
    cid = client.post(
        "/api/v1/contents", json=spec["body"], headers={**agent_token["header"], **key("out-1")}
    ).json()["data"]["id"]

    with SessionLocal() as s:
        kinds = [o.kind for o in s.scalars(select(NotificationOutbox).where(NotificationOutbox.content_id == cid))]
        assert "draft_agent_masuk_review" in kinds
        row = s.scalars(select(NotificationOutbox).where(NotificationOutbox.content_id == cid)).first()
        assert row.status == "kirim"
        assert row.sent_at is None
        assert row.attempts == 0

    item = content_service.ambil(db, cid)
    lengkapi_checklist(db, item)
    content_service.approve(db, item)
    db.commit()

    with SessionLocal() as s:
        kinds = [o.kind for o in s.scalars(select(NotificationOutbox).where(NotificationOutbox.content_id == cid))]
    assert "disetujui" in kinds


def test_ambil_menunggu_untuk_poller(client, db, agent_token, fixtures):
    spec = fixtures["positif"][1]
    cid = client.post(
        "/api/v1/contents", json=spec["body"], headers={**agent_token["header"], **key("out-2")}
    ).json()["data"]["id"]

    with SessionLocal() as s:
        rows = notify.ambil_menunggu(s, limit=10)
        assert len(rows) == 1
        assert rows[0].content_id == cid


def test_audit_mencatat_aktor_dan_transisi(client, db, agent_token, fixtures):
    spec = fixtures["positif"][2]
    cid = client.post(
        "/api/v1/contents", json=spec["body"], headers={**agent_token["header"], **key("aud-1")}
    ).json()["data"]["id"]
    item = content_service.ambil(db, cid)
    content_service.reject(db, item, alasan="Level 3 wajib mencantumkan kitab, bukan hanya nomor")
    db.commit()

    with SessionLocal() as s:
        logs = list(
            s.scalars(select(ActivityLog).where(ActivityLog.entity_id == cid).order_by(ActivityLog.id))
        )
    aktor = {log.actor_type for log in logs}
    assert enums.ACTOR_AGENT in aktor and enums.ACTOR_BOSS in aktor
    assert any(log.from_status == enums.STATUS_REVIEW and log.to_status == enums.STATUS_DITOLAK for log in logs)
    assert any(log.action == "create" and log.to_status == enums.STATUS_DRAFT for log in logs)


def test_checklist_wajib_sebelum_siap_tayang(db, boss_item):
    from app.errors import ValidationError
    from app.services import content as cs
    import pytest

    cs.pindah_status_boss(db, boss_item, enums.STATUS_DRAFT)
    db.flush()
    with pytest.raises(ValidationError):
        cs.pindah_status_boss(db, boss_item, enums.STATUS_SIAP_TAYANG)

    lengkapi_checklist(db, boss_item)
    cs.pindah_status_boss(db, boss_item, enums.STATUS_SIAP_TAYANG)
    assert boss_item.status == enums.STATUS_SIAP_TAYANG

    rows = checklist.daftar(db, boss_item)
    assert rows and all(r.tercentang for r in rows)
    assert all(r.actor_type == enums.ACTOR_BOSS for r in rows)


def test_checklist_mengikuti_format(db, boss_item):
    from app.services.checklist import butir_untuk

    boss_item.format = "doa"
    db.flush()
    daftar = butir_untuk("doa")
    assert any("Arab + latin + arti" in b for b in daftar)
    assert len(daftar) == 3


def test_hook_registry_seed_dan_lepas_saat_arsip(db):
    """D24: baris seed tetap, baris konten dilepas saat arsip (bukan sebaliknya)."""
    n_seed = db.scalar(select(func.count(HookRegistry.hook_norm)).where(HookRegistry.asal == "seed"))
    assert n_seed == 30

    item = content_service.buat_dari_boss(
        db,
        {
            "judul_hook": "Hook unik sekali untuk uji arsip registry",
            "pilar": "pengingat",
            "format": "quote",
            "naskah_md": "x",
            "cta": "share",
            "fact_level": 1,
        },
    )
    db.flush()
    content_service.pindah_status_boss(db, item, enums.STATUS_DRAFT)
    db.flush()
    norm = hooks.normalisasi_hook(item.judul_hook)
    assert db.get(HookRegistry, norm) is not None
    assert db.get(HookRegistry, norm).asal == "konten"

    lengkapi_checklist(db, item)
    content_service.pindah_status_boss(db, item, enums.STATUS_SIAP_TAYANG)
    content_service.jadwalkan(db, item, date(2035, 1, 2), "pagi")
    content_service.tandai_tayang_boss(db, item, link_posting="https://fb.com/uji-arsip")
    content_service.arsipkan(db, item)
    db.flush()
    assert db.get(HookRegistry, norm) is None
    assert (
        db.scalar(select(func.count(HookRegistry.hook_norm)).where(HookRegistry.asal == "seed"))
        == n_seed
    )


def test_soft_delete_hanya_boss(db, boss_item):
    from app.errors import NotFound

    content_service.soft_delete(db, boss_item)
    db.flush()
    import pytest

    with pytest.raises(NotFound):
        content_service.ambil(db, boss_item.id)
