"""Pydantic validation schemas for request and response serialization."""
from typing import Annotated, List, Literal, Optional, Dict, Any
from datetime import datetime
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator


# --- Health & Diagnostic Schemas ---
class AdapterHealth(BaseModel):
    status: str
    details: Optional[Dict[str, Any]] = None


class HealthResponse(BaseModel):
    status: str = "ok"
    app_name: str
    version: str
    environment: str
    timestamp: datetime
    adapters: Dict[str, Any]


# --- Chat & Context Schemas ---
class ChatMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    session_id: Optional[str] = "default"
    user_id: Optional[str] = "usr_default"


class EvidenceItem(BaseModel):
    kind: str  # memory, sui_chain, goal
    reference: str
    summary: str


class CandidateMemory(BaseModel):
    type: str = "semantic"  # semantic, commitment, preference, goal
    text: str
    category: str = "personal"
    confidence: float = 1.0


class RecommendationItem(BaseModel):
    title: str
    reason: str
    source_memory_id: Optional[str] = None
    suggested_action: Optional[str] = None


class ChatMessageResponse(BaseModel):
    answer: str
    session_id: str
    evidence: List[EvidenceItem] = []
    candidate_memories: List[CandidateMemory] = []
    recommendations: List[RecommendationItem] = []
    needs_confirmation: bool = False


# --- Memory Schemas ---
class MemoryConfirmRequest(BaseModel):
    text: str = Field(..., min_length=3, max_length=2000)
    category: str = "personal"
    type: str = "semantic"
    user_id: Optional[str] = "usr_default"


class MemoryConfirmResponse(BaseModel):
    status: str
    memory_id: str
    blob_id: Optional[str] = None
    stored_at: datetime


class MemoryItem(BaseModel):
    id: str
    text: str
    category: str
    memory_type: str
    status: str
    confidence: float
    source: str
    created_at: datetime
    confirmed_at: Optional[datetime] = None
    blob_id: Optional[str] = None


class MemoryListResponse(BaseModel):
    total: int
    memories: List[MemoryItem]


# --- Blockchain / Tatum Sui Schemas ---
DEFAULT_SUI_PAGE_LIMIT = 20
MAX_SUI_PAGE_LIMIT = 50
MAX_SUI_CURSOR_LENGTH = 512
SUI_TRANSACTION_DIGEST_BYTES = 32


def validate_sui_address(value: str) -> str:
    """Accept Sui hex addresses up to 32 bytes, including short canonical forms."""
    if not isinstance(value, str) or not value.startswith("0x"):
        raise ValueError("Sui address must start with 0x.")
    hex_value = value[2:]
    if not 1 <= len(hex_value) <= 64 or any(
        character not in "0123456789abcdefABCDEF" for character in hex_value
    ):
        raise ValueError("Sui address must contain 1 to 64 hexadecimal digits.")
    return value


def validate_sui_transaction_digest(value: str) -> str:
    """Validate a Sui transaction digest as a base58-encoded 32-byte value."""
    if not isinstance(value, str) or not value:
        raise ValueError("Sui transaction digest cannot be empty.")
    if len(value) > 44:
        raise ValueError("Sui transaction digest cannot exceed 44 base58 characters.")

    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    decoded = 0
    for character in value:
        digit = alphabet.find(character)
        if digit < 0:
            raise ValueError("Sui transaction digest must use base58 encoding.")
        decoded = decoded * 58 + digit

    byte_length = (decoded.bit_length() + 7) // 8
    leading_zero_bytes = len(value) - len(value.lstrip("1"))
    if leading_zero_bytes + byte_length != SUI_TRANSACTION_DIGEST_BYTES:
        raise ValueError("Sui transaction digest must decode to 32 bytes.")
    return value


def _validate_sui_cursor(value: Optional[str]) -> Optional[str]:
    if value is None:
        return value
    if not value or value != value.strip():
        raise ValueError("Sui pagination cursor must be a non-empty opaque value.")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("Sui pagination cursor cannot contain control characters.")
    return value


SuiAddress = Annotated[str, AfterValidator(validate_sui_address)]
SuiCursor = Annotated[
    Optional[str],
    AfterValidator(_validate_sui_cursor),
    Field(max_length=MAX_SUI_CURSOR_LENGTH),
]
SuiTransactionDigest = Annotated[
    str, AfterValidator(validate_sui_transaction_digest)
]


class SuiWalletRegisterRequest(BaseModel):
    sui_address: SuiAddress = Field(
        ...,
        description="Sui hexadecimal address; shorter forms are accepted.",
    )
    label: Optional[str] = "My Sui Wallet"
    user_id: Optional[str] = "usr_default"


class SuiPaginationQuery(BaseModel):
    """Bounded query parameters for Sui object-list endpoints."""

    model_config = ConfigDict(extra="forbid")

    cursor: Optional[str] = Field(
        default=None,
        max_length=MAX_SUI_CURSOR_LENGTH,
        description="Opaque provider cursor; format is provider-defined.",
    )
    limit: int = Field(
        default=DEFAULT_SUI_PAGE_LIMIT,
        ge=1,
        le=MAX_SUI_PAGE_LIMIT,
        description=f"Page size from 1 to {MAX_SUI_PAGE_LIMIT}.",
    )

    @field_validator("limit", mode="before")
    @classmethod
    def reject_boolean_limit(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("Sui pagination limit must be an integer.")
        return value

    @field_validator("cursor")
    @classmethod
    def validate_cursor(cls, value: Optional[str]) -> Optional[str]:
        return _validate_sui_cursor(value)


class SuiPaginationMetadata(BaseModel):
    """ORHEX-normalized pagination metadata; provider cursors remain opaque."""

    model_config = ConfigDict(extra="forbid")

    next_cursor: Optional[str] = Field(default=None, max_length=MAX_SUI_CURSOR_LENGTH)
    has_next_page: bool

    @field_validator("next_cursor")
    @classmethod
    def validate_next_cursor(cls, value: Optional[str]) -> Optional[str]:
        return _validate_sui_cursor(value)


class SuiNetworkResponse(BaseModel):
    """Configured Sui network and verified chain identifier."""

    model_config = ConfigDict(extra="forbid")

    network: Literal["mainnet", "testnet", "devnet"]
    endpoint: str
    chain_identifier: str


class SuiHealthResponse(BaseModel):
    """Tatum readiness status normalized from the existing adapter response."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["unconfigured", "ready", "unreachable", "degraded"]
    configured: bool
    connected: bool
    network: Literal["mainnet", "testnet", "devnet"]
    endpoint: str
    latest_checkpoint: Optional[int] = Field(default=None, ge=0, strict=True)
    error: Optional[str] = None
    message: Optional[str] = None


class SuiCoinBalance(BaseModel):
    """A normalized coin balance represented without floating-point amounts."""

    model_config = ConfigDict(extra="forbid")

    coin_type: str
    raw_balance: str = Field(
        ...,
        strict=True,
        pattern=r"^(0|[1-9][0-9]*)$",
    )
    amount: Optional[str] = Field(
        default=None,
        strict=True,
        pattern=r"^(0|[1-9][0-9]*)(\.[0-9]+)?$",
        description="Decimal string; omitted when verified token decimals are unavailable.",
    )
    decimals: Optional[int] = Field(default=None, ge=0, le=255)
    symbol: Optional[str] = None


class SuiWalletBalancesResponse(BaseModel):
    """Owner-independent public balances for one Sui address."""

    model_config = ConfigDict(extra="forbid")

    sui_address: SuiAddress
    network: Literal["mainnet", "testnet", "devnet"]
    balances: List[SuiCoinBalance] = Field(default_factory=list)
    pagination: SuiPaginationMetadata = Field(
        default_factory=lambda: SuiPaginationMetadata(has_next_page=False)
    )


class SuiOwnedObject(BaseModel):
    """Normalized object fields suitable for a read-only owned-object listing."""

    model_config = ConfigDict(extra="forbid")

    object_id: SuiAddress
    version: Optional[str] = Field(default=None, pattern=r"^(0|[1-9][0-9]*)$")
    digest: Optional[SuiTransactionDigest] = None
    type: Optional[str] = None


class SuiOwnedObjectsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sui_address: SuiAddress
    network: Literal["mainnet", "testnet", "devnet"]
    objects: List[SuiOwnedObject] = Field(default_factory=list)
    pagination: SuiPaginationMetadata


class SuiTransactionResponse(BaseModel):
    """Normalized transaction summary based on fields currently parsed by ORHEX."""

    model_config = ConfigDict(extra="forbid")

    digest: SuiTransactionDigest
    timestamp_ms: Optional[int] = Field(default=None, ge=0, strict=True)
    status: Optional[str] = None


class SuiErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: str
    message: str
    operation_id: Optional[str] = None


class SuiActivityResponse(BaseModel):
    sui_address: str
    network: str
    balance_sui: str
    raw_balance_mist: str
    last_checkpoint: Optional[int] = None
    fetched_at: datetime
    recent_transactions: List[Dict[str, Any]] = []
