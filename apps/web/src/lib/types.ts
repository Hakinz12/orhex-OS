export interface AdapterDiagnostic {
  status: string;
  configured?: boolean;
  connected?: boolean;
  network?: string;
  type?: string;
  model?: string;
  error?: string;
  message?: string;
}

export interface HealthResponse {
  status: "ok" | "degraded" | "error";
  app_name: string;
  version: string;
  environment: string;
  timestamp: string;
  adapters: {
    database: AdapterDiagnostic;
    gemini: AdapterDiagnostic;
    walrus: AdapterDiagnostic;
    tatum: AdapterDiagnostic;
  };
}

export interface EvidenceItem {
  kind: "memory" | "sui_chain" | "goal";
  reference: string;
  summary: string;
}

export interface CandidateMemory {
  type: string;
  text: string;
  category: string;
  confidence: number;
}

export interface RecommendationItem {
  title: string;
  reason: string;
  source_memory_id?: string;
  suggested_action?: string;
}

export interface ChatMessage {
  id: string;
  sender: "user" | "orhex";
  text: string;
  timestamp: string;
  evidence?: EvidenceItem[];
  recommendations?: RecommendationItem[];
  candidateMemories?: CandidateMemory[];
}

export interface MemoryRecord {
  id: string;
  text: string;
  category: "personal" | "goals" | "commitments" | "work";
  memory_type: "semantic" | "episodic" | "procedural";
  status: "draft" | "queued" | "confirmed" | "failed";
  confidence: number;
  source: string;
  created_at: string;
  confirmed_at?: string;
  blob_id?: string;
}

export interface SessionStatus {
  authenticated: boolean;
  expiresAt: string;
}

export interface MemoryServiceStatus {
  status: string;
  write_ready?: boolean;
  relayer_status?: string;
  relayer_version?: string;
  mode?: string;
  error?: string;
  message?: string;
}

export interface MemoryOperation {
  status: string;
  operation_id: string;
  blob_id?: string | null;
  memory_id?: string | null;
  category?: string;
  memory_type?: string;
  metadata?: Record<string, unknown>;
  created_at?: string;
  confirmed_at?: string | null;
  error?: string | null;
}

export interface MemoryRecallHit {
  blob_id: string;
  text: string;
  distance: number;
  score?: number;
  created_at?: string | null;
}

export interface MemoryRecallResponse {
  query: string;
  total: number;
  results: MemoryRecallHit[];
}

export interface SuiActivity {
  sui_address: string;
  network: string;
  balance_sui: string;
  raw_balance_mist: string;
  last_checkpoint?: number;
  fetched_at: string;
}
