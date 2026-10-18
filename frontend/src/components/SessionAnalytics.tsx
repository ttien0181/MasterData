"use client";

import { useEffect, useState } from "react";
import { getResolutionAnalytics } from "@/lib/api";
import type { ResolutionAnalytics } from "@/lib/contracts";

const fieldLabels: Record<string, string> = { name: "Họ tên", phone: "Điện thoại", email: "Email", address: "Địa chỉ" };
const number = (value: number) => value.toLocaleString("vi-VN", { maximumFractionDigits: 1 });
const sourceOrder = (value: string) => ({ CRM: 0, ERP: 1, BILL: 2 }[value] ?? 3);
const percent = (share: number) => share > 0 && share < 0.001 ? "<0,1%" : `${number(share * 100)}%`;
function piePoint(share: number, radius: number) {
  const angle = share * Math.PI * 2 - Math.PI / 2;
  return { x: 150 + Math.cos(angle) * radius, y: 150 + Math.sin(angle) * radius };
}
function pieSlicePath(start: number, end: number) {
  const from = piePoint(start, 100);
  const to = piePoint(end, 100);
  return `M 150 150 L ${from.x} ${from.y} A 100 100 0 ${end - start > 0.5 ? 1 : 0} 1 ${to.x} ${to.y} Z`;
}

export default function SessionAnalytics({ runId, revision }: { runId: string; revision: string }) {
  const [data, setData] = useState<{ key: string; value: ResolutionAnalytics } | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const key = `${runId}:${revision}:${retry}`;
  useEffect(() => {
    let current = true;
    setError("");
    getResolutionAnalytics(runId).then((value) => {
      if (!value.decisions || !value.conflicts || !value.quality) throw new Error("Máy chủ chưa cung cấp số liệu tổng hợp cho đợt xử lý này.");
      if (current) setData({ key, value });
    }).catch((reason: unknown) => { if (current) setError(reason instanceof Error ? reason.message : "Không tải được số liệu"); });
    return () => { current = false; };
  }, [runId, key]);
  if (error) return <div className="card analytics-state" role="alert"><p>{error}</p><button className="button outline" onClick={() => setRetry((value) => value + 1)}>Tải lại số liệu</button></div>;
  if (data?.key !== key) return <div className="card analytics-state" role="status">Đang tổng hợp số liệu đợt xử lý…</div>;
  const { decisions, conflicts, quality } = data.value;
  const pieSlices = [
    { label: "Đề xuất gộp tự động", count: decisions.automatic, className: "is-auto" },
    { label: "Cần xem xét ban đầu", count: decisions.review, className: "is-review" },
    { label: "Chưa phát hiện trùng", count: decisions.no_duplicate_detected, className: "is-separate" },
  ];
  let pieStart = 0;
  const maxConflict = Math.max(1, ...conflicts.fields.map((entry) => entry.count));
  const maxSourceCount = Math.max(1, ...quality.sources.map((source) => source.record_count));
  const totalSourceRecords = quality.sources.reduce((total, source) => total + source.record_count, 0);
  return <div className="session-analytics">
    <div className="overview-insights analytics-cards">
      <section className="card overview-insight-card analytics-decisions" aria-label="Phân loại dòng thông tin ban đầu">
        <h3>PHÂN LOẠI BAN ĐẦU</h3>
        <div className="analytics-decision-content">
          <svg className="analytics-pie" viewBox="0 0 300 300" role="img"
            aria-label={`Phân loại ban đầu: ${pieSlices.map((slice) => `${slice.label} ${percent(decisions.total ? slice.count / decisions.total : 0)}`).join(", ")}`}>
            {decisions.total ? pieSlices.map((slice) => {
              if (!slice.count) return null;
              const share = slice.count / decisions.total;
              const start = pieStart;
              pieStart += share;
              const middle = start + share / 2;
              const labelPoint = piePoint(middle, 126);
              const lineStart = piePoint(middle, 103);
              const lineEnd = piePoint(middle, 115);
              return <g key={slice.className}>
                {share >= 1 ? <circle className={`analytics-pie-slice ${slice.className}`} cx="150" cy="150" r="100" />
                  : <path className={`analytics-pie-slice ${slice.className}`} d={pieSlicePath(start, pieStart)} />}
                <line className="analytics-pie-leader" x1={lineStart.x} y1={lineStart.y} x2={lineEnd.x} y2={lineEnd.y} />
                <text className="analytics-pie-label" x={labelPoint.x} y={labelPoint.y} dominantBaseline="central" textAnchor="middle">{percent(share)}</text>
              </g>;
            }) : <circle className="analytics-pie-empty" cx="150" cy="150" r="100" />}
          </svg>
          <dl className="analytics-legend">
            <div><dt><i className="is-auto" />Đề xuất gộp tự động</dt><dd>{number(decisions.automatic)} dòng</dd></div>
            <div><dt><i className="is-review" />Cần xem xét ban đầu</dt><dd>{number(decisions.review)} dòng</dd></div>
            <div><dt><i className="is-separate" />Chưa phát hiện trùng</dt><dd>{number(decisions.no_duplicate_detected)} dòng</dd></div>
          </dl>
        </div>
      </section>
      <section className="card overview-insight-card analytics-conflicts" aria-label="Thông tin không khớp trong các cặp đang chờ">
        <h3>THÔNG TIN KHÔNG KHỚP</h3>
        <div className="analytics-conflict-list">{conflicts.fields.map((entry) => <div className="analytics-conflict-row" data-field={entry.field} key={entry.field}>
          <span>{fieldLabels[entry.field] || entry.field}</span><div className="analytics-bar-track" aria-hidden="true"><i style={{ width: `${entry.count / maxConflict * 100}%` }} /></div><b>{number(entry.count)}</b>
        </div>)}</div>
        <p>{conflicts.review_pair_count ? `Trong ${number(conflicts.review_pair_count)} cặp đang chờ xem xét. Mỗi số là số cặp có giá trị gốc khác nhau ở mục đó, giống như trong bảng Cần xem xét. Một cặp có thể khác ở nhiều mục.` : "Không còn cặp chờ xem xét."}</p>
      </section>
      <section className="card overview-insight-card analytics-quality" aria-label="Số lượng dòng thông tin theo nguồn">
        <h3>SỐ LƯỢNG DÒNG THÔNG TIN THEO NGUỒN</h3>
        <div className="analytics-quality-columns">{[...quality.sources].sort((a, b) => sourceOrder(a.source) - sourceOrder(b.source) || a.source.localeCompare(b.source)).map((source) => <div key={source.source} className="analytics-quality-column"
          title={`${source.source}: ${number(source.record_count)} dòng thông tin`}>
          <strong>{number(source.record_count)}</strong>
          <div className="analytics-quality-track" aria-hidden="true"><i style={{ height: `${source.record_count / maxSourceCount * 100}%` }} /></div>
          <span>{source.source === "BILL" ? "Billing" : source.source}</span>
        </div>)}</div>
        <p>{quality.sources.length ? `Tổng ${number(totalSourceRecords)} dòng thông tin đầu vào từ ${number(quality.sources.length)} nguồn.` : "Chưa có dòng thông tin đầu vào."}</p>
      </section>
    </div>
    <details className="analytics-definitions"><summary>Cách tính các chỉ số</summary>
      <p><b>Phân loại ban đầu:</b> đếm các dòng thông tin đầu vào, không đếm cặp. Dòng liên quan đến cặp cần xem xét được ưu tiên xếp vào Cần xem xét ban đầu, rồi đến Đề xuất gộp tự động; phần còn lại là Chưa phát hiện trùng. Ba nhóm cộng lại bằng tổng số dòng. Quyết định duyệt sau đó không đổi biểu đồ này.</p>
      <p><b>Cặp đang chờ xem xét:</b> mỗi cặp gồm hai dòng thông tin. Ví dụ, 500 cặp không dùng chung dòng tương ứng với 1.000 dòng; hai cặp A–B và B–C chỉ có 3 dòng khác nhau. Số cặp đang chờ giảm khi bạn xác nhận hoặc đưa cặp sang Chờ gộp.</p>
      <p><b>Thông tin không khớp:</b> số cặp đang chờ có giá trị gốc khác nhau ở từng mục trong bảng Cần xem xét. Một bên trống hoặc khác cách viết cũng được tính; một cặp có thể xuất hiện ở nhiều mục.</p>
      <p><b>Dòng thông tin theo nguồn:</b> số dòng thông tin đầu vào của từng nguồn trong đợt xử lý, trước khi gộp. Các file có cùng nguồn được cộng chung; chiều cao cột tỷ lệ với số lượng dòng thông tin.</p>
    </details>
  </div>;
}
