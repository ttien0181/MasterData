"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { executeResolutionSplit, getResolutionMerges, getResolutionSplitPreview } from "@/lib/api";
import type { ResolutionMergeHistory, ResolutionSplitPreview } from "@/lib/contracts";

const PAGE_SIZE = 25;

export default function SplitHistoryDialog({ runId, exportId, onClose, onCompleted }: {
  runId: string; exportId: string; onClose: () => void; onCompleted: () => Promise<void>;
}) {
  const [offset, setOffset] = useState(0);
  const [history, setHistory] = useState<ResolutionMergeHistory | null>(null);
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [preview, setPreview] = useState<ResolutionSplitPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const requestId = useRef("");

  const load = useCallback(async () => {
    setBusy(true); setError(""); setHistory(null);
    try { setHistory(await getResolutionMerges(runId, exportId, offset, search)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Không tải được nhóm đã gộp"); }
    finally { setBusy(false); }
  }, [runId, exportId, offset, search]);

  useEffect(() => { void load(); }, [load]);

  async function inspect(eventId: string, memberId: string) {
    setBusy(true); setError("");
    try {
      setPreview(await getResolutionSplitPreview(runId, exportId, eventId, memberId));
      requestId.current = crypto.randomUUID();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Không xem trước được lần tách"); }
    finally { setBusy(false); }
  }

  async function execute() {
    if (!preview) return;
    setBusy(true); setError("");
    try {
      await executeResolutionSplit(runId, exportId, preview.event_id, preview.separated.id,
                                   preview.version, requestId.current);
      await onCompleted();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Không tách được bản ghi"); }
    finally { setBusy(false); }
  }

  return <div className="history-overlay" role="dialog" aria-modal="true" aria-labelledby="split-heading">
    <div className="card resolution-item-dialog split-history-dialog">
      <button type="button" className="dialog-close" aria-label="Đóng" onClick={onClose} disabled={busy}>×</button>
      <p className="section-kicker">LỊCH SỬ · TÁCH CHỌN LỌC</p>
      <h2 id="split-heading">{preview ? "Xem trước khi tách" : "Chọn bản ghi cần tách"}</h2>
      {error && <p role="alert" className="split-error">{error} <button type="button" className="button outline" onClick={() => void load()}>Thử lại</button></p>}
      {preview ? <div className="split-preview">
        <p>Tách <b>{preview.separated.external_id}</b> ({preview.separated.name}) thành một bản ghi riêng.</p>
        <p>Nhóm còn lại: <b>{preview.retained.map((row) => row.external_id).join(" · ")}</b>.</p>
        <p>CSV mới dự kiến có <b>{preview.estimated_output_rows.toLocaleString("vi-VN")}</b> dòng. Các nhóm gộp khác được giữ lại; liên kết giữa bản ghi tách và nhóm cũ sẽ bị chặn.</p>
        <div className="split-actions"><button type="button" className="button outline" disabled={busy} onClick={() => setPreview(null)}>Quay lại</button>
          <button type="button" className="button primary" disabled={busy} onClick={() => void execute()}>{busy ? "Đang tách…" : "Xác nhận tách và tạo CSV mới"}</button></div>
      </div> : <>
        <p className="muted-copy">Mỗi nhóm bên dưới là một kết quả gộp trong bản CSV mới nhất. Chọn một bản ghi nguồn để tách khỏi nhóm.</p>
        <form className="split-search" onSubmit={(event) => { event.preventDefault(); setOffset(0); setSearch(searchInput.trim()); }}>
          <input aria-label="Tìm theo tên khách hàng" placeholder="Tìm theo tên khách hàng…" value={searchInput}
            onChange={(event) => setSearchInput(event.target.value)} />
          <button type="submit" className="button outline" disabled={busy}>Tìm</button>
        </form>
        {busy && !history ? <p role="status">Đang tải nhóm gộp…</p> : history?.items.map((event) =>
          <article className="split-group" key={event.event_id}>
            <div><strong>{event.name || "Khách hàng"}</strong><small>{event.member_count} bản ghi</small></div>
            <div className="split-members">{event.members.map((member) => <button type="button" className="button outline" key={member.id}
              disabled={busy} onClick={() => void inspect(event.event_id, member.id)} title={`Tách ${member.external_id}`}>
              Tách {member.source_system} · {member.external_id}
            </button>)}</div>
          </article>)}
        {history?.total === 0 && <p className="muted-copy">Không tìm thấy nhóm gộp phù hợp.</p>}
        {history && <div className="split-actions"><span>{history.total.toLocaleString("vi-VN")} nhóm · trang {Math.floor(offset / PAGE_SIZE) + 1}/{Math.max(1, Math.ceil(history.total / PAGE_SIZE))}</span>
          <button type="button" className="button outline" disabled={busy || offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Trước</button>
          <button type="button" className="button outline" disabled={busy || offset + PAGE_SIZE >= history.total} onClick={() => setOffset(offset + PAGE_SIZE)}>Sau</button></div>}
      </>}
    </div>
  </div>;
}
