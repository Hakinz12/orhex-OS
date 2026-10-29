"""Configuration settings for ORHEX OS API using Pydantic Settings."""
import re
from typing import List
from pathlib import Path
from urllib.parse import urlsplit
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Search order for .env files: current working dir, services/api dir, or workspace root
_base_dir = Path(__file__).resolve().parent.parent.parent
_workspace_root = _base_dir.parent.parent


class Settings(BaseSettings):
    # --- Application Core ---
    APP_NAME: str = "ORHEX OS"
    APP_ENV: str = "development"
    API_HOST: str = "127.0.0.1"
    API_PORT: int = 8000
    SECRET_KEY: str = "orhrex-os-development-secret-key-change-in-prod"
    CORS_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"
    API_PUBLIC_URL: str = ""
    AUTH_TOKEN_TTL_HOURS: int = Field(default=24, ge=1, le=720)
    AUTH_RATE_LIMIT_PER_MINUTE: int = Field(default=60, ge=1, le=600)

    # --- Relational Database ---
    DATABASE_URL: str = "sqlite+aiosqlite:///./orhrex.db"

    # --- AI Provider: Google Gemini ---
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.5-flash"
    LLM_PROVIDER: str = "gemini"

    # --- Persistent Memory: Walrus Memory (Mainnet) ---
    MEMWAL_AGENT_ID: str = ""
    MEMWAL_ACCOUNT_ID: str = ""
    MEMWAL_PRIVATE_KEY: str = ""
    MEMWAL_SERVER_URL: str = "https://relayer.memory.walrus.xyz"  # Official Mainnet relayer
    MEMWAL_NAMESPACE: str = "orhrex-personal-memory"              # Dedicated ORHEX namespace
    WALRUS_NETWORK: str = "mainnet"

    # --- Blockchain: Tatum Sui RPC ---
    TATUM_API_KEY: str = ""
    TATUM_SUI_RPC_URL: str = "https://sui-mainnet.gateway.tatum.io"
    SUI_NETWORK: str = "mainnet"
    DEMO_SUI_ADDRESS: str = ""

    @field_validator("APP_NAME", mode="before")
    @classmethod
    def normalize_legacy_app_name(cls, value):
        if isinstance(value, str) and value.strip().casefold() == "orhrex os":
            return "ORHEX OS"
        return value

    model_config = SettingsConfigDict(
        env_file=(_workspace_root / ".env", _base_dir / ".env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @property
    def cors_origins_list(self) -> List[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    def validate_security_configuration(self) -> None:
        """Fail closed on production deployments without HTTPS-facing URLs."""
        if self.APP_ENV.lower() != "production":
            return
        public_url = urlsplit(self.API_PUBLIC_URL.strip())
        if (
            public_url.scheme != "https"
            or not public_url.hostname
            or public_url.username
            or public_url.password
            or public_url.path not in ("", "/")
            or public_url.query
            or public_url.fragment
        ):
            raise ValueError("API_PUBLIC_URL must use HTTPS in production.")
        origins = [urlsplit(origin) for origin in self.cors_origins_list]
        if not origins or any(
            origin.scheme != "https"
            or not origin.hostname
            or origin.username
            or origin.password
            or origin.path not in ("", "/")
            or origin.query
            or origin.fragment
            or "*" in origin.netloc
            for origin in origins
        ):
            raise ValueError(
                "CORS_ORIGINS must contain only explicit HTTPS origins in production."
            )

    @property
    def has_gemini_credentials(self) -> bool:
        return bool(self.GEMINI_API_KEY and len(self.GEMINI_API_KEY) > 5)

    @property
    def has_walrus_credentials(self) -> bool:
        return bool(
            re.fullmatch(r"[0-9a-fA-F]{64}", self.MEMWAL_PRIVATE_KEY)
            and re.fullmatch(r"0x[0-9a-fA-F]{64}", self.MEMWAL_ACCOUNT_ID)
        )

    @property
    def has_tatum_credentials(self) -> bool:
        return bool(self.TATUM_API_KEY and len(self.TATUM_API_KEY) > 5)


settings = Settings()
