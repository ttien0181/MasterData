import type { Candidate, Decision } from "@/lib/contracts";
import { compareField, fields, highlightDifferences, labels } from "@/lib/comparison";
import { SourceBadge, StatusBadge } from "./DashboardPrimitives";

export function ReviewQueue({ candidates, selected, pendingCount, acceptedCount, onSelect }: {
  candidates: Candidate[]; selected: string | null; pendingCount: number; acceptedCount: number;
  onSelect: (id: string) => void;
}) {
  return <section className="card queue-panel" aria-label="Hàng đợi duyệt ứng viên">
    <div className="queue-head">
      <div className="queue-heading-line"><div className="queue-title">Hàng đợi duyệt</div><span className="badge badge-blue">{candidates.length.toString().padStart(2, "0")}</span></div>
      <div className="queue-subtitle">{pendingCount} cần duyệt · {acceptedCount} đã chấp nhận</div>
    </div>
    <div className="queue-toolbar queue-meta"><span>Ứng viên từ phiên nạp hiện tại</span><span>{candidates.length} cặp</span></div>
    <div className="queue-list candidate-list">
      {candidates.map((item, index) => <button type="button" className={`queue-item candidate-item ${selected === item.id ? "selected" : ""}`} key={item.id} onClick={() => onSelect(item.id)}>
        <span className="q-index candidate-index">{String(index + 1).padStart(2, "0")}</span>
        <div className="q-info candidate-copy">
          <b>{item.left.raw_fields.name}</b>
          <span>{item.left.source}·{item.left.source_record_id} <i>↔</i> {item.right.source}·{item.right.source_record_id}</span>
        </div>
        <StatusBadge decision={item.decision} />
      </button>)}
      {!candidates.length && <div className="empty-state"><span>∅</span><b>Chưa có gợi ý trùng lặp</b><p>Hãy kiểm tra các file đã nạp hoặc thử dữ liệu có email, số điện thoại hay tên trùng khớp.</p></div>}
    </div>
  </section>;
}

function RecordCell({ field, candidate, side, kind }: { field: string; candidate: Candidate; side: "left" | "right"; kind: string }) {
  const record = candidate[side];
  const other = candidate[side === "left" ? "right" : "left"];
  const value = record.raw_fields[field];
  const otherValue = other.raw_fields[field];
  return <td className="record-card">
    <div className={`data-row ${kind}`}>
      <b>{value ? (otherValue && value !== otherValue ? highlightDifferences(value, otherValue) : value) : <em>Chưa có dữ liệu</em>}</b>
      {value && value !== record.normalized_fields[field] && <small className="normalized-value">Chuẩn hoá: {record.normalized_fields[field]}</small>}
    </div>
  </td>;
}

export function CompareTable({ candidate }: { candidate: Candidate }) {
  return <table className="diff-table">
    <thead><tr>
      <th style={{ width: "160px" }}>Trường</th>
      {[candidate.left, candidate.right].map((record) => <th key={record.id}><SourceBadge source={record.source} /><code>{record.source_record_id}</code></th>)}
    </tr></thead>
    <tbody>{fields.map((field) => {
      const comparison = compareField(field, candidate);
      const isConflict = comparison.kind === "different" || comparison.kind === "missing";
      return <tr className={isConflict ? "conflict" : ""} key={field}>
        <td className="field-name">{labels[field]}<span className={`field-tag ${comparison.kind === "match" ? "match" : isConflict ? "diff" : "missing"}`}>
          {comparison.kind === "match" ? "✓ khớp" : isConflict ? "⚠ khác" : comparison.label}
        </span></td>
        <RecordCell field={field} candidate={candidate} side="left" kind={comparison.kind} />
        <RecordCell field={field} candidate={candidate} side="right" kind={comparison.kind} />
      </tr>;
    })}</tbody>
  </table>;
}

export function ReviewDetail({ candidate, busy, onDecision }: {
  candidate: Candidate; busy: boolean;
  onDecision: (decision: Exclude<Decision, "pending">) => Promise<void>;
}) {
  const reviewFieldCount = fields.filter((field) => ["different", "missing"].includes(compareField(field, candidate).kind)).length;
  return <div className="review-detail-stack">
    <section className="card detail-panel detail-card" aria-label="Chi tiết so sánh hồ sơ">
      <div className="detail-head detail-top">
        <div><h2 className="detail-title">So sánh hồ sơ</h2><div className="detail-id step-kicker">CANDIDATE · {candidate.id.slice(0, 8).toUpperCase()}</div></div>
        <span className="engine-badge engine-label">◈ EXACT-SIGNAL PROTOTYPE</span>
      </div>
      <div className="signal-bar evidence-row">
        <div className="signal-summary"><span className="signal-total signal-count">{candidate.evidence.length.toString().padStart(2, "0")}</span><span className="signal-summary-label signal-label">tín hiệu khớp chính xác</span></div>
        <div className="signal-fields">{candidate.evidence.map((signal) => <span className="signal-chip evidence-chip" key={signal.field}>{labels[signal.field] ?? signal.field}</span>)}</div>
        <span className="needs-review-badge review-count">{reviewFieldCount} trường cần kiểm tra</span>
      </div>
      <div className="compare-legend">
        <span><i className="legend-swatch exact" /> Tín hiệu matcher dùng</span>
        <span><i className="legend-swatch differs" /> Giá trị khác hoặc thiếu</span>
        <span className="legend-note">Màu sắc hỗ trợ rà soát trực quan</span>
      </div>
      <CompareTable candidate={candidate} />
    </section>
    <div className="decision-footer decision-row" role="group" aria-label="Quyết định của Data Steward">
      <div className="decision-actions">
        <button className="button subtle btn btn-danger btn-sm" disabled={busy || (candidate.decision !== "pending" && candidate.decision !== "needs_review")} onClick={() => void onDecision("rejected")}>Từ chối</button>
        <button className="button outline btn btn-secondary btn-sm" disabled={busy || candidate.decision !== "pending"} onClick={() => void onDecision("needs_review")}>Để xem lại</button>
        <button className="button primary btn btn-primary btn-sm" disabled={busy || candidate.decision === "accepted" || candidate.decision === "rejected"} onClick={() => void onDecision("accepted")}>Chấp nhận cặp →</button>
      </div>
    </div>
  </div>;
}
