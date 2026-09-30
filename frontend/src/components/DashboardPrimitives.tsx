import type { ReactNode } from "react";
import type { Decision, Source } from "@/lib/contracts";
import type { Stage } from "./AppShell";

const pageCopy: Record<Stage, { title: string; description: string }> = {
  overview: { title: "Tổng quan dữ liệu", description: "Theo dõi các đề xuất phân giải và hoạt động gần đây." },
  import: { title: "Nạp dữ liệu", description: "Tải một hoặc nhiều file để tìm bản ghi trùng trên toàn bộ dữ liệu." },
  auto: { title: "Gộp", description: "Chọn giá trị cho các nhóm sẽ gộp, rồi xuất CSV cuối cùng cho toàn bộ dữ liệu." },
  rejected: { title: "Không gộp", description: "Xem các cặp được xác định là không cùng khách hàng." },
  review: { title: "Cần review", description: "Kiểm tra các trường hợp chưa đủ bằng chứng hoặc có rủi ro cụm." },
  history: { title: "Lịch sử", description: "Tải lại các bản CSV đã xuất trong lần nạp dữ liệu này." },
};

export function PageHeader({ stage }: { stage: Stage }) {
  const copy = pageCopy[stage];
  return <div className="page-header">
    <div>
      <h1 className="page-title">{copy.title}</h1>
      <p className="page-desc">{copy.description}</p>
    </div>
  </div>;
}

export function KpiCard({ icon, value, label, detail, tone = "neutral" }: {
  icon: ReactNode; value: string | number; label: string; detail: string; tone?: "neutral" | "up";
}) {
  return <div className="card stat-card">
    <div className="stat-icon">{icon}</div>
    <div className="stat-value">{value}</div>
    <div className="stat-label">{label}</div>
    <div className={`stat-change ${tone}`}>{detail}</div>
  </div>;
}

export function SourceBadge({ source }: { source: Source }) {
  return <span className={`source-tag src-badge ${source.toLowerCase()}`}>{source}</span>;
}

const decisionLabel: Record<Decision, string> = {
  pending: "Chờ duyệt",
  accepted: "Đã nhận",
  rejected: "Từ chối",
  needs_review: "Xem lại",
};

export function StatusBadge({ decision }: { decision: Decision }) {
  return <span className={`state-pill q-status ${decision}`}>{decisionLabel[decision]}</span>;
}

export function FileDropzone({ source, file, onChange, orderLabel, title, hint }: {
  source: Source; file: File | null; onChange: (file: File | null) => void;
  orderLabel: string; title: string; hint: string;
}) {
  return <label className={`upload-card file-card ${file ? "filled" : ""}`}>
    <div className="upload-icon">{file ? "✓" : "CSV"}</div>
    <SourceBadge source={source} />
    <div className="upload-label file-hint">{orderLabel}</div>
    <div className="upload-title">{title}</div>
    <div className="upload-desc">{hint}</div>
    {file ? <div className="upload-file file-name"><span className="dot" /><span>{file.name} ({(file.size / 1024).toFixed(1)} KB)</span></div>
      : <div className="upload-empty file-action"><span>Chọn tệp CSV...</span><span>＋</span></div>}
    <input type="file" accept=".csv,text/csv" aria-label={`Chọn file ${source}`} onChange={(event) => onChange(event.target.files?.[0] ?? null)} />
  </label>;
}
