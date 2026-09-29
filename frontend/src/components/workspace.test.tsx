import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Home from "../../app/page";

vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn() }) }));
const me = { id: "user-1", email: "steward@example.com", csrf_token: "csrf-test" };

const profile = {
  CRM: { row_count: 1, accepted_count: 1, rejected_count: 0, missing_optional: {}, validation_issue_count: 0 },
  ERP: { row_count: 1, accepted_count: 1, rejected_count: 0, missing_optional: {}, validation_issue_count: 0 },
};

describe("MasterOne workspace", () => {
  beforeEach(() => { localStorage.clear(); vi.spyOn(window, "scrollTo").mockImplementation(() => {}); });
  afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

  it("does not load MDM data before the session is verified", async () => {
    localStorage.setItem("masterone.active-import.v2.user-1", "batch-1");
    const fetchMock = vi.fn().mockResolvedValue(Response.json({ detail: "Authentication required" }, { status: 401 }));
    vi.stubGlobal("fetch", fetchMock);
    render(<Home />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(String(fetchMock.mock.calls[0][0])).toContain("/auth/me");
    expect(screen.queryByText("Nạp dữ liệu")).not.toBeInTheDocument();
  });

  it("uploads both source files and shows the review queue", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation(async (input: string) => {
      if (input.endsWith("/auth/me")) return Response.json(me);
      if (input.endsWith("/imports")) return Response.json({ import_id: "batch-1", profile, candidate_count: 1 }, { status: 201 });
      if (input.endsWith("/imports/batch-1")) return Response.json({ import_id: "batch-1", profile });
      if (input.endsWith("/candidates/candidate-1/decision")) return Response.json({ candidate_id: "candidate-1", decision: "accepted" });
      if (input.endsWith("/candidates/candidate-1/golden-record")) return Response.json({ state: null, fields: {} });
      if (input.endsWith("/imports/batch-1/candidates")) return Response.json([{
        id: "candidate-1", engine_id: "exact-signal-prototype-v1", evidence: [{ field: "email", value: "hello@example.test" }], decision: "pending",
        left: { id: "record-1", source: "CRM", source_record_id: "C-1", raw_fields: { name: "Acme", phone: "0901234567", email: "hello@example.test", address: "12 Lê Lợi, Hà Nội" }, normalized_fields: { name: "Acme", phone: "0901234567", email: "hello@example.test", address: "12 Lê Lợi, Hà Nội" } },
        right: { id: "record-2", source: "ERP", source_record_id: "E-9", raw_fields: { name: "Acme", phone: "0241234567", email: "hello@example.test", address: "12 Le Loi, Ha Noi" }, normalized_fields: { name: "Acme", phone: "0241234567", email: "hello@example.test", address: "12 Le Loi, Ha Noi" } },
      }]);
      throw new Error(`Unexpected request: ${input}`);
    }));
    const { container } = render(<Home />);
    const files = await screen.findAllByLabelText(/file CRM|file ERP/i).catch(() => document.querySelectorAll('input[type="file"]'));
    fireEvent.change(files[0], { target: { files: [new File(["csv"], "crm.csv", { type: "text/csv" })] } });
    fireEvent.change(files[1], { target: { files: [new File(["csv"], "erp.csv", { type: "text/csv" })] } });
    fireEvent.click(screen.getByRole("button", { name: /Phân tích dữ liệu/ }));
    expect((await screen.findAllByText("Acme")).length).toBeGreaterThan(0);
    expect(localStorage.getItem("masterone.active-import.v2.user-1")).toBe("batch-1");
    await waitFor(() => expect(screen.getByText(/EXACT-SIGNAL PROTOTYPE/)).toBeInTheDocument());
    expect(container.querySelectorAll(".data-row.match")).toHaveLength(2);
    expect(container.querySelectorAll(".data-row.different mark").length).toBeGreaterThan(0);
    expect(container.querySelectorAll(".record-card .data-row.different")).toHaveLength(4);
    expect(container.querySelector(".signal-summary-label")).toHaveTextContent("tín hiệu khớp chính xác");
    fireEvent.click(screen.getByRole("button", { name: /Chấp nhận cặp/ }));
    expect(await screen.findByText("Chọn giá trị theo trường")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Đã chấp nhận. Bạn có thể chọn nguồn cho từng trường.");
    expect(container.querySelector(".notice.toast")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Đóng thông báo" }));
    expect(container.querySelector(".notice.toast")).not.toBeInTheDocument();
  });

  it("clears a stale saved import after a 404", async () => {
    localStorage.setItem("masterone.active-import.v2.user-1", "missing-batch");
    vi.stubGlobal("fetch", vi.fn().mockImplementation(async (input: string) => input.endsWith("/auth/me") ? Response.json(me) : Response.json({ detail: "Import not found" }, { status: 404 })));
    render(<Home />);
    expect(await screen.findByText(/Import đã bị xoá/)).toBeInTheDocument();
    expect(localStorage.getItem("masterone.active-import.v2.user-1")).toBeNull();
  });

  it("restores a saved field choice when reopening Golden Record", async () => {
    localStorage.setItem("masterone.active-import.v2.user-1", "batch-1");
    const fetchMock = vi.fn().mockImplementation(async (input: string) => {
      if (input.endsWith("/auth/me")) return Response.json(me);
      if (input.endsWith("/imports/batch-1")) return Response.json({ import_id: "batch-1", profile });
      if (input.endsWith("/imports/batch-1/candidates")) return Response.json([{
        id: "candidate-1", engine_id: "exact-signal-prototype-v1", evidence: [], decision: "accepted",
        left: { id: "record-1", source: "CRM", source_record_id: "C-1", raw_fields: { name: "Acme" }, normalized_fields: { name: "Acme" } },
        right: { id: "record-2", source: "ERP", source_record_id: "E-9", raw_fields: { name: "Acme Inc" }, normalized_fields: { name: "Acme Inc" } },
      }]);
      if (input.endsWith("/candidates/candidate-1/golden-record")) return Response.json({ state: "draft", fields: { name: { value: "Acme", source_record_id: "record-1" } } });
      throw new Error(`Unexpected request: ${input}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(<Home />);
    const sidebarNav = await screen.findByRole("navigation", { name: "Các bước xử lý" });
    expect(screen.queryByRole("navigation", { name: "Điều hướng các bước" })).not.toBeInTheDocument();
    fireEvent.click(within(sidebarNav).getByRole("button", { name: /Golden Record/ }));
    expect(within(sidebarNav).getByRole("button", { name: /Golden Record/ })).toHaveAttribute("aria-current", "page");
    const crmChoice = await screen.findByRole("button", { name: "Chọn Tên khách hàng từ CRM" });
    await waitFor(() => expect(crmChoice).toHaveAttribute("aria-pressed", "true"));
    expect(screen.getByText("ĐÃ LƯU")).toBeInTheDocument();
    expect(screen.getByText("CRM · C-1")).toBeInTheDocument();
    const erpChoice = screen.getByRole("button", { name: "Chọn Tên khách hàng từ ERP" });
    fireEvent.click(erpChoice);
    expect(erpChoice).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("CHƯA LƯU")).toBeInTheDocument();
    expect(container.querySelector(".preview-field.filled b")).toHaveTextContent("Acme Inc");
    expect(screen.getAllByText("ERP · E-9").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: /Lưu Golden Record nháp/ }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/candidates/candidate-1/golden-record"), expect.objectContaining({ method: "PUT" })));
    expect(await screen.findByText("ĐÃ LƯU")).toBeInTheDocument();
  });

  it("keeps presentation controls separate from workflow data", async () => {
    const fetchMock = vi.fn().mockImplementation(async (input: string) => input.endsWith("/auth/logout") ? Response.json({ ok: true }) : Response.json(me));
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(<Home />);
    const themeToggle = await screen.findByRole("button", { name: "Bật giao diện tối" });
    fireEvent.click(themeToggle);
    expect(container.querySelector(".app-shell")).toHaveAttribute("data-theme", "dark");
    expect(localStorage.getItem("masterone.theme")).toBe("dark");
    expect(within(screen.getByRole("navigation", { name: "Các bước xử lý" })).getByRole("button", { name: "Nạp dữ liệu" })).toHaveAttribute("aria-current", "page");
    fireEvent.click(screen.getByRole("button", { name: "Thu gọn thanh bên" }));
    expect(container.querySelector(".app-shell")).toHaveClass("sidebar-collapsed");
    fireEvent.click(screen.getByRole("button", { name: "Mở menu" }));
    expect(container.querySelector(".app-shell")).toHaveClass("drawer-open");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(container.querySelector(".app-shell")).not.toHaveClass("drawer-open");
    fireEvent.click(screen.getByRole("button", { name: "Tài khoản steward@example.com" }));
    fireEvent.click(screen.getByRole("button", { name: "Đăng xuất" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/auth/logout"), expect.anything()));
  });
});
