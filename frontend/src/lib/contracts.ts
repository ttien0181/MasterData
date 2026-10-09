export type Capability = "read" | "read_audit" | "create_run" | "start_run" | "approve_pair"
  | "reject_pair" | "edit_golden" | "explain" | "export_data" | "approve_d_subgroup"
  | "selective_unmerge" | "edit_policy" | "simulate_policy" | "manage_run";
export type AuthUser = {
  id: string; email: string; csrf_token: string; role: string; capabilities: Capability[];
};
export type UploadState = "empty" | "selected" | "uploading" | "validating" | "uploaded" | "error";
export type RunStatus = "UPLOADED" | "QUEUED" | "RUNNING" | "COMPLETED" | "RETRYABLE_FAILED" | "FAILED";
export type ResolutionTask = {
  task_id: string; run_id: string; status: string; phase: string; processed_count: number;
  total_count: number; error_code: string | null; updated_at: string;
};
export type ResolutionRun = {
  run_id: string; status: string; created_at?: string; record_count: number; file_count: number;
  source_counts: Record<string, number>;
};
export type ResolutionRunHistory = {
  total: number; next_cursor?: string | null; items: { run_id: string; name?: string | null; archived_at?: string | null;
    status: string; phase?: string | null; created_at: string; record_count: number; file_count: number; files: string[] }[];
};
export type UploadSessionStatus = "UPLOADING" | "VALIDATING" | "READY" | "ERROR" | "COMMITTED";
export type UploadSessionFile = {
  file_id: string; position: number; filename: string; expected_size: number; received_size: number;
  status: string; sha256: string | null; row_count: number | null; source_counts: Record<string, number>;
  validation_error: string | null; parts: { part_number: number; byte_size: number; sha256: string }[];
  replacement?: UploadSessionFile | null;
};
export type UploadSession = {
  session_id: string; name: string | null; status: UploadSessionStatus; total_bytes: number;
  committed_run_id: string | null; error_message: string | null; created_at: string; updated_at: string;
  files: UploadSessionFile[];
};
export type ResolutionOverview = {
  run_id: string; status: string; phase: string; processed_count: number; total_count: number;
  record_count: number; file_count: number; source_counts: Record<string, number>; history_count: number;
  candidate_count: number; estimated_entity_count?: number; duplicate_record_count?: number;
  eligible_merge_group_count?: number;
  task?: ResolutionTask | null;
  counts: { auto: number; rejected: number; review: number; review_pairs?: number; review_cases: number; manual: number;
    hard_blocked: number; insufficient: number; group_a: number; group_c: number; group_d: number; separate: number };
};
export type ResolutionAnalytics = {
  run_id: string; metric_version: string;
  decisions: { basis: "initial_classification"; unit: "source_record"; total: number;
    automatic: number; review: number; no_duplicate_detected: number; automatic_percent: number };
  conflicts: { basis: "pending_review_pairs"; unit: "pair"; review_pair_count: number;
    fields: { field: string; count: number }[] };
  quality: { basis: "valid_assessed_fields"; fields: string[];
    sources: { source: string; record_count: number; valid_fields: number; assessed_fields: number;
      expected_fields: number; score_percent: number | null; coverage_percent: number }[] };
};
export type ResolutionRecord = {
  id: string; external_id: string; source_system: string; raw_fields: Record<string, string>;
  normalized_fields?: Record<string, string>; quality_flags?: string[];
};
export type SeparateRecord = {
  id: string; external_id: string; source_system: string; raw_fields: Record<string, string>;
  review_pending: boolean;
};
export type ReviewCategory = {
  primary_group: string; subgroup: string; signature_hash: string; pair_count: number;
  unique_cluster_count: number; rule_template_vi: string;
};
export type ResolutionItem = {
  id: string; bucket: "merge" | "no_merge" | "review" | "review_pairs"; kind: "proposal" | "pair" | "review" | "deferred";
  origin: "AUTO" | "REVIEW"; member_record_ids: string[]; member_count: number;
  pair_count: number; label: string; reason?: string; score?: number;
  primary_group?: string; subgroup?: string; signature_hash?: string;
  can_merge?: boolean; status?: string;
  revision?: number; record_labels?: string[]; affected_record_count?: number;
};
export type ResolutionItemDetail = ResolutionItem & {
  records: ResolutionRecord[];
  field_options: Record<string, { source_record_id: string; source_system: string; external_id: string; value: string }[]>;
  choices: Record<string, string>; missing_fields: string[];
  field_evidence?: { field: string; similarity: number; left: string; right: string }[];
  pairs?: { id: string; left_external_id: string; right_external_id: string;
    primary_group?: string | null; subgroup?: string | null; score: number;
    decision: string; reason: string;
    field_evidence: { field: string; state?: string; provenance?: string;
      similarity: number; left: string; right: string; normalization_tags?: string[] }[] }[];
  merge_blocked_reason?: string | null;
};
export type ResolutionExportReadiness = {
  merge_count: number; pending_review_count: number; staged_conflict_count: number; incomplete_count: number;
  incomplete_items: { item_id: string; fields: string[] }[];
  blockers: { item_id: string; kind: string; label: string; message: string; fields?: string[] }[];
  can_export: boolean;
};
export type ResolutionExportSummary = {
  id: string; row_count: number; merged_group_count: number;
  pending_review_count: number; created_at: string; parent_export_id?: string | null;
};
export type ResolutionAuditEvent = {
  id: string; run_id: string; actor_user_id: string; pair_id: string | null; proposal_id: string | null;
  action: string; details: Record<string, unknown>; created_at: string;
};
export type ResolutionAuditPage = { total: number; items: ResolutionAuditEvent[] };
export type ResolutionMergeHistory = {
  total: number; items: { event_id: string; proposal_id: string; name: string; member_count: number;
    members: { id: string; external_id: string; source_system: string; name: string }[] }[];
};
export type ResolutionSplitPreview = {
  version: string; source_export_id: string; event_id: string;
  separated: { id: string; external_id: string; name: string };
  retained: { id: string; external_id: string; name: string }[];
  estimated_output_rows: number;
};
export type ResolutionPreview = {
  preview_id: string; status: string; eligible_count: number; review_required_count: number;
  separate_record_count: number; estimated_output_rows: number; policy_version: string; expires_at: string;
};
