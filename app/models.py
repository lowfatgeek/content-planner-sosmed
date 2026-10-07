"""Model SQLAlchemy 2.0 — mengikuti PRD §8 (Data Model)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app import enums
from app.db import Base


def _enum(values, name: str, length: int = 30):
    """Enum portabel: VARCHAR + CHECK (native_enum=False) → aman di MariaDB & SQLite."""
    return Enum(
        *values,
        name=name,
        native_enum=False,
        length=length,
        validate_strings=True,
        values_callable=lambda e: list(e),
    )


class Pilar(Base):
    __tablename__ = "pilar"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    nama: Mapped[str] = mapped_column(String(120), nullable=False)
    warna: Mapped[str] = mapped_column(String(20), default="#666666", nullable=False)
    urutan: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    kuota_persen: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    kuota_mingguan: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    dibuat: Mapped[datetime | None] = mapped_column(DateTime)

    template = relationship("CaptionTemplate", back_populates="pilar")


class CaptionTemplate(Base):
    __tablename__ = "caption_template"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nama: Mapped[str] = mapped_column(String(120), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    pilar_id: Mapped[int | None] = mapped_column(ForeignKey("pilar.id", ondelete="SET NULL"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    dibuat: Mapped[datetime | None] = mapped_column(DateTime)

    pilar = relationship("Pilar", back_populates="template")


class User(Base):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    last_login: Mapped[datetime | None] = mapped_column(DateTime)


class ApiToken(Base):
    __tablename__ = "api_token"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nama: Mapped[str] = mapped_column(String(120), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    owner_actor: Mapped[str] = mapped_column(String(20), default=enums.ACTOR_AGENT, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)
    dibuat: Mapped[datetime | None] = mapped_column(DateTime)

    @property
    def scope_list(self) -> list[str]:
        return [s.strip() for s in (self.scope or "").split(",") if s.strip()]

    def has_scope(self, scope: str) -> bool:
        return scope in self.scope_list


class ActivityLog(Base):
    """Audit 2 aktor + kursor sinkronisasi (id AUTO_INCREMENT monotonik)."""

    __tablename__ = "activity_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_type: Mapped[str] = mapped_column(_enum(enums.ACTOR_TYPES, "actor_type"), nullable=False)
    actor_id: Mapped[int | None] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    entity: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_id: Mapped[int | None] = mapped_column(Integer)
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str | None] = mapped_column(String(20))
    meta: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (Index("ix_activity_log_entity", "entity_id", "id"),)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_key"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    token_id: Mapped[int] = mapped_column(Integer, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    response_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)


class NotificationOutbox(Base):
    """App hanya MENULIS baris; pengirimnya poller @hermes (D18).

    Klaim atomik: `status='proses'` + `claim_token` + `claimed_at`. Lihat
    `app/services/notify.py` dan `docs/POLLER-OUTBOX.md`.
    """

    __tablename__ = "notification_outbox"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    content_id: Mapped[int | None] = mapped_column(Integer)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default=enums.OUTBOX_KIRIM, nullable=False
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(500))
    claim_token: Mapped[str | None] = mapped_column(String(36))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime | None] = mapped_column(DateTime)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (Index("ix_outbox_status_id", "status", "id"),)


class ContentItem(Base):
    __tablename__ = "content_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    judul_hook: Mapped[str] = mapped_column(String(200), nullable=False)
    hook_norm: Mapped[str | None] = mapped_column(String(190), index=True)
    pilar_id: Mapped[int] = mapped_column(ForeignKey("pilar.id"), nullable=False)
    format: Mapped[str] = mapped_column(_enum(enums.FORMATS, "format"), nullable=False)
    kanal: Mapped[str] = mapped_column(_enum(enums.KANALS, "kanal"), default="fb_feed", nullable=False)
    naskah_md: Mapped[str] = mapped_column(Text, nullable=False)
    caption_fb: Mapped[str | None] = mapped_column(Text)
    cta: Mapped[str] = mapped_column(_enum(enums.CTAS, "cta"), nullable=False)
    hashtags: Mapped[str | None] = mapped_column(String(300))
    visual_note: Mapped[str | None] = mapped_column(Text)
    sumber_dalil: Mapped[str | None] = mapped_column(String(300))
    fact_level: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    verifikator: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(
        _enum(enums.STATUSES, "content_status"), default=enums.STATUS_IDE, nullable=False
    )
    source: Mapped[str] = mapped_column(_enum(enums.SOURCES, "source"), nullable=False)
    owner_token_id: Mapped[int | None] = mapped_column(
        ForeignKey("api_token.id", ondelete="SET NULL")
    )
    request_ref: Mapped[str | None] = mapped_column(String(300))
    usulan_jadwal: Mapped[datetime | None] = mapped_column(DateTime)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    reject_reason: Mapped[str | None] = mapped_column(String(500))
    jadwal_tayang: Mapped[datetime | None] = mapped_column(Date)
    slot_waktu: Mapped[str | None] = mapped_column(_enum(enums.SLOTS, "slot_waktu"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime)
    tayang_at: Mapped[datetime | None] = mapped_column(DateTime)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime)
    platform_post_id: Mapped[str | None] = mapped_column(String(100))
    last_error: Mapped[str | None] = mapped_column(String(500))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime)
    link_posting: Mapped[str | None] = mapped_column(String(300))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)
    dibuat: Mapped[datetime | None] = mapped_column(DateTime)
    diubah: Mapped[datetime | None] = mapped_column(DateTime)

    pilar = relationship("Pilar")
    revisi = relationship(
        "ContentRevision",
        back_populates="content",
        cascade="all, delete-orphan",
        order_by="ContentRevision.versi",
    )
    aset = relationship("Asset", back_populates="content", cascade="all, delete-orphan")
    metrik = relationship("MetricSnapshot", back_populates="content", cascade="all, delete-orphan")
    slot = relationship("ScheduleSlot", back_populates="content")
    checklist = relationship(
        "ProductionChecklist", back_populates="content", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("fact_level BETWEEN 1 AND 4", name="ck_content_fact_level"),
        Index("ix_content_status_pilar_tayang", "status", "pilar_id", "tayang_at"),
        Index("ix_content_owner_status", "owner_token_id", "status"),
    )


class ContentRevision(Base):
    __tablename__ = "content_revision"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content_id: Mapped[int] = mapped_column(
        ForeignKey("content_item.id", ondelete="CASCADE"), nullable=False
    )
    versi: Mapped[int] = mapped_column(Integer, nullable=False)
    naskah_md: Mapped[str] = mapped_column(Text, nullable=False)
    caption: Mapped[str | None] = mapped_column(Text)
    catatan: Mapped[str | None] = mapped_column(Text)
    actor_type: Mapped[str] = mapped_column(_enum(enums.ACTOR_TYPES, "actor_type"), nullable=False)
    actor_id: Mapped[int | None] = mapped_column(Integer)
    dibuat: Mapped[datetime | None] = mapped_column(DateTime)

    content = relationship("ContentItem", back_populates="revisi")

    __table_args__ = (UniqueConstraint("content_id", "versi", name="uq_revisi_versi"),)


class Asset(Base):
    __tablename__ = "asset"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content_id: Mapped[int] = mapped_column(
        ForeignKey("content_item.id", ondelete="CASCADE"), nullable=False
    )
    jenis: Mapped[str] = mapped_column(String(20), default="gambar", nullable=False)
    path: Mapped[str] = mapped_column(String(400), nullable=False)
    alt: Mapped[str | None] = mapped_column(String(200))
    urutan: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ukuran: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    uploaded_by_token_id: Mapped[int | None] = mapped_column(Integer)
    dibuat: Mapped[datetime | None] = mapped_column(DateTime)

    content = relationship("ContentItem", back_populates="aset")


class ScheduleSlot(Base):
    __tablename__ = "schedule_slot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tanggal: Mapped[datetime] = mapped_column(Date, nullable=False)
    platform: Mapped[str] = mapped_column(String(20), default="facebook", nullable=False)
    slot_waktu: Mapped[str] = mapped_column(_enum(enums.SLOTS, "slot_waktu"), nullable=False)
    content_id: Mapped[int | None] = mapped_column(
        ForeignKey("content_item.id", ondelete="SET NULL")
    )
    status_slot: Mapped[str] = mapped_column(String(20), default="terisi", nullable=False)

    content = relationship("ContentItem", back_populates="slot")

    __table_args__ = (
        UniqueConstraint("tanggal", "platform", "slot_waktu", name="uq_slot_harian"),
    )


class MetricSnapshot(Base):
    __tablename__ = "metric_snapshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content_id: Mapped[int] = mapped_column(
        ForeignKey("content_item.id", ondelete="CASCADE"), nullable=False
    )
    capture_date: Mapped[datetime] = mapped_column(Date, nullable=False)
    reach: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    likes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    komen: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    share: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    dibuat: Mapped[datetime | None] = mapped_column(DateTime)

    content = relationship("ContentItem", back_populates="metrik")

    __table_args__ = (
        UniqueConstraint("content_id", "capture_date", name="uq_metrik_harian"),
    )


class HookRegistry(Base):
    __tablename__ = "hook_registry"

    hook_norm: Mapped[str] = mapped_column(String(190), primary_key=True)
    content_id: Mapped[int | None] = mapped_column(
        ForeignKey("content_item.id", ondelete="SET NULL")
    )
    asal: Mapped[str] = mapped_column(_enum(("seed", "konten"), "hook_asal"), nullable=False)
    dibuat: Mapped[datetime | None] = mapped_column(DateTime)


class ProductionChecklist(Base):
    """Checklist produksi per format (F7).

    Detail implementasi: PRD §6 F7 mewajibkan semua butir tercentang sebelum
    `siap_tayang`, tetapi §8 tidak menetapkan tabelnya. Dipisah sebagai tabel
    supaya jejak "siapa mencentang" ikut terekam — sejalan dengan alasan
    `verifikator` menggantikan checkbox di F21.
    """

    __tablename__ = "production_checklist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content_id: Mapped[int] = mapped_column(
        ForeignKey("content_item.id", ondelete="CASCADE"), nullable=False
    )
    butir: Mapped[str] = mapped_column(String(255), nullable=False)
    tercentang: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    actor_type: Mapped[str | None] = mapped_column(String(20))
    actor_id: Mapped[int | None] = mapped_column(Integer)
    diubah: Mapped[datetime | None] = mapped_column(DateTime)

    content = relationship("ContentItem", back_populates="checklist")

    __table_args__ = (UniqueConstraint("content_id", "butir", name="uq_checklist_butir"),)


__all__ = [
    "Asset",
    "ActivityLog",
    "ApiToken",
    "CaptionTemplate",
    "ContentItem",
    "ContentRevision",
    "HookRegistry",
    "IdempotencyKey",
    "MetricSnapshot",
    "NotificationOutbox",
    "Pilar",
    "ProductionChecklist",
    "ScheduleSlot",
    "User",
]
