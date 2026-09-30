import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ResolutionFlow from "./ResolutionFlow";
import ResolutionWorkspace from "./ResolutionWorkspace";
import { createResolutionExport, getResolutionExportReadiness, getResolutionItem,
  getResolutionItems, getResolutionOverview, getResolutionRuns, getReviewCategories,
  saveResolutionChoices, transitionReviewItems } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  ApiError: class extends Error { status = 0; },
  compareResolutionExport: vi.fn(), createResolutionExport: vi.fn(), downloadResolutionExport: vi.fn(),
  downloadResolutionSource: vi.fn(), explainReviewCategory: vi.fn(),
  getResolutionExportReadiness: vi.fn(), getResolutionExports: vi.fn(), getResolutionItem: vi.fn(),
  getResolutionItems: vi.fn(), getResolutionOverview: vi.fn(), getResolutionRuns: vi.fn(),
  getReviewCategories: vi.fn(), saveResolutionChoices: vi.fn(), setCsrfToken: vi.fn(),
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
    vi.mocked(getResolutionExportReadiness).mockResolvedValue({ merge_count: 1, pending_review_count: 0,
      incomplete_count: 0, incomplete_items: [], can_export: true });
    vi.mocked(saveResolutionChoices).mockResolvedValue({ ...detail, choices: { phone: "b" }, missing_fields: [] });
    vi.mocked(transitionReviewItems).mockResolvedValue({ decision: "merge", changed_count: 1, changed_ids: ["proposal-1"] });
    vi.mocked(createResolutionExport).mockResolvedValue(undefined);
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
    await waitFor(() => expect(saveResolutionChoices).toHaveBeenCalledWith("run-1", "review", "case-1", { phone: "b" }));
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
    await waitFor(() => expect(saveResolutionChoices).toHaveBeenCalledWith("run-1", "review", "case-1", { address: "b", phone: "b" }));
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
    await waitFor(() => expect(saveResolutionChoices).toHaveBeenCalledWith("run-1", "review", "case-1", { phone: "b" }));
  });

  it("allows a partial save in Merge while keeping CSV export blocked", async () => {
    vi.mocked(getResolutionExportReadiness).mockResolvedValue({ merge_count: 1, pending_review_count: 0,
      incomplete_count: 1, incomplete_items: [{ item_id: "case-1", fields: ["address"] }], can_export: false });
    vi.mocked(getResolutionItem).mockResolvedValue({ ...detail, origin: "REVIEW", missing_fields: ["phone", "address"],
      field_options: { ...detail.field_options, address: [
        { source_record_id: "a", source_system: "CRM", external_id: "A", value: "Hà Nội" },
        { source_record_id: "b", source_system: "ERP", external_id: "B", value: "Đà Nẵng" },
      ] },
    });
    render(<ResolutionFlow runId="run-1" stage="auto" onChanged={vi.fn()} notify={vi.fn()} />);
    expect(await screen.findByRole("button", { name: /Xuất CSV sau gộp/ })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /Xem so sánh/ }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("radio", { name: /0901001002/ }));
    expect(within(dialog).getByText(/cần chọn đủ trước khi xuất CSV/i)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Lưu lựa chọn" }));
    await waitFor(() => expect(saveResolutionChoices).toHaveBeenCalledWith("run-1", "merge", "case-1", { phone: "b" }));
    expect(createResolutionExport).not.toHaveBeenCalled();
  });

  it("sends the optional field choice when moving an item from review to merge", async () => {
    render(<ResolutionFlow runId="run-1" stage="review" onChanged={vi.fn()} notify={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: /Xem so sánh/ }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("radio", { name: /0901001002/ }));
    fireEvent.click(within(dialog).getByRole("button", { name: /Đưa sang Gộp/ }));
    await waitFor(() => expect(saveResolutionChoices).toHaveBeenCalledWith("run-1", "review", "case-1", { phone: "b" }));
    await waitFor(() => expect(transitionReviewItems).toHaveBeenCalledWith("run-1", ["case-1"], "merge"));
  });

  it("requires confirmation when exporting with review items still pending", async () => {
    vi.mocked(getResolutionExportReadiness).mockResolvedValue({ merge_count: 1, pending_review_count: 2,
      incomplete_count: 0, incomplete_items: [], can_export: true });
    vi.mocked(getResolutionItems).mockResolvedValue({ total: 0, items: [] });
    render(<ResolutionFlow runId="run-1" stage="auto" onChanged={vi.fn()} notify={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: /Xuất CSV sau gộp/ }));
    expect(createResolutionExport).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Vẫn xuất CSV" }));
    await waitFor(() => expect(createResolutionExport).toHaveBeenCalledWith("run-1", true));
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
