"use client";

import { useEffect, useRef, useState } from "react";
import { decideReviewPair, getPairCategories, getResolutionItem, getResolutionItems, type PairCategory } from "@/lib/api";
import type { AuthUser, ResolutionItem, ResolutionItemDetail } from "@/lib/contracts";
import { can } from "@/lib/permissions";
import { differenceSegments } from "./resolutionFields";

const groupNames: Record<string, string> = {
  A: "Nhiều thông tin trùng khớp", C: "Thiếu thông tin xác nhận", D: "Thông tin mâu thuẫn",
  SHARED_VALUE: "Thông tin liên hệ dùng chung", VIETNAM_VARIANT: "Khác cách viết",
  SPARSE_EVIDENCE: "Thiếu dữ liệu", SINGLE_MAJOR_CONFLICT: "Có thông tin quan trọng khác nhau",
  CONSTRAINT_OR_CLUSTER_RISK: "Liên kết cần kiểm tra", MODEL_DISAGREEMENT: "Dấu hiệu chưa thống nhất",
};
const subgroupNames: Record<string, string> = {
  A1_NAME_PHONE: "Tên và số điện thoại giống nhau", A2_NAME_EMAIL: "Tên và email giống nhau",
  C_NAME_ANCHOR_WEAK: "Tên chỉ giống một phần", C_NAME_ANCHOR_MISSING: "Thiếu tên đáng tin cậy",
  D2_NAME_CONFLICT: "Tên khác nhau rõ rệt", PHONE: "Số điện thoại dùng chung", EMAIL: "Email dùng chung",
  SHARED_PHONE: "Số điện thoại dùng chung", GENERIC_EMAIL: "Email dùng chung",
  TEXT_VARIANT: "Khác cách viết", CLUSTER_GUARD: "Kiểm tra bản ghi liên quan",
  TRANSITIVE_RISK: "Liên kết trong nhóm chưa thống nhất", NO_VALID_IDENTITY_ANCHOR: "Thiếu thông tin nhận diện",
  AMBIGUOUS_FIELD_EVIDENCE: "Thông tin đối chiếu chưa rõ", WEAK_FIELD_EVIDENCE: "Ít thông tin trùng khớp",
};
const groupLabel = (value?: string) => groupNames[value || ""] || "Cần kiểm tra thêm";
const subLabel = (value?: string) => subgroupNames[value || ""] || "Đối chiếu thông tin nguồn";
const fields = { name: "Tên khách hàng", phone: "Điện thoại", email: "Email", address: "Địa chỉ" };
const PAGE_SIZE = 25;

export default function PairReviewFlow({ user, runId, rejected, onChanged, notify }: {
  user: AuthUser; runId: string; rejected: boolean; onChanged: () => Promise<unknown>; notify: (text: string) => void;
}) {
  const bucket = rejected ? "no_merge" : "review_pairs";
  const [categories, setCategories] = useState<PairCategory[]>([]);
  const [group, setGroup] = useState("");
  const [subgroup, setSubgroup] = useState("");
  const [offset, setOffset] = useState(0);
  const [pageInput, setPageInput] = useState("1");
  const [reload, setReload] = useState(0);
  const [result, setResult] = useState<{ key: string; total: number; items: ResolutionItem[] } | null>(null);
  const [error, setError] = useState("");
  const [active, setActive] = useState<ResolutionItemDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [decisionError, setDecisionError] = useState("");
  const opener = useRef<HTMLButtonElement | null>(null);
  const dialog = useRef<HTMLDivElement | null>(null);
  const queryKey = JSON.stringify([runId, bucket, group, subgroup, offset, reload]);
  const loading = result?.key !== queryKey && !error;
  const total = result?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  useEffect(() => {
    let current = true;
    setError("");
    Promise.all([
      getResolutionItems(runId, bucket, { limit: PAGE_SIZE, offset,
        ...(group ? { primary_group: group } : {}), ...(subgroup ? { subgroup } : {}) }),
      rejected ? Promise.resolve([]) : getPairCategories(runId),
    ]).then(([data, groups]) => {
      if (!current) return;
      if (offset && offset >= data.total) { setOffset(Math.max(0, Math.ceil(data.total / PAGE_SIZE) - 1) * PAGE_SIZE); return; }
      setResult({ ...data, key: queryKey }); setCategories(groups);
    }).catch((reason: unknown) => { if (current) setError(reason instanceof Error ? reason.message : "Không tải được danh sách"); });
    return () => { current = false; };
  }, [runId, bucket, rejected, group, subgroup, offset, reload, queryKey]);
  useEffect(() => { setPageInput(String(Math.floor(offset / PAGE_SIZE) + 1)); }, [offset]);
  useEffect(() => {
    if (!active) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const controls = () => Array.from(dialog.current?.querySelectorAll<HTMLButtonElement>('button:not([disabled])') ?? []);
    controls()[0]?.focus();
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) setActive(null);
      if (event.key === "Tab") {
        const elements = controls(); const first = elements[0]; const last = elements[elements.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    };
    window.addEventListener("keydown", handleKey);
    return () => { document.body.style.overflow = previous; window.removeEventListener("keydown", handleKey); opener.current?.focus(); };
  }, [active, busy]);

  async function open(item: ResolutionItem, button: HTMLButtonElement) {
    opener.current = button; setBusy(true); setDecisionError("");
    try { setActive(await getResolutionItem(runId, bucket, item.id)); }
    catch (reason) { notify(reason instanceof Error ? reason.message : "Không mở được cặp bản ghi"); }
    finally { setBusy(false); }
  }
  async function decide(decision: "merge" | "no_merge") {
    if (!active?.revision) return;
    setBusy(true); setDecisionError("");
    try {
      const response = await decideReviewPair(runId, active.id, decision, active.revision);
      setActive(null); setReload((value) => value + 1);
      notify(decision === "merge" ? `Đã xác nhận gộp ${response.affected_record_count} bản ghi thành một hồ sơ; xem trong Tự động gộp → Bạn đã xác nhận.` : "Đã chuyển cặp bản ghi sang Không gộp.");
      void onChanged().catch(() => notify("Quyết định đã lưu; chưa cập nhật được số liệu tổng quan."));
    } catch (reason) { setDecisionError(reason instanceof Error ? reason.message : "Không lưu được quyết định"); }
    finally { setBusy(false); }
  }
  const records = active?.member_record_ids.map((id) => active.records.find((record) => record.id === id)).filter((record) => !!record) ?? [];

  return <section className="resolution-flow resolution-flow-review">
    {!rejected && <div className="card review-filters pair-filters">
      <label>Nhóm xem xét<select value={group} disabled={busy} onChange={(event) => { setGroup(event.target.value); setSubgroup(""); setOffset(0); }}>
        <option value="">Tất cả nhóm</option>{Array.from(new Set(categories.map((item) => item.primary_group))).map((value) =>
          <option key={value} value={value}>{groupLabel(value)} ({categories.filter((item) => item.primary_group === value).reduce((sum, item) => sum + item.pair_count, 0).toLocaleString("vi-VN")})</option>)}
      </select></label>
      <label>Phân nhóm chi tiết<select value={subgroup} disabled={!group || busy} onChange={(event) => { setSubgroup(event.target.value); setOffset(0); }}>
        <option value="">Tất cả phân nhóm</option>{categories.filter((item) => item.primary_group === group).map((item) =>
          <option key={item.subgroup} value={item.subgroup}>{subLabel(item.subgroup)} ({item.pair_count.toLocaleString("vi-VN")})</option>)}
      </select></label>
    </div>}
    <div className="resolution-flow-toolbar"><div><h2>{rejected ? "Đã xác định không gộp" : "Đối chiếu bản ghi nghi trùng"}</h2>
      <p>{loading ? "Đang tải…" : `${total.toLocaleString("vi-VN")} lượt đối chiếu${rejected ? "" : " cần xác nhận"}`}</p></div>
      <p>{rejected ? "Mỗi dòng là hai bản ghi được giữ riêng với nhau. Một bản ghi vẫn có thể gộp với bản ghi khác phù hợp." : "Mỗi lượt so sánh hai bản ghi để bạn xác nhận gộp hoặc giữ riêng."}</p></div>
    <p className="muted-copy">Một bản ghi có thể xuất hiện trong nhiều lượt đối chiếu. Ví dụ: A so với B, rồi A so với C. Vì vậy, số lượt đối chiếu có thể lớn hơn số bản ghi đầu vào.</p>
    <section className="card review-list-panel" aria-label="Danh sách cặp bản ghi">
      {error ? <div className="empty-state" role="alert"><p>{error}</p><button className="button outline" onClick={() => { setError(""); setReload((value) => value + 1); }}>Thử lại</button></div> : loading ?
        <p className="empty-state" role="status">Đang tải danh sách…</p> : !result?.items.length ? <div className="empty-state">Không có cặp bản ghi trong nhóm này.</div> :
        <div className="review-table-scroll" tabIndex={0} role="region" aria-label="Bảng các cặp bản ghi">
          <table className="review-table pair-list-table"><thead><tr><th scope="col">Bản ghi thứ nhất</th><th scope="col">Bản ghi thứ hai</th><th scope="col">{rejected ? "Quyết định" : "Phân nhóm"}</th><th scope="col" className="review-action-cell">So sánh</th></tr></thead>
            <tbody>{result.items.map((item) => <tr key={item.id}><td><strong className="review-table-name">{item.record_labels?.[0] || item.label || "Chưa có tên"}</strong></td>
              <td><strong className="review-table-name">{item.record_labels?.[1] || "Chưa có tên"}</strong></td>
              <td>{rejected ? (item.origin === "REVIEW" ? "Người dùng xác nhận không gộp" : "Hệ thống xác định không gộp") : <>{subLabel(item.subgroup)}<small className="review-table-group">{groupLabel(item.primary_group)}</small></>}</td>
              <td className="review-action-cell"><button className="button outline" disabled={busy} aria-label={`So sánh cặp ${item.id}`} onClick={(event) => void open(item, event.currentTarget)}>So sánh →</button></td></tr>)}</tbody>
          </table>
        </div>}
      <nav className="pagination review-pagination" aria-label="Phân trang các cặp">
        <button className="button outline" disabled={loading || busy || !offset} onClick={() => setOffset((value) => Math.max(0, value - PAGE_SIZE))}>← Trước</button>
        <span className="pagination-range">{total ? `${offset + 1}–${Math.min(offset + PAGE_SIZE, total)} / ${total.toLocaleString("vi-VN")}` : "0 cặp"}</span>
        <form className="pagination-jump" onSubmit={(event) => { event.preventDefault(); const page = Number(pageInput); if (Number.isFinite(page)) setOffset((Math.max(1, Math.min(pages, Math.trunc(page))) - 1) * PAGE_SIZE); }}>
          <label htmlFor="pair-page">Trang</label><input id="pair-page" type="number" min={1} max={pages} value={pageInput} onChange={(event) => setPageInput(event.target.value)} /><span>/ {pages}</span><button className="button outline" disabled={loading || busy}>Đi</button>
        </form>
        <button className="button outline" disabled={loading || busy || offset + PAGE_SIZE >= total} onClick={() => setOffset((value) => value + PAGE_SIZE)}>Sau →</button>
      </nav>
    </section>
    {active && <div className="history-overlay review-detail-overlay" role="dialog" aria-modal="true" aria-labelledby="pair-title" ref={dialog}>
      <div className="pair-comparison-dialog">
        <header className="pair-comparison-heading"><div><p className="section-kicker">{rejected ? "KHÔNG GỘP" : "CẦN XEM XÉT"}</p><h2 id="pair-title">So sánh hai bản ghi</h2><p>{subLabel(active.subgroup)}</p></div>
          <button className="review-close-button" aria-label="Đóng so sánh" disabled={busy} onClick={() => setActive(null)}>×</button></header>
        <div className="pair-comparison-scroll" tabIndex={0} role="region" aria-label="Giá trị của hai bản ghi">
          <table className="pair-comparison-table"><thead><tr><th scope="col">Thông tin</th>{records.map((record, index) => <th scope="col" key={record.id}>
            <strong>Bản ghi {index + 1}</strong><span>{record.source_system} · {record.external_id}</span></th>)}</tr></thead>
            <tbody>{Object.entries(fields).map(([field, label]) => {
              const values = records.map((record) => String(record.raw_fields[field] || ""));
              return <tr key={field} className={values[0] !== values[1] ? "is-different" : ""}><th scope="row">{label}{values[0] !== values[1] && <small>Khác nhau</small>}</th>
                {values.map((value, index) => <td key={index}>{value ? differenceSegments(value, [values[1 - index] || ""]).map((segment, part) => segment.different ? <mark key={part}>{segment.text}</mark> : <span key={part}>{segment.text}</span>) : <em>Chưa có dữ liệu</em>}</td>)}</tr>;
            })}</tbody>
          </table>
        </div>
        <footer className="pair-comparison-footer">
          {!rejected && <p>Chỉ xác nhận mối liên hệ giữa hai bản ghi này. {(active.affected_record_count ?? 2) > 2 && `Một bản ghi đã thuộc nhóm gộp: xác nhận sẽ nối các nhóm liên quan, tổng cộng ${active.affected_record_count} bản ghi.`} Giá trị hợp nhất được kiểm tra trong nhóm Tự động gộp trước khi xuất CSV.</p>}
          {active.merge_blocked_reason && <p role="alert">{active.merge_blocked_reason}</p>}
          {decisionError && <p role="alert">{decisionError}</p>}
          <div><button className="button outline" disabled={busy} onClick={() => setActive(null)}>Đóng</button>
            {!rejected && <><button className="button outline" disabled={busy || !can(user, "reject_pair") || !active.revision} onClick={() => void decide("no_merge")}>Xác nhận không gộp</button>
              <button className="button primary" disabled={busy || !can(user, "approve_pair") || !active.revision || !!active.merge_blocked_reason} onClick={() => void decide("merge")}>{busy ? "Đang lưu…" : "Xác nhận gộp"}</button></>}
          </div>
        </footer>
      </div>
    </div>}
  </section>;
}
