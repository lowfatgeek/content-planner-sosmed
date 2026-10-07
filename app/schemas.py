"""Skema request API v1 (flat JSON, §7c). Agent TIDAK diizinkan menyetel field Boss-only:
`approved_at`, `reject_reason`, `deleted_at`, `status`, `owner_token_id`, `source` (dihitung server).
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ContentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    judul_hook: str = Field(min_length=3, max_length=200)
    pilar: str = Field(min_length=2, max_length=60)
    format: str = Field(min_length=2, max_length=30)
    kanal: str | None = "fb_feed"
    naskah_md: str = Field(min_length=1)
    caption_fb: str | None = None
    cta: str = Field(min_length=2, max_length=30)
    hashtags: str | None = Field(default=None, max_length=300)
    visual_note: str | None = None
    sumber_dalil: str | None = Field(default=None, max_length=300)
    fact_level: int = Field(ge=1, le=4)
    verifikator: str | None = Field(default=None, max_length=200)
    usulan_jadwal: str | None = None
    request_ref: str | None = Field(default=None, max_length=300)
    source: str | None = None  # diterima tapi DIABAIKAN; server menghitungnya
    catatan: str | None = None

    @field_validator("judul_hook", "naskah_md", "cta", "pilar", "format")
    @classmethod
    def _tidak_kosong(cls, v: str) -> str:
        if not str(v).strip():
            raise ValueError("tidak boleh kosong")
        return v


class ContentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    judul_hook: str | None = Field(default=None, max_length=200)
    pilar: str | None = None
    format: str | None = None
    kanal: str | None = None
    naskah_md: str | None = None
    caption_fb: str | None = None
    cta: str | None = None
    hashtags: str | None = Field(default=None, max_length=300)
    visual_note: str | None = None
    sumber_dalil: str | None = Field(default=None, max_length=300)
    fact_level: int | None = Field(default=None, ge=1, le=4)
    verifikator: str | None = Field(default=None, max_length=200)
    usulan_jadwal: str | None = None
    request_ref: str | None = Field(default=None, max_length=300)
    catatan: str | None = None


class PostedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    link_posting: str = Field(min_length=3, max_length=300)
    platform_post_id: str | None = Field(default=None, max_length=100)
    posted_at: str | None = None


class FailedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=500)
