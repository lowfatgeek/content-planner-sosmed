"""Enum baku PRD §9 — satu sumber kebenaran untuk nilai kolom enum."""
from __future__ import annotations

import enum

# --- status (8 nilai, `revisi` dihapus) --------------------------------
STATUS_IDE = "ide"
STATUS_DRAFT = "draft"
STATUS_REVIEW = "review"
STATUS_DITOLAK = "ditolak"
STATUS_SIAP_TAYANG = "siap_tayang"
STATUS_TERJADWAL = "terjadwal"
STATUS_TAYANG = "tayang"
STATUS_ARSIP = "arsip"

STATUSES = (
    STATUS_IDE,
    STATUS_DRAFT,
    STATUS_REVIEW,
    STATUS_DITOLAK,
    STATUS_SIAP_TAYANG,
    STATUS_TERJADWAL,
    STATUS_TAYANG,
    STATUS_ARSIP,
)

STATUS_LABEL = {
    STATUS_IDE: "Ide",
    STATUS_DRAFT: "Draft",
    STATUS_REVIEW: "Review",
    STATUS_DITOLAK: "Ditolak",
    STATUS_SIAP_TAYANG: "Siap Tayang",
    STATUS_TERJADWAL: "Terjadwal",
    STATUS_TAYANG: "Tayang",
    STATUS_ARSIP: "Arsip",
}

# --- format ------------------------------------------------------------
FORMATS = (
    "quote",
    "mini_story",
    "doa",
    "meme_relatable",
    "question",
    "carousel",
    "reels",
    "text",
)

# --- kanal -------------------------------------------------------------
KANALS = ("fb_feed", "fb_reels", "fb_story")

# --- cta ---------------------------------------------------------------
CTAS = ("komentar", "share", "save", "follow", "tanpa_cta")

# --- source ------------------------------------------------------------
SOURCES = ("agent_auto", "agent_brief", "boss_manual")

# --- actor -------------------------------------------------------------
ACTOR_BOSS = "boss"
ACTOR_AGENT = "agent"
ACTOR_SYSTEM = "system"
ACTOR_TYPES = (ACTOR_BOSS, ACTOR_AGENT, ACTOR_SYSTEM)

# --- slot --------------------------------------------------------------
SLOTS = ("pagi", "malam")

# --- scope token API (tanpa approve/reject/delete) ---------------------
SCOPE_READ = "content:read"
SCOPE_WRITE = "content:write"
SCOPE_ASSET = "asset:write"
SCOPES = (SCOPE_READ, SCOPE_WRITE, SCOPE_ASSET)

# --- fact level --------------------------------------------------------
FACT_LEVELS = (1, 2, 3, 4)
FACT_LEVEL_MAX_AGENT = 3  # level 4 diparkir di MVP (D19)

# --- format yang hanya bisa diposting Boss (D20) -----------------------
MANUAL_FORMATS = ("reels",)
MANUAL_KANALS = ("fb_reels",)

# --- status baris notification_outbox (D18) ----------------------------
# App hanya menulis `kirim`. Pengirim (poller @hermes) mengklaim baris secara
# atomik supaya tidak ada pengiriman dobel: kirim -> proses -> terkirim|gagal.
OUTBOX_KIRIM = "kirim"
OUTBOX_PROSES = "proses"
OUTBOX_TERKIRIM = "terkirim"
OUTBOX_GAGAL = "gagal"
OUTBOX_STATUSES = (OUTBOX_KIRIM, OUTBOX_PROSES, OUTBOX_TERKIRIM, OUTBOX_GAGAL)


class StrEnum(str, enum.Enum):
    def __str__(self) -> str:  # pragma: no cover - kenyamanan debug
        return self.value
