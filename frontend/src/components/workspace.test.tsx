import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ResolutionFlow from "./ResolutionFlow";
import ResolutionWorkspace from "./ResolutionWorkspace";
import { createResolutionPreview, executeResolutionPreview, getResolutionExportReadiness, getResolutionItem,
  getResolutionItems, getResolutionOverview, getResolutionRuns, getReviewCategories, resolveMergeConflict,
  getSeparateRecords, transitionReviewItems } from "@/lib/api";
import { getSavedChoices } from "@/lib/resolutionChoices";

vi.mock("@/lib/api", () => ({
  ApiError: class extends Error { status = 0; },
  compareResolutionExport: vi.fn(), createResolutionPreview: vi.fn(), executeResolutionPreview: vi.fn(), downloadResolutionExport: vi.fn(),
  downloadResolutionSource: vi.fn(), explainReviewCategory: vi.fn(),
  getResolutionExportReadiness: vi.fn(), getResolutionExports: vi.fn(), getResolutionItem: vi.fn(),
  getResolutionItems: vi.fn(), getResolutionOverview: vi.fn(), getResolutionRuns: vi.fn(), getSeparateRecords: vi.fn(),
  getReviewCategories: vi.fn(), resolveMergeConflict: vi.fn(), setCsrfToken: vi.fn(),
  transitionReviewItems: vi.fn(),
}));

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
  afterEach(() => { cleanup(); vi.useRealTimers(); vi.clearAllMocks(); localStorage.clear(); });

  it("shows equal fields once and lets the steward select a differing source", async () => {
    render(<ResolutionFlow runId="run-1" stage="review" onChanged={vi.fn()} notify={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: /Xem so sánh/ }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("button", { name: /2 trường giống nhau/ })).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(within(dialog).getByRole("button", { name: /2 trường giống nhau/ }));
    expect(within(dialog).getAllByText("same@example.test")).toHaveLength(1);
    expect(within(dialog).getAllByText("An Bình")).toHaveLength(2); // title and shared field
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
    render(<ResolutionFlow runId="run-1" stage="review" onChanged={vi.fn()} notify={vi.fn()} />);
    fireEvent.click(await within(screen.getByLabelText("Nhóm trường hợp review"))
      .findByRole("button", { name: /Nhiều thông tin trùng khớp/ }));
    await waitFor(() => expect(getResolutionItems).toHaveBeenCalledWith("run-1", "review",
      expect.objectContaining({ primary_group: "A", subgroup: "A1_NAME_PHONE" })));
    expect(screen.getByText(/Tên và thông tin liên hệ giống nhau, nhưng chưa đủ điều kiện gộp tự động/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Xem so sánh/ }));
    await waitFor(() => expect(getResolutionItem).toHaveBeenCalledWith("run-1", "review", "case-1", category));
    const dialog = within(await screen.findByRole("dialog"));
    expect(dialog.getByText(/Tên và thông tin liên hệ giống nhau, nhưng chưa đủ điều kiện gộp tự động/)).toBeInTheDocument();
    expect(dialog.getByText("Điện thoại")).toBeInTheDocument();
    expect(dialog.queryByText("Bằng chứng theo cặp")).not.toBeInTheDocument();
  });

  it("lets a conflicting item enter Merge staging and exposes the split action", async () => {
    vi.mocked(getResolutionItems).mockResolvedValue({ total: 1, items: [{ ...reviewItem, can_merge: false }] });
    vi.mocked(getResolutionItem).mockResolvedValue({ ...detail,
      merge_blocked_reason: "Cụm có cặp Không gộp." });
    const props = { runId: "run-1", onChanged: vi.fn(), notify: vi.fn() };
    const { rerender } = render(<ResolutionFlow {...props} stage="review" />);
    fireEvent.click(await screen.findByRole("button", { name: /Xem so sánh/ }));
    const reviewDialog = await screen.findByRole("dialog");
    expect(within(reviewDialog).getByRole("button", { name: /Đưa sang Chờ gộp/ })).toBeEnabled();
    fireEvent.click(within(reviewDialog).getAllByRole("button", { name: "Đóng" })[0]);

    vi.mocked(getResolutionItems).mockResolvedValue({ total: 1, items: [{ ...reviewItem,
      bucket: "merge", kind: "proposal", status: "REVIEW_STAGED_CONFLICT" }] });
    vi.mocked(getResolutionItem).mockResolvedValue({ ...detail, bucket: "merge", kind: "proposal",
      status: "REVIEW_STAGED_CONFLICT", merge_blocked_reason: "Cụm còn cặp Không gộp." });
    vi.mocked(getResolutionExportReadiness).mockResolvedValue({ merge_count: 0, pending_review_count: 0,
      staged_conflict_count: 1, incomplete_count: 0, incomplete_items: [], blockers: [], can_export: false });
    rerender(<ResolutionFlow {...props} stage="auto" />);
    fireEvent.click(await screen.findByRole("button", { name: /Xem so sánh/ }));
    const mergeDialog = await screen.findByRole("dialog");
    expect(within(mergeDialog).queryAllByRole("radio")).toHaveLength(0);
    fireEvent.click(within(mergeDialog).getByRole("button", { name: "Tách cụm an toàn" }));
    await waitFor(() => expect(resolveMergeConflict).toHaveBeenCalledWith("run-1", "case-1"));
  });

  it("keeps keyboard focus inside the comparison dialog and restores it on close", async () => {
    render(<ResolutionFlow runId="run-1" stage="review" onChanged={vi.fn()} notify={vi.fn()} />);
    const opener = await screen.findByRole("button", { name: /Xem so sánh/ });
    opener.focus();
    fireEvent.click(opener);
    const dialog = await screen.findByRole("dialog");
    const close = within(dialog).getAllByRole("button", { name: "Đóng" })[0];
    await waitFor(() => expect(close).toHaveFocus());
    fireEvent.click(close);
    await waitFor(() => expect(opener).toHaveFocus());
  });

  it("shows a skeleton immediately on tab change and ignores a late response from the previous tab", async () => {
    const notify = vi.fn();
    const props = { runId: "run-1", onChanged: vi.fn(), notify };
    const { rerender } = render(<ResolutionFlow {...props} stage="review" />);
    expect(await screen.findByText("An Bình")).toBeInTheDocument();

    let resolveOld!: (value: { total: number; items: typeof reviewItem[] }) => void;
    vi.mocked(getResolutionItems).mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }));
    rerender(<ResolutionFlow {...props} stage="auto" />);
    expect(screen.getByRole("status", { name: "Đang tải danh sách" })).toBeInTheDocument();
    expect(screen.queryByText("An Bình")).not.toBeInTheDocument();

    vi.mocked(getResolutionItems).mockResolvedValueOnce({ total: 1, items: [{ ...reviewItem, id: "new-item", label: "Bản ghi mới" }] });
    rerender(<ResolutionFlow {...props} stage="review" />);
    expect(await screen.findByText("Bản ghi mới")).toBeInTheDocument();
    await act(async () => { resolveOld({ total: 1, items: [{ ...reviewItem, label: "Kết quả cũ" }] }); });
    expect(screen.getByText("Bản ghi mới")).toBeInTheDocument();
    expect(screen.queryByText("Kết quả cũ")).not.toBeInTheDocument();
  });

  it("offers a retry when the list request fails", async () => {
    vi.mocked(getResolutionItems).mockRejectedValueOnce(new Error("Máy chủ tạm thời không phản hồi"));
    render(<ResolutionFlow runId="run-1" stage="review" onChanged={vi.fn()} notify={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Máy chủ tạm thời không phản hồi");
    expect(screen.queryByText("An Bình")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Thử lại" }));
    expect(await screen.findByText("An Bình")).toBeInTheDocument();
  });

  it("shows loading feedback while changing pages", async () => {
    vi.mocked(getResolutionItems).mockResolvedValueOnce({ total: 26, items: [reviewItem] });
    const { container } = render(<ResolutionFlow runId="run-1" stage="review" onChanged={vi.fn()} notify={vi.fn()} />);
    expect(await screen.findByText("An Bình")).toBeInTheDocument();
    let resolvePage!: (value: { total: number; items: typeof reviewItem[] }) => void;
    vi.mocked(getResolutionItems).mockImplementationOnce(() => new Promise((resolve) => { resolvePage = resolve; }));
    fireEvent.click(screen.getByRole("button", { name: "Sau →" }));
    expect(screen.getByRole("status", { name: "Đang tải danh sách" })).toBeInTheDocument();
    expect(container.querySelector(".resolution-item-list")).toHaveAttribute("aria-busy", "true");
    expect(screen.getByRole("button", { name: "Sau →" })).toBeDisabled();
    await act(async () => { resolvePage({ total: 26, items: [{ ...reviewItem, id: "page-two", label: "Trang tiếp theo" }] }); });
    expect(await screen.findByText("Trang tiếp theo")).toBeInTheDocument();
    expect(container.querySelector(".resolution-item-list")).toHaveAttribute("aria-busy", "false");
  });

  it("jumps to a typed page and keeps review actions inside the list toolbar", async () => {
    vi.mocked(getResolutionItems).mockResolvedValue({ total: 76, items: [reviewItem] });
    const { container } = render(<ResolutionFlow runId="run-1" stage="review" onChanged={vi.fn()} notify={vi.fn()} />);
    expect(await screen.findByText("An Bình")).toBeInTheDocument();
    const toolbar = container.querySelector(".resolution-flow-toolbar")!;
    fireEvent.click(screen.getByRole("checkbox", { name: "Chọn An Bình" }));
    expect(within(toolbar as HTMLElement).getByText("1 item đã chọn")).toBeInTheDocument();
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
    });
    render(<ResolutionFlow runId="run-1" stage="review" onChanged={vi.fn()} notify={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: /Xem so sánh/ }));
    const dialog = await screen.findByRole("dialog");
    const addressRow = within(dialog).getByText("Địa chỉ").closest<HTMLElement>(".resolution-field")!;
    expect(within(addressRow).getByText("2 giá trị khác nhau")).toBeInTheDocument();
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

  it("allows saving an incomplete choice and discards unsaved edits on cancel", async () => {
    vi.mocked(getResolutionItem).mockResolvedValue({ ...detail, missing_fields: ["phone", "address"],
      field_options: { ...detail.field_options, address: [
        { source_record_id: "a", source_system: "CRM", external_id: "A", value: "Hà Nội" },
        { source_record_id: "b", source_system: "ERP", external_id: "B", value: "Đà Nẵng" },
      ] },
    });
    render(<ResolutionFlow runId="run-1" stage="review" onChanged={vi.fn()} notify={vi.fn()} />);
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

  it("allows a partial save in Merge while keeping CSV export blocked", async () => {
    vi.mocked(getResolutionExportReadiness).mockResolvedValue({ merge_count: 1, pending_review_count: 0, staged_conflict_count: 0,
      incomplete_count: 1, incomplete_items: [{ item_id: "case-1", fields: ["address"] }], blockers: [], can_export: false });
    vi.mocked(getResolutionItem).mockResolvedValue({ ...detail, origin: "REVIEW", missing_fields: ["phone", "address"],
      field_options: { ...detail.field_options, address: [
        { source_record_id: "a", source_system: "CRM", external_id: "A", value: "Hà Nội" },
        { source_record_id: "b", source_system: "ERP", external_id: "B", value: "Đà Nẵng" },
      ] },
    });
    render(<ResolutionFlow runId="run-1" stage="auto" onChanged={vi.fn()} notify={vi.fn()} />);
    expect(await screen.findByRole("button", { name: /Xem trước CSV kết quả/ })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /Xem so sánh/ }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("radio", { name: /0901001002/ }));
    expect(within(dialog).getByText(/cần chọn đủ trước khi xuất CSV/i)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Lưu lựa chọn" }));
    await waitFor(() => expect(getSavedChoices("run-1", "merge", "case-1", ["a", "b"])).toEqual({ phone: "b" }));
    expect(createResolutionPreview).not.toHaveBeenCalled();
  });

  it("sends the optional field choice when moving an item from review to merge", async () => {
    render(<ResolutionFlow runId="run-1" stage="review" onChanged={vi.fn()} notify={vi.fn()} />);
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
    render(<ResolutionFlow runId="run-1" stage="auto" onChanged={vi.fn()} notify={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: /Xem trước CSV kết quả/ }));
    expect(createResolutionPreview).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Tiếp tục xem trước" }));
    await waitFor(() => expect(createResolutionPreview).toHaveBeenCalledWith("run-1", true));
    fireEvent.click(await screen.findByRole("button", { name: "Xác nhận gộp và xuất CSV" }));
    await waitFor(() => expect(executeResolutionPreview).toHaveBeenCalledWith("run-1", "preview-1", expect.any(String)));
  });

  it("names the item blocking export and opens its comparison", async () => {
    vi.mocked(getResolutionItems).mockResolvedValue({ total: 1, items: [{ ...reviewItem, bucket: "merge", kind: "proposal" }] });
    vi.mocked(getResolutionExportReadiness).mockResolvedValue({ merge_count: 1, pending_review_count: 0,
      staged_conflict_count: 0, incomplete_count: 1, incomplete_items: [{ item_id: "case-1", fields: ["phone"] }],
      blockers: [{ item_id: "case-1", kind: "MISSING_CHOICES", label: "An Bình", message: "Chọn giá trị cho các trường khác nhau.", fields: ["phone"] }], can_export: false });
    render(<ResolutionFlow runId="run-1" stage="auto" onChanged={vi.fn()} notify={vi.fn()} />);
    expect(await screen.findByRole("button", { name: /Xem trước CSV kết quả/ })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /An Bình.*Chọn giá trị/ }));
    expect(await screen.findByRole("dialog", { name: "An Bình" })).toBeInTheDocument();
  });

  it("shows the cluster chart and lets the user inspect separate records", async () => {
    localStorage.setItem("masterone.resolution-run.v1.user-1", "run-1");
    vi.mocked(getResolutionRuns).mockResolvedValue({ total: 0, items: [] });
    vi.mocked(getResolutionOverview).mockResolvedValue({ run_id: "run-1", status: "COMPLETED", phase: "DONE",
      processed_count: 4, total_count: 4, record_count: 4, file_count: 1, source_counts: { CRM: 4 },
      history_count: 0, candidate_count: 2,
      counts: { auto: 1, review: 1, separate: 2, rejected: 0, review_cases: 1, manual: 0,
        hard_blocked: 0, insufficient: 0, group_a: 0, group_c: 0, group_d: 0 } });
    vi.mocked(getSeparateRecords).mockResolvedValue({ total: 2, items: [{ id: "a", external_id: "CRM-1",
      source_system: "CRM", raw_fields: { name: "Nguyễn A", phone: "0901234567" }, review_pending: true }] });
    render(<ResolutionWorkspace user={{ id: "user-1", email: "a@example.test", csrf_token: "csrf" }} onLogout={vi.fn()} />);
    expect(await screen.findByRole("button", { name: /Biểu đồ: Chờ gộp 1 cụm, Cần review 1 cụm/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Dự kiến giữ riêng/ }));
    const dialog = await screen.findByRole("dialog", { name: "Dự kiến giữ riêng" });
    expect(within(dialog).getAllByText("Nguyễn A").length).toBeGreaterThan(0);
    expect(within(dialog).getByText("Còn cần review")).toBeInTheDocument();
    expect(getSeparateRecords).toHaveBeenCalledWith("run-1", 0, "");
  });

  it("shows a readable toast and dismisses it after six seconds", async () => {
    vi.useFakeTimers();
    localStorage.setItem("masterone.resolution-run.v1.user-1", "run-1");
    vi.mocked(getResolutionRuns).mockResolvedValue({ total: 0, items: [] });
    vi.mocked(getResolutionOverview).mockRejectedValue(new Error("Item không còn ở tab Gộp"));
    render(<ResolutionWorkspace user={{ id: "user-1", email: "a@example.test", csrf_token: "csrf" }} onLogout={vi.fn()} />);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    const toast = screen.getByRole("status");
    expect(within(toast).getByText("Item không còn ở tab Gộp")).toHaveClass("toast-message");
    act(() => { vi.advanceTimersByTime(6000); });
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
