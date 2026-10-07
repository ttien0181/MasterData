"use client";

import { Fragment, useEffect, useRef, useState } from "react";
import type { RunListOptions } from "@/lib/api";
import type { ResolutionRunHistory } from "@/lib/contracts";
import { runStatusLabel } from "@/lib/sessionView";
import { ShellIcon } from "./ShellIcons";

type Run = ResolutionRunHistory["items"][number];
type ArchiveFilter = NonNullable<RunListOptions["archived"]>;

export default function SessionsHub({ items, total, nextCursor, loading, error, onPage, onRetry, onOpen, onClose,
  canManage, onRename, onBulk, embedded = false, onExpand }: {
  items: Run[];
  total: number;
  nextCursor: string | null;
  loading: boolean;
  error: string;
  onPage: (cursor: string | null, options: RunListOptions) => void;
  onRetry: (options: RunListOptions) => void;
  onOpen: (runId: string) => void;
  onClose: () => void;
  canManage: boolean;
  onRename: (runId: string, name: string) => Promise<void>;
  onBulk: (operation: "archive" | "restore", runIds: string[]) => Promise<void>;
  embedded?: boolean;
  onExpand?: () => void;
}) {
  const [preview, setPreview] = useState<Run | null>(null);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [archiveFilter, setArchiveFilter] = useState<ArchiveFilter>("active");
  const [cursorStack, setCursorStack] = useState<(string | null)[]>([null]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [renameValue, setRenameValue] = useState("");
  const [actionBusy, setActionBusy] = useState(false);
  const [actionError, setActionError] = useState("");
  const closeRef = useRef<HTMLButtonElement>(null);
  const skippedInitialFilter = useRef(false);
  const filters = { q: search, status: statusFilter, archived: archiveFilter } satisfies RunListOptions;

  useEffect(() => { if (!embedded) closeRef.current?.focus(); }, [embedded]);
  useEffect(() => {
    if (embedded) return;
    function escape(event: KeyboardEvent) { if (event.key === "Escape") onClose(); }
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [embedded, onClose]);
  useEffect(() => {
    if (!skippedInitialFilter.current) { skippedInitialFilter.current = true; return; }
    const timer = window.setTimeout(() => {
      setCursorStack([null]); setSelected(new Set()); setPreview(null); setActionError("");
      onPage(null, filters);
    }, 220);
    return () => window.clearTimeout(timer);
  }, [search, statusFilter, archiveFilter]);
  useEffect(() => {
    setRenameValue(preview?.name || "");
  }, [preview?.run_id, preview?.name]);

  const pageNumber = cursorStack.length;
  const allSelected = items.length > 0 && items.every((run) => selected.has(run.run_id));
  function groupLabel(run: Run) {
    if (["QUEUED", "RUNNING"].includes(run.status)) return "Đang chạy";
    const today = new Date();
    const created = new Date(run.created_at);
    if (created.toDateString() === today.toDateString()) return "Hôm nay";
    today.setDate(today.getDate() - 1);
    return created.toDateString() === today.toDateString() ? "Hôm qua" : "Trước đó";
  }
  const displayItems = [...items].sort((a, b) => Number(["QUEUED", "RUNNING"].includes(b.status)) - Number(["QUEUED", "RUNNING"].includes(a.status)));

  function toggleOne(runId: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(runId)) next.delete(runId); else next.add(runId);
      return next;
    });
  }

  async function bulkAction(operation: "archive" | "restore") {
    if (!selected.size || actionBusy) return;
    setActionBusy(true); setActionError("");
    try {
      await onBulk(operation, [...selected]);
      setSelected(new Set()); setPreview(null);
      onPage(cursorStack[cursorStack.length - 1], filters);
    } catch (cause) { setActionError(cause instanceof Error ? cause.message : "Không cập nhật được phiên."); }
    finally { setActionBusy(false); }
  }

  async function saveName() {
    if (!preview || actionBusy) return;
    setActionBusy(true); setActionError("");
    try {
      await onRename(preview.run_id, renameValue.trim());
      setPreview({ ...preview, name: renameValue.trim() || null });
      onPage(cursorStack[cursorStack.length - 1], filters);
    } catch (cause) { setActionError(cause instanceof Error ? cause.message : "Không lưu được tên phiên."); }
    finally { setActionBusy(false); }
  }

  function goPrevious() {
    if (cursorStack.length <= 1) return;
    const next = cursorStack.slice(0, -1);
    setCursorStack(next); onPage(next[next.length - 1], filters);
  }
  function goNext() {
    if (!nextCursor) return;
    const next = [...cursorStack, nextCursor];
    setCursorStack(next); onPage(nextCursor, filters);
  }

  return <div className={`history-overlay sessions-overlay${embedded ? " is-embedded" : ""}`}
    role={embedded ? "region" : "dialog"} aria-modal={embedded ? undefined : true} aria-labelledby="sessions-heading"
    onMouseDown={(event) => { if (!embedded && event.target === event.currentTarget) onClose(); }}>
    <section className="card sessions-hub">
      <header className="sessions-heading"><div><p className="section-kicker">Không gian làm việc</p><h2 id="sessions-heading">Phiên phân giải</h2><p className="muted-copy">Tìm và quản lý các phiên thuộc tài khoản của bạn.</p></div><button ref={closeRef} type="button" className="dialog-close" aria-label="Đóng danh sách phiên" onClick={onClose}>×</button></header>
      <div className="sessions-filters">
        <label className="sessions-search"><ShellIcon name="search" size={17} /><input aria-label="Tìm phiên" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Tìm theo tên phiên hoặc tên file" /></label>
        <div className="sessions-filter-tabs" role="group" aria-label="Lọc trạng thái phiên">{[
          ["", "Tất cả"], ["QUEUED,RUNNING", "Đang chạy"], ["COMPLETED", "Hoàn tất"], ["FAILED,RETRYABLE_FAILED", "Lỗi"],
        ].map(([value, label]) => <button key={value} type="button" aria-pressed={statusFilter === value} className={statusFilter === value ? "is-active" : ""} onClick={() => setStatusFilter(value)}>{label}</button>)}</div>
        <label className="sessions-archive-filter"><span>Hiển thị</span><select aria-label="Lưu trữ" value={archiveFilter} onChange={(event) => setArchiveFilter(event.target.value as ArchiveFilter)}><option value="active">Chưa lưu trữ</option><option value="archived">Đã lưu trữ</option><option value="all">Tất cả phiên</option></select></label>
      </div>
      <div className="sessions-hub-body">
        <div className="sessions-list" aria-label="Danh sách phiên">
          <div className="sessions-list-summary"><strong>{total.toLocaleString("vi-VN")} phiên</strong>{canManage && items.length > 0 && <label><input type="checkbox" checked={allSelected} onChange={() => setSelected(allSelected ? new Set() : new Set(items.map((run) => run.run_id)))} /> Chọn trang này</label>}</div>
          {selected.size > 0 && canManage && <div className="sessions-bulk-bar"><span>Đã chọn {selected.size}</span>{archiveFilter !== "archived" && <button type="button" className="button outline" disabled={actionBusy || [...selected].some((id) => items.find((run) => run.run_id === id)?.status === "RUNNING" || items.find((run) => run.run_id === id)?.status === "QUEUED")} onClick={() => void bulkAction("archive")}>Lưu trữ</button>}{archiveFilter !== "active" && <button type="button" className="button outline" disabled={actionBusy} onClick={() => void bulkAction("restore")}>Khôi phục</button>}</div>}
          {actionError && <p className="sessions-action-error" role="alert">{actionError}</p>}
          {loading && <p role="status">Đang tải phiên…</p>}
          {error && <div role="alert"><p>{error}</p><button type="button" className="button outline" onClick={() => onRetry(filters)}>Thử lại</button></div>}
          {!loading && !error && items.length === 0 && <p className="muted-copy">Không tìm thấy phiên phù hợp.</p>}
          {!error && displayItems.map((run, index) => <Fragment key={run.run_id}>
            {(index === 0 || groupLabel(displayItems[index - 1]) !== groupLabel(run)) && <h3 className="sessions-date-group">{groupLabel(run)}</h3>}
            <div className={`sessions-row-wrap${["RUNNING", "QUEUED"].includes(run.status) ? " is-running" : ""}${preview?.run_id === run.run_id ? " is-selected" : ""}`}>
            {canManage && <input className="sessions-row-check" type="checkbox" aria-label={`Chọn phiên ${run.name || run.files.join(", ")}`} checked={selected.has(run.run_id)} onChange={() => toggleOne(run.run_id)} />}
            <button type="button" className={`sessions-row ${preview?.run_id === run.run_id ? "selected" : ""}`} onClick={() => setPreview(run)} aria-pressed={preview?.run_id === run.run_id}>
              <span className="sessions-row-title">{run.name || run.files.join(", ") || `Phiên ${run.run_id.slice(0, 8)}`}</span>
              <span className="sessions-row-meta">{new Date(run.created_at).toLocaleString("vi-VN")} · {run.record_count.toLocaleString("vi-VN")} bản ghi · {run.file_count} file</span>
              <span className="sessions-row-statuses"><span className="session-status" data-status={run.status.toLowerCase()}>{runStatusLabel(run.status)}</span>{run.archived_at && <span className="session-status archived">Đã lưu trữ</span>}</span>
            </button>
            <button className="sessions-open-row" type="button" aria-label={`Mở phiên ${run.name || run.files.join(", ")}`} onClick={() => onOpen(run.run_id)}><ShellIcon name="chevron" size={17} /></button>
          </div></Fragment>)}
          {!loading && !error && (cursorStack.length > 1 || nextCursor) && <nav className="sessions-pagination" aria-label="Phân trang phiên"><button type="button" className="button outline" disabled={cursorStack.length <= 1 || loading} onClick={goPrevious}>Trước</button><span>Trang {pageNumber} · {total.toLocaleString("vi-VN")} phiên</span><button type="button" className="button outline" disabled={!nextCursor || loading} onClick={goNext}>Tiếp</button></nav>}
        </div>
        <aside className={`sessions-preview${preview ? " has-selection" : ""}`} aria-label="Xem nhanh phiên">
          {preview ? <><p className="section-kicker">Xem nhanh</p><h3>{preview.name || preview.files.join(", ") || `Phiên ${preview.run_id.slice(0, 8)}`}</h3>
            {canManage && <form className="sessions-rename" onSubmit={(event) => { event.preventDefault(); void saveName(); }}><label htmlFor="sessions-rename-input">Tên phiên</label><div><input id="sessions-rename-input" maxLength={255} value={renameValue} onChange={(event) => setRenameValue(event.target.value)} placeholder="Tên file mặc định" /><button type="submit" className="button outline" disabled={actionBusy}>Lưu</button></div></form>}
            <dl><div><dt>Trạng thái</dt><dd>{runStatusLabel(preview.status)}</dd></div><div><dt>Lưu trữ</dt><dd>{preview.archived_at ? "Đã lưu trữ" : "Đang hoạt động"}</dd></div><div><dt>Tạo lúc</dt><dd>{new Date(preview.created_at).toLocaleString("vi-VN")}</dd></div><div><dt>Bản ghi</dt><dd>{preview.record_count.toLocaleString("vi-VN")}</dd></div><div><dt>Tệp</dt><dd>{preview.file_count}</dd></div></dl><button type="button" className="button primary" onClick={() => onOpen(preview.run_id)}>Mở phiên</button>
          </> : <p className="muted-copy">Chọn một phiên để xem thông tin nhanh.</p>}
        </aside>
      </div>
      {onExpand && <button className="sessions-expand-footer" type="button" onClick={onExpand}>Xem tất cả phiên <ShellIcon name="expand" size={17} /></button>}
    </section>
  </div>;
}
