export type Source = "CRM" | "ERP";
export type Decision = "pending" | "accepted" | "rejected" | "needs_review";
export type Profile = Record<Source, {
  row_count: number;
  accepted_count: number;
  rejected_count: number;
  missing_optional: Record<string, number>;
  validation_issue_count: number;
}>;
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
