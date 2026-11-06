"""Deterministic action proposal preparation with explicit approval gates."""
import hashlib
import json
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.domain import ActionProposal, AnticipationInsight


async def prepare_action_proposals(
    db: AsyncSession, *, user_id: str, commit: bool = True
) -> list[ActionProposal]:
    """Create one proposed task action per active insight, without executing it."""
    insights = list((await db.scalars(select(AnticipationInsight).where(
        AnticipationInsight.user_id == user_id,
        AnticipationInsight.status == "active",
    ))).all())
    existing = list((await db.scalars(select(ActionProposal).where(
        ActionProposal.user_id == user_id,
    ))).all())
    known = {item.fingerprint for item in existing}

    for insight in insights:
        fingerprint = hashlib.sha256(
            f"create_task:{insight.id}".encode("utf-8")
        ).hexdigest()
        if fingerprint in known:
            continue
        payload = {
            "title": insight.title[:256],
            "description": insight.explanation,
            "priority": insight.priority,
            "due_at": insight.due_at.isoformat() if insight.due_at else None,
            "source_type": "anticipation",
            "source_id": insight.id,
        }
        proposal = ActionProposal(
            user_id=user_id,
            fingerprint=fingerprint,
            action_type="create_task",
            title=f"Create task: {insight.title[:210]}",
            rationale=insight.explanation,
            payload_json=json.dumps(payload, sort_keys=True),
            status="proposed",
        )
        try:
            async with db.begin_nested():
                db.add(proposal)
                await db.flush()
        except IntegrityError:
            winner = await db.scalar(select(ActionProposal).where(
                ActionProposal.user_id == user_id,
                ActionProposal.fingerprint == fingerprint,
            ))
            if winner is None:
                raise
        known.add(fingerprint)

    await db.flush()
    if commit:
        await db.commit()
    return list((await db.scalars(select(ActionProposal).where(
        ActionProposal.user_id == user_id,
    ).order_by(ActionProposal.created_at.desc()))).all())
