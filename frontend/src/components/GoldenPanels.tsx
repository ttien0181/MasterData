import type { Candidate, DraftField, RecordSummary } from "@/lib/contracts";
import { compareField, fields, labels } from "@/lib/comparison";
import { SourceBadge } from "./DashboardPrimitives";

export function FieldSourcePicker({ candidate, draft, requiredFields, onChoose }: {
  candidate: Candidate; draft: Record<string, DraftField>; requiredFields: readonly string[];
  onChoose: (field: string, source: RecordSummary) => void;
}) {
  return <div className="card golden-chooser">
    <div className="chooser-head golden-section-head">
      <div><b>Chọn giá trị theo trường</b><span>Chọn một nguồn cho mỗi trường có dữ liệu.</span></div>
      <span className="completion-count completion">{requiredFields.filter((field) => draft[field]).length}/{requiredFields.length} đã chọn</span>
    </div>
    {fields.map((field) => {
      const comparison = compareField(field, candidate);
      const required = requiredFields.includes(field);
      return <div className="field-group choice-field" key={field}>
        <div className="field-group-head choice-field-head"><b>{labels[field]}</b><span className={`field-context status ${comparison.kind}`}>{required ? comparison.label : "Không có dữ liệu"}</span></div>
        <div className="radio-group source-choices">{[candidate.left, candidate.right].map((record) => {
          const value = record.raw_fields[field];
          const chosen = draft[field]?.source_record_id === record.id;
          return <button type="button" className={`radio-option source-choice ${chosen ? "selected chosen" : ""}`} key={record.id} disabled={!value} aria-pressed={chosen} aria-label={`Chọn ${labels[field]} từ ${record.source}`} onClick={() => onChoose(field, record)}>
            <div className="opt-top"><span className="radio-dot" /><SourceBadge source={record.source} /></div>
            <strong>{value || "Nguồn không có giá trị"}</strong>
            <small><span>{record.source_record_id}</span>{chosen && <span className="selected-label chosen-mark">✓ Đang chọn</span>}</small>
          </button>;
        })}</div>
      </div>;
    })}
  </div>;
}

export function GoldenRecordPreview({ candidate, draft, requiredFields, saved }: {
  candidate: Candidate; draft: Record<string, DraftField>; requiredFields: readonly string[]; saved: boolean;
}) {
  const selectedCount = requiredFields.filter((field) => draft[field]).length;
  return <aside className="card preview-card golden-preview" aria-label="Bản xem trước Golden Record">
    <div className="preview-head">
      <div className="preview-title"><h3>Golden Record</h3></div>
      <span className={`preview-badge preview-state ${saved ? "saved" : ""}`}>{saved ? "ĐÃ LƯU" : "CHƯA LƯU"}</span>
    </div>
    <div className="preview-progress" role="progressbar" aria-label="Tiến độ chọn trường" aria-valuemin={0} aria-valuemax={requiredFields.length || 1} aria-valuenow={selectedCount}>
      <span style={{ width: `${requiredFields.length ? (selectedCount / requiredFields.length) * 100 : 0}%` }} />
    </div>
    <div className="preview-body preview-fields">{fields.map((field) => {
      const choice = draft[field];
      const source = choice ? [candidate.left, candidate.right].find((record) => record.id === choice.source_record_id) : null;
      const hasValue = Boolean(candidate.left.raw_fields[field] || candidate.right.raw_fields[field]);
      return <div className={`preview-row preview-field ${choice ? "filled" : ""}`} key={field}>
        <div className="preview-row-left"><span className="preview-label">{labels[field]}</span><b className={`preview-val ${!choice && !hasValue ? "empty" : ""}`}>{choice?.value ?? (hasValue ? "Chọn giá trị từ nguồn" : "Không có dữ liệu")}</b></div>
        <small className={`preview-origin origin ${source ? source.source.toLowerCase() : ""}`}>{source ? `${source.source} · ${source.source_record_id}` : hasValue ? "Chưa chọn nguồn" : "Bỏ trống"}</small>
      </div>;
    })}</div>
    <div className="preview-note"><span>◉</span><p>Đây là bản nháp. Lưu sẽ ghi audit; dữ liệu nguồn vẫn nguyên vẹn.</p></div>
  </aside>;
}
