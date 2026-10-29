"""Trusted operator commands for provisioning and revoking API users."""
import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

from app.core.auth import (
    AuthenticationDatabaseError,
    provision_user,
    revoke_user_tokens,
)
from app.core.config import settings
from app.core.database import AsyncSessionLocal, init_db


async def _provision(preferred_name: Optional[str]) -> None:
    await init_db()
    expires_at = datetime.now(timezone.utc) + timedelta(
        hours=settings.AUTH_TOKEN_TTL_HOURS
    )
    async with AsyncSessionLocal() as session:
        user, token = await provision_user(
            session,
            preferred_name=preferred_name,
            expires_at=expires_at,
        )
    print(f"User ID: {user.id}")
    print("Access token (shown once; store securely):")
    print(token)
    print(f"Token expires at: {expires_at.isoformat()}")


async def _revoke(user_id: str) -> None:
    await init_db()
    async with AsyncSessionLocal() as session:
        revoked = await revoke_user_tokens(session, user_id=user_id)
    if revoked == 0:
        print("No active tokens found for that user.")
    else:
        print(f"Revoked {revoked} active token(s) for user {user_id}.")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Trusted ORHEX API user/token administration."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    provision = commands.add_parser(
        "provision-user",
        help="Create a user and display its new bearer token once.",
    )
    provision.add_argument("--preferred-name")

    revoke = commands.add_parser(
        "revoke-user-tokens",
        help="Immediately revoke every active token belonging to one user.",
    )
    revoke.add_argument("--user-id", required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "provision-user":
            asyncio.run(_provision(args.preferred_name))
        elif args.command == "revoke-user-tokens":
            asyncio.run(_revoke(args.user_id))
    except AuthenticationDatabaseError as exc:
        raise SystemExit(str(exc)) from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
