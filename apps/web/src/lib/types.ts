export interface AdapterDiagnostic {
  status: string;
  [key: string]: any;
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
  sender: "user" | "orhrex";
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

export interface SuiActivity {
  sui_address: string;
  network: string;
  balance_sui: string;
  raw_balance_mist: string;
  last_checkpoint?: number;
  fetched_at: string;
}

