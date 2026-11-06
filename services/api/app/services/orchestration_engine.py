"""Central, deterministic ORHEX decision cycle.

This v1 coordinates existing local capabilities. It may prepare proposals, but
never executes external side effects or bypasses user approval.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import (
    ActionProposal, AnticipationInsight, Commitment, Goal, OrchestrationRun, Task,
)
from app.services.anticipation_engine import refresh_anticipations
from app.services.task_action_engine import prepare_action_proposals


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _json_dict(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


async def run_orchestration(
    db: AsyncSession, *, user_id: str, idempotency_key: str,
    trigger: str = "manual", now: datetime | None = None,
) -> OrchestrationRun:
    """Run one idempotent decision cycle for exactly one authenticated owner."""
    existing = await db.scalar(select(OrchestrationRun).where(
        OrchestrationRun.user_id == user_id,
        OrchestrationRun.idempotency_key == idempotency_key,
    ))
    if existing is not None:
        return existing

    now = _utc(now or datetime.now(timezone.utc))
    record = OrchestrationRun(
        user_id=user_id, idempotency_key=idempotency_key, trigger=trigger,
        status="running", summary_json="{}",
        created_at=now,
    )
    db.add(record)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        winner = await db.scalar(select(OrchestrationRun).where(
            OrchestrationRun.user_id == user_id,
            OrchestrationRun.idempotency_key == idempotency_key,
        ))
        if winner is not None:
            return winner
        raise
    except BaseException:
        await db.rollback()
        raise

    try:
        insights = await refresh_anticipations(
            db, user_id=user_id, now=now, commit=False,
        )
        proposals = await prepare_action_proposals(
            db, user_id=user_id, commit=False,
        )

        tasks = list((await db.scalars(select(Task).where(
            Task.user_id == user_id, Task.status.in_(["todo", "in_progress"]),
        ))).all())
        goals = list((await db.scalars(select(Goal).where(
            Goal.user_id == user_id, Goal.status == "active",
        ))).all())
        commitments = list((await db.scalars(select(Commitment).where(
            Commitment.user_id == user_id, Commitment.is_completed.is_(False),
        ))).all())
        pending = [item for item in proposals if item.status == "proposed"]

        overdue_tasks = [item for item in tasks if _utc(item.due_at) and _utc(item.due_at) < now]
        due_soon_tasks = [item for item in tasks if _utc(item.due_at) and now <= _utc(item.due_at) <= now + timedelta(days=1)]
        active_insights = [item for item in insights if item.status == "active"]

        recommendations: list[dict[str, Any]] = []
        for item in pending:
            recommendations.append({
                "kind": "approval_required", "priority": "high",
                "title": item.title, "reason": item.rationale,
                "source_type": "action_proposal", "source_id": item.id,
                "requires_approval": True,
            })
        for item in overdue_tasks:
            recommendations.append({
                "kind": "overdue_task", "priority": "high",
                "title": item.title, "reason": "This task is overdue and still open.",
                "source_type": "task", "source_id": item.id,
                "requires_approval": False,
            })
        for item in due_soon_tasks:
            recommendations.append({
                "kind": "due_soon_task", "priority": "medium",
                "title": item.title, "reason": "This open task is due within 24 hours.",
                "source_type": "task", "source_id": item.id,
                "requires_approval": False,
            })
        for item in active_insights:
            recommendations.append({
                "kind": "anticipation_insight", "priority": item.priority,
                "title": item.title, "reason": item.explanation,
                "source_type": "anticipation", "source_id": item.id,
                "requires_approval": False,
            })

        priority_order = {"high": 0, "medium": 1, "low": 2}
        recommendations.sort(key=lambda item: (
            priority_order.get(item["priority"], 3),
            item["kind"],
            item["title"].casefold(),
        ))
        deduped: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for item in recommendations:
            key = (item["source_type"], item["source_id"])
            if key not in seen:
                seen.add(key)
                deduped.append(item)
            if len(deduped) >= 20:
                break

        if pending:
            decision = "approval_required"
            headline = f"{len(pending)} action proposal(s) are waiting for your approval."
        elif overdue_tasks:
            decision = "attention_needed"
            headline = f"You have {len(overdue_tasks)} overdue task(s) to review."
        elif deduped:
            decision = "next_steps_ready"
            headline = "ORHEX found useful next steps based on your current commitments."
        else:
            decision = "all_clear"
            headline = "No urgent next action was identified from the current ORHEX data."

        summary = {
            "decision": decision, "headline": headline,
            "counts": {
                "active_goals": len(goals), "open_tasks": len(tasks),
                "overdue_tasks": len(overdue_tasks), "tasks_due_within_24h": len(due_soon_tasks),
                "open_commitments": len(commitments), "active_insights": len(active_insights),
                "pending_approvals": len(pending),
            },
            "recommendations": deduped,
            "safety": {
                "external_actions_executed": 0, "wallet_transactions_signed": 0,
                "messages_sent": 0, "approval_required_for_proposals": True,
            },
            "generated_at": now.isoformat(),
        }
        record.status = "completed"
        record.summary_json = json.dumps(summary, sort_keys=True)
        await db.commit()
        await db.refresh(record)
        return record
    except BaseException:
        await db.rollback()
        raise


def orchestration_payload(record: OrchestrationRun) -> dict[str, Any]:
    return {
        "id": record.id, "trigger": record.trigger, "status": record.status,
        "created_at": record.created_at, "summary": _json_dict(record.summary_json),
    }
