import type { Candidate, DraftField, ImportSummary, ResolutionOverview, ResolutionPreview, ResolutionRunHistory,
  ResolutionProposal, ResolutionReviewCase, ResolutionRun, ResolutionPair, ReviewCategory,
  ResolutionItem, ResolutionItemDetail, ResolutionExportReadiness, ResolutionExportSummary } from "./contracts";

const base = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8001";
let csrfToken = "";
export function setCsrfToken(token: string) { csrfToken = token; }

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

function errorMessage(detail: unknown, status: number) {
  return typeof detail === "string" ? detail : `Yêu cầu thất bại (${status})`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = init?.method?.toUpperCase() ?? "GET";
  const headers = new Headers(init?.headers);
  if (method !== "GET" && method !== "HEAD") headers.set("X-CSRF-Token", csrfToken);
  const response = await fetch(`${base}/api/v1/mdm${path}`, { ...init, credentials: "include", headers });
  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    throw new ApiError(errorMessage(detail?.detail, response.status), response.status);
  }
  return response.json() as Promise<T>;
}

export type AuthUser = { id: string; email: string; csrf_token: string };
async function authRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${base}/api/v1/auth${path}`, { ...init, credentials: "include" });
  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    throw new ApiError(errorMessage(detail?.detail, response.status), response.status);
  }
  return response.json() as Promise<T>;
}
export const getMe = () => authRequest<AuthUser>("/me");
export const register = (email: string, password: string) => authRequest<{ id: string; email: string }>("/register", {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email, password }),
});
export const login = (email: string, password: string) => authRequest<AuthUser>("/login", {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email, password }),
});
export const logout = () => authRequest<{ ok: boolean }>("/logout", { method: "POST", headers: { "X-CSRF-Token": csrfToken } });

export function uploadFiles(crm: File, erp: File) {
  const data = new FormData();
  data.append("crm_file", crm);
  data.append("erp_file", erp);
  return request<{ import_id: string; profile: ImportSummary["profile"]; candidate_count: number }>("/imports", { method: "POST", body: data });
}
export const getImport = (id: string) => request<ImportSummary>(`/imports/${id}`);
export const getCandidates = (id: string) => request<Candidate[]>(`/imports/${id}/candidates`);
export const getDraft = (id: string) => request<{ state: string | null; fields: Record<string, DraftField> }>(`/candidates/${id}/golden-record`);
export const decideCandidate = (id: string, decision: Exclude<Candidate["decision"], "pending">) =>
  request<{ candidate_id: string; decision: Candidate["decision"] }>(`/candidates/${id}/decision`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ decision }),
  });
export const saveDraft = (id: string, fields: Record<string, DraftField>) =>
  request<{ state: string; fields: Record<string, DraftField> }>(`/candidates/${id}/golden-record`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ fields }),
  });

export async function uploadResolutionFiles(files: File[]) {
  const data = new FormData();
  files.forEach((file) => data.append("files", file));
  return request<{ run_id: string; status: string; profile: { row_count: number; file_count: number; source_counts: Record<string, number> } }>(
    "/runs", { method: "POST", body: data });
}
export const getResolutionRun = (id: string) => request<ResolutionRun>(`/runs/${id}`);
export const getResolutionRuns = () => request<ResolutionRunHistory>("/runs?limit=25&offset=0");
export const startResolutionRun = (id: string) => request<{ task_id: string; run_id: string; status: string }>(`/runs/${id}/start`, { method: "POST" });
export const getResolutionTask = (id: string) => request<{ task_id: string; run_id: string; status: string; phase: string; processed_count: number; total_count: number; error_code: string | null }>(`/runs/tasks/${id}`);
export const getResolutionOverview = (id: string) => request<ResolutionOverview>(`/runs/${id}/overview`);
export async function getResolutionBucket(id: string, bucket: "auto" | "rejected" | "review", options: { limit?: number; offset?: number; primary_group?: string; subgroup?: string } = {}) {
  const query = new URLSearchParams();
  Object.entries(options).forEach(([key, value]) => { if (value !== undefined) query.set(key, String(value)); });
  return request<{ total: number; items: (ResolutionProposal | ResolutionReviewCase | ResolutionPair)[] }>(`/runs/${id}/buckets/${bucket}?${query}`);
}
export const getReviewCategories = (id: string) => request<ReviewCategory[]>(`/runs/${id}/categories`);
export const explainReviewCategory = (runId: string, signature: string) => request<{ status: string; explanation?: { summary_vi: string; attention_fields: string[]; limitations: string[] }; model_version?: string; usage?: Record<string, number> }>(`/runs/${runId}/categories/${signature}/explanation`, { method: "POST" });
export const decideResolutionPair = (runId: string, pairId: string, decision: "ACCEPTED" | "REJECTED" | "DEFERRED", expected_revision: number) =>
  request<{ pair_id: string; user_decision: string; revision: number }>(`/runs/${runId}/pairs/${pairId}/decision`, {
    method: "PATCH", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ decision, expected_revision }),
  });
export const approveReviewCase = (runId: string, caseId: string, expected_revision: number, accepted_pair_ids: string[]) =>
  request<{ proposal_id: string; status: string; member_count: number; revision: number }>(`/runs/${runId}/review-cases/${caseId}/approve`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ expected_revision, accepted_pair_ids }),
  });
export const approveDeferredProposal = (runId: string, proposalId: string, expected_revision: number) =>
  request<{ proposal_id: string; status: string; member_count: number; revision: number }>(`/runs/${runId}/proposals/${proposalId}/approve`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ expected_revision }),
  });
export const createMergePreview = (runId: string, proposalIds?: string[]) =>
  request<ResolutionPreview>(`/runs/${runId}/merges/preview`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(proposalIds ? { proposal_ids: proposalIds } : { selection_scope: "all_eligible", bucket: "auto" }),
  });
export const confirmMergePreview = (runId: string, previewToken: string, idempotencyKey: string,
  choices: Record<string, Record<string, string>>) => request<{ entities: { entity_id: string; proposal_id: string; member_count: number }[]; merged_count: number }>(`/runs/${runId}/merges/confirm`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ preview_token: previewToken, idempotency_key: idempotencyKey, choices }),
  });
export const cancelMergePreview = (runId: string, previewToken: string) => request<{ cancelled_count: number; review_section: string }>(`/runs/${runId}/merges/cancel`, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ preview_token: previewToken }),
});
export const getResolutionHistory = (runId: string, offset = 0) => request<{ total: number; items: { id: string; proposal_id: string; entity_id: string | null; action: string; actor_user_id: string; created_at: string; member_count: number }[] }>(`/runs/${runId}/history?limit=25&offset=${offset}`);
export const getResolutionHistoryDetail = (runId: string, eventId: string) => request<{ id: string; action: string; before_fields: Record<string, unknown>; after_fields: Record<string, unknown>; records: { id: string; external_id: string; source_system: string; file_name: string; raw_fields: Record<string, string> }[] }>(`/runs/${runId}/history/${eventId}`);

export type ItemBucket = "merge" | "no_merge" | "review";
export function getResolutionItems(runId: string, bucket: ItemBucket, options: { offset?: number; limit?: number; origin?: string; primary_group?: string; subgroup?: string } = {}) {
  const query = new URLSearchParams();
  Object.entries(options).forEach(([key, value]) => { if (value !== undefined) query.set(key, String(value)); });
  return request<{ total: number; items: ResolutionItem[] }>(`/runs/${runId}/items/${bucket}?${query}`);
}
export const getResolutionItem = (runId: string, bucket: ItemBucket, itemId: string) =>
  request<ResolutionItemDetail>(`/runs/${runId}/items/${bucket}/${itemId}`);
export const saveResolutionChoices = (runId: string, bucket: ItemBucket, itemId: string, choices: Record<string, string>) =>
  request<ResolutionItemDetail>(`/runs/${runId}/items/${bucket}/${itemId}/choices`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ choices }),
  });
export const transitionReviewItems = (runId: string, itemIds: string[], decision: "merge" | "no_merge") =>
  request<{ decision: string; changed_count: number; changed_ids: string[] }>(`/runs/${runId}/review-items/transition`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ item_ids: itemIds, decision }),
  });
export const getResolutionExportReadiness = (runId: string) =>
  request<ResolutionExportReadiness>(`/runs/${runId}/export-readiness`);
export const getResolutionExports = (runId: string) =>
  request<{ items: ResolutionExportSummary[] }>(`/runs/${runId}/exports`);

async function requestCsv(path: string, method: "GET" | "POST", body?: object) {
  const headers = new Headers();
  if (method !== "GET") {
    headers.set("X-CSRF-Token", csrfToken);
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`${base}/api/v1/mdm${path}`, {
    method, headers, credentials: "include", body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    throw new ApiError(errorMessage(detail?.detail, response.status), response.status);
  }
  const filename = /filename="?([^";]+)"?/.exec(response.headers.get("Content-Disposition") ?? "")?.[1] ?? "masterone.csv";
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url; link.download = filename; document.body.append(link); link.click(); link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export const createResolutionExport = (runId: string, acknowledgeUnreviewed: boolean) =>
  requestCsv(`/runs/${runId}/exports`, "POST", { acknowledge_unreviewed: acknowledgeUnreviewed });
export const downloadResolutionExport = (runId: string, exportId: string) =>
  requestCsv(`/runs/${runId}/exports/${exportId}/download`, "GET");
export const downloadResolutionSource = (runId: string) =>
  requestCsv(`/runs/${runId}/source-records.csv`, "GET");
export const compareResolutionExport = (runId: string, exportId: string) =>
  request<{ source_count: number; output_count: number; source_sample: Record<string, string>[]; output_sample: Record<string, string>[] }>(
    `/runs/${runId}/exports/${exportId}/compare`);
