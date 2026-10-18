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
  expect(await screen.findByRole("img", { name: "25% hồ sơ được phân loại tự xử lý" })).toBeInTheDocument();
  const sources = within(screen.getByLabelText("Số lượng hồ sơ theo nguồn"));
  expect(sources.getByTitle("CRM: 2 hồ sơ")).toHaveTextContent("2");
  expect(sources.getByTitle("ERP: 1 hồ sơ")).toHaveTextContent("1");
  expect(sources.getByText("Tổng 3 hồ sơ đầu vào từ 2 nguồn.")).toBeInTheDocument();
  expect(sources.queryByText("75")).not.toBeInTheDocument();
  expect(sources.queryByText("Chưa đánh giá")).not.toBeInTheDocument();
});
it("refreshes after decisions but does not refetch on an unchanged render", async () => {
  vi.mocked(getResolutionAnalytics).mockResolvedValue(metrics);
  const { rerender } = render(<SessionAnalytics runId="r" revision="1" />);
  await screen.findByLabelText("Xung đột theo trường");
  rerender(<SessionAnalytics runId="r" revision="1" />);
  expect(getResolutionAnalytics).toHaveBeenCalledTimes(1);
  vi.mocked(getResolutionAnalytics).mockResolvedValue({ ...metrics, conflicts: { ...metrics.conflicts, review_pair_count: 0, fields: [{ field: "email", count: 0 }] } });
  rerender(<SessionAnalytics runId="r" revision="2" />);
  await screen.findByText("Không còn cặp chờ xem xét.");
  expect(getResolutionAnalytics).toHaveBeenCalledTimes(2);
});
it("shows an error and retries instead of substituting sample metrics", async () => {
  vi.mocked(getResolutionAnalytics).mockRejectedValueOnce(new Error("Không kết nối được máy chủ"));
  render(<SessionAnalytics runId="r" revision="1" />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Không kết nối");
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
  vi.mocked(getResolutionAnalytics).mockResolvedValue(metrics);
  fireEvent.click(screen.getByRole("button", { name: "Tải lại số liệu" }));
  await waitFor(() => expect(screen.getByLabelText("Phân bố quyết định")).toBeInTheDocument());
});
