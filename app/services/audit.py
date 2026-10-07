"""Audit `activity_log` (F19) + feed kursor (D11)."""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import enums
from app.models import ActivityLog
from app.timeutil import now_wib_naive


def catat(
    db: Session,
    *,
    actor_type: str,
    actor_id: int | None,
    action: str,
    entity: str,
    entity_id: int | None,
    from_status: str | None = None,
    to_status: str | None = None,
    meta: dict | None = None,
    when: datetime | None = None,
) -> ActivityLog:
    row = ActivityLog(
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        entity=entity,
        entity_id=entity_id,
        from_status=from_status,
        to_status=to_status,
        meta=json.dumps(meta, ensure_ascii=False) if meta else None,
        created_at=when or now_wib_naive(),
    )
    db.add(row)
    db.flush()
    return row


def feed(
    db: Session,
    *,
    cursor: int = 0,
    limit: int = 100,
    exclude_agent: bool = False,
) -> list[ActivityLog]:
    """Feed inkremental berbasis sequence `activity_log.id` (monotonik)."""
    stmt = select(ActivityLog).where(ActivityLog.id > cursor).order_by(ActivityLog.id.asc())
    if exclude_agent:
        stmt = stmt.where(ActivityLog.actor_type != enums.ACTOR_AGENT)
    return list(db.scalars(stmt.limit(limit)))


def cursor_terakhir(db: Session) -> int:
    row = db.scalar(select(ActivityLog.id).order_by(ActivityLog.id.desc()).limit(1))
    return int(row or 0)


def parse_meta(row: ActivityLog) -> dict:
    if not row.meta:
        return {}
    try:
        return json.loads(row.meta)
    except (TypeError, ValueError):  # pragma: no cover
        return {"raw": row.meta}
