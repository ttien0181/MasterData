"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { Stage } from "./AppShell";
import { ShellIcon } from "./ShellIcons";
import { type AiExplanation, ApiError, compareResolutionExport, createResolutionPreview, executeResolutionPreview,
  downloadResolutionExport, downloadResolutionSource,
  explainResolutionItem, explainReviewCategory, saveStagedGroupChoices, unstageMergeGroup, unstageReviewPair,
  getResolutionExports, getResolutionItem, getResolutionItems, getReviewCategories, resolveMergeConflict,
  transitionReviewItems } from "@/lib/api";
import type { ResolutionExportSummary, ResolutionPreview, ResolutionItem,
  ResolutionItemDetail, ReviewCategory, AuthUser } from "@/lib/contracts";
import { can } from "@/lib/permissions";
import { differenceSegments, groupFieldOptions } from "./resolutionFields";
import { discardMergeChoices, discardReviewChoices, getSavedChoices, moveReviewChoices, saveChoices } from "@/lib/resolutionChoices";
import SplitHistoryDialog from "./SplitHistoryDialog";

const PAGE_SIZE = 25;
const FIELDS = ["name", "phone", "email", "address"] as const;
const labels: Record<string, string> = { name: "Họ tên", phone: "Điện thoại", email: "Email", address: "Địa chỉ" };
const categoryGroups: Record<string, string> = {
  A: "Nhiều thông tin trùng khớp", C: "Chưa đủ thông tin để xác nhận", D: "Có thông tin không khớp",
  CONSTRAINT_OR_CLUSTER_RISK: "Các dòng thông tin có liên kết cần kiểm tra",
  SINGLE_MAJOR_CONFLICT: "Có thông tin quan trọng khác nhau",
  SHARED_VALUE: "Thông tin liên hệ được dùng chung",
  VIETNAM_VARIANT: "Thông tin có cách viết khác nhau",
  SPARSE_EVIDENCE: "Chưa đủ dữ liệu để đối chiếu",
  MODEL_DISAGREEMENT: "Các dấu hiệu khớp chưa thống nhất",
};
const subgroupLabels: Record<string, string> = {
  A1_NAME_PHONE: "Tên và số điện thoại giống nhau", A2_NAME_EMAIL: "Tên và email giống nhau",
  C_NAME_ANCHOR_WEAK: "Tên chỉ giống một phần", C_NAME_ANCHOR_MISSING: "Thiếu tên đáng tin cậy",
  D2_NAME_CONFLICT: "Tên khác nhau rõ rệt", NO_VALID_IDENTITY_ANCHOR: "Thiếu thông tin xác định khách hàng",
  TRANSITIVE_RISK: "Các liên kết trong nhóm chưa thống nhất", SHARED_PHONE: "Số điện thoại có thể được dùng chung",
  GENERIC_EMAIL: "Email có thể được dùng chung", TEXT_VARIANT: "Cách viết thông tin khác nhau",
  AMBIGUOUS_FIELD_EVIDENCE: "Thông tin đối chiếu chưa rõ ràng", WEAK_FIELD_EVIDENCE: "Thông tin trùng khớp còn ít",
  PHONE: "Số điện thoại có thể được dùng chung", EMAIL: "Email có thể được dùng chung",
  CLUSTER_GUARD: "Cần kiểm tra các dòng thông tin liên quan",
};
function categoryGroupLabel(value: string) {
  return categoryGroups[value] ?? "Cần kiểm tra thêm";
}
function subgroupLabel(value?: string) {
  return value ? subgroupLabels[value] ?? "Cần đối chiếu thêm thông tin" : "Cần đối chiếu thêm thông tin";
}

const reviewReasons: Record<string, string> = {
  POLICY_BLOCKED: "Tên và thông tin liên hệ giống nhau, nhưng chưa đủ điều kiện gộp tự động",
  CLUSTER_RISK: "Gộp các dòng thông tin này có thể tạo ra liên kết mâu thuẫn",
  CLUSTER_NOT_CONFIRMED: "Nhóm dòng thông tin chưa đủ chắc chắn để gộp tự động",
  MISSING_REQUIRED_NAME_ANCHOR: "Tên chưa đủ rõ để xác nhận cùng khách hàng",
  NAME_CONFLICT: "Họ tên khác nhau rõ rệt",
  AMBIGUOUS: "Chưa thể xác nhận từ các thông tin hiện có",
  AMBIGUOUS_FIELD_EVIDENCE: "Một số thông tin chưa thể đối chiếu rõ ràng",
  WEAK_FIELD_EVIDENCE: "Có quá ít thông tin trùng khớp để xác nhận",
};
function reviewReason(value?: string) {
  return value ? reviewReasons[value] ?? subgroupLabel(value) : "Cần kiểm tra thêm thông tin";
}

function valueWithDifference(value: string, others: string[]) {
  return differenceSegments(value, others).map((segment, index) => segment.different
    ? <mark key={index}>{segment.text}</mark> : <span key={index}>{segment.text}</span>);
}

function sourceColor(source: string) {
  return ["CRM", "ERP", "BILL"].includes(source) ? `source-${source.toLowerCase()}` : "source-other";
}

function ListSkeleton() {
  return <div className="resolution-list-skeleton" role="status" aria-label="Đang tải danh sách">
    <span>Đang tải danh sách…</span>
    {Array.from({ length: 3 }, (_, index) => <div className="card resolution-skeleton-row" key={index} aria-hidden="true">
      <i className="resolution-skeleton-icon" /><span><i /><i /></span><i className="resolution-skeleton-action" />
    </div>)}
  </div>;
}

function ListLoadError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return <div className="card empty-state" role="alert"><b>Không tải được danh sách</b><p>{message}</p>
    <button type="button" className="button outline" onClick={onRetry}>Thử lại</button>
  </div>;
}

export default function ResolutionFlow({ user, runId, stage, onChanged, notify }: {
  user: AuthUser; runId: string; stage: Stage; onChanged: () => Promise<unknown>; notify: (message: string) => void;
}) {
  const canEdit = can(user, "edit_golden");
  const canApprove = can(user, "approve_pair");
  const canReject = can(user, "reject_pair");
  const canReview = canApprove || canReject;
  const canExport = can(user, "export_data");
  const canSplit = can(user, "selective_unmerge");
  const canExplain = can(user, "explain");
  const bucket: "merge" | "review" = stage === "auto" ? "merge" : "review";
  const [offset, setOffset] = useState(0);
  const [pageInput, setPageInput] = useState("1");
  const [items, setItems] = useState<ResolutionItem[]>([]);
  const [total, setTotal] = useState(0);
  const [active, setActive] = useState<ResolutionItemDetail | null>(null);
  const [choices, setChoices] = useState<Record<string, string>>({});
  const [sharedOpen, setSharedOpen] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [origin, setOrigin] = useState("");
  const [category, setCategory] = useState<ReviewCategory | null>(null);
  const [categories, setCategories] = useState<ReviewCategory[]>([]);
  const [exports, setExports] = useState<ResolutionExportSummary[]>([]);
  const [comparison, setComparison] = useState<{ source_count: number; output_count: number;
    source_sample: Record<string, string>[]; output_sample: Record<string, string>[] } | null>(null);
  const [splitExportId, setSplitExportId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const requestId = useRef(0);
  const reviewItemTrigger = useRef<HTMLButtonElement | null>(null);
  const queryKey = JSON.stringify([runId, stage, offset, origin, category?.signature_hash]);
  const [loadState, setLoadState] = useState<{ key: string; phase: "loading" | "ready" | "error"; error: string }>({ key: "", phase: "loading", error: "" });
  const loading = loadState.key !== queryKey || loadState.phase === "loading";
  const loadError = loadState.key === queryKey && loadState.phase === "error" ? loadState.error : "";
  const [confirmExport, setConfirmExport] = useState(false);
  const [preview, setPreview] = useState<ResolutionPreview | null>(null);
  const [previewStale, setPreviewStale] = useState(false);
  const executeRequestId = useRef("");
  const [explanations, setExplanations] = useState<Record<string, AiExplanation & { status: string }>>({});
  const [itemExplanation, setItemExplanation] = useState<{ itemId: string; summary: string; status: string } | null>(null);
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1;

  useEffect(() => { setPageInput(String(currentPage)); }, [currentPage, runId, stage, origin, category?.signature_hash]);

  function jumpToPage(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const requested = Number(pageInput);
    const page = Number.isFinite(requested) ? Math.min(pageCount, Math.max(1, Math.trunc(requested))) : currentPage;
    setPageInput(String(page));
    setOffset((page - 1) * PAGE_SIZE);
  }

  const load = useCallback(async () => {
    if (!runId) return;
    const currentRequest = ++requestId.current;
    setLoadState({ key: queryKey, phase: "loading", error: "" });
    try {
      if (stage === "history") {
        const result = await getResolutionExports(runId);
        if (currentRequest !== requestId.current) return;
        setExports(result.items);
      } else {
        const [result, categoryResults] = await Promise.all([
          getResolutionItems(runId, bucket, { offset, limit: PAGE_SIZE,
            ...(stage === "auto" && origin ? { origin } : {}),
            ...(stage === "review" && category ? { primary_group: category.primary_group, subgroup: category.subgroup } : {}),
          }),
          stage === "review" ? getReviewCategories(runId) : Promise.resolve([]),
        ]);
        if (currentRequest !== requestId.current) return;
        setItems(result.items); setTotal(result.total);
        if (stage === "review") setCategories(categoryResults);
      }
      setLoadState({ key: queryKey, phase: "ready", error: "" });
    } catch (error) {
      if (currentRequest !== requestId.current) return;
      const message = error instanceof Error ? error.message : "Không tải được danh sách";
      setLoadState({ key: queryKey, phase: "error", error: message });
      notify(message);
    }
  }, [runId, stage, bucket, offset, origin, category, queryKey, notify]);

  useEffect(() => { void load(); return () => { requestId.current += 1; }; }, [load]);
  useEffect(() => { setOffset(0); setSelected([]); setActive(null); }, [runId, stage]);
  useEffect(() => {
    if (!active && !comparison && !confirmExport && !preview && !splitExportId) return;
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        if (stage === "review" && active) closeItem();
        else { setActive(null); setChoices({}); setSharedOpen(false); }
        setComparison(null); setConfirmExport(false); setPreview(null); setSplitExportId(null);
      }
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [active, comparison, confirmExport, preview, splitExportId, stage]);
  const modalKind = splitExportId ? "split" : preview ? "preview" : confirmExport ? "export" : comparison ? "compare" : active ? "item" : "";
  useEffect(() => {
    if (!modalKind) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const dialog = document.querySelector<HTMLElement>('.history-overlay[role="dialog"]');
    const focusable = () => Array.from(dialog?.querySelectorAll<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), select:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])',
    ) ?? []);
    focusable()[0]?.focus();
    const trapTab = (event: KeyboardEvent) => {
      if (event.key !== "Tab") return;
      const controls = focusable();
      if (!controls.length) { event.preventDefault(); return; }
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    window.addEventListener("keydown", trapTab);
    return () => { window.removeEventListener("keydown", trapTab); document.body.style.overflow = previousOverflow; opener?.focus(); };
  }, [modalKind]);

  async function openItem(item: ResolutionItem) {
    if (stage === "review") { setActive(null); setChoices({}); }
    setBusy(true);
    try {
      const detail = await getResolutionItem(runId, bucket, item.id, stage === "review" ? category ?? undefined : undefined);
      const saved = detail.status === "STAGED_FOR_MERGE" ? null : getSavedChoices(runId, bucket, item.id, detail.member_record_ids);
      const valid = saved ? Object.fromEntries(Object.entries(saved).filter(([field, sourceId]) =>
        detail.field_options[field]?.some((option) => option.source_record_id === sourceId))) : {};
      const selected = { ...detail.choices, ...valid };
      setActive({ ...detail, label: item.label, choices: selected }); setChoices(selected); setSharedOpen(false); setItemExplanation(null);
    } catch (error) { notify(error instanceof Error ? error.message : "Không mở được đề xuất"); }
    finally { setBusy(false); }
  }

  async function save() {
    if (!active || !canEdit) return;
    setBusy(true);
    try {
      if (active.status === "STAGED_FOR_MERGE") {
        const result = await saveStagedGroupChoices(runId, active.id, active.revision ?? 1, choices);
        setActive({ ...active, choices, revision: result.revision, choice_conflicts: [] });
        await onChanged();
      } else {
        saveChoices(runId, bucket, active.id, active.member_record_ids, choices);
        setActive({ ...active, choices });
      }
      notify("Đã lưu lựa chọn giá trị.");
    } catch (error) { notify(error instanceof Error ? error.message : "Không lưu được lựa chọn"); }
    finally { setBusy(false); }
  }

  async function transition(ids: string[], decision: "merge" | "no_merge") {
    if (!ids.length || !(decision === "merge" ? canApprove : canReject)) return;
    setBusy(true);
    try {
      if (active && ids.includes(active.id) && JSON.stringify(choices) !== JSON.stringify(active.choices)) {
        saveChoices(runId, "review", active.id, active.member_record_ids, choices);
      }
      const result = await transitionReviewItems(runId, ids, decision);
      ids.forEach((id, index) => {
        if (decision === "merge") moveReviewChoices(runId, id, result.changed_ids[index]);
        else discardReviewChoices(runId, id);
      });
      setActive(null); setSelected([]);
      await Promise.all([load(), onChanged()]);
      notify(`Đã ${decision === "merge" ? "đưa" : "đánh dấu"} ${result.changed_count} đề xuất ${decision === "merge" ? "sang Chờ gộp" : "là Không gộp"}.`);
    } catch (error) { notify(error instanceof Error ? error.message : "Không chuyển được item"); }
    finally { setBusy(false); }
  }

  async function splitConflict() {
    if (!active || !canReject) return;
    setBusy(true);
    try {
      const itemId = active.id;
      const result = await resolveMergeConflict(runId, itemId);
      discardMergeChoices(runId, itemId);
      setActive(null);
      await Promise.all([load(), onChanged()]);
      notify(`Đã tách nhóm. ${result.rejected_pair_count} liên kết chưa quyết định được chuyển sang Không gộp.`);
    } catch (error) { notify(error instanceof Error ? error.message : "Không tách được nhóm"); }
    finally { setBusy(false); }
  }

  async function selectAll() {
    if (selected.length === total) { setSelected([]); return; }
    setBusy(true);
    try {
      const ids: string[] = [];
      for (let next = 0; next < total; next += 100) {
        const result = await getResolutionItems(runId, "review", {
          offset: next, limit: 100,
          ...(category ? { primary_group: category.primary_group, subgroup: category.subgroup } : {}),
        });
        ids.push(...result.items.map((item) => item.id));
      }
      setSelected(ids);
    } catch (error) { notify(error instanceof Error ? error.message : "Không chọn được tất cả"); }
    finally { setBusy(false); }
  }

  async function previewCsv(acknowledge: boolean) {
    if (!canExport) return;
    setBusy(true);
    try {
      const result = await createResolutionPreview(runId, acknowledge);
      setConfirmExport(false);
      executeRequestId.current = crypto.randomUUID();
      setPreviewStale(false);
      setPreview(result);
      await Promise.all([load(), onChanged()]);
    } catch (error) {
      if (error instanceof ApiError && error.status === 409 && error.reason === "PENDING_REVIEW") {
        setConfirmExport(true);
      } else notify(error instanceof Error ? error.message : "Không tạo được bản xem trước");
    }
    finally { setBusy(false); }
  }

  async function returnStagedPair(item: ResolutionItem) {
    setBusy(true);
    try {
      if (item.kind === "proposal") await unstageMergeGroup(runId, item.id);
      else await unstageReviewPair(runId, item.id.replace(/^staged:/, ""));
      setActive(null);
      await Promise.all([load(), onChanged()]);
      notify("Đã đưa các cặp trong nhóm trở lại danh sách Cặp cần xem xét.");
    } catch (error) { notify(error instanceof Error ? error.message : "Không đưa lại được cặp nghi trùng"); }
    finally { setBusy(false); }
  }

  async function executeCsv() {
    if (!preview || !canExport) return;
    setBusy(true);
    try {
      await executeResolutionPreview(runId, preview.preview_id, executeRequestId.current);
      setPreview(null);
      notify("Đã xác nhận gộp và tải CSV kết quả. Bản xuất được lưu trong Lịch sử.");
      await onChanged();
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        setPreviewStale(true);
        notify("Dữ liệu hoặc quyết định đã thay đổi. Hãy tạo lại bản xem trước trước khi xuất CSV.");
      } else notify(error instanceof Error ? error.message : "Không thực hiện được bản xem trước");
    }
    finally { setBusy(false); }
  }

  async function explainCategory(category: ReviewCategory) {
    if (!canExplain) return;
    setBusy(true);
    try {
      const result = await explainReviewCategory(runId, category.signature_hash);
      if (result.explanation && ["READY", "TEMPLATE"].includes(result.status)) setExplanations((current) => ({ ...current, [category.signature_hash]: { ...result.explanation!, status: result.status } }));
      else notify(result.status === "STALE" ? "Dữ liệu đã thay đổi; hãy tải lại giải thích." : "Giải thích chưa sẵn sàng; hãy đối chiếu thông tin trong dòng thông tin.");
    } catch (error) { notify(error instanceof Error ? error.message : "Không lấy được giải thích AI"); }
    finally { setBusy(false); }
  }

  async function explainActiveItem() {
    if (!active || !canExplain) return;
    setBusy(true);
    try {
      const result = await explainResolutionItem(runId, bucket, active.id);
      if (result.explanation && ["READY", "TEMPLATE"].includes(result.status)) setItemExplanation({ itemId: active.id, summary: result.explanation.summary_vi, status: result.status });
      else notify(result.status === "STALE" ? "Dữ liệu đã thay đổi; hãy tải lại giải thích." : "Giải thích chưa sẵn sàng; hãy đối chiếu thông tin trong dòng thông tin.");
    } catch (error) { notify(error instanceof Error ? error.message : "Không lấy được giải thích AI"); }
    finally { setBusy(false); }
  }

  function closeItem() {
    setActive(null);
    setChoices({});
    setSharedOpen(false);
    if (stage === "review") window.requestAnimationFrame(() => reviewItemTrigger.current?.focus());
  }

  const differentFields = active ? FIELDS.filter((field) => groupFieldOptions(active.field_options[field] ?? []).length > 1) : [];
  const emptyFields = active ? FIELDS.filter((field) => !(active.field_options[field] ?? []).length) : [];
  const sharedFields = active ? FIELDS.filter((field) => groupFieldOptions(active.field_options[field] ?? []).length === 1) : [];
  const conflictFields = active ? FIELDS.filter((field) =>
    active.pairs?.some((pair) => pair.field_evidence.some((entry) => entry.field === field && entry.state === "CF")))
    : [];
  const pureDifferentFields = differentFields.filter((field) => !conflictFields.includes(field));
  const selectedCount = differentFields.filter((field) => (active?.field_options[field] ?? [])
    .some((option) => option.source_record_id === choices[field])).length;
  const choicesDirty = !!active && FIELDS.some((field) => choices[field] !== active.choices[field]);
  const hasSemanticAddress = active?.pairs?.some((pair) => pair.field_evidence.some((entry) =>
    entry.field === "address" && entry.provenance === "LLM")) ?? false;

  function renderField(field: typeof FIELDS[number]) {
    if (!active) return null;
    const options = active.field_options[field] ?? [];
    const groups = groupFieldOptions(options);
    const different = groups.length > 1;
    return <div className={`resolution-field ${different ? "different" : "same"} ${field === "address" ? "long-value" : ""}`} key={field}>
      <div className="resolution-field-label"><strong>{labels[field]}</strong><small>{different ? (pureDifferentFields.includes(field) ? `${groups.length} giá trị khác nhau (không phải xung đột)` : `${groups.length} giá trị khác nhau`) : options.length ? "Giống nhau" : "Chưa có dữ liệu"}</small></div>
      {!options.length ? <span className="muted-copy">Chưa có dữ liệu</span> : !different ? <div className="resolution-shared-value"><b>{groups[0].value}</b><span className="resolution-source-list">{options.map((option) => <span key={option.source_record_id} className={`resolution-source ${sourceColor(option.source_system)}`}>{option.source_system} · {option.external_id}</span>)}</span></div> :
        <div className="resolution-options">{groups.map((group) => {
          const selected = group.sources.some((option) => option.source_record_id === choices[field]);
          const sourceId = selected ? choices[field] : group.sources[0].source_record_id;
          return <label key={group.value} className={`resolution-option ${selected ? "chosen" : ""} ${active.status === "REVIEW_STAGED_CONFLICT" ? "read-only" : ""}`}>
            {active.status !== "REVIEW_STAGED_CONFLICT" && <input type="radio" disabled={!canEdit} name={`field-${field}`} checked={selected} onChange={() => setChoices((current) => ({ ...current, [field]: sourceId }))} />}
            <span className="resolution-option-content"><b>{valueWithDifference(group.value, groups.map((candidate) => candidate.value))}</b><span className="resolution-source-list">{group.sources.map((option) => <span key={option.source_record_id} className={`resolution-source ${sourceColor(option.source_system)}`}>{option.source_system} · {option.external_id}</span>)}</span></span>
            {selected && <span className="resolution-option-check" aria-hidden="true">✓</span>}
          </label>;
        })}</div>}
    </div>;
  }

  function renderItemList() {
    if (stage === "review") return <div className="review-table-region" aria-busy={loading}>
      {loading ? <ListSkeleton /> : loadError ? <ListLoadError message={loadError} onRetry={() => void load()} /> : !items.length ?
        <div className="empty-state"><b>Không có nhóm cần xem xét</b><p>Chọn nhóm khác để xem thêm đề xuất.</p></div> :
        <div className="review-table-scroll" tabIndex={0} role="region" aria-label="Bảng nhóm cần xem xét">
          <table className="review-table">
            <thead><tr>{canReview && <th scope="col" className="review-check-cell"><span className="review-visually-hidden">Chọn nhóm</span></th>}
              <th scope="col">Nhóm cần kiểm tra</th><th scope="col" className="review-count-cell">Dòng thông tin</th>
              <th scope="col">Lý do cần kiểm tra</th><th scope="col" className="review-action-cell"><span className="review-visually-hidden">Thao tác</span></th></tr></thead>
            <tbody>{items.map((item) => <tr key={item.id} className={selected.includes(item.id) ? "is-selected" : undefined}>
              {canReview && <td className="review-check-cell"><input type="checkbox" aria-label={`Chọn ${item.label}`} checked={selected.includes(item.id)} disabled={busy}
                onChange={() => setSelected((current) => current.includes(item.id) ? current.filter((id) => id !== item.id) : [...current, item.id])} /></td>}
              <td><strong className="review-table-name">{item.label || "Khách hàng"}</strong>
                {item.primary_group && <span className="review-table-group">{categoryGroupLabel(item.primary_group)}</span>}
                {item.can_merge === false && <span className="resolution-conflict-badge">Có liên kết cần giữ riêng</span>}</td>
              <td className="review-count-cell"><strong>{item.member_count.toLocaleString("vi-VN")}</strong></td>
              <td><span>{item.subgroup ? subgroupLabel(item.subgroup) : reviewReason(item.reason)}</span>
                {item.subgroup && <small className="review-table-reason">{reviewReason(item.reason)}</small>}</td>
              <td className="review-action-cell"><button type="button" className="button outline" disabled={busy}
                aria-label={`Xem so sánh ${item.label || "Khách hàng"}`} onClick={(event) => { reviewItemTrigger.current = event.currentTarget; void openItem(item); }}>Xem so sánh →</button></td>
            </tr>)}</tbody>
          </table>
        </div>}
    </div>;
    return <div className="resolution-item-list" aria-busy={loading}>
      {loading ? <ListSkeleton /> : loadError ? <ListLoadError message={loadError} onRetry={() => void load()} /> : <>
        {items.map((item) => <article key={item.id} className="card resolution-item">
          <span className="resolution-item-glyph" aria-hidden="true"><ShellIcon name={stage === "auto" ? "auto" : "review"} size={17} /></span>
          <button type="button" className="resolution-item-open" onClick={() => void openItem(item)}>
            <span className="resolution-item-title"><strong>{item.label || "Khách hàng"}</strong><span className="source-pill">{item.status === "STAGED_FOR_MERGE" ? "Bạn đã chọn" : item.origin === "REVIEW" ? "Bạn đã xác nhận" : "Tự động"}</span></span>
            {item.status === "REVIEW_STAGED_CONFLICT" && <span className="resolution-conflict-badge">Cần tách nhóm trước khi xuất CSV</span>}
            <span className="resolution-item-meta">{item.member_count.toLocaleString("vi-VN")} dòng thông tin đầu vào → 1 hồ sơ chuẩn dự kiến</span>
            <span className="resolution-item-link">Xem so sánh →</span>
          </button>
          {item.status === "STAGED_FOR_MERGE" && <button type="button" className="button outline" disabled={busy || !canApprove} onClick={() => void returnStagedPair(item)}>Đưa các cặp lại Cần xem xét</button>}
        </article>)}
        {!items.length && <div className="card empty-state"><b>Không có đề xuất trong danh sách</b><p>Chọn bộ lọc khác hoặc nạp dữ liệu để tiếp tục.</p></div>}
      </>}
    </div>;
  }

  function renderPagination() {
    return <nav className={`pagination ${stage === "review" ? "review-pagination" : ""}`} aria-label="Phân trang danh sách">
      <button type="button" className="button outline" disabled={loading || !!loadError || offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>← Trước</button>
      <span className="pagination-range">{loading ? "Đang tải…" : loadError ? "Không tải được" : total ? `${offset + 1}–${Math.min(offset + PAGE_SIZE, total)} / ${total}` : "0 kết quả"}</span>
      <form className="pagination-jump" onSubmit={jumpToPage}><label htmlFor="resolution-page-input">Trang</label>
        <input id="resolution-page-input" aria-label="Số trang muốn đến" type="number" inputMode="numeric" min="1" max={pageCount} value={pageInput} disabled={loading || !!loadError || total === 0} onChange={(event) => setPageInput(event.target.value)} />
        <span>/ {pageCount}</span><button type="submit" className="button outline" disabled={loading || !!loadError || total === 0}>Đi</button>
      </form>
      <button type="button" className="button outline" disabled={loading || !!loadError || offset + PAGE_SIZE >= total} onClick={() => setOffset(offset + PAGE_SIZE)}>Sau →</button>
    </nav>;
  }

  if (!runId) return <div className="card empty-state"><b>Chưa có lần nạp dữ liệu</b><p>Nạp CSV để xem kết quả.</p></div>;
  if (stage === "history") return <section className="resolution-flow">
    <div className="resolution-flow-toolbar"><div><h2>Các bản CSV đã xuất</h2><p>Mỗi file lưu kết quả tại thời điểm xuất. File dữ liệu đầu vào được tạo lại từ các dòng đã nạp.</p></div>
      <button type="button" className="button outline" disabled={!canExport} title={!canExport ? "Tài khoản chưa có quyền xuất dữ liệu" : undefined} onClick={() => void downloadResolutionSource(runId).catch((error: unknown) => notify(error instanceof Error ? error.message : "Không tải được dữ liệu nguồn"))}>Tải dữ liệu đầu vào (CSV)</button></div>
    {loading ? <ListSkeleton /> : loadError ? <ListLoadError message={loadError} onRetry={() => void load()} /> : exports.map((item) => <article className="card resolution-item" key={item.id}>
      <div><strong>{new Date(item.created_at).toLocaleString("vi-VN")}</strong><p>{item.row_count.toLocaleString("vi-VN")} dòng · {item.merged_group_count.toLocaleString("vi-VN")} nhóm gộp · {item.pending_review_count > 0 ? "Còn dữ liệu chưa được xác nhận gộp" : "Đã xem xét xong"}{item.parent_export_id ? " · sau khi tách dòng thông tin" : ""}</p></div>
      <div className="resolution-flow-actions"><button type="button" className="button outline" disabled={busy} onClick={() => void compareResolutionExport(runId, item.id).then(setComparison).catch((error: unknown) => notify(error instanceof Error ? error.message : "Không xem được so sánh"))}>Xem trước / sau</button>
        <button type="button" className="button outline" disabled={busy || !canExport} title={!canExport ? "Tài khoản chưa có quyền xuất dữ liệu" : undefined} onClick={() => void downloadResolutionExport(runId, item.id).catch((error: unknown) => notify(error instanceof Error ? error.message : "Không tải được CSV"))}>Tải file kết quả (CSV)</button>
        {canSplit && item.id === exports[0]?.id && item.merged_group_count > 0 && <button type="button" className="button outline" disabled={busy} onClick={() => setSplitExportId(item.id)}>Tách dòng thông tin</button>}</div>
    </article>)}
    {!loading && !loadError && !exports.length && <div className="card empty-state"><b>Chưa có bản xuất</b><p>Chọn giá trị, xem trước và xác nhận gộp để tạo CSV.</p></div>}
    {comparison && <div className="history-overlay" role="dialog" aria-modal="true" aria-labelledby="compare-heading"><div className="card resolution-item-dialog"><button type="button" className="dialog-close" aria-label="Đóng" onClick={() => setComparison(null)}>×</button><h2 id="compare-heading">Trước và sau khi gộp</h2><p className="muted-copy">Hiển thị 10 dòng đầu tiên của mỗi CSV.</p>
      <div className="resolution-compare-grid">{([{ title: `Trước · ${comparison.source_count} dòng`, rows: comparison.source_sample, fields: ["id", "source_system", "full_name", "phone_number"] },
        { title: `Sau · ${comparison.output_count} dòng`, rows: comparison.output_sample, fields: ["master_record_id", "source_systems", "full_name", "phone_number"] }]).map((table) =>
        <div key={table.title}><h3>{table.title}</h3><div className="resolution-compare-table"><table><thead><tr>{table.fields.map((field) => <th key={field}>{field}</th>)}</tr></thead><tbody>{table.rows.map((row, index) => <tr key={index}>{table.fields.map((field) => <td key={field} title={row[field]}>{field === "master_record_id" && row[field]?.length > 24 ? <span className="history-long-id">{row[field].slice(0, 11)}…{row[field].slice(-10)}<button type="button" aria-label="Sao chép mã dòng thông tin" onClick={() => void navigator.clipboard.writeText(row[field]).then(() => notify("Đã sao chép mã dòng thông tin.")).catch(() => notify("Không sao chép được mã dòng thông tin."))}>Sao chép</button></span> : row[field]}</td>)}</tr>)}</tbody></table></div></div>)}</div>
    </div></div>}
    {canSplit && splitExportId && <SplitHistoryDialog runId={runId} exportId={splitExportId} onClose={() => setSplitExportId(null)}
      onCompleted={async () => { setSplitExportId(null); await Promise.all([load(), onChanged()]); notify("Đã tách dòng thông tin và tạo một phiên bản CSV mới."); }} />}
  </section>;

  return <section className={`resolution-flow ${stage === "review" ? "resolution-flow-review" : ""}`}>
    <div className={`resolution-sticky-head ${stage === "auto" || stage === "review" ? "is-sticky" : ""}`}>
    {stage === "review" && <div className="card review-filters" aria-label="Nhóm trường hợp cần xem xét">
      <label htmlFor="review-category">Lọc theo lý do cần kiểm tra
        <select id="review-category" value={category?.signature_hash ?? ""} disabled={busy} onChange={(event) => {
          setCategory(categories.find((item) => item.signature_hash === event.target.value) ?? null); setOffset(0); setSelected([]);
        }}><option value="">Tất cả đề xuất</option>
          {Array.from(new Set(categories.map((item) => item.primary_group))).map((group) => <optgroup key={group} label={categoryGroupLabel(group)}>
            {categories.filter((item) => item.primary_group === group).map((item) => <option key={item.signature_hash} value={item.signature_hash}>
              {subgroupLabel(item.subgroup)} · {item.unique_cluster_count.toLocaleString("vi-VN")} nhóm
            </option>)}
          </optgroup>)}
        </select>
      </label>
      <p>Mỗi dòng là một nhóm dòng thông tin cần đối chiếu. Chọn <strong>Xem so sánh</strong> để kiểm tra và quyết định.</p>
    </div>}
    <div className="resolution-flow-toolbar">
      <div><h2>{stage === "auto" ? "Danh sách chờ gộp" : "Danh sách cần xem xét"}</h2><p>{loading ? "Đang tải…" : loadError ? "Không tải được dữ liệu" : `${total.toLocaleString("vi-VN")} nhóm`}</p>{stage === "auto" && <p>Mỗi mục là một nhóm gộp dự kiến. Mở nhóm để xem hoặc thay đổi giá trị sẽ dùng khi xuất CSV.</p>}</div>
      {stage === "auto" && <div className="resolution-flow-actions">
        <label>Nguồn quyết định <select value={origin} onChange={(event) => { setOrigin(event.target.value); setOffset(0); }}><option value="">Tất cả đề xuất</option><option value="AUTO">Tự động</option><option value="REVIEW">Bạn đã chọn hoặc xác nhận</option></select></label>
        <button type="button" className="button primary" disabled={busy || loading || !!loadError || !canExport} title={!canExport ? "Tài khoản chưa có quyền xuất dữ liệu" : undefined} onClick={() => void previewCsv(false)}>{busy ? <><span className="button-spinner" />Đang tạo bản xem trước…</> : "Xem trước file kết quả (CSV) →"}</button>
      </div>}
      {stage === "review" && canReview && <div className="resolution-review-controls"><label className="resolution-select-all"><input type="checkbox" disabled={busy || loading || !!loadError} checked={!loading && !loadError && total > 0 && selected.length === total} onChange={() => void selectAll()} /> Chọn tất cả {loading || loadError ? "" : total.toLocaleString("vi-VN")} nhóm</label>
        {!loading && !loadError && selected.length > 0 && <div className="resolution-flow-actions"><strong className="resolution-selected-count">{selected.length.toLocaleString("vi-VN")} nhóm đã chọn</strong>
          <button type="button" className="button outline" disabled={busy || !canReject} onClick={() => void transition(selected, "no_merge")}>Không gộp ({selected.length.toLocaleString("vi-VN")})</button>
          <button type="button" className="button primary" disabled={busy || !canApprove} onClick={() => void transition(selected, "merge")}>Đưa sang Chờ gộp ({selected.length.toLocaleString("vi-VN")})</button>
        </div>}</div>}
    </div>
    </div>
    {category && stage === "review" && <div className="card resolution-category-guide"><div><strong>{categoryGroupLabel(category.primary_group)} · {subgroupLabel(category.subgroup)}</strong><p>{category.rule_template_vi}</p>
      {explanations[category.signature_hash] && <div className="resolution-ai-review"><strong>{explanations[category.signature_hash].status === "TEMPLATE" ? "Giải thích theo quy tắc" : "Giải thích từ AI"}</strong><p>{explanations[category.signature_hash].summary_vi}</p><small>AI giải thích tín hiệu tổng hợp, quyết định thuộc về bạn.</small></div>}</div>
      <button type="button" className="button outline" disabled={busy || !canExplain} onClick={() => void explainCategory(category)}>Giải thích bằng AI</button></div>}
    {stage === "review" ? <>
      <section className="card review-list-panel" aria-label="Nhóm cần xem xét">
        {renderItemList()}
        {renderPagination()}
      </section>
      {active && <div className="history-overlay review-detail-overlay" role="dialog" aria-modal="true"
        aria-labelledby="resolution-item-heading" aria-busy={busy}>
      <div className="review-detail-dialog"><div className="review-detail-grid">
        <section className="card review-comparison-panel">
          {active ? <>
            <div className="review-column-heading"><div><p className="section-kicker">SO SÁNH · {active.member_count.toLocaleString("vi-VN")} DÒNG THÔNG TIN</p>
              <h2 id="resolution-item-heading">{active.label || active.records[0]?.raw_fields.name || "Dòng thông tin cần xem xét"}</h2></div>
              <button type="button" className="review-close-button" aria-label={choicesDirty ? "Hủy thay đổi" : "Đóng"} onClick={closeItem}>×</button></div>
            <div className="resolution-record-strip">{active.records.map((record) => <span key={record.id} className={`resolution-source ${sourceColor(record.source_system)}`}><b>{record.source_system}</b> · {record.external_id}</span>)}</div>
            <div className="resolution-review-reason"><b>Lý do cần xem xét:</b> {reviewReason(active.reason)}</div>
            {active.merge_blocked_reason && <div className="resolution-readiness needs-input" role="alert">{active.merge_blocked_reason}</div>}
            {hasSemanticAddress && <p className="review-evidence-note">Địa chỉ đã được AI đối chiếu. Đây là tín hiệu hỗ trợ; hãy kiểm tra giá trị nguồn.</p>}
            <div className="resolution-field-list">
              {!!differentFields.length && <p className="resolution-field-section-label">Cần đối chiếu <span>Điểm khác biệt được tô màu</span></p>}
              {differentFields.map(renderField)}
              {emptyFields.map(renderField)}
              {!!sharedFields.length && <div className="resolution-shared-section"><button type="button" className="resolution-shared-toggle" aria-expanded={sharedOpen} onClick={() => setSharedOpen((open) => !open)}>{sharedFields.length} trường giống nhau <span aria-hidden="true">{sharedOpen ? "▴" : "▾"}</span></button>{sharedOpen && <div className="resolution-field-list">{sharedFields.map(renderField)}</div>}</div>}
            </div>
          </> : <div className="review-panel-empty"><span aria-hidden="true">◎</span><strong>{busy ? "Đang tải dữ liệu đối chiếu…" : "Chọn một nhóm trong danh sách"}</strong><p>Giá trị nguồn và các trường khác nhau sẽ hiện ở đây.</p></div>}
        </section>
        <aside className="card review-decision-panel" aria-label="Quyết định gộp">
          {active ? <>
            <div className="review-column-heading"><div><p className="section-kicker">GIÁ TRỊ ĐANG CHỌN</p><h3>Hồ sơ chuẩn dự kiến</h3></div></div>
            <dl className="review-golden-summary">{FIELDS.map((field) => {
              const options = active.field_options[field] ?? [];
              const groups = groupFieldOptions(options);
              const chosen = options.find((option) => option.source_record_id === choices[field]);
              const shared = groups.length === 1 ? groups[0] : null;
              const value = chosen?.value ?? shared?.value;
              const sources = chosen ? [chosen] : shared?.sources ?? [];
              return <div key={field}><dt>{labels[field]}</dt><dd><strong>{value || "Chưa chọn"}</strong>
                {sources.length > 0 && <small>{sources.map((source) => `${source.source_system} · ${source.external_id}`).join(", ")}</small>}</dd></div>;
            })}</dl>
            <div className="review-assistant-card"><div><strong>Giải thích bằng chứng</strong><p>AI chỉ tóm tắt tín hiệu; quyết định cuối vẫn thuộc về bạn.</p></div>
              <button type="button" className="button outline" disabled={busy || !canExplain} onClick={() => void explainActiveItem()}>Giải thích bằng AI</button>
              {itemExplanation?.itemId === active.id && <div className="resolution-ai-review"><strong>{itemExplanation.status === "TEMPLATE" ? "Giải thích theo quy tắc" : "Giải thích từ AI"}</strong><p>{itemExplanation.summary}</p></div>}
            </div>
            <div className="resolution-footer-status"><strong>{active.status === "REVIEW_STAGED_CONFLICT" ? "Nhóm đang chờ tách" : conflictFields.length ? `${conflictFields.length} trường xung đột` : differentFields.length ? `${selectedCount}/${differentFields.length} trường khác nhau đã có lựa chọn` : "Không có trường khác nhau cần chọn"}</strong>
              <small>{choicesDirty ? "Có thay đổi chưa lưu." : "Chưa có thay đổi để lưu."} {selectedCount < differentFields.length && "Có thể lưu từng phần và hoàn tất sau."}</small></div>
            <div className="review-decision-actions">
              {active.status !== "REVIEW_STAGED_CONFLICT" && <button type="button" className="button outline" disabled={busy || !choicesDirty || !canEdit} onClick={() => void save()}>Lưu lựa chọn</button>}
              {active.status === "REVIEW_STAGED_CONFLICT" && <button type="button" className="button outline" disabled={busy || !canReject} onClick={() => void splitConflict()}>Tách nhóm không hợp lệ</button>}
              <button type="button" className="button outline" disabled={busy || !canReject} onClick={() => void transition([active.id], "no_merge")}>Không gộp</button>
              <button type="button" className="button primary" disabled={busy || !canApprove} onClick={() => void transition([active.id], "merge")}>Đưa sang Chờ gộp →</button>
            </div>
          </> : <div className="review-panel-empty"><span aria-hidden="true">✓</span><strong>Quyết định có bằng chứng</strong><p>Đối chiếu các giá trị trước khi chọn Không gộp hoặc đưa sang Chờ gộp.</p></div>}
        </aside>
      </div></div></div>}
    </> : <>{renderItemList()}{renderPagination()}</>}

    {active && stage !== "review" && <div className="history-overlay" role="dialog" aria-modal="true" aria-labelledby="resolution-item-heading" onMouseDown={(event) => { if (event.target === event.currentTarget) closeItem(); }}>
      <div className="card resolution-item-dialog"><div className="resolution-dialog-head"><div><p className="section-kicker">{stage === "auto" ? "CHỜ GỘP" : "CẦN XEM XÉT"} · {active.member_count} DÒNG THÔNG TIN</p><h2 id="resolution-item-heading">{active.label || active.records[0]?.raw_fields.name}</h2><p>So sánh các giá trị nguồn trước khi quyết định.</p></div><button type="button" className="dialog-close" aria-label="Đóng" onClick={closeItem}>×</button></div>
        <div className="resolution-record-strip">{active.records.map((record) => <span key={record.id} className={`resolution-source ${sourceColor(record.source_system)}`}><b>{record.source_system}</b> · {record.external_id}</span>)}</div>
        {hasSemanticAddress && <div className="resolution-review-reason"><b>Địa chỉ đã được AI đối chiếu.</b> Đây là tín hiệu hỗ trợ; hãy xem giá trị nguồn trước khi quyết định.</div>}
        <div className="resolution-review-reason"><button type="button" className="button outline" disabled={busy || !canExplain} onClick={() => void explainActiveItem()}>Giải thích đề xuất bằng AI</button>
          {itemExplanation?.itemId === active.id && <div className="resolution-ai-review"><strong>{itemExplanation.status === "TEMPLATE" ? "Giải thích theo quy tắc" : "Giải thích từ AI"}</strong><p>{itemExplanation.summary}</p><small>AI chỉ giải thích tín hiệu; quyết định thuộc về bạn.</small></div>}
        </div>
        {active.merge_blocked_reason && <div className="resolution-readiness needs-input" role="alert">{active.merge_blocked_reason} Tách nhóm sẽ từ chối các liên kết chưa quyết định và giữ các nhóm khớp an toàn.</div>}
        {active.status === "STAGED_FOR_MERGE" && <p className="muted-copy">Các trường chưa sửa đã có giá trị mặc định. Bạn có thể đổi lựa chọn trước khi xem trước CSV.</p>}
        {!!active.choice_conflicts?.length && <p className="resolution-readiness needs-input" role="alert">Các nhóm được nối có lựa chọn khác nhau. Hãy chọn lại: {active.choice_conflicts.map((field) => labels[field] ?? field).join(", ")}.</p>}
        <div className="resolution-field-list">
          {!!differentFields.length && <p className="resolution-field-section-label">Cần đối chiếu <span>Phần tô màu là điểm khác biệt</span></p>}
          {differentFields.map(renderField)}
          {emptyFields.map(renderField)}
          {!!sharedFields.length && <div className="resolution-shared-section"><button type="button" className="resolution-shared-toggle" aria-expanded={sharedOpen} onClick={() => setSharedOpen((open) => !open)}>{sharedFields.length} trường giống nhau <span aria-hidden="true">{sharedOpen ? "▴" : "▾"}</span></button>{sharedOpen && <div className="resolution-field-list">{sharedFields.map(renderField)}</div>}</div>}
        </div>
        <div className="resolution-dialog-footer"><div className="resolution-footer-status">{active.status === "REVIEW_STAGED_CONFLICT" ? <><strong>Nhóm đang chờ tách</strong><small>Sau khi tách, chọn lại giá trị trên từng nhóm hợp lệ.</small></> : <><strong>{differentFields.length ? `${selectedCount}/${differentFields.length} trường khác nhau đã có lựa chọn` : "Không có trường khác nhau cần chọn"}</strong><small>{choicesDirty ? "Có thay đổi chưa lưu." : "Chưa có thay đổi để lưu."} {selectedCount < differentFields.length && "Cần chọn đủ trước khi xuất CSV."}</small></>}</div>
          <button type="button" className="button outline" disabled={busy} onClick={closeItem}>{choicesDirty ? "Hủy thay đổi" : "Đóng"}</button>
          {active.status !== "REVIEW_STAGED_CONFLICT" && <button type="button" className="button primary" disabled={busy || !choicesDirty || !canEdit} title={!choicesDirty ? "Chưa có thay đổi để lưu" : undefined} onClick={() => void save()}>Lưu lựa chọn</button>}
          {active.status === "REVIEW_STAGED_CONFLICT" && <button type="button" className="button primary" disabled={busy || !canReject} onClick={() => void splitConflict()}>Tách nhóm không hợp lệ</button>}
        </div>
      </div>
    </div>}
    {confirmExport && <div className="history-overlay" role="dialog" aria-modal="true" aria-labelledby="export-warning-heading"><div className="card resolution-confirm-dialog"><h2 id="export-warning-heading">Còn cặp đang chờ xem xét</h2><p>CSV vẫn bao gồm toàn bộ dữ liệu đầu vào. Những dòng thông tin chưa được xác nhận gộp sẽ được giữ riêng.</p><div><button type="button" className="button outline" onClick={() => setConfirmExport(false)}>Quay lại</button><button type="button" className="button primary" disabled={busy} onClick={() => void previewCsv(true)}>{busy ? <><span className="button-spinner" />Đang tạo lại…</> : "Tiếp tục xem trước"}</button></div></div></div>}
    {preview && <div className="history-overlay" role="dialog" aria-modal="true" aria-labelledby="preview-heading"><div className="card resolution-confirm-dialog"><h2 id="preview-heading">Xem trước file kết quả (CSV)</h2>{!!preview.accepted_pair_count && <p>Đã kiểm tra {preview.accepted_pair_count.toLocaleString("vi-VN")} cặp bạn đưa sang Chờ gộp. Các cặp sẽ được xác nhận khi xuất CSV.</p>}<p>{preview.eligible_count.toLocaleString("vi-VN")} nhóm gộp dự kiến. CSV dự kiến có {preview.estimated_output_rows.toLocaleString("vi-VN")} dòng, gồm {(preview.separate_record_count ?? Math.max(0, preview.estimated_output_rows - preview.eligible_count)).toLocaleString("vi-VN")} dòng thông tin giữ riêng.</p><p>{preview.review_required_count > 0 && "Những dòng thông tin chưa được xác nhận gộp sẽ được giữ riêng."} Bản xem trước hết hạn lúc {new Date(preview.expires_at).toLocaleTimeString("vi-VN")}.</p>{previewStale && <p role="alert">Bản xem trước đã cũ vì dữ liệu hoặc quyết định thay đổi. Tạo lại để tiếp tục.</p>}<div><button type="button" className="button outline" disabled={busy} onClick={() => { setPreview(null); setPreviewStale(false); }}>Quay lại</button>{previewStale ? <button type="button" className="button primary" disabled={busy} onClick={() => void previewCsv(preview.review_required_count > 0)}>{busy ? <><span className="button-spinner" />Đang tạo lại…</> : "Tạo lại bản xem trước"}</button> : <button type="button" className="button primary" disabled={busy} onClick={() => void executeCsv()}>{busy ? <><span className="button-spinner" />Đang xuất CSV…</> : "Xác nhận gộp và xuất CSV"}</button>}</div></div></div>}
  </section>;
}
