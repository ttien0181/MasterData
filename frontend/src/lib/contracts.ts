export type Source = "CRM" | "ERP" | "BILL";
export type Decision = "pending" | "accepted" | "rejected" | "needs_review";
export type SourceProfile = {
  row_count: number;
  accepted_count: number;
  rejected_count: number;
  missing_optional: Record<string, number>;
  validation_issue_count: number;
};
export type Profile = Partial<Record<Source, SourceProfile>>;
export type ImportSummary = { import_id: string; profile: Profile; created_at?: string };
export type RecordSummary = {
  id: string; source: Source; source_record_id: string;
  raw_fields: Record<string, string>; normalized_fields: Record<string, string>;
};
export type Candidate = {
  id: string; engine_id: string; evidence: { field: string; value: string }[];
  decision: Decision; left: RecordSummary; right: RecordSummary;
};
export type DraftField = { value: string; source_record_id: string };

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
  counts: { auto: number; rejected: number; review: number; review_cases: number; manual: number };
};
export type ResolutionRecord = {
  id: string; external_id: string; source_system: string; raw_fields: Record<string, string>;
  normalized_fields?: Record<string, string>; quality_flags?: string[];
};
export type ResolutionProposal = {
  id: string; status: string; revision: number; member_record_ids: string[];
  golden_fields: { member_record_ids: string[]; fields: Record<string, DraftField & { source_system: string }> };
  records?: ResolutionRecord[]; field_options?: Record<string, { source_record_id: string; source_system: string; value: string }[]>;
};
export type ResolutionPair = {
  id: string; score: number; pair_decision: string; triage_status: string; triage_reason: string;
    revision?: number; user_decision?: string; decision_source?: "ALGORITHM" | "STEWARD";
    field_evidence: { field: string; similarity: number; left: string; right: string }[];
  left: ResolutionRecord; right: ResolutionRecord;
};
export type ResolutionReviewCase = {
  id: string; kind: "case" | "USER_DEFERRED"; primary_group?: string; subgroup?: string;
  triage_reason: string; revision?: number; pair_count: number; member_record_ids: string[]; pairs?: ResolutionPair[];
  golden_fields?: ResolutionProposal["golden_fields"];
};
export type ReviewCategory = {
  primary_group: string; subgroup: string; signature_hash: string; pair_count: number;
  unique_cluster_count: number; rule_template_vi: string;
};
export type ResolutionPreview = {
  preview_token: string; selected_count: number; excluded_count: number;
  proposals: ResolutionProposal[];
};

export type ResolutionItem = {
  id: string; bucket: "merge" | "no_merge" | "review"; kind: "proposal" | "pair" | "review" | "deferred";
  origin: "AUTO" | "REVIEW"; member_record_ids: string[]; member_count: number;
  pair_count: number; label: string; reason?: string; score?: number;
  primary_group?: string; subgroup?: string; signature_hash?: string;
  can_merge?: boolean;
};
export type ResolutionItemDetail = ResolutionItem & {
  records: ResolutionRecord[];
  field_options: Record<string, { source_record_id: string; source_system: string; external_id: string; value: string }[]>;
  choices: Record<string, string>; missing_fields: string[];
  field_evidence?: ResolutionPair["field_evidence"];
  merge_blocked_reason?: string | null;
};
export type ResolutionExportReadiness = {
  merge_count: number; pending_review_count: number; incomplete_count: number;
  incomplete_items: { item_id: string; fields: string[] }[]; can_export: boolean;
};
export type ResolutionExportSummary = {
  id: string; row_count: number; merged_group_count: number;
  pending_review_count: number; created_at: string;
};
