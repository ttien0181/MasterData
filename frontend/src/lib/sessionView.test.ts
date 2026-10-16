import { describe, expect, it } from "vitest";
import type { AuthUser, ResolutionOverview } from "./contracts";
import { can } from "./permissions";
import { toSessionViewModel } from "./sessionView";

const user: AuthUser = { id: "user", email: "user@example.test", csrf_token: "csrf", role: "STEWARD",
  capabilities: ["read", "start_run", "approve_pair", "export_data"] };
const overview: ResolutionOverview = {
  run_id: "run-1", status: "COMPLETED", phase: "completed", processed_count: 100, total_count: 100,
  record_count: 100, file_count: 2, source_counts: { CRM: 60, ERP: 40 }, history_count: 0, candidate_count: 230,
  estimated_entity_count: 55, duplicate_record_count: 45, eligible_merge_group_count: 20,
  counts: { auto: 20, review: 8, review_cases: 7, manual: 1, separate: 35,
    rejected: 12, hard_blocked: 4, insufficient: 15, group_a: 20, group_c: 8, group_d: 4 },
};

describe("run to session contract", () => {
  it("keeps degraded rule results reviewable without reporting normal completion", () => {
    const session = toSessionViewModel({...overview, status:"DEGRADED",phase:"degraded"}, user);
    expect(session.resultsAvailable).toBe(true);
    expect(session.actions.review).toBe(true);
    expect(session.progress).toEqual({mode:"error",percent:null});
    expect(session.statusLabel).toBe("Đối chiếu nâng cao chưa đầy đủ");
  });
  it("keeps completed processing separate from pending review and no export", () => {
    const session = toSessionViewModel(overview, user);
    expect(session.resultsAvailable).toBe(true);
    expect(session.reviewState).toBe("pending");
    expect(session.exportState).toBe("not_exported");
    expect(session.actions.start).toBe(false);
    expect(session.actions.retry).toBe(false);
    expect(session.metrics.merge).toMatchObject({ value: 20, unit: "merge_group" });
    expect(session.metrics.review).toMatchObject({ value: 8, unit: "review_item" });
    expect(session.metrics.candidates).toMatchObject({ value: 230, unit: "pair" });
    expect(session.metrics.separate).toMatchObject({ value: 35, unit: "record" });
  });

  it.each(["blocking", "persisting"])("never turns coarse %s counters into a percentage", (phase) => {
    const session = toSessionViewModel({ ...overview, status: "RUNNING", phase }, user);
    expect(session.progress).toEqual({ mode: "indeterminate", percent: null });
    expect(session.reviewState).toBe("unavailable");
    expect(session.actions.prepareExport).toBe(false);
  });

  it("unknown status fails closed instead of displaying success", () => {
    const session = toSessionViewModel({ ...overview, status: "FUTURE_STATE" }, user);
    expect(session.resultsAvailable).toBe(false);
    expect(session.progress.percent).toBeNull();
    expect(Object.values(session.actions)).toEqual([false, false, false, false]);
  });

  it("restores an uploaded run and distinguishes retryable failure", () => {
    expect(toSessionViewModel({ ...overview, status: "UPLOADED" }, user).actions.start).toBe(true);
    const session = toSessionViewModel({ ...overview, status: "RETRYABLE_FAILED", task: {
      task_id: "task", run_id: "run-1", status: "RETRYABLE_FAILED", phase: "interrupted",
      processed_count: 0, total_count: 100, error_code: "WORKER_RESTARTED", updated_at: "2026-10-06T00:00:00Z",
    } }, user);
    expect(session.actions.retry).toBe(true);
    expect(session.errorCode).toBe("WORKER_RESTARTED");
  });

  it("uses files only from the matching run", () => {
    const history = { run_id: "other", files: ["other.csv"], status: "COMPLETED",
      created_at: "2026-10-06", record_count: 1, file_count: 1 };
    expect(toSessionViewModel(overview, user, history).title).toBe("Phiên run-1");
    expect(toSessionViewModel(overview, user, { ...history, run_id: "run-1" }).title).toBe("other.csv");
  });

  it("uses capabilities, not a client-side role hierarchy, and denies absent permissions", () => {
    const viewer = { ...user, role: "VIEWER", capabilities: ["read"] as AuthUser["capabilities"] };
    expect(Object.values(toSessionViewModel(overview, viewer).actions)).toEqual([false, false, false, false]);
    expect(can(undefined, "export_data")).toBe(false);
    expect(can({ capabilities: undefined } as unknown as AuthUser, "export_data")).toBe(false);
  });
});
