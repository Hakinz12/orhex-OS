"""Authenticated session metadata for same-origin frontend BFFs."""
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.dependencies import AuthenticatedUserSession, get_current_user_session

router = APIRouter(prefix="/auth", tags=["Authentication"])


class SessionMetadataResponse(BaseModel):
    expires_at: datetime


@router.get("/session", response_model=SessionMetadataResponse)
async def get_session_metadata(
    session: AuthenticatedUserSession = Depends(get_current_user_session),
):
    """Return only the verified token expiration; never return token or user data."""
    return SessionMetadataResponse(expires_at=session.expires_at)
