"""Deterministic, evidence-backed anticipation rules for ORHEX.

The engine only uses owner-scoped local goals, commitments and confirmed memories.
It does not infer facts with an LLM, execute actions, or write to Walrus.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import Commitment, Goal, AnticipationInsight


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _fingerprint(kind: str, source_type: str, source_id: str) -> str:
    raw = f"{kind}:{source_type}:{source_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _candidate(*, user_id: str, kind: str, source_type: str, source_id: str,
               title: str, explanation: str, priority: str, due_at: datetime | None,
               evidence: dict[str, Any], now: datetime) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "fingerprint": _fingerprint(kind, source_type, source_id),
        "kind": kind,
        "source_type": source_type,
        "source_id": source_id,
        "title": title,
        "explanation": explanation,
        "priority": priority,
        "due_at": due_at,
        "evidence_json": json.dumps(evidence, sort_keys=True),
        "status": "active",
        "created_at": now,
        "updated_at": now,
    }


async def refresh_anticipations(db: AsyncSession, *, user_id: str,
                                now: datetime | None = None,
                                commit: bool = True) -> list[AnticipationInsight]:
    """Upsert current deadline insights without resurrecting dismissed items."""
    now = _aware(now or datetime.now(timezone.utc))
    horizon = now + timedelta(days=7)
    goals = list((await db.scalars(select(Goal).where(
        Goal.user_id == user_id, Goal.status == "active"
    ))).all())
    commitments = list((await db.scalars(select(Commitment).where(
        Commitment.user_id == user_id, Commitment.is_completed.is_(False)
    ))).all())
    candidates: list[dict[str, Any]] = []

    for item in commitments:
        due = _aware(item.due_date)
        if due is None:
            continue
        if due < now:
            kind, priority = "commitment_overdue", "high"
            title = f"Overdue commitment: {item.text[:180]}"
            explanation = "This incomplete commitment has passed its due date. Review it, reschedule it, or mark it complete."
        elif due <= horizon:
            kind, priority = "commitment_due_soon", "medium"
            title = f"Coming up: {item.text[:180]}"
            explanation = "This incomplete commitment is due within the next seven days. Consider setting aside time for it."
        else:
            continue
        candidates.append(_candidate(
            user_id=user_id, kind=kind, source_type="commitment", source_id=item.id,
            title=title, explanation=explanation, priority=priority, due_at=due,
            evidence={"commitment_id": item.id, "commitment_text": item.text,
                      "due_at": due.isoformat(), "rule": "commitment_due_window_v1"}, now=now,
        ))

    for goal in goals:
        due = _aware(goal.target_date)
        if due is None or due > horizon:
            continue
        if due < now:
            kind, priority, label = "goal_overdue", "high", "has passed"
        else:
            kind, priority, label = "goal_due_soon", "medium", "is approaching"
        candidates.append(_candidate(
            user_id=user_id, kind=kind, source_type="goal", source_id=goal.id,
            title=f"Goal deadline {label}: {goal.title[:160]}",
            explanation=f"This active goal is {label}. Its recorded progress is {max(0, min(100, goal.progress_percent or 0))}%. Review the next step or revise the target date.",
            priority=priority, due_at=due,
            evidence={"goal_id": goal.id, "goal_title": goal.title,
                      "progress_percent": goal.progress_percent or 0,
                      "target_date": due.isoformat(), "rule": "goal_deadline_window_v1"}, now=now,
        ))

    existing = list((await db.scalars(select(AnticipationInsight).where(
        AnticipationInsight.user_id == user_id
    ))).all())
    by_fingerprint = {item.fingerprint: item for item in existing}
    candidate_fingerprints = {item["fingerprint"] for item in candidates}
    for payload in candidates:
        record = by_fingerprint.get(payload["fingerprint"])
        if record is None:
            candidate = AnticipationInsight(**payload)
            try:
                async with db.begin_nested():
                    db.add(candidate)
                    await db.flush()
                by_fingerprint[payload["fingerprint"]] = candidate
            except IntegrityError:
                record = await db.scalar(select(AnticipationInsight).where(
                    AnticipationInsight.user_id == user_id,
                    AnticipationInsight.fingerprint == payload["fingerprint"],
                ))
                if record is None:
                    raise
                by_fingerprint[payload["fingerprint"]] = record
                if record.status in {"active", "resolved"}:
                    for key, value in payload.items():
                        if key not in {"user_id", "fingerprint", "created_at"}:
                            setattr(record, key, value)
        elif record.status in {"active", "resolved"}:
            for key, value in payload.items():
                if key not in {"user_id", "fingerprint", "created_at"}:
                    setattr(record, key, value)
    for record in existing:
        if record.status == "active" and record.fingerprint not in candidate_fingerprints:
            record.status = "resolved"
            record.updated_at = now
    await db.flush()
    if commit:
        await db.commit()
    return list((await db.scalars(select(AnticipationInsight).where(
        AnticipationInsight.user_id == user_id,
        AnticipationInsight.status == "active",
    ).order_by(AnticipationInsight.due_at.asc().nullslast(), AnticipationInsight.created_at.desc()))).all())


async def list_anticipations(db: AsyncSession, *, user_id: str,
                             include_inactive: bool = False) -> list[AnticipationInsight]:
    query = select(AnticipationInsight).where(AnticipationInsight.user_id == user_id)
    if not include_inactive:
        query = query.where(AnticipationInsight.status == "active")
    return list((await db.scalars(query.order_by(
        AnticipationInsight.due_at.asc().nullslast(), AnticipationInsight.created_at.desc()
    ))).all())


async def set_anticipation_status(db: AsyncSession, *, user_id: str,
                                  insight_id: str, status: str) -> AnticipationInsight | None:
    if status not in {"dismissed", "active"}:
        raise ValueError("Unsupported anticipation status.")
    item = await db.scalar(select(AnticipationInsight).where(
        AnticipationInsight.id == insight_id,
        AnticipationInsight.user_id == user_id,
    ))
    if item is None:
        return None
    item.status = status
    item.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(item)
    return item
