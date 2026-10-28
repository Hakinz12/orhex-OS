"""Configuration settings for ORHREX OS API using Pydantic Settings."""
from typing import List
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

# Search order for .env files: current working dir, services/api dir, or workspace root
_base_dir = Path(__file__).resolve().parent.parent.parent
_workspace_root = _base_dir.parent.parent


class Settings(BaseSettings):
    # --- Application Core ---
    APP_NAME: str = "ORHREX OS"
    APP_ENV: str = "development"
    API_HOST: str = "127.0.0.1"
    API_PORT: int = 8000
    SECRET_KEY: str = "orhrex-os-development-secret-key-change-in-prod"
    CORS_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"

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
    MEMWAL_SERVER_URL: str = "https://memory.wal.app"
    WALRUS_NETWORK: str = "mainnet"

    # --- Blockchain: Tatum Sui RPC ---
    TATUM_API_KEY: str = ""
    TATUM_SUI_RPC_URL: str = "https://sui-mainnet.gateway.tatum.io"
    SUI_NETWORK: str = "mainnet"
    DEMO_SUI_ADDRESS: str = ""

    model_config = SettingsConfigDict(
        env_file=(_workspace_root / ".env", _base_dir / ".env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @property
    def cors_origins_list(self) -> List[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def has_gemini_credentials(self) -> bool:
        return bool(self.GEMINI_API_KEY and len(self.GEMINI_API_KEY) > 5)

    @property
    def has_walrus_credentials(self) -> bool:
        return bool(self.MEMWAL_AGENT_ID and self.MEMWAL_PRIVATE_KEY)

    @property
    def has_tatum_credentials(self) -> bool:
        return bool(self.TATUM_API_KEY and len(self.TATUM_API_KEY) > 5)


settings = Settings()

