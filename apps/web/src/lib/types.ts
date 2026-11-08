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

export interface ConversationMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
}

export interface ConversationResponse {
  conversation_id: string;
  version: number;
  messages: ConversationMessage[];
  memory_context: {
    status: "retrieved" | "empty" | "unavailable" | "not_retrieved";
    count: number;
  };
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

export interface PatSessionStatus {
  authenticated: true;
  kind: "pat";
  expiresAt: string;
}

export interface VisitorSessionStatus {
  authenticated: true;
  kind: "visitor";
  visitorIdentityId: string;
  createdAt: string;
  credentialCreatedAt: string;
  credentialExpiresAt: string | null;
}

export type SessionStatus = PatSessionStatus | VisitorSessionStatus;

export interface VerifiedWalletStatus {
  verified: boolean;
  chain?: "sui";
  network?: string;
  address?: string;
}

export interface WalletVerificationChallenge {
  challenge_id: string;
  purpose: "wallet_link";
  chain: "sui";
  network: string;
  address: string;
  message: string;
  expires_at: string;
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
  trashed_at?: string | null;
  permanent_delete_started_at?: string | null;
  error?: string | null;
}

export interface ManagedMemory extends MemoryOperation {
  text: string;
  category: string;
  memory_type: string;
}

export interface MemoryFolderResponse {
  folder: "active" | "trash";
  total: number;
  memories: ManagedMemory[];
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

export interface AnticipationInsight {
  id: string;
  kind: string;
  source_type: string;
  source_id: string;
  title: string;
  explanation: string;
  priority: "low" | "medium" | "high";
  due_at: string | null;
  evidence: Record<string, unknown>;
  status: "active" | "dismissed" | "resolved";
  created_at: string;
  updated_at: string;
}


export interface OrhexTask { id:string; title:string; description:string|null; priority:"low"|"medium"|"high"; status:"todo"|"in_progress"|"completed"|"cancelled"; due_at:string|null; source_type:string|null; source_id:string|null; created_at:string; updated_at:string; completed_at:string|null; }
export interface ActionProposal { id:string; action_type:string; title:string; rationale:string; payload:Record<string,unknown>; status:"proposed"|"executed"|"dismissed"; created_at:string; updated_at:string; executed_at:string|null; }

export interface OrchestrationRecommendation {
  kind: string;
  priority: "low" | "medium" | "high";
  title: string;
  reason: string;
  source_type: string;
  source_id: string;
  requires_approval: boolean;
}

export interface OrchestrationRun {
  id: string;
  trigger: string;
  status: string;
  created_at: string;
  summary: {
    decision: "approval_required" | "attention_needed" | "next_steps_ready" | "all_clear";
    headline: string;
    counts: {
      active_goals: number;
      open_tasks: number;
      overdue_tasks: number;
      tasks_due_within_24h: number;
      open_commitments: number;
      active_insights: number;
      pending_approvals: number;
    };
    recommendations: OrchestrationRecommendation[];
    safety: {
      external_actions_executed: number;
      wallet_transactions_signed: number;
      messages_sent: number;
      approval_required_for_proposals: boolean;
    };
    generated_at: string;
  };
}
