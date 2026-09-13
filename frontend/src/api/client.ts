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

export async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  const requestId = response.headers.get("x-request-id");
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // non-JSON error body: keep the status text
    }
    throw new ApiError(detail, response.status, requestId);
  }
  return (await response.json()) as T;
}

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
