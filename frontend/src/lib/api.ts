import type { Candidate, DraftField, ImportSummary } from "./contracts";

const base = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
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

export function uploadFiles(crm: File, erp: File, bill?: File | null) {
  const data = new FormData();
  data.append("crm_file", crm);
  data.append("erp_file", erp);
  if (bill) data.append("bill_file", bill);
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
