export type ResolutionRun = {
  run_id: string; status: string; created_at?: string; record_count: number; file_count: number;
  source_counts: Record<string, number>;
};
export type ResolutionRunHistory = {
  total: number; items: { run_id: string; status: string; created_at: string; record_count: number;
    file_count: number; files: string[] }[];
};
export type ResolutionOverview = {
  run_id: string; status: string; phase: string; processed_count: number; total_count: number;
  record_count: number; file_count: number; source_counts: Record<string, number>; history_count: number;
  candidate_count: number;
  counts: { auto: number; rejected: number; review: number; review_cases: number; manual: number;
    hard_blocked: number; insufficient: number; group_a: number; group_c: number; group_d: number; separate: number };
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
  id: string; bucket: "merge" | "no_merge" | "review"; kind: "proposal" | "pair" | "review" | "deferred";
  origin: "AUTO" | "REVIEW"; member_record_ids: string[]; member_count: number;
  pair_count: number; label: string; reason?: string; score?: number;
  primary_group?: string; subgroup?: string; signature_hash?: string;
  can_merge?: boolean; status?: string;
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
