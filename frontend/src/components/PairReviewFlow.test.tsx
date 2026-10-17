import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import PairReviewFlow from "./PairReviewFlow";
import { decideReviewPair, getPairCategories, getResolutionItem, getResolutionItems, stageReviewPairs } from "@/lib/api";
import type { AuthUser, ResolutionItemDetail } from "@/lib/contracts";

vi.mock("@/lib/api", () => ({ decideReviewPair: vi.fn(), getPairCategories: vi.fn(), getResolutionItem: vi.fn(), getResolutionItems: vi.fn(), stageReviewPairs: vi.fn() }));
const user: AuthUser = { id: "u", email: "u@example.test", role: "STEWARD", csrf_token: "csrf", capabilities: ["read", "approve_pair", "reject_pair"] };
const pair: ResolutionItemDetail = { id: "p", bucket: "review_pairs", kind: "pair", origin: "REVIEW", revision: 7,
  label: "Lan", record_labels: ["Lan", "Lân"], member_count: 2, pair_count: 1, member_record_ids: ["a", "b"],
  primary_group: "A", subgroup: "A1_NAME_PHONE", choices: {}, field_options: {}, missing_fields: [],
  records: [{ id: "a", external_id: "A", source_system: "CRM", raw_fields: { name: "Lan", phone: "0901" } },
    { id: "b", external_id: "B", source_system: "ERP", raw_fields: { name: "Lân", phone: "0901" } }] };
beforeEach(() => {
  vi.mocked(getResolutionItems).mockResolvedValue({ total: 1, items: [pair] });
  vi.mocked(getResolutionItem).mockResolvedValue(pair);
  vi.mocked(getPairCategories).mockResolvedValue([{ primary_group: "A", subgroup: "A1_NAME_PHONE", pair_count: 1 }]);
  vi.mocked(decideReviewPair).mockResolvedValue({ pair_id: "p", proposal_id: "approved", affected_record_count: 2 });
  vi.mocked(stageReviewPairs).mockResolvedValue({ staged_count: 29 });
});
afterEach(() => { cleanup(); vi.resetAllMocks(); });
const props = { user, runId: "run", rejected: false, onChanged: vi.fn().mockResolvedValue(undefined), notify: vi.fn() };

it("confirms only the compared pair with its current revision", async () => {
  render(<PairReviewFlow {...props} />);
  fireEvent.click(await screen.findByRole("button", { name: "So sánh cặp p" }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getAllByRole("columnheader")).toHaveLength(3);
  fireEvent.click(within(dialog).getByRole("button", { name: "Xác nhận gộp" }));
  await waitFor(() => expect(decideReviewPair).toHaveBeenCalledWith("run", "p", "merge", 7));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});
it("keeps a conflict visible and does not pretend the decision was saved", async () => {
  vi.mocked(decideReviewPair).mockRejectedValue(new Error("Cặp đã thay đổi; hãy tải lại"));
  render(<PairReviewFlow {...props} />);
  fireEvent.click(await screen.findByRole("button", { name: "So sánh cặp p" }));
  fireEvent.click((await within(await screen.findByRole("dialog")).findByRole("button", { name: "Xác nhận không gộp" })));
  expect(await screen.findByRole("alert")).toHaveTextContent("Cặp đã thay đổi");
  expect(screen.getByRole("dialog")).toBeInTheDocument();
});
it("keeps viewers read-only and hides decision actions in the rejected group", async () => {
  const { rerender } = render(<PairReviewFlow {...props} user={{ ...user, role: "VIEWER", capabilities: ["read"] }} />);
  fireEvent.click(await screen.findByRole("button", { name: "So sánh cặp p" }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByRole("button", { name: "Xác nhận gộp" })).toBeDisabled();
  expect(within(dialog).getByRole("button", { name: "Xác nhận không gộp" })).toBeDisabled();
  fireEvent.click(within(dialog).getByRole("button", { name: "Đóng" }));
  rerender(<PairReviewFlow {...props} rejected />);
  fireEvent.click(await screen.findByRole("button", { name: "So sánh cặp p" }));
  expect(within(await screen.findByRole("dialog")).queryByRole("button", { name: "Xác nhận gộp" })).not.toBeInTheDocument();
});
it("filters subgroups on the server instead of filtering the current page", async () => {
  render(<PairReviewFlow {...props} />);
  await screen.findByRole("option", { name: /Nhiều thông tin trùng khớp/ });
  fireEvent.change(screen.getByRole("combobox", { name: "Nhóm xem xét" }), { target: { value: "A" } });
  fireEvent.change(screen.getByRole("combobox", { name: "Phân nhóm chi tiết" }), { target: { value: "A1_NAME_PHONE" } });
  await waitFor(() => expect(getResolutionItems).toHaveBeenCalledWith("run", "review_pairs", {
    limit: 25, offset: 0, primary_group: "A", subgroup: "A1_NAME_PHONE",
  }));
});

it("stages all matching pages with an exclusion and warns about default fields", async () => {
  vi.mocked(getResolutionItems).mockResolvedValue({ total: 30, items: [pair] });
  render(<PairReviewFlow {...props} />);
  fireEvent.click(await screen.findByRole("checkbox", { name: /Chọn tất cả 30 cặp trên mọi trang/ }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Chọn cặp p" }));
  fireEvent.click(screen.getByRole("button", { name: "Đưa sang Tự động gộp" }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByText(/giá trị mặc định cho các trường/)).toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole("button", { name: "Đưa sang Tự động gộp" }));
  await waitFor(() => expect(stageReviewPairs).toHaveBeenCalledWith("run", {
    all_matching: true, pair_ids: [], excluded_ids: ["p"],
  }));
  expect(getResolutionItems).not.toHaveBeenCalledWith("run", "review_pairs", expect.objectContaining({ offset: 25 }));
});
