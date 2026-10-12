import { expect, test } from "@playwright/test";
import type { UploadSession } from "../src/lib/contracts";

for (const theme of ["light", "dark"] as const) {
  test(`reference layout: upload, replacement and session panels in ${theme} mode`, async ({ page }) => {
    await page.addInitScript((value) => localStorage.setItem("masterone.theme", value), theme);
    const runs = [
      { run_id: "run-1", name: "Customer tháng 10", status: "COMPLETED", created_at: new Date().toISOString(), record_count: 4213, file_count: 3, files: ["crm.csv", "erp.csv", "billing.csv"] },
      { run_id: "run-2", name: "Billing tháng 9", status: "FAILED", created_at: "2026-09-30T09:00:00Z", record_count: 1200, file_count: 1, files: ["billing.csv"] },
    ];
    let upload: UploadSession | null = null;
    let lastStatusFilter = "";
    let commitCount = 0;
    await page.route("**/api/v1/**", async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      const path = url.pathname;
      let body: unknown = {};
      if (path.endsWith("/auth/me")) body = { id: "test-user", email: "steward@example.test", role: "STEWARD", csrf_token: "test-csrf", capabilities: ["read", "read_audit", "create_run", "start_run", "manage_run"] };
      else if (path.endsWith("/overview")) body = { run_id: "run-1", status: "COMPLETED", phase: "COMPLETED", record_count: 4213, file_count: 3, history_count: 1, candidate_count: 850, source_counts: { CRM: 2100, ERP: 1500, BILL: 613 }, counts: { auto: 127, review: 38, separate: 3010, insufficient: 16, group_a: 710, group_c: 92, group_d: 48, rejected: 0, review_cases: 38, manual: 0, hard_blocked: 0 } };
      else if (path.endsWith("/analytics")) body = {
        run_id: "run-1", metric_version: "1",
        decisions: { basis: "initial_classification", unit: "source_record", total: 4213, automatic: 1000, review: 203, no_duplicate_detected: 3010, automatic_percent: 23.7 },
        conflicts: { basis: "pending_review_pairs", unit: "pair", review_pair_count: 38, fields: [{ field: "address", count: 20 }, { field: "phone", count: 12 }, { field: "email", count: 8 }, { field: "name", count: 4 }] },
        quality: { sources: [{ source: "CRM", record_count: 2100, valid_fields: 7560, assessed_fields: 8400, expected_fields: 8400, score_percent: 90, coverage_percent: 100 }] },
      };
      else if (path.endsWith("/runs")) {
        const filter = url.searchParams.get("run_status") || "";
        if (url.searchParams.get("limit") !== "5") lastStatusFilter = filter;
        const items = filter ? runs.filter((run) => filter.split(",").includes(run.status)) : runs;
        body = { items, total: items.length, next_cursor: null };
      } else if (path.endsWith("/upload-sessions") && request.method() === "GET") body = { items: upload ? [upload] : [] };
      else if (path.endsWith("/upload-sessions") && request.method() === "POST") {
        const input = request.postDataJSON();
        upload = { session_id: "upload-1", name: input.name, status: "UPLOADING", total_bytes: input.files[0].size, committed_run_id: null, error_message: null, created_at: new Date().toISOString(), updated_at: new Date().toISOString(), files: input.files.map((file: { filename: string; size: number }, index: number) => ({ file_id: `file-${index}`, position: index, filename: file.filename, expected_size: file.size, received_size: 0, status: "UPLOADING", sha256: null, row_count: null, source_counts: {}, validation_error: null, parts: [] })) };
        body = upload;
      } else if (upload && path.endsWith("/replace")) {
        const input = request.postDataJSON();
        upload.status = "UPLOADING";
        upload.files[0].replacement = { ...upload.files[0], file_id: "replacement", filename: input.filename, expected_size: input.size, received_size: 0, status: "UPLOADING", row_count: null, validation_error: null, parts: [], replacement: null };
        body = upload;
      } else if (upload && request.method() === "PUT") {
        const file = upload.files[0].replacement ?? upload.files[0];
        file.received_size = file.expected_size;
      } else if (upload && path.endsWith("/validate")) {
        const file = upload.files[0].replacement ?? upload.files[0];
        const invalid = file.filename === "invalid.csv";
        file.status = invalid ? "ERROR" : "READY";
        file.validation_error = invalid ? "Thiếu cột bắt buộc: record_id" : null;
        file.row_count = invalid ? null : 4213;
        upload.status = file.status as UploadSession["status"];
        if (!invalid && upload.files[0].replacement) upload.files[0] = file;
        body = upload;
      } else if (path.endsWith("/commit")) {
        commitCount++;
        body = { run_id: "run-1", status: "UPLOADED", profile: { row_count: 4213, file_count: 1 } };
      } else if (upload && path.includes("/upload-sessions/")) body = upload;
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
    });

    await page.route("**/api/v1/mdm/runs/*/workflow", route => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ workflow_version: 1 }) }));
  await page.goto("/app/legacy?run=run-1");
    await expect(page.getByText("Bản ghi", { exact: true })).toBeVisible();
    await expect(page.locator(".analytics-decisions")).toBeVisible();
    await page.screenshot({ path: `test-results/overview-${theme}.png`, fullPage: true });
    await page.locator(".sessions-orbit").click();
    const sessions = page.getByRole("dialog", { name: "Lịch sử phiên" });
    await expect(sessions).toHaveClass(/from-right/);
    await expect(sessions.getByRole("button", { name: "Mở phiên Customer tháng 10" })).toBeVisible();
    await expect(sessions.getByRole("button", { name: "Xem tất cả phiên" })).toBeInViewport();
    await sessions.getByRole("button", { name: "Lỗi", exact: true }).click();
    await expect.poll(() => lastStatusFilter).toBe("FAILED,RETRYABLE_FAILED");
    await expect(sessions.getByRole("button", { name: "Mở phiên Billing tháng 9" })).toBeVisible();
    await sessions.getByRole("button", { name: "Tất cả", exact: true }).click();
    await expect(sessions.getByRole("button", { name: "Mở phiên Customer tháng 10" })).toBeVisible();
    await page.screenshot({ path: `test-results/sessions-${theme}.png` });
    await sessions.getByRole("button", { name: "Xem tất cả phiên" }).click();
    await expect(sessions).toHaveClass(/is-expanded/);
    await sessions.locator(".sessions-row").first().click();
    await expect(sessions.getByRole("button", { name: "Mở phiên", exact: true })).toBeVisible();
    await page.screenshot({ path: `test-results/sessions-expanded-${theme}.png` });
    await sessions.getByRole("button", { name: "Đóng bảng bên" }).click();

    await page.locator(".upload-orbit").click();
    const panel = page.getByRole("dialog", { name: "Nạp dữ liệu" });
    await expect(panel).toHaveClass(/from-left/);
    await page.screenshot({ path: `test-results/upload-empty-${theme}.png`, animations: "disabled" });
    await panel.getByLabel("Tên phiên").fill("Customer mới");
    await panel.getByLabel("Chọn các file CSV").setInputFiles({ name: "customer.csv", mimeType: "text/csv", buffer: Buffer.from("record_id,source,name\n1,CRM,Ada\n") });
    await expect(panel.getByRole("button", { name: "Bắt đầu phân giải", exact: true })).toBeEnabled();
    await expect(panel.getByText("4.213 bản ghi", { exact: true })).toBeVisible();
    expect(commitCount).toBe(0);
    await page.screenshot({ path: `test-results/upload-ready-${theme}.png` });
    await panel.getByLabel("Đổi hoặc tiếp tục tải customer.csv").setInputFiles({ name: "invalid.csv", mimeType: "text/csv", buffer: Buffer.from("name\nAda\n") });
    await expect(panel.getByText("Thiếu cột bắt buộc: record_id", { exact: true })).toBeVisible();
    await expect(panel.getByRole("button", { name: "Bắt đầu phân giải", exact: true })).toBeDisabled();
    await expect(panel.getByLabel("Tên phiên")).toHaveValue("Customer mới");
    await page.screenshot({ path: `test-results/upload-error-${theme}.png` });
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(panel.getByRole("button", { name: "Bắt đầu phân giải", exact: true })).toBeInViewport();
    expect(await panel.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
    await page.screenshot({ path: `test-results/upload-mobile-${theme}.png` });
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.goto("/login");
    await expect(page.getByRole("heading", { name: "Đăng nhập" })).toBeVisible();
    await page.screenshot({ path: `test-results/login-${theme}.png` });
  });
}
