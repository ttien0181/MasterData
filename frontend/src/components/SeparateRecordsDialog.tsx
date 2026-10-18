"use client";

import { useEffect, useRef, useState } from "react";
import { getSeparateRecords } from "@/lib/api";
import type { SeparateRecord } from "@/lib/contracts";

const PAGE_SIZE = 25;

export default function SeparateRecordsDialog({ runId, onClose }: { runId: string; onClose: () => void }) {
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [items, setItems] = useState<SeparateRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const closeRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeRef.current?.focus();
    const keepFocus = (event: KeyboardEvent) => {
      if (event.key !== "Tab") return;
      const controls = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>("button:not([disabled]), input:not([disabled]), summary") ?? []);
      const first = controls[0]; const last = controls[controls.length - 1];
      if (!first || !last) { event.preventDefault(); return; }
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    window.addEventListener("keydown", keepFocus);
    return () => { window.removeEventListener("keydown", keepFocus); opener?.focus(); };
  }, []);
  useEffect(() => {
    const timer = window.setTimeout(() => { setOffset(0); setSearch(query.trim()); }, 250);
    return () => window.clearTimeout(timer);
  }, [query]);
  useEffect(() => {
    let cancelled = false;
    setLoading(true); setError("");
    void getSeparateRecords(runId, offset, search).then((result) => {
      if (cancelled) return;
      setItems(result.items); setTotal(result.total); setLoading(false);
    }).catch((cause: unknown) => {
      if (cancelled) return;
      setError(cause instanceof Error ? cause.message : "Không tải được dòng thông tin"); setLoading(false);
    });
    return () => { cancelled = true; };
  }, [runId, offset, search]);
  useEffect(() => {
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [onClose]);

  return <div className="history-overlay" role="dialog" aria-modal="true" aria-labelledby="separate-heading" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <div ref={dialogRef} className="card separate-records-dialog">
      <div className="separate-dialog-head"><div><p className="section-kicker">DÒNG THÔNG TIN ĐẦU VÀO</p><h2 id="separate-heading">Dự kiến giữ riêng</h2><p>Các dòng thông tin dự kiến được giữ riêng trong file kết quả. Danh sách có thể thay đổi sau khi bạn duyệt các cặp nghi trùng.</p></div><button ref={closeRef} type="button" className="dialog-close" aria-label="Đóng" onClick={onClose}>×</button></div>
      <label className="separate-search">Tìm dòng thông tin<input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Tên, mã dòng thông tin hoặc nguồn" /></label>
      <div className="separate-list" aria-busy={loading}>
        {loading ? <p role="status">Đang tải dòng thông tin…</p> : error ? <p role="alert">{error}</p> : items.length ? items.map((item) => <details key={item.id} className="separate-record"><summary><strong>{item.raw_fields.name || item.external_id}</strong><span>{item.source_system} · {item.external_id}</span><small>{item.review_pending ? "Còn cần xem xét" : "Giữ riêng"}</small></summary><dl>{Object.entries(item.raw_fields).map(([field, value]) => <div key={field}><dt>{field}</dt><dd>{value || "—"}</dd></div>)}</dl></details>) : <p>Không tìm thấy dòng thông tin phù hợp.</p>}
      </div>
      <div className="separate-pagination"><span>{total ? `${offset + 1}–${Math.min(offset + PAGE_SIZE, total)} / ${total}` : "0 dòng thông tin"}</span><button className="button outline" type="button" disabled={loading || offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>← Trước</button><button className="button outline" type="button" disabled={loading || offset + PAGE_SIZE >= total} onClick={() => setOffset(offset + PAGE_SIZE)}>Sau →</button></div>
    </div>
  </div>;
}
