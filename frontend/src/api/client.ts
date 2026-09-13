// Thin fetch wrapper. Same-origin: nginx proxies /api, /health and /ready to the backend.

export class ApiError extends Error {
  status: number;
  requestId: string | null;
  constructor(message: string, status: number, requestId: string | null) {
    super(message);
    this.status = status;
    this.requestId = requestId;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { Accept: "application/json", ...(init?.headers ?? {}) },
  });
  const requestId = response.headers.get("x-request-id");
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail)) detail = "invalid input";
    } catch {
      // non-JSON error body: keep the status text
    }
    throw new ApiError(detail, response.status, requestId);
  }
  return (await response.json()) as T;
}

export const getJson = <T>(path: string) => request<T>(path);

// ---------------------------------------------------------------- system

export interface ReadinessCheck {
  name: string;
  ok: boolean;
  detail: string | null;
  optional: boolean;
}

export interface ReadinessResponse {
  status: "ready" | "not_ready";
  checks: ReadinessCheck[];
  timestamp: string;
}

export interface SystemStatus {
  service: string;
  version: string;
  environment: string;
  database_ok: boolean;
  schema_version: string | null;
  migrations: string[];
  runs_total: number;
  runs_by_status: Record<string, number>;
  llm_provider: string;
  llm_configured: boolean;
  timestamp: string;
}

// /ready answers 503 while not ready but still returns the JSON body, so read it either way.
export async function getReadiness(): Promise<ReadinessResponse> {
  const response = await fetch("/ready", { headers: { Accept: "application/json" } });
  return (await response.json()) as ReadinessResponse;
}

export const getSystemStatus = () => getJson<SystemStatus>("/api/v1/system/status");

// ------------------------------------------------------------------ runs

export type RunStatus = "queued" | "running" | "answered" | "inconclusive" | "failed";
export type Stance = "supports" | "refutes" | "neutral";

export interface StageRecord {
  name: string;
  attempt: number;
  status: "ok" | "failed" | "skipped" | "timeout";
  started_at: string;
  finished_at: string;
  duration_ms: number;
  llm_calls: number;
  input_tokens: number;
  output_tokens: number;
  detail: Record<string, unknown>;
  error: string | null;
}

export interface Source {
  source_id: string;
  kind: "arxiv" | "wikipedia";
  title: string;
  url: string;
  authors: string[];
  published_year: number | null;
  summary: string;
  authority: number;
  sub_question_index: number | null;
}

export interface Claim {
  claim_id: string;
  source_id: string;
  sub_question_index: number | null;
  text: string;
  stance: Stance;
  confidence: number;
  evidence_type: string;
}

export interface SideScore {
  score: number;
  claims: number;
  sources: number;
  newest_year: number | null;
}

export interface Resolution {
  question_index: number;
  question: string;
  winner: "supports" | "refutes" | "inconclusive";
  method: string;
  support: SideScore;
  refute: SideScore;
  margin: number;
  confidence: number;
  reasoning: string;
  minority_report: {
    stance: "supports" | "refutes";
    claim_ids: string[];
    source_ids: string[];
    summary: string;
  } | null;
  superseded: [string, string][];
}

export interface Cluster {
  cluster_id: string;
  label: string;
  claim_ids: string[];
  source_ids: string[];
  supports: number;
  refutes: number;
  neutral: number;
}

export interface Consensus {
  overall?: string;
  strength?: string;
  key_agreements?: string[];
  key_disagreements?: string[];
  research_gaps?: string[];
  confidence?: number;
  method?: string;
}

export interface Critic {
  passed: boolean;
  recommendation: "pass" | "retry" | "inconclusive";
  issues: string[];
  evidence_claims: number;
  evidence_sources: number;
  unresolved_conflicts: number;
  confidence: number;
}

export interface Verification {
  citations_found: number;
  valid_citations: string[];
  invalid_removed: string[];
  has_valid_citation: boolean;
}

export interface RunUsage {
  llm_calls: number;
  input_tokens: number;
  output_tokens: number;
  estimated_cost_usd: number;
}

export interface RunDetail {
  id: string;
  request_id: string | null;
  question: string;
  status: RunStatus;
  outcome_reason: string | null;
  answer: string | null;
  confidence: number | null;
  iteration_count: number;
  usage: RunUsage;
  latency_ms: number | null;
  error: string | null;
  started_at: string;
  finished_at: string | null;
  sub_questions: string[];
  sources: Source[];
  claims: Claim[];
  resolutions: Resolution[];
  clusters: Cluster[];
  consensus: Consensus | null;
  critic: Critic | null;
  verification: Verification | null;
  caveats: string[];
  stages: StageRecord[];
  graph_summary: Record<string, number>;
}

export interface RunSummary {
  id: string;
  question: string;
  status: RunStatus;
  confidence: number | null;
  latency_ms: number | null;
  llm_calls: number;
  started_at: string;
  finished_at: string | null;
}

export interface RunList {
  runs: RunSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface GraphNode {
  id: string;
  kind: "question" | "source" | "claim";
  [key: string]: unknown;
}

export interface GraphEdge {
  source_node: string;
  target_node: string;
  edge_type: "supports" | "refutes" | "extends" | "supersedes";
  weight: number;
  explanation: string;
}

export interface GraphResponse {
  run_id: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  summary: Record<string, number>;
}

export const createRun = (question: string) =>
  request<RunDetail>("/api/v1/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, client_id: "web" }),
  });

export const getRun = (id: string) => getJson<RunDetail>(`/api/v1/runs/${encodeURIComponent(id)}`);
export const getRunGraph = (id: string) =>
  getJson<GraphResponse>(`/api/v1/runs/${encodeURIComponent(id)}/graph`);
export const listRuns = (limit = 20, offset = 0) =>
  getJson<RunList>(`/api/v1/runs?limit=${limit}&offset=${offset}`);

export const isRunning = (status: RunStatus) => status === "queued" || status === "running";
