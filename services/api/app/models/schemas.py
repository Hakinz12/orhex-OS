"""Pydantic validation schemas for request and response serialization."""
from typing import List, Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field


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
class SuiWalletRegisterRequest(BaseModel):
    sui_address: str = Field(..., pattern=r"^0x[a-fA-F0-9]{64}$", description="Valid 32-byte Sui address in hex")
    label: Optional[str] = "My Sui Wallet"
    user_id: Optional[str] = "usr_default"


class SuiActivityResponse(BaseModel):
    sui_address: str
    network: str
    balance_sui: str
    raw_balance_mist: str
    last_checkpoint: Optional[int] = None
    fetched_at: datetime
    recent_transactions: List[Dict[str, Any]] = []
