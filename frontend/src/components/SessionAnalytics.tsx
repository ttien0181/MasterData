"use client";

import { type CSSProperties, useEffect, useState } from "react";
import { getResolutionAnalytics } from "@/lib/api";
import type { ResolutionAnalytics } from "@/lib/contracts";

const fieldLabels: Record<string, string> = { name: "Tên", phone: "Điện thoại", email: "Email", address: "Địa chỉ" };
const number = (value: number) => value.toLocaleString("vi-VN", { maximumFractionDigits: 1 });
const sourceOrder = (value: string) => ({ CRM: 0, ERP: 1, BILL: 2 }[value] ?? 3);

export default function SessionAnalytics({ runId, revision }: { runId: string; revision: string }) {
  const [data, setData] = useState<{ key: string; value: ResolutionAnalytics } | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const key = `${runId}:${revision}:${retry}`;
  useEffect(() => {
    let current = true;
    setError("");
    getResolutionAnalytics(runId).then((value) => {
      if (!value.decisions || !value.conflicts || !value.quality) throw new Error("Máy chủ chưa cung cấp số liệu tổng hợp cho phiên này.");
      if (current) setData({ key, value });
    }).catch((reason: unknown) => { if (current) setError(reason instanceof Error ? reason.message : "Không tải được số liệu"); });
    return () => { current = false; };
  }, [runId, key]);
  if (error) return <div className="card analytics-state" role="alert"><p>{error}</p><button className="button outline" onClick={() => setRetry((value) => value + 1)}>Tải lại số liệu</button></div>;
  if (data?.key !== key) return <div className="card analytics-state" role="status">Đang tổng hợp số liệu phiên…</div>;
  const { decisions, conflicts, quality } = data.value;
  const autoShare = decisions.total ? decisions.automatic / decisions.total * 100 : 0;
  const reviewEnd = decisions.total ? (decisions.automatic + decisions.review) / decisions.total * 100 : 0;
  const maxConflict = Math.max(1, ...conflicts.fields.map((entry) => entry.count));
  const maxSourceCount = Math.max(1, ...quality.sources.map((source) => source.record_count));
  const totalSourceRecords = quality.sources.reduce((total, source) => total + source.record_count, 0);
  return <div className="session-analytics">
    <div className="overview-insights analytics-cards">
      <section className="card overview-insight-card analytics-decisions" aria-label="Phân bố quyết định">
        <h3>PHÂN BỐ QUYẾT ĐỊNH</h3>
        <div className="analytics-decision-content">
          <div className={`analytics-donut ${decisions.total ? "" : "is-empty"}`} role="img" aria-label={`${number(decisions.automatic_percent)}% bản ghi được phân loại tự xử lý`}
            style={{ "--auto-share": `${autoShare}%`, "--review-end": `${reviewEnd}%` } as CSSProperties}>
            <span><strong>{number(decisions.automatic_percent)}%</strong><small>tự xử lý</small></span>
          </div>
          <dl className="analytics-legend">
            <div><dt><i className="is-auto" />Tự xử lý</dt><dd>{number(decisions.automatic)}</dd></div>
            <div><dt><i className="is-review" />Cần xem xét</dt><dd>{number(decisions.review)}</dd></div>
            <div><dt><i className="is-separate" />Không phát hiện trùng</dt><dd>{number(decisions.no_duplicate_detected)}</dd></div>
          </dl>
        </div>
        <p>Phân loại ban đầu · {number(decisions.total)} bản ghi, không đếm lặp.</p>
      </section>
      <section className="card overview-insight-card analytics-conflicts" aria-label="Xung đột theo trường">
        <h3>XUNG ĐỘT THEO TRƯỜNG</h3>
        <div className="analytics-conflict-list">{conflicts.fields.map((entry) => <div className="analytics-conflict-row" key={entry.field}>
          <span>{fieldLabels[entry.field] || entry.field}</span><div className="analytics-bar-track" aria-hidden="true"><i style={{ width: `${entry.count / maxConflict * 100}%` }} /></div><b>{number(entry.count)}</b>
        </div>)}</div>
        <p>{conflicts.review_pair_count ? `Trong ${number(conflicts.review_pair_count)} cặp chờ xem xét. Một cặp có thể xung đột nhiều trường.` : "Không còn cặp chờ xem xét."}</p>
      </section>
      <section className="card overview-insight-card analytics-quality" aria-label="Số lượng bản ghi theo nguồn">
        <h3>SỐ LƯỢNG BẢN GHI THEO NGUỒN</h3>
        <div className="analytics-quality-columns">{[...quality.sources].sort((a, b) => sourceOrder(a.source) - sourceOrder(b.source) || a.source.localeCompare(b.source)).map((source) => <div key={source.source} className="analytics-quality-column"
          title={`${source.source}: ${number(source.record_count)} bản ghi`}>
          <strong>{number(source.record_count)}</strong>
          <div className="analytics-quality-track" aria-hidden="true"><i style={{ height: `${source.record_count / maxSourceCount * 100}%` }} /></div>
          <span>{source.source === "BILL" ? "Billing" : source.source}</span>
        </div>)}</div>
        <p>{quality.sources.length ? `Tổng ${number(totalSourceRecords)} bản ghi đầu vào từ ${number(quality.sources.length)} nguồn.` : "Chưa có bản ghi đầu vào."}</p>
      </section>
    </div>
    <details className="analytics-definitions"><summary>Cách tính các chỉ số</summary>
      <p><b>Phân bố quyết định:</b> tính trên bản ghi đầu vào theo kết quả phân loại ban đầu. Bản ghi liên quan đến cặp cần xem xét được ưu tiên xếp vào Cần xem xét, rồi đến Tự xử lý; phần còn lại là Không phát hiện trùng. Ba nhóm cộng lại bằng tổng bản ghi. Quyết định duyệt sau đó không đổi biểu đồ này.</p>
      <p><b>Xung đột:</b> số cặp đang chờ xem xét được pipeline đánh dấu xung đột cho từng trường. Thiếu dữ liệu hoặc chỉ khác cách viết không tự động tính là xung đột.</p>
      <p><b>Bản ghi theo nguồn:</b> số bản ghi đầu vào của từng nguồn trong phiên, trước khi gộp. Các file có cùng nguồn được cộng chung; chiều cao cột tỷ lệ với số lượng bản ghi.</p>
    </details>
  </div>;
}
