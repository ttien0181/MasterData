import { useState } from "react";
import type { ResolutionRunHistory } from "@/lib/contracts";
import { runPhaseLabel, runStatusLabel } from "@/lib/sessionView";

type RunSummary = ResolutionRunHistory["items"][number];

export default function SessionStatusBar({ runStatus, totalCount, canCreate, activeRuns, activeTotal,
  activeStale, onOpenRun }: {
  runStatus?: string;
  totalCount: number;
  canCreate: boolean;
  activeRuns: RunSummary[];
  activeTotal: number;
  activeStale: boolean;
  onOpenRun: (runId: string) => void;
}) {
  const [detailsOpen, setDetailsOpen] = useState(false);

  return <div className="sidebar-session">
    <div className="status-current-run">
      <span className="session-overline">Trạng thái đợt xử lý đang xem</span>
      <strong>{runStatus ? runStatusLabel(runStatus) : "Chưa có lần nạp dữ liệu"}</strong>
      <small>{totalCount ? `${totalCount.toLocaleString("vi-VN")} dòng thông tin đầu vào` : canCreate ? "Nạp CSV để bắt đầu" : "Tài khoản đang ở chế độ chỉ xem"}</small>
    </div>
    <div className="status-active-runs">
      <button type="button" className={`status-active-trigger${activeStale ? " is-stale" : ""}`}
        aria-label={`${activeTotal ? `${activeTotal.toLocaleString("vi-VN")} đợt xử lý đang xử lý` : "Không có đợt xử lý đang xử lý"}${activeStale ? ". Chưa cập nhật trạng thái mới nhất" : ""}`}
        aria-expanded={detailsOpen}
        aria-controls="active-run-details" onClick={() => setDetailsOpen((open) => !open)}>
        <i aria-hidden="true" className={`${activeTotal ? "is-running" : "is-idle"}${activeStale ? " is-stale" : ""}`} />
        {activeTotal ? `${activeTotal.toLocaleString("vi-VN")} đợt xử lý đang xử lý` : "Không có đợt xử lý đang xử lý"}
        <span aria-hidden="true">{detailsOpen ? "⌄" : "⌃"}</span>
      </button>
      <div id="active-run-details" className="status-active-panel" role="region" aria-label="Các đợt xử lý đang xử lý" hidden={!detailsOpen}>
        {activeStale && <p className="status-active-stale" role="status">Chưa cập nhật được trạng thái mới nhất.</p>}
        {activeRuns.length > 0 ? <ul>
          {activeRuns.map((run) => <li key={run.run_id}>
            <button type="button" onClick={() => { setDetailsOpen(false); onOpenRun(run.run_id); }}>
              <strong>{run.name || run.files.join(", ") || `Đợt xử lý ${run.run_id.slice(0, 8)}`}</strong>
              <span>{runStatusLabel(run.status)} · {runPhaseLabel(run.phase)}</span>
              <small>{run.record_count.toLocaleString("vi-VN")} dòng thông tin · {run.file_count} file</small>
            </button>
          </li>)}
        </ul> : <p>{activeTotal ? "Đang tải danh sách đợt xử lý…" : "Các đợt xử lý đã xử lý xong sẽ không xuất hiện ở đây."}</p>}
        {activeTotal > activeRuns.length && <small className="status-active-more">Đang hiển thị {activeRuns.length} trên {activeTotal} đợt xử lý.</small>}
      </div>
    </div>
  </div>;
}
