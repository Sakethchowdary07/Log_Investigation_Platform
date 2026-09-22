// Typed client for the LogSleuth backend API.
//
// These TypeScript types mirror the Pydantic v2 models in
// backend/app/models.py. Optional Python fields (Optional[...]) are modeled as
// `T | null` and datetimes are serialized as ISO-8601 strings.

export interface DeploymentCorrelation {
  service: string | null;
  version: string | null;
  deployed_at: string | null;
  delta_seconds: number | null;
  note: string | null;
}

export interface Detection {
  type: string;
  window_start: string | null;
  window_end: string | null;
  peak_time: string | null;
  error_count: number;
  possible_cause: string | null;
  related_errors: string[];
  suggested_steps: string[];
  matched_signature: string | null;
  confidence: number;
  deployment_correlations: DeploymentCorrelation[];
}

export interface AnalysisResult {
  analysis_id: number | null;
  filename: string;
  total_entries: number;
  error_count: number;
  detections: Detection[];
  deployment_correlations: DeploymentCorrelation[];
}

// A single rule as returned by GET /api/knowledge-base (see
// knowledge_base.Rule.to_dict in the backend).
export interface KnowledgeBaseRule {
  id: string;
  name: string;
  match: {
    any_of: string[];
    all_of: string[];
  };
  possible_cause: string | null;
  related_errors: string[];
  suggested_steps: string[];
  weight: number;
}

export interface KnowledgeBaseResponse {
  rules: KnowledgeBaseRule[];
}

const API_BASE = "/api";

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body && body.detail) {
        detail = body.detail;
      }
    } catch {
      // Response had no JSON body; keep the status-based detail.
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export async function uploadLog(
  file: File,
  deployments?: File | null,
): Promise<AnalysisResult> {
  const form = new FormData();
  form.append("file", file);
  if (deployments) {
    form.append("deployments", deployments);
  }
  const res = await fetch(`${API_BASE}/upload`, {
    method: "POST",
    body: form,
  });
  return handle<AnalysisResult>(res);
}

export async function listAnalyses(): Promise<AnalysisResult[]> {
  const res = await fetch(`${API_BASE}/analyses`);
  return handle<AnalysisResult[]>(res);
}

export async function getAnalysis(id: number): Promise<AnalysisResult> {
  const res = await fetch(`${API_BASE}/analyses/${id}`);
  return handle<AnalysisResult>(res);
}

export async function getKnowledgeBase(): Promise<KnowledgeBaseResponse> {
  const res = await fetch(`${API_BASE}/knowledge-base`);
  return handle<KnowledgeBaseResponse>(res);
}
