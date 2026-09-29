import type { ReactNode } from "react";
import type { Decision, Source } from "@/lib/contracts";
import type { Stage } from "./AppShell";

const pageCopy: Record<Stage, { title: string; description: string; number: string }> = {
  import: { title: "Nạp dữ liệu", description: "Kết nối hai nguồn dữ liệu để tìm những hồ sơ có thể thuộc cùng một khách hàng.", number: "01" },
  review: { title: "Rà soát hồ sơ", description: "Kiểm tra bằng chứng, quyết định từng cặp và giữ quyền kiểm soát trong tay Data Steward.", number: "02" },
  golden: { title: "Tạo bản ghi chuẩn", description: "Chọn giá trị tốt nhất từ hồ sơ nguồn. Mỗi trường đều giữ lại xuất xứ.", number: "03" },
};

export function PageHeader({ stage }: { stage: Stage }) {
  const copy = pageCopy[stage];
  return <div className="page-header">
    <div>
      <p className="page-overline">CUSTOMER DATA OPERATIONS <span>/</span> {copy.number}</p>
      <h1 className="page-title">{copy.title}</h1>
      <p className="page-desc">{copy.description}</p>
    </div>
    <span className="page-stage-label">BƯỚC {copy.number} / 03</span>
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
