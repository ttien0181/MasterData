import { exportChoices } from "./resolutionChoices";
import type { ResolutionOverview, ResolutionRunHistory, ResolutionRun, ReviewCategory, SeparateRecord,
  ResolutionItem, ResolutionItemDetail, ResolutionExportReadiness, ResolutionExportSummary,
  ResolutionPreview, ResolutionMergeHistory, ResolutionSplitPreview, AuthUser, ResolutionTask,
  UploadSession, ResolutionAuditPage, ResolutionAnalytics } from "./contracts";

export const getResolutionAnalytics = (runId: string) => request<ResolutionAnalytics>(`/runs/${runId}/analytics`);

function apiBase() {
  const configured = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();
  if (configured) return configured === "/" ? "" : configured.replace(/\/+$/, "");
  if (process.env.NODE_ENV === "production") return "";
  const host = typeof window !== "undefined" ? window.location.hostname : "localhost";
  return `http://${host === "127.0.0.1" ? host : "localhost"}:8001`;
}
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
  const response = await fetch(`${apiBase()}/api/v1/mdm${path}`, { ...init, credentials: "include", headers });
  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    throw new ApiError(errorMessage(detail?.detail, response.status), response.status);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export type { AuthUser } from "./contracts";
export type BackendHealth = { status: string; env?: string };
export async function getBackendHealth() {
  const response = await fetch(`${apiBase()}/health`, { cache: "no-store" });
  if (!response.ok) throw new ApiError(`Health check failed (${response.status})`, response.status);
  return response.json() as Promise<BackendHealth>;
}
async function authRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBase()}/api/v1/auth${path}`, { ...init, credentials: "include" });
  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    throw new ApiError(errorMessage(detail?.detail, response.status), response.status);
  }
  if (response.status === 204) return undefined as T;
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

export async function uploadResolutionFiles(files: File[]) {
  const data = new FormData();
  files.forEach((file) => data.append("files", file));
  return request<{ run_id: string; status: string; profile: { row_count: number; file_count: number; source_counts: Record<string, number> } }>(
    "/runs", { method: "POST", body: data });
}
export const getResolutionRun = (id: string) => request<ResolutionRun>(`/runs/${id}`);
export type RunListOptions = { cursor?: string | null; q?: string; status?: string; archived?: "active" | "archived" | "all" };
export const getResolutionRuns = (limit = 25, offset = 0, options: RunListOptions = {}) => {
  const query = new URLSearchParams({ limit: String(limit), offset: String(offset), archived: options.archived ?? "active" });
  if (options.cursor) query.set("cursor", options.cursor);
  if (options.q?.trim()) query.set("q", options.q.trim());
  if (options.status?.trim()) query.set("run_status", options.status.trim());
  return request<ResolutionRunHistory>(`/runs?${query}`);
};
export const renameResolutionRun = (runId: string, name: string | null) =>
  request<{ run_id: string; name: string | null }>(`/runs/${runId}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }),
  });
export const bulkResolutionRuns = (operation: "archive" | "restore", runIds: string[]) =>
  request<{ changed_count: number; operation: string; run_ids: string[] }>("/runs/bulk", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ operation, run_ids: runIds }),
  });
export const getUploadSessions = (limit = 20) =>
  request<{ items: UploadSession[] }>(`/upload-sessions?limit=${limit}`);
export const getUploadSession = (sessionId: string) => request<UploadSession>(`/upload-sessions/${sessionId}`);
export const createUploadSession = (name: string, files: File[]) =>
  request<UploadSession>("/upload-sessions", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: name.trim() || null, files: files.map((file) => ({ filename: file.name, size: file.size })) }) });
export const renameUploadSession = (sessionId: string, name: string) =>
  request<UploadSession>(`/upload-sessions/${sessionId}`, { method: "PATCH", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: name.trim() || null }) });
export const replaceUploadSessionFile = (sessionId: string, fileId: string, file: File) =>
  request<UploadSession>(`/upload-sessions/${sessionId}/files/${fileId}/replace`, {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename: file.name, size: file.size }),
  });
export const validateUploadSession = (sessionId: string) =>
  request<UploadSession>(`/upload-sessions/${sessionId}/validate`, { method: "POST" });
export const commitUploadSession = (sessionId: string) =>
  request<{ run_id: string; status: string; profile: { row_count: number; file_count: number;
    source_counts: Record<string, number>; files: { filename: string; row_count: number; sha256: string }[] } }>(
    `/upload-sessions/${sessionId}/commit`, { method: "POST" });
export const deleteUploadSession = (sessionId: string) =>
  request<void>(`/upload-sessions/${sessionId}`, { method: "DELETE" });

export function uploadResolutionUploadPart(sessionId: string, fileId: string, partNumber: number,
                                           blob: Blob, digest: string,
                                           onProgress: (loaded: number) => void): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", `${apiBase()}/api/v1/mdm/upload-sessions/${encodeURIComponent(sessionId)}/files/${encodeURIComponent(fileId)}/parts/${partNumber}`);
    xhr.withCredentials = true;
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.setRequestHeader("X-CSRF-Token", csrfToken);
    xhr.setRequestHeader("X-Chunk-SHA256", digest);
    xhr.upload.onprogress = (event) => onProgress(event.loaded);
    xhr.onerror = () => reject(new Error("Kết nối bị gián đoạn khi tải file."));
    xhr.onabort = () => reject(new Error("Đã hủy khối upload."));
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) { resolve(); return; }
      let message = `Yêu cầu thất bại (${xhr.status})`;
      try {
        const payload = JSON.parse(xhr.responseText) as { detail?: unknown };
        if (typeof payload.detail === "string") message = payload.detail;
      } catch { /* Keep the status fallback for non-JSON errors. */ }
      reject(new ApiError(message, xhr.status));
    };
    xhr.send(blob);
  });
}
export const startResolutionRun = (id: string) => request<{ task_id: string; run_id: string; status: string }>(`/runs/${id}/start`, { method: "POST" });
export const getResolutionTask = (id: string) => request<ResolutionTask>(`/runs/tasks/${id}`);
export const getResolutionOverview = (id: string) => request<ResolutionOverview>(`/runs/${id}/overview`);
export const getResolutionAudit = (runId: string, options: { offset?: number; limit?: number; action?: string;
  pair_id?: string; proposal_id?: string } = {}) => {
  const query = new URLSearchParams();
  Object.entries(options).forEach(([key, value]) => { if (value !== undefined && value !== "") query.set(key, String(value)); });
  return request<ResolutionAuditPage>(`/runs/${runId}/audit?${query}`);
};
export const getSeparateRecords = (runId: string, offset = 0, q = "") =>
  request<{ total: number; items: SeparateRecord[] }>(
    `/runs/${runId}/separate-records?offset=${offset}&limit=25&q=${encodeURIComponent(q)}`);
export const getReviewCategories = (id: string) => request<ReviewCategory[]>(`/runs/${id}/categories`);
export const explainReviewCategory = (runId: string, signature: string) => request<{ status: string; explanation?: { summary_vi: string; attention_fields: string[]; limitations: string[] }; model_version?: string; usage?: Record<string, number> }>(`/runs/${runId}/categories/${signature}/explanation`, { method: "POST" });
export type ItemBucket = "merge" | "no_merge" | "review" | "review_pairs";
export type PairCategory = { primary_group: string; subgroup: string; pair_count: number };
export const getPairCategories = (runId: string) => request<PairCategory[]>(`/runs/${runId}/pair-categories`);
export const decideReviewPair = (runId: string, pairId: string, decision: "merge" | "no_merge", revision: number) =>
  request<{ pair_id: string; proposal_id: string | null; affected_record_count: number }>(
    `/runs/${runId}/review-pairs/${pairId}/decision`, { method: "POST", body: JSON.stringify({ decision, revision }) });
export function getResolutionItems(runId: string, bucket: ItemBucket, options: { offset?: number; limit?: number; origin?: string; primary_group?: string; subgroup?: string; signature_hash?: string } = {}) {
  const query = new URLSearchParams();
  Object.entries(options).forEach(([key, value]) => { if (value !== undefined) query.set(key, String(value)); });
  return request<{ total: number; items: ResolutionItem[] }>(`/runs/${runId}/items/${bucket}?${query}`);
}
export const getResolutionItem = (runId: string, bucket: ItemBucket, itemId: string, category?: ReviewCategory) => {
  const query = new URLSearchParams();
  if (category) {
    query.set("primary_group", category.primary_group);
    query.set("subgroup", category.subgroup);
  }
  return request<ResolutionItemDetail>(`/runs/${runId}/items/${bucket}/${itemId}${query.size ? `?${query}` : ""}`);
};
export const explainResolutionItem = (runId: string, bucket: ItemBucket, itemId: string) =>
  request<{ status: string; explanation?: { summary_vi: string; attention_fields: string[]; limitations: string[] } }>(
    `/runs/${runId}/items/${bucket}/${itemId}/explanation`, { method: "POST" });
export const transitionReviewItems = (runId: string, itemIds: string[], decision: "merge" | "no_merge") =>
  request<{ decision: string; changed_count: number; changed_ids: string[] }>(`/runs/${runId}/review-items/transition`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ item_ids: itemIds, decision }),
  });
export const resolveMergeConflict = (runId: string, itemId: string) =>
  request<{ rejected_pair_count: number }>(`/runs/${runId}/merge-items/${itemId}/resolve-conflict`, {
    method: "POST",
  });
export const getResolutionExportReadiness = (runId: string) =>
  request<ResolutionExportReadiness>(`/runs/${runId}/export-readiness`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ choices: exportChoices(runId) }),
  });
export const getResolutionExports = (runId: string) =>
  request<{ items: ResolutionExportSummary[] }>(`/runs/${runId}/exports`);
export const getResolutionMerges = (runId: string, exportId: string, offset: number, search = "") =>
  request<ResolutionMergeHistory>(`/runs/${runId}/exports/${exportId}/merges?offset=${offset}&limit=25&q=${encodeURIComponent(search)}`);
export const getResolutionSplitPreview = (runId: string, exportId: string, eventId: string, memberId: string) =>
  request<ResolutionSplitPreview>(`/runs/${runId}/exports/${exportId}/merges/${eventId}/split-preview?member_id=${encodeURIComponent(memberId)}`);
export const executeResolutionSplit = (runId: string, exportId: string, eventId: string, memberId: string,
                                       version: string, executeRequestId: string) =>
  request<{ export_id: string; parent_export_id: string; row_count: number }>(
    `/runs/${runId}/exports/${exportId}/merges/${eventId}/split`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ member_id: memberId, version, execute_request_id: executeRequestId }),
    });

async function requestCsv(path: string, method: "GET" | "POST", body?: object) {
  const headers = new Headers();
  if (method !== "GET") {
    headers.set("X-CSRF-Token", csrfToken);
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`${apiBase()}/api/v1/mdm${path}`, {
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
  requestCsv(`/runs/${runId}/exports`, "POST", { acknowledge_unreviewed: acknowledgeUnreviewed, choices: exportChoices(runId) });
export const createResolutionPreview = (runId: string, acknowledgeUnreviewed: boolean) =>
  request<ResolutionPreview>(`/runs/${runId}/previews`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ acknowledge_unreviewed: acknowledgeUnreviewed, choices: exportChoices(runId) }),
  });
export const executeResolutionPreview = (runId: string, previewId: string, executeRequestId: string) =>
  requestCsv(`/runs/${runId}/previews/${previewId}/execute`, "POST", { execute_request_id: executeRequestId });
export const downloadResolutionExport = (runId: string, exportId: string) =>
  requestCsv(`/runs/${runId}/exports/${exportId}/download`, "GET");
export const downloadResolutionSource = (runId: string) =>
  requestCsv(`/runs/${runId}/source-records.csv`, "GET");
export const compareResolutionExport = (runId: string, exportId: string) =>
  request<{ source_count: number; output_count: number; source_sample: Record<string, string>[]; output_sample: Record<string, string>[] }>(
    `/runs/${runId}/exports/${exportId}/compare`);

export type PolicySimulateDetail = {
  pair_id: string;
  primary_group: string;
  current_decision: string;
  new_decision: string;
  changed: boolean;
};
export type PolicySimulateResult = {
  total_pairs: number;
  changed: number;
  review_to_merge: number;
  merge_to_review: number;
  unchanged_merge: number;
  unchanged_review: number;
  details: PolicySimulateDetail[];
  policy_file_version: string;
};
export const getPolicyYaml = () =>
  request<{ yaml_content: string; version: string }>("/runs/policies/current");
export const simulatePolicy = (runId: string, yamlContent: string) =>
  request<PolicySimulateResult>("/runs/policies/simulate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ run_id: runId, policy_yaml: yamlContent }),
  });
