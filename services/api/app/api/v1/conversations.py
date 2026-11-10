"""Visitor- and PAT-scoped conversation exchange."""
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from sqlalchemy import desc, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.llm.gemini import (
    GeminiAdapter,
    GeminiAuthenticationError,
    GeminiModelUnavailableError,
    GeminiProviderError,
    GeminiRateLimitError,
    GeminiResponseError,
    GeminiTimeoutError,
    GeminiUnavailableError,
)
from app.adapters.walrus.memwal import MemWalAdapter
from app.api.dependencies import (
    AuthenticatedConversationOwner,
    get_current_conversation_owner,
)
from app.core.database import get_db
from app.models.domain import Conversation, ConversationMessage
from app.services.conversation_memory_context import retrieve_conversation_memory_context
from app.services.wallet_context import retrieve_visitor_wallet_context

router = APIRouter(prefix="/conversations", tags=["Conversations"])
_gemini = GeminiAdapter()
_walrus = MemWalAdapter()
_MAX_MESSAGE_LENGTH = 4000
_MAX_CONTEXT_MESSAGES = 20
_MAX_CONTEXT_CHARACTERS = 24000
_MAX_STORED_EXCHANGES = 100


class ConversationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(..., min_length=1, max_length=_MAX_MESSAGE_LENGTH)
    conversation_id: UUID | None = None
    idempotency_key: UUID
    include_wallet_context: StrictBool = False

    @field_validator("message")
    @classmethod
    def _require_non_blank_message(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Message cannot be blank.")
        return normalized


class ConversationMessageResponse(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime


class ConversationMemorySummary(BaseModel):
    status: Literal["retrieved", "empty", "unavailable", "not_retrieved"]
    count: int = Field(ge=0, le=5)


class ConversationResponse(BaseModel):
    conversation_id: str
    version: int
    messages: list[ConversationMessageResponse]
    memory_context: ConversationMemorySummary


def _memory_context_summary(
    status: str, count: int
) -> ConversationMemorySummary:
    return ConversationMemorySummary(status=status, count=count)


def _owner_filter(owner: AuthenticatedConversationOwner):
    if owner.visitor_identity_id is not None:
        return Conversation.visitor_identity_id == owner.visitor_identity_id
    return Conversation.visitor_identity_id.is_(None)


def _message_response(message: ConversationMessage) -> ConversationMessageResponse:
    return ConversationMessageResponse(
        id=message.id,
        role=message.role,
        content=message.content,
        created_at=message.created_at,
    )


async def _existing_idempotent_response(
    db: AsyncSession,
    *,
    idempotency_key: str,
    owner: AuthenticatedConversationOwner,
) -> ConversationResponse | None:
    conversation = await db.scalar(
        select(Conversation)
        .join(ConversationMessage)
        .where(
            ConversationMessage.idempotency_key == idempotency_key,
            ConversationMessage.role == "user",
            Conversation.user_id == owner.user_id,
            _owner_filter(owner),
        )
    )
    if conversation is None:
        return None
    messages = await _load_messages(
        db,
        conversation.id,
        limit=_MAX_CONTEXT_MESSAGES * 2,
    )
    return ConversationResponse(
        conversation_id=conversation.id,
        version=conversation.version,
        messages=[_message_response(message) for message in messages],
        memory_context=_memory_context_summary(
            conversation.memory_context_status,
            conversation.memory_context_count,
        ),
    )


async def _load_messages(
    db: AsyncSession,
    conversation_id: str,
    *,
    limit: int,
) -> list[ConversationMessage]:
    result = await db.scalars(
        select(ConversationMessage)
        .where(ConversationMessage.conversation_id == conversation_id)
        .order_by(desc(ConversationMessage.sequence))
        .limit(limit)
    )
    return list(reversed(result.all()))


@router.get("/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    conversation_id: UUID,
    owner: AuthenticatedConversationOwner = Depends(get_current_conversation_owner),
    db: AsyncSession = Depends(get_db),
):
    conversation = await db.scalar(
        select(Conversation).where(
            Conversation.id == str(conversation_id),
            Conversation.user_id == owner.user_id,
            _owner_filter(owner),
        )
    )
    if conversation is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "The conversation was not found."},
        )

    messages = await _load_messages(
        db,
        conversation.id,
        limit=_MAX_CONTEXT_MESSAGES * 2,
    )
    return ConversationResponse(
        conversation_id=conversation.id,
        version=conversation.version,
        messages=[_message_response(message) for message in messages],
        memory_context=_memory_context_summary(
            conversation.memory_context_status,
            conversation.memory_context_count,
        ),
    )


@router.post("", response_model=ConversationResponse)
async def send_message(
    request: ConversationRequest,
    owner: AuthenticatedConversationOwner = Depends(get_current_conversation_owner),
    db: AsyncSession = Depends(get_db),
):
    existing_response = await _existing_idempotent_response(
        db,
        idempotency_key=str(request.idempotency_key),
        owner=owner,
    )
    if existing_response is not None:
        await db.rollback()
        return existing_response

    conversation: Conversation | None = None
    conversation_id: str | None = None
    current_version = 0
    history: list[ConversationMessage] = []

    if request.conversation_id is not None:
        conversation = await db.scalar(
            select(Conversation).where(
                Conversation.id == str(request.conversation_id),
                Conversation.user_id == owner.user_id,
                _owner_filter(owner),
            )
        )
        if conversation is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "not_found", "message": "The conversation was not found."},
            )
        conversation_id = conversation.id
        current_version = conversation.version
        if current_version >= _MAX_STORED_EXCHANGES:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "conversation_limit_reached",
                    "message": "This conversation reached its message limit. Start a new conversation.",
                },
            )
        history = await _load_messages(
            db,
            conversation.id,
            limit=_MAX_CONTEXT_MESSAGES,
        )
        history_characters = sum(len(message.content) for message in history)
        while history and history_characters > _MAX_CONTEXT_CHARACTERS:
            removed = history[:2]
            history = history[2:]
            history_characters -= sum(len(message.content) for message in removed)

    memory_context = await retrieve_conversation_memory_context(
        db,
        _walrus,
        user_id=owner.user_id,
        query=request.message,
    )
    wallet_context = None
    if request.include_wallet_context and owner.visitor_identity_id is not None:
        retrieved_wallet_context = await retrieve_visitor_wallet_context(
            db,
            user_id=owner.user_id,
            visitor_identity_id=owner.visitor_identity_id,
        )
        if retrieved_wallet_context.status == "retrieved":
            wallet_context = retrieved_wallet_context
    prompt_messages = [
        {
            "role": "user" if message.role == "user" else "model",
            "content": message.content,
        }
        for message in history
    ]
    # Release the read transaction while the external model call is in flight.
    await db.rollback()
    prompt_messages.append({"role": "user", "content": request.message})

    try:
        answer = await _gemini.generate_conversation_response(
            prompt_messages,
            memory_context=memory_context,
            wallet_context=wallet_context,
        )
    except GeminiUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "assistant_not_configured",
                "message": "The AI provider is not configured. Ask an administrator to configure Gemini.",
            },
        ) from exc
    except GeminiTimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail={
                "code": "assistant_timeout",
                "message": "The assistant did not respond in time. Please retry.",
            },
        ) from exc
    except GeminiAuthenticationError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "assistant_provider_auth",
                "message": "The AI provider rejected its server credentials. Ask an administrator to check the AI configuration.",
            },
        ) from exc
    except GeminiModelUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "assistant_model_unavailable",
                "message": "The configured AI model is unavailable. Ask an administrator to check the model configuration.",
            },
        ) from exc
    except GeminiRateLimitError as exc:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "assistant_provider_rate_limited",
                "message": "The AI provider is rate limited. Wait briefly, then retry your message.",
            },
        ) from exc
    except GeminiResponseError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "invalid_assistant_response",
                "message": "The assistant returned an unusable response. Please retry.",
            },
        ) from exc
    except GeminiProviderError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "assistant_provider_unavailable",
                "message": "Gemini could not complete the request. Check provider connectivity and retry.",
            },
        ) from exc

    now = datetime.now(timezone.utc)
    if conversation is None:
        conversation = Conversation(
            user_id=owner.user_id,
            visitor_identity_id=owner.visitor_identity_id,
            version=1,
            memory_context_status=memory_context.status,
            memory_context_count=memory_context.count,
            created_at=now,
            updated_at=now,
        )
        db.add(conversation)
        await db.flush()
        conversation_id = conversation.id
        first_sequence = 0
    else:
        assert conversation_id is not None
        first_sequence = current_version * 2
        update_result = await db.execute(
            update(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.user_id == owner.user_id,
                _owner_filter(owner),
                Conversation.version == current_version,
            )
            .values(
                version=current_version + 1,
                memory_context_status=memory_context.status,
                memory_context_count=memory_context.count,
                updated_at=now,
            )
        )
        if update_result.rowcount != 1:
            await db.rollback()
            existing_response = await _existing_idempotent_response(
                db,
                idempotency_key=str(request.idempotency_key),
                owner=owner,
            )
            if existing_response is not None:
                return existing_response
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "conversation_changed",
                    "message": "This conversation changed while your message was being processed. Reload it and retry.",
                },
            )

    db.add_all(
        [
            ConversationMessage(
                conversation_id=conversation_id,
                sequence=first_sequence,
                role="user",
                content=request.message,
                idempotency_key=str(request.idempotency_key),
                created_at=now,
            ),
            ConversationMessage(
                conversation_id=conversation_id,
                sequence=first_sequence + 1,
                role="assistant",
                content=answer,
                created_at=now,
            ),
        ]
    )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing_response = await _existing_idempotent_response(
            db,
            idempotency_key=str(request.idempotency_key),
            owner=owner,
        )
        if existing_response is not None:
            return existing_response
        raise

    messages = await _load_messages(
        db,
        conversation_id,
        limit=_MAX_CONTEXT_MESSAGES * 2,
    )
    return ConversationResponse(
        conversation_id=conversation_id,
        version=current_version + 1,
        messages=[_message_response(message) for message in messages],
        memory_context=_memory_context_summary(
            memory_context.status,
            memory_context.count,
        ),
    )
