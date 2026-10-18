import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getResolutionAudit } from "@/lib/api";
import AuditTrail from "./AuditTrail";

vi.mock("@/lib/api", () => ({ getResolutionAudit: vi.fn() }));

const event = {
  id: "event-1", run_id: "run-1", actor_user_id: "actor-user-123456",
  pair_id: null, proposal_id: "proposal-1", action: "SESSION_RENAMED",
  details: { from: "CRM.csv", to: "October contacts" }, created_at: "2026-10-07T00:00:00Z",
};

describe("AuditTrail", () => {
  beforeEach(() => {
    vi.mocked(getResolutionAudit).mockResolvedValue({ total: 1, items: [event] });
  });

  it("shows recorded action details and filters by action type", async () => {
    render(<AuditTrail runId="run-1" />);
    expect(await screen.findByText("Đổi tên phiên", {selector: "strong"})).toBeInTheDocument();
    expect(screen.queryByText("SESSION_RENAMED")).not.toBeInTheDocument();
    expect(screen.getByText("1 sự kiện")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Thông tin tra cứu chi tiết"));
    expect(screen.getByText(/October contacts/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Loại thao tác"), { target: { value: "SESSION_CREATED" } });
    fireEvent.click(screen.getByRole("button", { name: "Lọc" }));
    await waitFor(() => expect(getResolutionAudit).toHaveBeenLastCalledWith("run-1", {
      action: "SESSION_CREATED", pair_id: "", proposal_id: "", offset: 0, limit: 25,
    }));
  });

  it("keeps the audit feed paged and exposes retry on API errors", async () => {
    vi.mocked(getResolutionAudit).mockRejectedValueOnce(new Error("Tạm mất kết nối"));
    render(<AuditTrail runId="run-1" />);
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("Tạm mất kết nối")).toBeInTheDocument();
    fireEvent.click(within(alert).getByRole("button", { name: "Thử lại" }));
    expect(await screen.findByText("Đổi tên phiên", {selector: "strong"})).toBeInTheDocument();
  });
});
