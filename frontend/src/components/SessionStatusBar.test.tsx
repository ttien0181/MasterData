import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import SessionStatusBar from "./SessionStatusBar";

describe("SessionStatusBar", () => {
  it("lists active runs and opens the selected run", () => {
    const onOpenRun = vi.fn();
    render(<SessionStatusBar runStatus="COMPLETED" totalCount={20} canCreate activeTotal={2}
      activeStale={false} onOpenRun={onOpenRun} activeRuns={[{
        run_id: "run-active-1", name: "Nhập tháng 10", status: "RUNNING", phase: "blocking",
        created_at: "2026-10-07T00:00:00Z", record_count: 1200, file_count: 3, files: ["crm.csv", "erp.csv", "bill.csv"],
      }]} />);

    const trigger = screen.getByRole("button", { name: /2 phiên đang xử lý/ });
    fireEvent.click(trigger);
    const panel = screen.getByRole("region", { name: "Các phiên đang xử lý" });
    expect(within(panel).getByText("Nhập tháng 10")).toBeInTheDocument();
    expect(within(panel).getByText("Đang phân giải · Đang đối chiếu")).toBeInTheDocument();
    expect(within(panel).getByText("1.200 bản ghi · 3 file")).toBeInTheDocument();

    fireEvent.click(within(panel).getByRole("button", { name: /Nhập tháng 10/ }));
    expect(onOpenRun).toHaveBeenCalledWith("run-active-1");
    expect(screen.queryByRole("region", { name: "Các phiên đang xử lý" })).not.toBeInTheDocument();
  });

  it("preserves a stale indicator when the active-run summary cannot refresh", () => {
    render(<SessionStatusBar runStatus="COMPLETED" totalCount={0} canCreate activeTotal={1}
      activeStale activeRuns={[]} onOpenRun={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /1 phiên đang xử lý/ }));
    expect(screen.getByRole("status")).toHaveTextContent("Chưa cập nhật được trạng thái mới nhất.");
  });
});
