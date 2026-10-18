import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { getResolutionAnalytics } from "@/lib/api";
import type { ResolutionAnalytics } from "@/lib/contracts";
import SessionAnalytics from "./SessionAnalytics";

vi.mock("@/lib/api", () => ({ getResolutionAnalytics: vi.fn() }));
const metrics: ResolutionAnalytics = { run_id: "r", metric_version: "1",
  decisions: { basis: "initial_classification", unit: "source_record", total: 4, automatic: 1,
    review: 2, no_duplicate_detected: 1, automatic_percent: 25 },
  conflicts: { basis: "pending_review_pairs", unit: "pair", review_pair_count: 1, fields: [{ field: "email", count: 1 }] },
  quality: { basis: "valid_assessed_fields", fields: ["name", "phone", "email", "address"], sources: [
    { source: "CRM", record_count: 2, valid_fields: 6, assessed_fields: 8, expected_fields: 8, score_percent: 75, coverage_percent: 100 },
    { source: "ERP", record_count: 1, valid_fields: 0, assessed_fields: 0, expected_fields: 4, score_percent: null, coverage_percent: 0 },
  ] } };
afterEach(() => { cleanup(); vi.resetAllMocks(); });

it("shows source record counts regardless of quality assessments and the actual distribution", async () => {
  vi.mocked(getResolutionAnalytics).mockResolvedValue(metrics);
  render(<SessionAnalytics runId="r" revision="1" />);
  const chart = await screen.findByRole("img", { name: "Phân loại ban đầu: Đề xuất gộp tự động 25%, Cần xem xét ban đầu 50%, Chưa phát hiện trùng 25%" });
  expect(within(chart).getAllByText("25%")).toHaveLength(2);
  expect(within(chart).getByText("50%")).toBeInTheDocument();
  expect(chart.querySelectorAll("path")).toHaveLength(3);
  expect(chart.querySelectorAll("line")).toHaveLength(3);
  const sources = within(screen.getByLabelText("Số lượng dòng thông tin theo nguồn"));
  expect(sources.getByTitle("CRM: 2 dòng thông tin")).toHaveTextContent("2");
  expect(sources.getByTitle("ERP: 1 dòng thông tin")).toHaveTextContent("1");
  expect(sources.getByText("Tổng 3 dòng thông tin đầu vào từ 2 nguồn.")).toBeInTheDocument();
  expect(sources.queryByText("75")).not.toBeInTheDocument();
  expect(sources.queryByText("Chưa đánh giá")).not.toBeInTheDocument();
});
it("distinguishes initial source rows from pending pairs after a decision", async () => {
  const initial: ResolutionAnalytics = { ...metrics,
    decisions: { ...metrics.decisions, total: 100000, automatic: 44600, review: 1000,
      no_duplicate_detected: 54400, automatic_percent: 44.6 },
    conflicts: { ...metrics.conflicts, review_pair_count: 500 },
  };
  vi.mocked(getResolutionAnalytics).mockResolvedValue(initial);
  const { rerender } = render(<SessionAnalytics runId="r" revision="1" />);
  const distribution = await screen.findByLabelText("Phân loại dòng thông tin ban đầu");
  expect(within(distribution).getByRole("img")).toHaveAttribute("aria-label", "Phân loại ban đầu: Đề xuất gộp tự động 44,6%, Cần xem xét ban đầu 1%, Chưa phát hiện trùng 54,4%");
  expect(within(distribution).getByRole("img").querySelectorAll("line")).toHaveLength(3);
  expect(within(distribution).getByText("Cần xem xét ban đầu").closest("div")?.lastElementChild).toHaveTextContent("1.000 dòng");
  expect(within(distribution).queryByText(/Biểu đồ giữ nguyên phân loại ban đầu/)).not.toBeInTheDocument();
  expect(screen.getByLabelText("Thông tin không khớp trong các cặp đang chờ")).toHaveTextContent("500 cặp đang chờ xem xét");
  rerender(<SessionAnalytics runId="r" revision="1" />);
  expect(getResolutionAnalytics).toHaveBeenCalledTimes(1);
  vi.mocked(getResolutionAnalytics).mockResolvedValue({ ...initial, conflicts: { ...initial.conflicts, review_pair_count: 400 } });
  rerender(<SessionAnalytics runId="r" revision="2" />);
  await screen.findByText(/Trong 400 cặp đang chờ xem xét/);
  expect(within(screen.getByLabelText("Phân loại dòng thông tin ban đầu")).getByText("Cần xem xét ban đầu").closest("div")).toHaveTextContent("1.000 dòng");
  expect(getResolutionAnalytics).toHaveBeenCalledTimes(2);
});
it("shows an error and retries instead of substituting sample metrics", async () => {
  vi.mocked(getResolutionAnalytics).mockRejectedValueOnce(new Error("Không kết nối được máy chủ"));
  render(<SessionAnalytics runId="r" revision="1" />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Không kết nối");
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
  vi.mocked(getResolutionAnalytics).mockResolvedValue(metrics);
  fireEvent.click(screen.getByRole("button", { name: "Tải lại số liệu" }));
  await waitFor(() => expect(screen.getByLabelText("Phân loại dòng thông tin ban đầu")).toBeInTheDocument());
});
it("displays difference count with conflict breakdown for discrepant fields", async () => {
  const customMetrics: ResolutionAnalytics = {
    ...metrics,
    conflicts: {
      basis: "pending_review_pairs",
      unit: "pair",
      review_pair_count: 84,
      fields: [
        { field: "name", count: 16, difference_count: 16, conflict_count: 16 },
        { field: "address", count: 18, difference_count: 18, conflict_count: 0 },
        { field: "phone", count: 0, difference_count: 0, conflict_count: 0 },
        { field: "email", count: 0, difference_count: 0, conflict_count: 0 },
      ],
    },
  };
  vi.mocked(getResolutionAnalytics).mockResolvedValue(customMetrics);
  render(<SessionAnalytics runId="r" revision="1" />);
  const conflictsSection = await screen.findByLabelText("Thông tin không khớp trong các cặp đang chờ");
  expect(within(conflictsSection).getByText("16 (16 xung đột)")).toBeInTheDocument();
  expect(within(conflictsSection).getByText("18 (0 xung đột)")).toBeInTheDocument();
  expect(within(conflictsSection).getByText("Họ tên").closest(".analytics-conflict-row")).toHaveAttribute("data-field", "name");
  expect(within(conflictsSection).getByText("Địa chỉ").closest(".analytics-conflict-row")).toHaveAttribute("data-field", "address");
  expect(within(conflictsSection).getAllByText("0")).toHaveLength(2);
});
