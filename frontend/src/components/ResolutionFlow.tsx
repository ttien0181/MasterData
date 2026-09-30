"use client";

import { useCallback, useEffect, useState } from "react";
import type { Stage } from "./AppShell";
import { compareResolutionExport, createResolutionExport, downloadResolutionExport, downloadResolutionSource,
  explainReviewCategory, getResolutionExportReadiness,
  getResolutionExports, getResolutionItem, getResolutionItems, getReviewCategories,
  saveResolutionChoices, transitionReviewItems, type ItemBucket } from "@/lib/api";
import type { ResolutionExportReadiness, ResolutionExportSummary, ResolutionItem,
  ResolutionItemDetail, ReviewCategory } from "@/lib/contracts";
import { differenceSegments, groupFieldOptions } from "./resolutionFields";

const PAGE_SIZE = 25;
const FIELDS = ["name", "phone", "email", "address"] as const;
const labels: Record<string, string> = { name: "Tên khách hàng", phone: "Điện thoại", email: "Email", address: "Địa chỉ" };
const groups: Record<string, string> = {
  CONSTRAINT_OR_CLUSTER_RISK: "Rủi ro cụm", SINGLE_MAJOR_CONFLICT: "Một trường khác biệt",
  SHARED_VALUE: "Giá trị liên hệ dùng chung", VIETNAM_VARIANT: "Biến thể dữ liệu Việt Nam",
  SPARSE_EVIDENCE: "Thiếu bằng chứng", MODEL_DISAGREEMENT: "Tín hiệu chưa thống nhất",
};

function valueWithDifference(value: string, others: string[]) {
  return differenceSegments(value, others).map((segment, index) => segment.different
    ? <mark key={index}>{segment.text}</mark> : <span key={index}>{segment.text}</span>);
}

function sourceColor(source: string) {
  return ["CRM", "ERP", "BILL"].includes(source) ? `source-${source.toLowerCase()}` : "source-other";
}

export default function ResolutionFlow({ runId, stage, onChanged, notify }: {
  runId: string; stage: Stage; onChanged: () => Promise<unknown>; notify: (message: string) => void;
}) {
  const bucket: ItemBucket = stage === "auto" ? "merge" : stage === "rejected" ? "no_merge" : "review";
  const [offset, setOffset] = useState(0);
  const [items, setItems] = useState<ResolutionItem[]>([]);
  const [total, setTotal] = useState(0);
  const [active, setActive] = useState<ResolutionItemDetail | null>(null);
  const [choices, setChoices] = useState<Record<string, string>>({});
  const [sharedOpen, setSharedOpen] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [blockedIds, setBlockedIds] = useState<string[]>([]);
  const [origin, setOrigin] = useState("");
  const [category, setCategory] = useState<ReviewCategory | null>(null);
  const [categories, setCategories] = useState<ReviewCategory[]>([]);
  const [readiness, setReadiness] = useState<ResolutionExportReadiness | null>(null);
  const [exports, setExports] = useState<ResolutionExportSummary[]>([]);
  const [comparison, setComparison] = useState<{ source_count: number; output_count: number;
    source_sample: Record<string, string>[]; output_sample: Record<string, string>[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmExport, setConfirmExport] = useState(false);
  const [explanations, setExplanations] = useState<Record<string, { summary_vi: string; attention_fields: string[]; limitations: string[] }>>({});

  const load = useCallback(async () => {
    if (!runId) return;
    if (stage === "history") {
      setExports((await getResolutionExports(runId)).items);
      return;
    }
    const [result, categoryResults, readinessResult] = await Promise.all([
      getResolutionItems(runId, bucket, { offset, limit: PAGE_SIZE,
        ...(stage === "auto" && origin ? { origin } : {}),
        ...(stage === "review" && category ? { primary_group: category.primary_group, subgroup: category.subgroup } : {}),
      }),
      stage === "review" ? getReviewCategories(runId) : Promise.resolve([]),
      stage === "auto" ? getResolutionExportReadiness(runId) : Promise.resolve(null),
    ]);
    setItems(result.items); setTotal(result.total);
    if (stage === "review") setBlockedIds((current) => Array.from(new Set([
      ...current.filter((id) => !result.items.some((item) => item.id === id)),
      ...result.items.filter((item) => item.can_merge === false).map((item) => item.id),
    ])));
    if (stage === "review") setCategories(categoryResults);
    if (stage === "auto") setReadiness(readinessResult);
  }, [runId, stage, bucket, offset, origin, category]);

  useEffect(() => { void load().catch((error: unknown) => notify(error instanceof Error ? error.message : "Không tải được danh sách")); }, [load, notify]);
  useEffect(() => { setOffset(0); setSelected([]); setActive(null); }, [runId, stage]);
  useEffect(() => {
    if (!active && !comparison && !confirmExport) return;
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") { setActive(null); setChoices({}); setSharedOpen(false); setComparison(null); setConfirmExport(false); }
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [active, comparison, confirmExport]);

  async function openItem(item: ResolutionItem) {
    setBusy(true);
    try {
      const detail = await getResolutionItem(runId, bucket, item.id);
      setActive({ ...detail, label: item.label }); setChoices(detail.choices); setSharedOpen(false);
    } catch (error) { notify(error instanceof Error ? error.message : "Không mở được item"); }
    finally { setBusy(false); }
  }

  async function save() {
    if (!active) return;
    setBusy(true);
    try {
      const detail = await saveResolutionChoices(runId, bucket, active.id, choices);
      setActive({ ...detail, label: active.label }); setChoices(detail.choices);
      notify("Đã lưu lựa chọn giá trị.");
      if (stage === "auto") setReadiness(await getResolutionExportReadiness(runId));
    } catch (error) { notify(error instanceof Error ? error.message : "Không lưu được lựa chọn"); }
    finally { setBusy(false); }
  }

  async function transition(ids: string[], decision: "merge" | "no_merge") {
    if (!ids.length) return;
    setBusy(true);
    try {
      if (active && ids.includes(active.id) && JSON.stringify(choices) !== JSON.stringify(active.choices)) {
        await saveResolutionChoices(runId, "review", active.id, choices);
      }
      const result = await transitionReviewItems(runId, ids, decision);
      setActive(null); setSelected([]); setBlockedIds([]);
      await Promise.all([load(), onChanged()]);
      notify(`Đã chuyển ${result.changed_count} item sang ${decision === "merge" ? "Gộp" : "Không gộp"}.`);
    } catch (error) { notify(error instanceof Error ? error.message : "Không chuyển được item"); }
    finally { setBusy(false); }
  }

  async function selectAll() {
    if (selected.length === total) { setSelected([]); return; }
    setBusy(true);
    try {
      const ids: string[] = [];
      const blocked: string[] = [];
      for (let next = 0; next < total; next += 100) {
        const result = await getResolutionItems(runId, "review", {
          offset: next, limit: 100,
          ...(category ? { primary_group: category.primary_group, subgroup: category.subgroup } : {}),
        });
        ids.push(...result.items.map((item) => item.id));
        blocked.push(...result.items.filter((item) => item.can_merge === false).map((item) => item.id));
      }
      setSelected(ids);
      setBlockedIds(blocked);
    } catch (error) { notify(error instanceof Error ? error.message : "Không chọn được tất cả"); }
    finally { setBusy(false); }
  }

  async function exportCsv(acknowledge: boolean) {
    setBusy(true);
    try {
      await createResolutionExport(runId, acknowledge);
      setConfirmExport(false);
      notify("Đã tạo và tải CSV cuối cùng. Có thể tải lại trong Lịch sử.");
      await onChanged();
    } catch (error) { notify(error instanceof Error ? error.message : "Không xuất được CSV"); }
    finally { setBusy(false); }
  }

  async function explainCategory(category: ReviewCategory) {
    setBusy(true);
    try {
      const result = await explainReviewCategory(runId, category.signature_hash);
      if (result.explanation) setExplanations((current) => ({ ...current, [category.signature_hash]: result.explanation! }));
      else notify(`Giải thích AI chưa sẵn sàng: ${result.status}`);
    } catch (error) { notify(error instanceof Error ? error.message : "Không lấy được giải thích AI"); }
    finally { setBusy(false); }
  }

  function closeItem() {
    setActive(null);
    setChoices({});
    setSharedOpen(false);
  }

  const differentFields = active ? FIELDS.filter((field) => groupFieldOptions(active.field_options[field] ?? []).length > 1) : [];
  const emptyFields = active ? FIELDS.filter((field) => !(active.field_options[field] ?? []).length) : [];
  const sharedFields = active ? FIELDS.filter((field) => groupFieldOptions(active.field_options[field] ?? []).length === 1) : [];
  const selectedCount = differentFields.filter((field) => (active?.field_options[field] ?? [])
    .some((option) => option.source_record_id === choices[field])).length;
  const choicesDirty = !!active && FIELDS.some((field) => choices[field] !== active.choices[field]);

  function renderField(field: typeof FIELDS[number]) {
    if (!active) return null;
    const options = active.field_options[field] ?? [];
    const groups = groupFieldOptions(options);
    const different = groups.length > 1;
    return <div className={`resolution-field ${different ? "different" : "same"} ${field === "address" ? "long-value" : ""}`} key={field}>
      <div className="resolution-field-label"><strong>{labels[field]}</strong><small>{different ? `${groups.length} giá trị khác nhau` : options.length ? "Giống nhau" : "Chưa có dữ liệu"}</small></div>
      {!options.length ? <span className="muted-copy">Chưa có dữ liệu</span> : !different ? <div className="resolution-shared-value"><b>{groups[0].value}</b><span className="resolution-source-list">{options.map((option) => <span key={option.source_record_id} className={`resolution-source ${sourceColor(option.source_system)}`}>{option.source_system} · {option.external_id}</span>)}</span></div> :
        <div className="resolution-options">{groups.map((group) => {
          const selected = group.sources.some((option) => option.source_record_id === choices[field]);
          const sourceId = selected ? choices[field] : group.sources[0].source_record_id;
          return <label key={group.value} className={`resolution-option ${selected ? "chosen" : ""}`}>
            {stage !== "rejected" && <input type="radio" name={`field-${field}`} checked={selected} onChange={() => setChoices((current) => ({ ...current, [field]: sourceId }))} />}
            <span className="resolution-option-content"><b>{valueWithDifference(group.value, groups.map((candidate) => candidate.value))}</b><span className="resolution-source-list">{group.sources.map((option) => <span key={option.source_record_id} className={`resolution-source ${sourceColor(option.source_system)}`}>{option.source_system} · {option.external_id}</span>)}</span></span>
            {selected && <span className="resolution-option-check" aria-hidden="true">✓</span>}
          </label>;
        })}</div>}
    </div>;
  }

  if (!runId) return <div className="card empty-state"><b>Chưa có lần nạp dữ liệu</b><p>Nạp CSV để xem kết quả.</p></div>;
  if (stage === "history") return <section className="resolution-flow">
    <div className="resolution-flow-toolbar"><div><h2>Các bản CSV đã xuất</h2><p>Mỗi bản là ảnh chụp của dữ liệu tại thời điểm xuất. CSV nguồn được tái dựng từ các dòng đã nạp.</p></div>
      <button type="button" className="button outline" onClick={() => void downloadResolutionSource(runId).catch((error: unknown) => notify(error instanceof Error ? error.message : "Không tải được dữ liệu nguồn"))}>Tải CSV trước gộp</button></div>
    {exports.map((item) => <article className="card resolution-item" key={item.id}>
      <div><strong>{new Date(item.created_at).toLocaleString("vi-VN")}</strong><p>{item.row_count.toLocaleString("vi-VN")} dòng · {item.merged_group_count} nhóm gộp · {item.pending_review_count} item chờ review</p></div>
      <div className="resolution-flow-actions"><button type="button" className="button outline" disabled={busy} onClick={() => void compareResolutionExport(runId, item.id).then(setComparison).catch((error: unknown) => notify(error instanceof Error ? error.message : "Không xem được so sánh"))}>Xem trước / sau</button>
        <button type="button" className="button outline" disabled={busy} onClick={() => void downloadResolutionExport(runId, item.id).catch((error: unknown) => notify(error instanceof Error ? error.message : "Không tải được CSV"))}>Tải CSV sau gộp</button></div>
    </article>)}
    {!exports.length && <div className="card empty-state"><b>Chưa có bản xuất</b><p>Chọn giá trị ở Gộp rồi xuất CSV cuối cùng.</p></div>}
    {comparison && <div className="history-overlay" role="dialog" aria-modal="true" aria-labelledby="compare-heading"><div className="card resolution-item-dialog"><button type="button" className="dialog-close" aria-label="Đóng" onClick={() => setComparison(null)}>×</button><h2 id="compare-heading">Trước và sau khi gộp</h2><p className="muted-copy">Hiển thị 10 dòng đầu tiên của mỗi CSV.</p>
      <div className="resolution-compare-grid">{([{ title: `Trước · ${comparison.source_count} dòng`, rows: comparison.source_sample, fields: ["id", "source_system", "full_name", "phone_number"] },
        { title: `Sau · ${comparison.output_count} dòng`, rows: comparison.output_sample, fields: ["master_record_id", "source_systems", "full_name", "phone_number"] }]).map((table) =>
        <div key={table.title}><h3>{table.title}</h3><div className="resolution-compare-table"><table><thead><tr>{table.fields.map((field) => <th key={field}>{field}</th>)}</tr></thead><tbody>{table.rows.map((row, index) => <tr key={index}>{table.fields.map((field) => <td key={field}>{row[field]}</td>)}</tr>)}</tbody></table></div></div>)}</div>
    </div></div>}
  </section>;

  return <section className="resolution-flow">
    {stage === "review" && <div className="review-categories" aria-label="Nhóm trường hợp review">
      <button type="button" className={`category-chip ${!category ? "selected" : ""}`} onClick={() => { setCategory(null); setOffset(0); setSelected([]); }}>Tất cả</button>
      {categories.map((item) => <button type="button" key={item.signature_hash} className={`category-chip ${category?.signature_hash === item.signature_hash ? "selected" : ""}`} onClick={() => { setCategory(item); setOffset(0); setSelected([]); }}>
        <span>{groups[item.primary_group] ?? item.primary_group}<small>{item.subgroup}</small></span><b>{item.unique_cluster_count}</b>
      </button>)}
    </div>}
    {category && stage === "review" && <div className="card resolution-category-guide"><div><strong>{groups[category.primary_group] ?? category.primary_group} · {category.subgroup}</strong><p>{category.rule_template_vi}</p>
      {explanations[category.signature_hash] && <div className="resolution-ai-review"><strong>Giải thích từ Gemini</strong><p>{explanations[category.signature_hash].summary_vi}</p><small>AI giải thích tín hiệu tổng hợp, quyết định thuộc về bạn.</small></div>}</div>
      <button type="button" className="button outline" disabled={busy} onClick={() => void explainCategory(category)}>Giải thích bằng Gemini</button></div>}
    <div className="resolution-flow-toolbar">
      <div><h2>{stage === "auto" ? "Danh sách Gộp" : stage === "rejected" ? "Danh sách Không gộp" : "Danh sách Cần review"}</h2><p>{total.toLocaleString("vi-VN")} item</p></div>
      {stage === "auto" && <div className="resolution-flow-actions">
        <label>Nguồn <select value={origin} onChange={(event) => { setOrigin(event.target.value); setOffset(0); }}><option value="">Tất cả</option><option value="AUTO">Tự động</option><option value="REVIEW">Từ review</option></select></label>
        <button type="button" className="button primary" disabled={busy || !readiness?.can_export} onClick={() => readiness?.pending_review_count ? setConfirmExport(true) : void exportCsv(false)}>Xuất CSV sau gộp →</button>
      </div>}
      {stage === "review" && <div className="resolution-flow-actions"><label className="resolution-select-all"><input type="checkbox" checked={total > 0 && selected.length === total} onChange={() => void selectAll()} /> Chọn tất cả</label>
        <button type="button" className="button outline" disabled={busy || !selected.length} onClick={() => void transition(selected, "no_merge")}>Không gộp ({selected.length})</button>
        <button type="button" className="button primary" disabled={busy || !selected.length || selected.some((id) => blockedIds.includes(id))} title={selected.some((id) => blockedIds.includes(id)) ? "Bỏ chọn item có cặp Không gộp trước khi đưa sang Gộp" : undefined} onClick={() => void transition(selected, "merge")}>Đưa sang Gộp ({selected.length})</button></div>}
    </div>
    {stage === "auto" && readiness && <div className={`resolution-readiness ${readiness.incomplete_count ? "needs-input" : ""}`}>
      {readiness.incomplete_count ? `${readiness.incomplete_count} item còn trường khác nhau chưa chọn. Mở item để hoàn tất.` : "Các trường khác nhau trong danh sách Gộp đã có giá trị được chọn."}
      {readiness.pending_review_count > 0 && ` Còn ${readiness.pending_review_count} item Cần review; khi xuất, các bản ghi này sẽ giữ riêng.`}
    </div>}
    <div className="resolution-item-list">
      {items.map((item) => <article key={item.id} className="card resolution-item">
        {stage === "review" && <input type="checkbox" aria-label={`Chọn ${item.label}`} checked={selected.includes(item.id)} onChange={() => setSelected((current) => current.includes(item.id) ? current.filter((id) => id !== item.id) : [...current, item.id])} />}
        <button type="button" className="resolution-item-open" onClick={() => void openItem(item)}>
          <span className="resolution-item-title"><strong>{item.label || "Khách hàng"}</strong><span className="source-pill">{item.origin === "REVIEW" ? "Từ review" : "Tự động"}</span></span>
          <span className="resolution-item-meta">{item.member_count} bản ghi · {item.member_record_ids.length === 2 ? "Cặp" : "Cụm"} · {item.primary_group ? `${groups[item.primary_group] ?? item.primary_group}${item.subgroup ? ` · ${item.subgroup}` : ""}` : item.kind === "proposal" ? "Đề xuất gộp" : item.reason?.replaceAll("_", " ").toLowerCase() ?? "Xem so sánh"}</span>
          {stage === "review" && item.can_merge === false && <span className="resolution-conflict-badge">Có cặp Không gộp</span>}
          <span className="resolution-item-link">Xem so sánh →</span>
        </button>
      </article>)}
      {!items.length && <div className="card empty-state"><b>Không có item trong danh sách</b><p>Chọn bộ lọc khác hoặc nạp dữ liệu để tiếp tục.</p></div>}
    </div>
    <div className="pagination"><button type="button" className="button outline" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>← Trước</button><span>{total ? `${offset + 1}–${Math.min(offset + PAGE_SIZE, total)} / ${total}` : "0 kết quả"}</span><button type="button" className="button outline" disabled={offset + PAGE_SIZE >= total} onClick={() => setOffset(offset + PAGE_SIZE)}>Sau →</button></div>

    {active && <div className="history-overlay" role="dialog" aria-modal="true" aria-labelledby="resolution-item-heading" onMouseDown={(event) => { if (event.target === event.currentTarget) closeItem(); }}>
      <div className="card resolution-item-dialog"><div className="resolution-dialog-head"><div><p className="section-kicker">{stage === "auto" ? "GỘP" : stage === "rejected" ? "KHÔNG GỘP" : "CẦN REVIEW"} · {active.member_count} BẢN GHI</p><h2 id="resolution-item-heading">{active.label || active.records[0]?.raw_fields.name}</h2><p>So sánh các giá trị nguồn trước khi quyết định.</p></div><button type="button" className="dialog-close" aria-label="Đóng" onClick={closeItem}>×</button></div>
        <div className="resolution-record-strip">{active.records.map((record) => <span key={record.id} className={`resolution-source ${sourceColor(record.source_system)}`}><b>{record.source_system}</b> · {record.external_id}</span>)}</div>
        {active.merge_blocked_reason && <div className="resolution-readiness needs-input" role="alert">{active.merge_blocked_reason} Bạn vẫn có thể đưa các cặp nghi ngờ sang Không gộp.</div>}
        <div className="resolution-field-list">
          {!!differentFields.length && <p className="resolution-field-section-label">Cần đối chiếu <span>Phần tô màu là điểm khác biệt</span></p>}
          {differentFields.map(renderField)}
          {emptyFields.map(renderField)}
          {!!sharedFields.length && <div className="resolution-shared-section"><button type="button" className="resolution-shared-toggle" aria-expanded={sharedOpen} onClick={() => setSharedOpen((open) => !open)}>{sharedFields.length} trường giống nhau <span aria-hidden="true">{sharedOpen ? "▴" : "▾"}</span></button>{sharedOpen && <div className="resolution-field-list">{sharedFields.map(renderField)}</div>}</div>}
        </div>
        <div className="resolution-dialog-footer"><div className="resolution-footer-status">{stage === "rejected" ? <strong>Item chỉ để xem</strong> : <><strong>{differentFields.length ? `${selectedCount}/${differentFields.length} trường khác nhau đã có lựa chọn` : "Không có trường khác nhau cần chọn"}</strong><small>{choicesDirty ? "Có thay đổi chưa lưu." : "Chưa có thay đổi để lưu."} {selectedCount < differentFields.length && (stage === "auto" ? "Có thể lưu từng phần; cần chọn đủ trước khi xuất CSV." : "Có thể lưu từng phần và chọn tiếp ở tab Gộp.")}</small></>}</div>
          <button type="button" className="button outline" disabled={busy} onClick={closeItem}>{choicesDirty ? "Hủy thay đổi" : "Đóng"}</button>
          {stage !== "rejected" && <button type="button" className="button primary" disabled={busy || !choicesDirty} title={!choicesDirty ? "Chưa có thay đổi để lưu" : undefined} onClick={() => void save()}>Lưu lựa chọn</button>}
          {stage === "review" && <><button type="button" className="button outline" disabled={busy} onClick={() => void transition([active.id], "no_merge")}>Không gộp</button><button type="button" className="button primary" disabled={busy || !!active.merge_blocked_reason} onClick={() => void transition([active.id], "merge")}>Đưa sang Gộp →</button></>}
        </div>
      </div>
    </div>}
    {confirmExport && <div className="history-overlay" role="dialog" aria-modal="true" aria-labelledby="export-warning-heading"><div className="card resolution-confirm-dialog"><h2 id="export-warning-heading">Còn item cần review</h2><p>{readiness?.pending_review_count} item chưa quyết định. CSV vẫn bao gồm toàn bộ bản ghi đầu vào; các bản ghi đó sẽ được xuất riêng, chưa gộp.</p><div><button type="button" className="button outline" onClick={() => setConfirmExport(false)}>Quay lại</button><button type="button" className="button primary" disabled={busy} onClick={() => void exportCsv(true)}>Vẫn xuất CSV</button></div></div></div>}
  </section>;
}
