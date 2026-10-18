"use client";

import { type FormEvent, useEffect, useState } from "react";
import { getResolutionAudit } from "@/lib/api";
import type { ResolutionAuditEvent } from "@/lib/contracts";

const PAGE_SIZE = 25;
const ACTION_LABELS: Record<string, string> = {
  SESSION_CREATED: "Tạo đợt xử lý",
  SESSION_RENAMED: "Đổi tên đợt xử lý",
  SESSION_ARCHIVED: "Lưu trữ đợt xử lý",
  SESSION_RESTORED: "Khôi phục đợt xử lý",
  RUN_STARTED: "Bắt đầu xử lý",
  RUN_RETRIED: "Chạy lại đợt xử lý",
  CSV_EXPORTED: "Xuất CSV",
  PREVIEW_EXECUTED: "Xác nhận bản xem trước",
  PAIRS_STAGED_FOR_MERGE: "Đưa các cặp đã chọn sang Chờ gộp",
  PAIR_UNSTAGED_FOR_MERGE: "Đưa cặp trở lại Cặp cần xem xét",
  PAIR_REJECTED: "Từ chối ghép cặp",
  PAIR_ACCEPTED: "Xác nhận gộp cặp nghi trùng",
  REVIEW_CASE_APPROVED: "Duyệt trường hợp cần xem xét",
  DEFERRED_PROPOSAL_APPROVED: "Duyệt đề xuất",
  REVIEW_TO_MERGE: "Chuyển sang Chờ gộp",
  REVIEW_STAGED_CONFLICT: "Đưa nhóm xung đột sang Chờ gộp",
  STAGED_CONFLICT_SPLIT: "Tách nhóm xung đột",
  GOLDEN_RECORD_OVERRIDE: "Chỉnh giá trị hợp nhất",
  PROPOSAL_SPLIT: "Tách đề xuất",
  ENTITY_SPLIT: "Tách dòng thông tin đã gộp",
};

function actionLabel(action: string) {
  return ACTION_LABELS[action] ?? "Thao tác khác";
}

function formatTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("vi-VN");
}

function AuditRow({ event }: { event: ResolutionAuditEvent }) {
  return <article className="audit-event">
    <div className="audit-event-marker" aria-hidden="true" />
    <div className="audit-event-main">
      <div className="audit-event-heading">
        <div><strong>{actionLabel(event.action)}</strong><span>{event.action}</span></div>
        <time dateTime={event.created_at}>{formatTime(event.created_at)}</time>
      </div>
      <p className="audit-event-actor">Tài khoản <code title={event.actor_user_id}>{event.actor_user_id.slice(0, 8)}</code></p>
      {(event.pair_id || event.proposal_id) && <p className="audit-event-scope">
        {event.pair_id && <>Cặp <code title={event.pair_id}>{event.pair_id.slice(0, 12)}</code></>}
        {event.pair_id && event.proposal_id && " · "}
        {event.proposal_id && <>Đề xuất <code title={event.proposal_id}>{event.proposal_id.slice(0, 12)}</code></>}
      </p>}
      <details className="audit-event-details">
        <summary>Chi tiết sự kiện</summary>
        <pre>{JSON.stringify(event.details, null, 2)}</pre>
      </details>
    </div>
  </article>;
}

export default function AuditTrail({ runId }: { runId: string }) {
  const [actionInput, setActionInput] = useState("");
  const [pairInput, setPairInput] = useState("");
  const [proposalInput, setProposalInput] = useState("");
  const [filters, setFilters] = useState<{ action?: string; pair_id?: string; proposal_id?: string }>({});
  const [offset, setOffset] = useState(0);
  const [events, setEvents] = useState<ResolutionAuditEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    let current = true;
    setLoading(true);
    setError("");
    getResolutionAudit(runId, { ...filters, offset, limit: PAGE_SIZE })
      .then((result) => {
        if (!current) return;
        setEvents(result.items);
        setTotal(result.total);
      })
      .catch((reason: unknown) => {
        if (current) setError(reason instanceof Error ? reason.message : "Không tải được lịch sử thao tác của đợt xử lý.");
      })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [filters, offset, retry, runId]);

  function applyFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setOffset(0);
    setFilters({ action: actionInput.trim(), pair_id: pairInput.trim(), proposal_id: proposalInput.trim() });
  }

  function clearFilters() {
    setActionInput("");
    setPairInput("");
    setProposalInput("");
    setOffset(0);
    setFilters({});
  }

  return <section className="audit-trail" aria-label="Lịch sử thao tác">
    <div className="audit-trail-intro">
      <div><p className="section-kicker">LỊCH SỬ THAO TÁC</p><h2>Lịch sử thao tác</h2>
        <p>Các thao tác đã được ghi nhận trong đợt xử lý này. Bạn có thể lọc để tìm thao tác cần xem.</p></div>
      <strong className="audit-total">{total.toLocaleString("vi-VN")} sự kiện</strong>
    </div>

    <form className="audit-filters card" onSubmit={applyFilters}>
      <label>Loại thao tác<input value={actionInput} onChange={(event) => setActionInput(event.target.value)}
        placeholder="Ví dụ: RUN_STARTED" /></label>
      <label>Mã cặp<input value={pairInput} onChange={(event) => setPairInput(event.target.value)} placeholder="Không bắt buộc" /></label>
      <label>Mã đề xuất<input value={proposalInput} onChange={(event) => setProposalInput(event.target.value)} placeholder="Không bắt buộc" /></label>
      <div className="audit-filter-actions"><button className="button primary" type="submit">Lọc</button>
        <button className="button outline" type="button" onClick={clearFilters}>Xóa lọc</button></div>
    </form>

    {loading ? <div className="card audit-state" role="status">Đang tải lịch sử thao tác…</div>
      : error ? <div className="card audit-state" role="alert"><p>{error}</p>
        <button type="button" className="button outline" onClick={() => setRetry((value) => value + 1)}>Thử lại</button></div>
        : events.length ? <div className="audit-event-list">{events.map((item) => <AuditRow key={item.id} event={item} />)}</div>
          : <div className="card audit-state"><strong>Chưa có sự kiện phù hợp</strong><p>Thử xóa bộ lọc hoặc xem lại sau khi có thao tác được ghi nhận.</p></div>}

    {!loading && !error && total > 0 && <nav className="audit-pagination" aria-label="Phân trang lịch sử thao tác">
      <span>{(offset + 1).toLocaleString("vi-VN")}–{Math.min(offset + events.length, total).toLocaleString("vi-VN")} / {total.toLocaleString("vi-VN")}</span>
      <button type="button" className="button outline" disabled={offset === 0}
        onClick={() => setOffset((value) => Math.max(0, value - PAGE_SIZE))}>Trước</button>
      <button type="button" className="button outline" disabled={offset + PAGE_SIZE >= total}
        onClick={() => setOffset((value) => value + PAGE_SIZE)}>Tiếp</button>
    </nav>}
  </section>;
}
