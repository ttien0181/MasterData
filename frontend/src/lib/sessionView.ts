import type { AuthUser, ResolutionOverview, ResolutionRunHistory, RunStatus } from "./contracts";
import { can } from "./permissions";

const STATUS_LABELS: Record<RunStatus, string> = {
  UPLOADED: "Đã nạp, chưa xử lý", IMPORTING: "Đang nạp dữ liệu", QUEUED: "Đang chờ xử lý", RUNNING: "Đang phân giải",
  COMPLETED: "Đã xử lý", RETRYABLE_FAILED: "Xử lý bị gián đoạn", FAILED: "Xử lý thất bại",
};
export function runStatusLabel(status: string) {
  return STATUS_LABELS[status as RunStatus] ?? "Trạng thái chưa được hỗ trợ";
}

export function runPhaseLabel(phase?: string | null) {
  if (!phase) return "Đang xử lý";
  const labels: Record<string, string> = {
    uploaded: "Sẵn sàng bắt đầu",
    idle: "Sẵn sàng bắt đầu",
    queued: "Đang chờ worker",
    loading: "Đang nạp dữ liệu",
    blocking: "Đang đối chiếu",
    persisting: "Đang lưu kết quả",
    completed: "Đã hoàn tất",
    failed: "Bị gián đoạn",
  };
  return labels[phase.toLowerCase()] ?? "Đang xử lý";
}

type Metric = { value: number; unit: "record" | "file" | "pair" | "merge_group" | "review_item" | "snapshot"; label: string };
export type SessionViewModel = {
  runId: string; title: string; status: string; statusLabel: string; phase: string;
  processing: boolean; resultsAvailable: boolean;
  reviewState: "unavailable" | "pending" | "clear";
  exportState: "unavailable" | "not_exported" | "has_snapshots";
  progress: { mode: "indeterminate" | "complete" | "idle" | "error"; percent: number | null };
  errorCode: string | null;
  actions: { start: boolean; retry: boolean; review: boolean; prepareExport: boolean };
  metrics: Record<"records" | "files" | "candidates" | "merge" | "review" | "separate" | "exports", Metric>;
};

/** One analysis session is the existing run. This adapter never infers final entities or probabilities. */
export function toSessionViewModel(overview: ResolutionOverview, user: AuthUser,
  history?: ResolutionRunHistory["items"][number]): SessionViewModel {
  const completed = overview.status === "COMPLETED";
  const processing = overview.status === "RUNNING" || overview.status === "QUEUED" || overview.status === "IMPORTING";
  const failed = overview.status === "FAILED" || overview.status === "RETRYABLE_FAILED";
  // Counters currently mark coarse phases (persisting may already be N/N).
  // They are not a measurable fraction of the complete computation.
  return {
    runId: overview.run_id,
    title: (history?.run_id === overview.run_id ? history.files.join(", ") : "") || `Phiên ${overview.run_id.slice(0, 8)}`,
    status: overview.status, statusLabel: runStatusLabel(overview.status), phase: overview.phase,
    processing, resultsAvailable: completed,
    reviewState: completed ? overview.counts.review > 0 ? "pending" : "clear" : "unavailable",
    exportState: completed ? overview.history_count > 0 ? "has_snapshots" : "not_exported" : "unavailable",
    progress: { mode: completed ? "complete" : failed ? "error" : processing ? "indeterminate" : "idle",
      percent: completed ? 100 : null },
    errorCode: overview.task?.error_code ?? null,
    actions: {
      start: overview.status === "UPLOADED" && can(user, "start_run"),
      retry: failed && can(user, "start_run"),
      review: completed && (can(user, "approve_pair") || can(user, "reject_pair")),
      // This is permission to request readiness/preview, NOT a replacement for backend blockers.
      prepareExport: completed && can(user, "export_data"),
    },
    metrics: {
      records: { value: overview.record_count, unit: "record", label: "Bản ghi đầu vào" },
      files: { value: overview.file_count, unit: "file", label: "File đã nạp" },
      candidates: { value: overview.candidate_count, unit: "pair", label: "Cặp được đối chiếu" },
      merge: { value: overview.counts.auto, unit: "merge_group", label: "Cụm chờ gộp" },
      review: { value: overview.counts.review, unit: "review_item", label: "Item cần review" },
      separate: { value: overview.counts.separate, unit: "record", label: "Bản ghi dự kiến giữ riêng" },
      exports: { value: overview.history_count, unit: "snapshot", label: "CSV đã xuất" },
    },
  };
}
