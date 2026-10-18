import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ResolutionFlow from "./ResolutionFlow";
import ResolutionWorkspace from "./ResolutionWorkspace";
import { ApiError, createResolutionPreview, executeResolutionPreview, getResolutionExportReadiness, getResolutionItem,
  getResolutionItems, getResolutionOverview, getResolutionRuns, getReviewCategories, resolveMergeConflict,
  getSeparateRecords, explainResolutionItem, saveStagedGroupChoices, transitionReviewItems } from "@/lib/api";
import type { AuthUser } from "@/lib/contracts";
import { getSavedChoices } from "@/lib/resolutionChoices";

vi.mock("@/lib/api", () => ({
  ApiError: class extends Error { constructor(message: string, readonly status: number, readonly reason?: string) { super(message); } },
  compareResolutionExport: vi.fn(), createResolutionPreview: vi.fn(), executeResolutionPreview: vi.fn(), downloadResolutionExport: vi.fn(),
  downloadResolutionSource: vi.fn(), explainReviewCategory: vi.fn(), explainResolutionItem: vi.fn(),
  getResolutionExportReadiness: vi.fn(), getResolutionExports: vi.fn(), getResolutionItem: vi.fn(),
  getResolutionItems: vi.fn(), getResolutionOverview: vi.fn(), getResolutionRuns: vi.fn(), getSeparateRecords: vi.fn(),
  getReviewCategories: vi.fn(), resolveMergeConflict: vi.fn(), setCsrfToken: vi.fn(),
  saveStagedGroupChoices: vi.fn(), unstageMergeGroup: vi.fn(),
  getPairCategories: vi.fn().mockResolvedValue([]), decideReviewPair: vi.fn(),
  getResolutionAnalytics: vi.fn().mockResolvedValue({
    decisions: { total: 3, automatic: 0, review: 2, no_duplicate_detected: 1, automatic_percent: 0 },
    conflicts: { review_pair_count: 1, fields: [] }, quality: { sources: [] },
  }),
  transitionReviewItems: vi.fn(),
}));

const steward: AuthUser = { id: "user-1", email: "a@example.test", csrf_token: "csrf", role: "STEWARD", capabilities: ["read", "create_run", "start_run", "approve_pair", "reject_pair", "edit_golden", "explain", "export_data"] };

const reviewItem = { id: "case-1", bucket: "review" as const, kind: "review" as const,
  origin: "REVIEW" as const, member_record_ids: ["a", "b"], member_count: 2,
  pair_count: 1, label: "An Bình" };
const records = [
  { id: "a", external_id: "A", source_system: "CRM", raw_fields: { name: "An Bình", phone: "0901001001", email: "same@example.test", address: "" } },
  { id: "b", external_id: "B", source_system: "ERP", raw_fields: { name: "An Bình", phone: "0901001002", email: "same@example.test", address: "" } },
];
const detail = {
  ...reviewItem, records, choices: {}, missing_fields: ["phone"],
  field_options: {
    name: records.map((row) => ({ source_record_id: row.id, source_system: row.source_system, external_id: row.external_id, value: row.raw_fields.name })),
    phone: records.map((row) => ({ source_record_id: row.id, source_system: row.source_system, external_id: row.external_id, value: row.raw_fields.phone })),
    email: records.map((row) => ({ source_record_id: row.id, source_system: row.source_system, external_id: row.external_id, value: row.raw_fields.email })),
    address: [],
  },
};

describe("Resolution item flow", () => {
  beforeEach(() => {
    vi.mocked(getResolutionItems).mockResolvedValue({ total: 1, items: [reviewItem] });
    vi.mocked(getResolutionItem).mockResolvedValue(detail);
    vi.mocked(getReviewCategories).mockResolvedValue([]);
    vi.mocked(getResolutionExportReadiness).mockResolvedValue({ merge_count: 1, pending_review_count: 0, staged_conflict_count: 0,
      incomplete_count: 0, incomplete_items: [], blockers: [], can_export: true });
    vi.mocked(transitionReviewItems).mockResolvedValue({ decision: "merge", changed_count: 1, changed_ids: ["proposal-1"] });
    vi.mocked(createResolutionPreview).mockResolvedValue({ preview_id: "preview-1", status: "OPEN",
      eligible_count: 1, review_required_count: 2, separate_record_count: 1, estimated_output_rows: 2,
      policy_version: "person-linh-v1", expires_at: new Date(Date.now() + 600000).toISOString() });
    vi.mocked(executeResolutionPreview).mockResolvedValue(undefined);
    vi.mocked(resolveMergeConflict).mockResolvedValue({ rejected_pair_count: 1 });
  });
  afterEach(() => { cleanup(); vi.useRealTimers(); vi.clearAllMocks(); localStorage.clear(); window.history.replaceState({}, "", "/app"); });

  it("lets a viewer inspect records but prevents decisions and field edits", async () => {
    const viewer: AuthUser = { ...steward, role: "VIEWER", capabilities: ["read"] };
    render(<ResolutionFlow user={viewer} runId="run-1" stage="review" onChanged={vi.fn()} notify={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: /Xem so sánh/ }));
    const dialog = await screen.findByRole("dialog");
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: /Đưa sang Chờ gộp/ })).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Không gộp" })).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Lưu lựa chọn" })).toBeDisabled();
    within(dialog).getAllByRole("radio").forEach((radio) => expect(radio).toBeDisabled());
    fireEvent.click(within(dialog).getByRole("button", { name: /Đưa sang Chờ gộp/ }));
    expect(transitionReviewItems).not.toHaveBeenCalled();
  });

  it.each([
    ["READY", "Giải thích từ AI"],
    ["TEMPLATE", "Giải thích theo quy tắc"],
  ])("labels a %s explanation by where its text came from", async (status, label) => {
    vi.mocked(explainResolutionItem).mockResolvedValue({ status, explanation: { summary_vi: "Cần xem tên.",
      attention_fields: ["name"], limitations: [] } });
    render(<ResolutionFlow user={steward} runId="run-1" stage="review" onChanged={vi.fn()} notify={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: /Xem so sánh/ }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: /Giải thích bằng AI/ }));
    expect(await within(dialog).findByText(label)).toBeInTheDocument();
    expect(within(dialog).getByText("Cần xem tên.")).toBeInTheDocument();
  });

  it("tells the steward to reload instead of showing a stale explanation", async () => {
    vi.mocked(explainResolutionItem).mockResolvedValue({ status: "STALE", explanation: null });
    const notify = vi.fn();
    render(<ResolutionFlow user={steward} runId="run-1" stage="review" onChanged={vi.fn()} notify={notify} />);
    fireEvent.click(await screen.findByRole("button", { name: /Xem so sánh/ }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: /Giải thích bằng AI/ }));
    await waitFor(() => expect(notify).toHaveBeenCalledWith("Dữ liệu đã thay đổi; hãy tải lại giải thích."));
    expect(within(dialog).queryByText("Giải thích từ AI")).not.toBeInTheDocument();
  });

  it("does not enable export for a viewer even when backend readiness has no blockers", async () => {
    const viewer: AuthUser = { ...steward, role: "VIEWER", capabilities: ["read"] };
    render(<ResolutionFlow user={viewer} runId="run-1" stage="auto" onChanged={vi.fn()} notify={vi.fn()} />);
    await screen.findByRole("button", { name: /Xem so sánh/ });
    expect(screen.getByRole("button", { name: /Xem trước file kết quả \(CSV\)/ })).toBeDisabled();
    expect(createResolutionPreview).not.toHaveBeenCalled();
  });

  it("shows equal fields once and lets the steward select a differing source", async () => {
    render(<ResolutionFlow user={steward} runId="run-1" stage="review" onChanged={vi.fn()} notify={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: /Xem so sánh/ }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("button", { name: /2 trường giống nhau/ })).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(within(dialog).getByRole("button", { name: /2 trường giống nhau/ }));
    const comparison = within(dialog.querySelector(".review-comparison-panel") as HTMLElement);
    expect(comparison.getAllByText("same@example.test")).toHaveLength(1);
    expect(comparison.getAllByText("An Bình")).toHaveLength(2); // title and shared field
    fireEvent.click(within(dialog).getByRole("radio", { name: /0901001002/ }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Lưu lựa chọn" }));
    await waitFor(() => expect(getSavedChoices("run-1", "review", "case-1", ["a", "b"])).toEqual({ phone: "b" }));
  });

  it("filters by the visible category and explains why an item needs review", async () => {
    const category = { primary_group: "A", subgroup: "A1_NAME_PHONE", signature_hash: "category-key",
      pair_count: 2, unique_cluster_count: 1,
      rule_template_vi: "Tên và thông tin liên hệ giống nhau, nhưng cần đối chiếu trước khi gộp." };
    vi.mocked(getReviewCategories).mockResolvedValue([category]);
    vi.mocked(getResolutionItems).mockResolvedValue({ total: 1, items: [{ ...reviewItem,
      primary_group: "A", subgroup: "A1_NAME_PHONE", reason: "POLICY_BLOCKED" }] });
    vi.mocked(getResolutionItem).mockResolvedValue({ ...detail, reason: "POLICY_BLOCKED", pairs: [{
      id: "pair-1", left_external_id: "A", right_external_id: "B", score: 0.8,
      decision: "REVIEW", reason: "POLICY_BLOCKED", field_evidence: [],
    }] });
    render(<ResolutionFlow user={steward} runId="run-1" stage="review" onChanged={vi.fn()} notify={vi.fn()} />);
    await screen.findByRole("option", { name: /Tên và số điện thoại giống nhau/ });
    fireEvent.change(screen.getByLabelText("Lọc theo lý do cần kiểm tra"), { target: { value: "category-key" } });
    await waitFor(() => expect(getResolutionItems).toHaveBeenCalledWith("run-1", "review",
      expect.objectContaining({ primary_group: "A", subgroup: "A1_NAME_PHONE" })));
    expect(screen.getByText(/Tên và thông tin liên hệ giống nhau, nhưng chưa đủ điều kiện gộp tự động/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Xem so sánh/ }));
    await waitFor(() => expect(getResolutionItem).toHaveBeenCalledWith("run-1", "review", "case-1", category));
    const dialogNode = await screen.findByRole("dialog");
    const dialog = within(dialogNode);
    expect(dialog.getByText(/Tên và thông tin liên hệ giống nhau, nhưng chưa đủ điều kiện gộp tự động/)).toBeInTheDocument();
    expect(within(dialogNode.querySelector(".review-comparison-panel") as HTMLElement).getByText("Điện thoại")).toBeInTheDocument();
    expect(dialog.queryByText("Bằng chứng theo cặp")).not.toBeInTheDocument();
  });

  it("lets a conflicting item enter Merge staging and exposes the split action", async () => {
    vi.mocked(getResolutionItems).mockResolvedValue({ total: 1, items: [{ ...reviewItem, can_merge: false }] });
    vi.mocked(getResolutionItem).mockResolvedValue({ ...detail,
      merge_blocked_reason: "Nhóm có cặp Không gộp." });
    const props = { user: steward, runId: "run-1", onChanged: vi.fn(), notify: vi.fn() };
    const { rerender } = render(<ResolutionFlow {...props} stage="review" />);
    fireEvent.click(await screen.findByRole("button", { name: /Xem so sánh/ }));
    const reviewDialog = await screen.findByRole("dialog");
    expect(within(reviewDialog).getByRole("button", { name: /Đưa sang Chờ gộp/ })).toBeEnabled();
    fireEvent.click(within(reviewDialog).getAllByRole("button", { name: "Đóng" })[0]);

    vi.mocked(getResolutionItems).mockResolvedValue({ total: 1, items: [{ ...reviewItem,
      bucket: "merge", kind: "proposal", status: "REVIEW_STAGED_CONFLICT" }] });
    vi.mocked(getResolutionItem).mockResolvedValue({ ...detail, bucket: "merge", kind: "proposal",
      status: "REVIEW_STAGED_CONFLICT", merge_blocked_reason: "Nhóm còn cặp Không gộp." });
    vi.mocked(getResolutionExportReadiness).mockResolvedValue({ merge_count: 0, pending_review_count: 0,
      staged_conflict_count: 1, incomplete_count: 0, incomplete_items: [], blockers: [], can_export: false });
    rerender(<ResolutionFlow {...props} stage="auto" />);
    fireEvent.click(await screen.findByRole("button", { name: /Xem so sánh/ }));
    const mergeDialog = await screen.findByRole("dialog");
    expect(within(mergeDialog).queryAllByRole("radio")).toHaveLength(0);
    fireEvent.click(within(mergeDialog).getByRole("button", { name: "Tách nhóm không hợp lệ" }));
    await waitFor(() => expect(resolveMergeConflict).toHaveBeenCalledWith("run-1", "case-1"));
  });

  it("opens comparison from the table and returns focus to its row on close", async () => {
    render(<ResolutionFlow user={steward} runId="run-1" stage="review" onChanged={vi.fn()} notify={vi.fn()} />);
    const opener = await screen.findByRole("button", { name: /Xem so sánh/ });
    opener.focus();
    fireEvent.click(opener);
    const dialog = await screen.findByRole("dialog", { name: "An Bình" });
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(screen.getByRole("complementary", { name: "Quyết định gộp" })).toBeInTheDocument();
    const close = within(dialog).getAllByRole("button", { name: "Đóng" })[0];
    await waitFor(() => expect(close).toHaveFocus());
    fireEvent.click(close);
    await waitFor(() => expect(opener).toHaveFocus());
  });

  it("shows a skeleton immediately on tab change and ignores a late response from the previous tab", async () => {
    const notify = vi.fn();
    const props = { user: steward, runId: "run-1", onChanged: vi.fn(), notify };
    const { rerender } = render(<ResolutionFlow {...props} stage="review" />);
    expect(await screen.findByText("An Bình")).toBeInTheDocument();

    let resolveOld!: (value: { total: number; items: typeof reviewItem[] }) => void;
    vi.mocked(getResolutionItems).mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }));
    rerender(<ResolutionFlow {...props} stage="auto" />);
    expect(screen.getByRole("status", { name: "Đang tải danh sách" })).toBeInTheDocument();
    expect(screen.queryByText("An Bình")).not.toBeInTheDocument();

    vi.mocked(getResolutionItems).mockResolvedValueOnce({ total: 1, items: [{ ...reviewItem, id: "new-item", label: "Dòng thông tin mới" }] });
    rerender(<ResolutionFlow {...props} stage="review" />);
    expect(await screen.findByText("Dòng thông tin mới")).toBeInTheDocument();
    await act(async () => { resolveOld({ total: 1, items: [{ ...reviewItem, label: "Kết quả cũ" }] }); });
    expect(screen.getByText("Dòng thông tin mới")).toBeInTheDocument();
    expect(screen.queryByText("Kết quả cũ")).not.toBeInTheDocument();
  });

  it("offers a retry when the list request fails", async () => {
    vi.mocked(getResolutionItems).mockRejectedValueOnce(new Error("Máy chủ tạm thời không phản hồi"));
    render(<ResolutionFlow user={steward} runId="run-1" stage="review" onChanged={vi.fn()} notify={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Máy chủ tạm thời không phản hồi");
    expect(screen.queryByText("An Bình")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Thử lại" }));
    expect(await screen.findByText("An Bình")).toBeInTheDocument();
  });

  it("shows loading feedback while changing pages", async () => {
    vi.mocked(getResolutionItems).mockResolvedValueOnce({ total: 26, items: [reviewItem] });
    const { container } = render(<ResolutionFlow user={steward} runId="run-1" stage="review" onChanged={vi.fn()} notify={vi.fn()} />);
    expect(await screen.findByText("An Bình")).toBeInTheDocument();
    let resolvePage!: (value: { total: number; items: typeof reviewItem[] }) => void;
    vi.mocked(getResolutionItems).mockImplementationOnce(() => new Promise((resolve) => { resolvePage = resolve; }));
    fireEvent.click(screen.getByRole("button", { name: "Sau →" }));
    expect(screen.getByRole("status", { name: "Đang tải danh sách" })).toBeInTheDocument();
    expect(container.querySelector(".review-table-region")).toHaveAttribute("aria-busy", "true");
    expect(screen.getByRole("button", { name: "Sau →" })).toBeDisabled();
    await act(async () => { resolvePage({ total: 26, items: [{ ...reviewItem, id: "page-two", label: "Trang tiếp theo" }] }); });
    expect(await screen.findByText("Trang tiếp theo")).toBeInTheDocument();
    expect(container.querySelector(".review-table-region")).toHaveAttribute("aria-busy", "false");
  });

  it("jumps to a typed page and keeps review actions inside the list toolbar", async () => {
    vi.mocked(getResolutionItems).mockResolvedValue({ total: 76, items: [reviewItem] });
    const { container } = render(<ResolutionFlow user={steward} runId="run-1" stage="review" onChanged={vi.fn()} notify={vi.fn()} />);
    expect(await screen.findByText("An Bình")).toBeInTheDocument();
    const toolbar = container.querySelector(".resolution-flow-toolbar")!;
    fireEvent.click(screen.getByRole("checkbox", { name: "Chọn An Bình" }));
    expect(within(toolbar as HTMLElement).getByText("1 nhóm đã chọn")).toBeInTheDocument();
    expect(within(toolbar as HTMLElement).getByRole("button", { name: /Đưa sang Chờ gộp/ })).toBeInTheDocument();
    expect(container.querySelector(".resolution-bulk-bar")).not.toBeInTheDocument();

    fireEvent.change(screen.getByRole("spinbutton", { name: "Số trang muốn đến" }), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "Đi" }));
    await waitFor(() => expect(getResolutionItems).toHaveBeenCalledWith("run-1", "review", expect.objectContaining({ offset: 50, limit: 25 })));
    expect(screen.getByRole("spinbutton", { name: "Số trang muốn đến" })).toHaveValue(3);
  });

  it("groups equal values without losing the selected source and saves partial choices", async () => {
    const third = { id: "c", external_id: "C", source_system: "BILL", raw_fields: {
      name: "An Bình", phone: "0901001003", email: "same@example.test", address: "Đà Nẵng",
    } };
    const address = [
      { source_record_id: "a", source_system: "CRM", external_id: "A", value: "Hà Nội" },
      { source_record_id: "b", source_system: "ERP", external_id: "B", value: "Hà Nội" },
      { source_record_id: "c", source_system: "BILL", external_id: "C", value: "Đà Nẵng" },
    ];
    vi.mocked(getResolutionItem).mockResolvedValue({ ...detail, records: [...records, third],
      choices: { address: "b" }, missing_fields: ["phone"],
      field_options: { ...detail.field_options, address },
      pairs: [{ id: "pair-1", left_external_id: "A", right_external_id: "B", score: 0.6,
        decision: "REVIEW", reason: "POLICY_BLOCKED",
        field_evidence: [{ field: "address", state: "DF", similarity: 0.3, left: "Hà Nội",
          right: "Đà Nẵng", normalization_tags: [] }] }],
    });
    render(<ResolutionFlow user={steward} runId="run-1" stage="review" onChanged={vi.fn()} notify={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: /Xem so sánh/ }));
    const dialog = await screen.findByRole("dialog");
    const comparison = within(dialog.querySelector(".review-comparison-panel") as HTMLElement);
    const addressRow = comparison.getByText("Địa chỉ").closest<HTMLElement>(".resolution-field")!;
    expect(within(addressRow).getByText("2 giá trị khác nhau (không phải xung đột)")).toBeInTheDocument();
    expect(within(addressRow).getAllByRole("radio")).toHaveLength(2);
    expect(within(addressRow).getAllByRole("radio")[0]).toBeChecked();
    expect(within(addressRow).getByText(/ERP · B/)).toBeInTheDocument();
    expect(within(dialog).getByText("1/2 trường khác nhau đã có lựa chọn")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Lưu lựa chọn" })).toBeDisabled();
    fireEvent.click(within(dialog).getByRole("radio", { name: /0901001002/ }));
    expect(within(dialog).getByText("2/2 trường khác nhau đã có lựa chọn")).toBeInTheDocument();
    expect(within(dialog).getByText(/Có thay đổi chưa lưu/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Lưu lựa chọn" }));
    await waitFor(() => expect(getSavedChoices("run-1", "review", "case-1", ["a", "b"])).toEqual({ address: "b", phone: "b" }));
  });

  it("shows address as different value but not a conflict when only address differs", async () => {
    vi.mocked(getResolutionItem).mockResolvedValue({ ...detail,
      field_options: { ...detail.field_options, address: [
        { source_record_id: "a", source_system: "CRM", external_id: "A", value: "268 đường Nguyễn Trãi" },
        { source_record_id: "b", source_system: "ERP", external_id: "B", value: "683 đường Trần Phú" },
      ] },
      pairs: [{ id: "pair-1", left_external_id: "A", right_external_id: "B", score: 0.6,
        decision: "REVIEW", reason: "POLICY_BLOCKED",
        field_evidence: [{ field: "address", state: "DF", similarity: 0.3, left: "268 đường Nguyễn Trãi",
          right: "683 đường Trần Phú", normalization_tags: [] }] }],
    });
    render(<ResolutionFlow user={steward} runId="run-1" stage="review" onChanged={vi.fn()} notify={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: /Xem so sánh/ }));
    const dialog = await screen.findByRole("dialog");
    const comparison = within(dialog.querySelector(".review-comparison-panel") as HTMLElement);
    const addressRow = comparison.getByText("Địa chỉ").closest<HTMLElement>(".resolution-field")!;
    expect(within(addressRow).getByText("2 giá trị khác nhau (không phải xung đột)")).toBeInTheDocument();
    expect(comparison.queryByText("trường xung đột")).not.toBeInTheDocument();
  });

  it("counts a field as conflict when engine state is CF", async () => {
    vi.mocked(getResolutionItem).mockResolvedValue({ ...detail,
      field_options: { ...detail.field_options, phone: [
        { source_record_id: "a", source_system: "CRM", external_id: "A", value: "0942963372" },
        { source_record_id: "b", source_system: "ERP", external_id: "B", value: "0901234567" },
      ] },
      pairs: [{ id: "pair-1", left_external_id: "A", right_external_id: "B", score: 0.6,
        decision: "REVIEW", reason: "POLICY_BLOCKED",
        field_evidence: [{ field: "phone", state: "CF", similarity: 0.0, left: "0942963372",
          right: "0901234567", normalization_tags: [] }] }],
    });
    render(<ResolutionFlow user={steward} runId="run-1" stage="review" onChanged={vi.fn()} notify={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: /Xem so sánh/ }));
    const dialog = await screen.findByRole("dialog");
    const comparison = within(dialog.querySelector(".review-comparison-panel") as HTMLElement);
    const phoneRow = comparison.getByText("Điện thoại").closest<HTMLElement>(".resolution-field")!;
    expect(within(phoneRow).getByText("2 giá trị khác nhau")).toBeInTheDocument();
    expect(within(dialog).getByText(/trường xung đột/)).toBeInTheDocument();
    expect(within(dialog).getByText("1 trường xung đột")).toBeInTheDocument();
    expect(within(phoneRow).queryByText("không phải xung đột")).not.toBeInTheDocument();
  });

  it("allows saving an incomplete choice and discards unsaved edits on cancel", async () => {
    vi.mocked(getResolutionItem).mockResolvedValue({ ...detail, missing_fields: ["phone", "address"],
      field_options: { ...detail.field_options, address: [
        { source_record_id: "a", source_system: "CRM", external_id: "A", value: "Hà Nội" },
        { source_record_id: "b", source_system: "ERP", external_id: "B", value: "Đà Nẵng" },
      ] },
    });
    render(<ResolutionFlow user={steward} runId="run-1" stage="review" onChanged={vi.fn()} notify={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: /Xem so sánh/ }));
    let dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/Có thể lưu từng phần/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("radio", { name: /0901001002/ }));
    expect(within(dialog).getByRole("button", { name: "Lưu lựa chọn" })).toBeEnabled();
    expect(within(dialog).getByText("1/2 trường khác nhau đã có lựa chọn")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Hủy thay đổi" }));
    fireEvent.click(await screen.findByRole("button", { name: /Xem so sánh/ }));
    dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("radio", { name: /0901001002/ })).not.toBeChecked();
    fireEvent.click(within(dialog).getByRole("radio", { name: /0901001002/ }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Lưu lựa chọn" }));
    await waitFor(() => expect(getSavedChoices("run-1", "review", "case-1", ["a", "b"])).toEqual({ phone: "b" }));
  });

  it("allows a partial save in Merge while deferring export checks until preview", async () => {
    vi.mocked(getResolutionExportReadiness).mockResolvedValue({ merge_count: 1, pending_review_count: 0, staged_conflict_count: 0,
      incomplete_count: 1, incomplete_items: [{ item_id: "case-1", fields: ["address"] }], blockers: [], can_export: false });
    vi.mocked(getResolutionItem).mockResolvedValue({ ...detail, origin: "REVIEW", missing_fields: ["phone", "address"],
      field_options: { ...detail.field_options, address: [
        { source_record_id: "a", source_system: "CRM", external_id: "A", value: "Hà Nội" },
        { source_record_id: "b", source_system: "ERP", external_id: "B", value: "Đà Nẵng" },
      ] },
    });
    render(<ResolutionFlow user={steward} runId="run-1" stage="auto" onChanged={vi.fn()} notify={vi.fn()} />);
    expect(await screen.findByRole("button", { name: /Xem trước file kết quả \(CSV\)/ })).toBeEnabled();
    expect(getResolutionExportReadiness).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /Xem so sánh/ }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("radio", { name: /0901001002/ }));
    expect(within(dialog).getByText(/cần chọn đủ trước khi xuất CSV/i)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Lưu lựa chọn" }));
    await waitFor(() => expect(getSavedChoices("run-1", "merge", "case-1", ["a", "b"])).toEqual({ phone: "b" }));
    expect(createResolutionPreview).not.toHaveBeenCalled();
  });

  it("sends the optional field choice when moving an item from review to merge", async () => {
    render(<ResolutionFlow user={steward} runId="run-1" stage="review" onChanged={vi.fn()} notify={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: /Xem so sánh/ }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("radio", { name: /0901001002/ }));
    fireEvent.click(within(dialog).getByRole("button", { name: /Đưa sang Chờ gộp/ }));
    await waitFor(() => expect(getSavedChoices("run-1", "review", "case-1", ["a", "b"])).toEqual({ phone: "b" }));
    await waitFor(() => expect(transitionReviewItems).toHaveBeenCalledWith("run-1", ["case-1"], "merge"));
    await waitFor(() => expect(getSavedChoices("run-1", "merge", "proposal-1", ["a", "b"])).toEqual({ phone: "b" }));
  });

  it("requires confirmation when exporting with review items still pending", async () => {
    vi.mocked(getResolutionExportReadiness).mockResolvedValue({ merge_count: 1, pending_review_count: 2, staged_conflict_count: 0,
      incomplete_count: 0, incomplete_items: [], blockers: [], can_export: true });
    vi.mocked(getResolutionItems).mockResolvedValue({ total: 0, items: [] });
    vi.mocked(createResolutionPreview).mockRejectedValueOnce(new ApiError("Còn mục cần xem xét; cần xác nhận xuất các dòng đó riêng lẻ", 409, "PENDING_REVIEW"));
    render(<ResolutionFlow user={steward} runId="run-1" stage="auto" onChanged={vi.fn()} notify={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: /Xem trước file kết quả \(CSV\)/ }));
    expect(createResolutionPreview).toHaveBeenCalledWith("run-1", false);
    fireEvent.click(await screen.findByRole("button", { name: "Tiếp tục xem trước" }));
    await waitFor(() => expect(createResolutionPreview).toHaveBeenCalledWith("run-1", true));
    fireEvent.click(await screen.findByRole("button", { name: "Xác nhận gộp và xuất CSV" }));
    await waitFor(() => expect(executeResolutionPreview).toHaveBeenCalledWith("run-1", "preview-1", expect.any(String)));
  });

  it("edits a staged group and saves the choice on the server", async () => {
    const staged = { ...reviewItem, id: "staged-group", bucket: "merge" as const,
      kind: "proposal" as const, status: "STAGED_FOR_MERGE", revision: 2 };
    vi.mocked(getResolutionItems).mockResolvedValue({ total: 1, items: [staged] });
    vi.mocked(getResolutionItem).mockResolvedValue({ ...detail, ...staged,
      choices: { phone: "a" }, missing_fields: [] });
    vi.mocked(saveStagedGroupChoices).mockResolvedValue({ revision: 3, choices: { phone: "b" } });
    render(<ResolutionFlow user={steward} runId="run-1" stage="auto" onChanged={vi.fn()} notify={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: /Xem so sánh/ }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("radio", { name: /0901001002/ }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Lưu lựa chọn" }));
    await waitFor(() => expect(saveStagedGroupChoices).toHaveBeenCalledWith(
      "run-1", "staged-group", 2, { phone: "b" }));
  });

  it("reports an export blocker only after preview is requested", async () => {
    vi.mocked(getResolutionItems).mockResolvedValue({ total: 1, items: [{ ...reviewItem, bucket: "merge", kind: "proposal" }] });
    vi.mocked(getResolutionExportReadiness).mockResolvedValue({ merge_count: 1, pending_review_count: 0,
      staged_conflict_count: 0, incomplete_count: 1, incomplete_items: [{ item_id: "case-1", fields: ["phone"] }],
      blockers: [{ item_id: "case-1", kind: "MISSING_CHOICES", label: "An Bình", message: "Chọn giá trị cho các trường khác nhau.", fields: ["phone"] }], can_export: false });
    vi.mocked(createResolutionPreview).mockRejectedValueOnce(new ApiError("Còn giá trị chưa chọn hoặc nhóm cần tách trước khi xem trước", 409));
    const notify = vi.fn();
    render(<ResolutionFlow user={steward} runId="run-1" stage="auto" onChanged={vi.fn()} notify={notify} />);
    const preview = await screen.findByRole("button", { name: /Xem trước file kết quả \(CSV\)/ });
    expect(preview).toBeEnabled();
    expect(getResolutionExportReadiness).not.toHaveBeenCalled();
    fireEvent.click(preview);
    await waitFor(() => expect(notify).toHaveBeenCalledWith("Còn giá trị chưa chọn hoặc nhóm cần tách trước khi xem trước"));
  });

  it("shows user-facing outcome metrics with separate records hidden", async () => {
    window.history.replaceState({}, "", "/app?run=run-1");
    vi.mocked(getResolutionRuns).mockResolvedValue({ total: 0, items: [] });
    vi.mocked(getResolutionOverview).mockResolvedValue({ run_id: "run-1", status: "COMPLETED", phase: "DONE",
      processed_count: 4, total_count: 4, record_count: 4, file_count: 1, source_counts: { CRM: 4 },
      history_count: 0, candidate_count: 2, estimated_entity_count: 3, duplicate_record_count: 1, eligible_merge_group_count: 1,
      counts: { auto: 1, review: 1, separate: 2, rejected: 0, review_cases: 1, manual: 0,
        hard_blocked: 0, insufficient: 0, group_a: 0, group_c: 0, group_d: 0 } });
    vi.mocked(getSeparateRecords).mockResolvedValue({ total: 2, items: [{ id: "a", external_id: "CRM-1",
      source_system: "CRM", raw_fields: { name: "Nguyễn A", phone: "0901234567" }, review_pending: true }] });
    render(<ResolutionWorkspace user={steward} onLogout={vi.fn()} />);
    expect((await screen.findAllByText("Số hồ sơ dự kiến")).some((label) => label.closest(".overview-kpi"))).toBe(true);
    expect(screen.queryByRole("button", { name: /Xem dòng thông tin được giữ riêng|Dự kiến giữ riêng/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "Dự kiến giữ riêng" })).not.toBeInTheDocument();
    expect(getSeparateRecords).not.toHaveBeenCalled();
  });

  it("uses a direct run URL and does not auto-select on /app without run query", async () => {
    window.history.replaceState({}, "", "/app");
    vi.mocked(getResolutionRuns).mockResolvedValue({
      total: 2,
      items: [
        {
          run_id: "run-hub-1", name: "Đợt CRM và ERP tháng 10", status: "COMPLETED",
          created_at: "2026-10-18T10:00:00Z", record_count: 500, file_count: 2,
          files: ["crm.csv", "erp.csv"],
        },
        {
          run_id: "run-hub-2", name: "Đợt Billing tháng 9", status: "RUNNING",
          created_at: "2026-10-17T09:00:00Z", record_count: 1200, file_count: 1,
          files: ["billing.csv"],
        },
      ],
    });
    render(<ResolutionWorkspace user={steward} onLogout={vi.fn()} />);
    const heading = await screen.findByRole("heading", { name: "Quản lý các đợt xử lý dữ liệu" });
    const landing = heading.closest("section")!;
    expect(within(landing).getByText("Đợt CRM và ERP tháng 10")).toBeInTheDocument();
    expect(within(landing).getByText("Đợt Billing tháng 9")).toBeInTheDocument();
    expect(within(landing).getByRole("button", { name: /＋ Nạp dữ liệu mới/ })).toBeInTheDocument();
  });

  it("uses a direct run URL before the saved run hint", async () => {
    localStorage.setItem("masterone.resolution-run.v1.user-1", "old-run");
    window.history.replaceState({}, "", "/app?run=url-run&view=review");
    vi.mocked(getResolutionRuns).mockResolvedValue({ total: 0, items: [] });
    vi.mocked(getResolutionOverview).mockResolvedValue({ run_id: "url-run", status: "COMPLETED", phase: "DONE",
      processed_count: 2, total_count: 2, record_count: 2, file_count: 1, source_counts: { CRM: 2 },
      history_count: 0, candidate_count: 1, estimated_entity_count: 1, duplicate_record_count: 1, eligible_merge_group_count: 1,
      counts: { auto: 1, review: 0, separate: 0, rejected: 0, review_cases: 0, manual: 0,
        hard_blocked: 0, insufficient: 0, group_a: 0, group_c: 0, group_d: 0 } });
    render(<ResolutionWorkspace user={steward} onLogout={vi.fn()} />);
    expect(await screen.findByRole("heading", { name: "Cặp cần xem xét" })).toBeInTheDocument();
    expect(getResolutionOverview).toHaveBeenCalledWith("url-run");
    expect(new URLSearchParams(window.location.search).get("run")).toBe("url-run");
    expect(new URLSearchParams(window.location.search).get("view")).toBe("review");
    expect(localStorage.getItem("masterone.resolution-run.v1.user-1")).toBe("url-run");
  });

  it("keeps the last processing state and marks it stale when overview polling loses connection", async () => {
    vi.useFakeTimers();
    window.history.replaceState({}, "", "/app?run=run-1");
    vi.mocked(getResolutionRuns).mockResolvedValue({ total: 0, items: [] });
    const running = { run_id: "run-1", status: "RUNNING", phase: "blocking", processed_count: 0, total_count: 4,
      record_count: 4, file_count: 1, source_counts: { CRM: 4 }, history_count: 0, candidate_count: 0,
      estimated_entity_count: 4, duplicate_record_count: 0, eligible_merge_group_count: 0,
      counts: { auto: 0, review: 0, separate: 0, rejected: 0, review_cases: 0, manual: 0,
        hard_blocked: 0, insufficient: 0, group_a: 0, group_c: 0, group_d: 0 } };
    vi.mocked(getResolutionOverview).mockResolvedValueOnce(running).mockRejectedValueOnce(new Error("offline"));
    render(<ResolutionWorkspace user={steward} onLogout={vi.fn()} />);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(screen.getByText("Đang đối chiếu")).toBeInTheDocument();

    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(screen.getByText("Chưa kết nối được để cập nhật đợt xử lý; đang giữ trạng thái gần nhất.")).toBeInTheDocument();
    expect(screen.getByText("Đang đối chiếu")).toBeInTheDocument();
  });

  it("shows a readable toast and dismisses it after six seconds", async () => {
    vi.useFakeTimers();
    window.history.replaceState({}, "", "/app?run=run-1");
    vi.mocked(getResolutionRuns).mockResolvedValue({ total: 0, items: [] });
    vi.mocked(getResolutionOverview).mockRejectedValue(new Error("Item không còn ở tab Gộp"));
    render(<ResolutionWorkspace user={steward} onLogout={vi.fn()} />);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    const toast = screen.getByRole("status");
    expect(within(toast).getByText("Item không còn ở tab Gộp")).toHaveClass("toast-message");
    act(() => { vi.advanceTimersByTime(6000); });
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
